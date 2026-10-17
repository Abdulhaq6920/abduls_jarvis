"""Persistence layer: audit chain integrity, approval state machine, memory, stats."""
import asyncio

from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.storage import ApprovalStore, AuditLog, MemoryStore, TurnStore, UserStore
from sqlalchemy import text

# --------------------------------------------------------------------------- audit


async def test_audit_chain_verifies(db):
    audit = AuditLog(db)
    for i in range(5):
        await audit.record("evt", agent_id="TONY", n=i)
    result = await audit.verify()
    assert result.ok and result.checked == 5


async def test_audit_detects_edited_entry(db):
    audit = AuditLog(db)
    for i in range(4):
        await audit.record("evt", n=i)
    async with db.session() as s:
        await s.execute(text("UPDATE audit_log SET event='forged' WHERE id=2"))
    result = await audit.verify()
    assert not result.ok and result.first_bad_id == 2


async def test_audit_detects_deleted_entry(db):
    audit = AuditLog(db)
    for i in range(4):
        await audit.record("evt", n=i)
    async with db.session() as s:
        await s.execute(text("DELETE FROM audit_log WHERE id=2"))
    result = await audit.verify()
    assert not result.ok and result.first_bad_id == 3


async def test_audit_concurrent_writers_do_not_fork_chain(db):
    audit = AuditLog(db)
    await asyncio.gather(*[audit.record("evt", n=i) for i in range(25)])
    result = await audit.verify()
    assert result.ok and result.checked == 25


# ----------------------------------------------------------------------- approvals


async def test_approval_happy_path(db):
    store = ApprovalStore(db)
    a = await store.create("NATASHA", "send email", "RESTRICTED", {"x": 1})
    assert a.status == "PENDING"
    assert (await store.resolve(a.id, True, by="admin")).status == "GRANTED"
    assert (await store.claim(a.id)).status == "EXECUTING"
    done = await store.complete(a.id, ok=True, result={"output": "sent"})
    assert done.status == "EXECUTED" and done.executed_at is not None


async def test_approval_cannot_be_resolved_twice(db):
    store = ApprovalStore(db)
    a = await store.create("TONY", "run", "RESTRICTED", None)
    assert await store.resolve(a.id, False, by="admin") is not None
    assert await store.resolve(a.id, True, by="admin") is None  # already DENIED


async def test_approval_claim_has_exactly_one_winner(db):
    store = ApprovalStore(db)
    a = await store.create("TONY", "run", "RESTRICTED", None)
    await store.resolve(a.id, True, by="admin")
    results = await asyncio.gather(*[store.claim(a.id) for _ in range(8)])
    assert sum(r is not None for r in results) == 1


async def test_denied_or_pending_cannot_be_claimed(db):
    store = ApprovalStore(db)
    pending = await store.create("TONY", "run", "RESTRICTED", None)
    assert await store.claim(pending.id) is None
    await store.resolve(pending.id, False, by="admin")
    assert await store.claim(pending.id) is None


async def test_stale_approvals_expire(db):
    store = ApprovalStore(db, ttl_seconds=0)
    a = await store.create("TONY", "run", "RESTRICTED", None)
    await asyncio.sleep(0.01)
    assert await store.resolve(a.id, True, by="admin") is None
    assert (await store.get(a.id)).status == "EXPIRED"


# ------------------------------------------------------------------------- memory


async def test_memory_search_ranks_by_relevance(db):
    mem = MemoryStore(db)
    await mem.add("My car is blue")
    await mem.add("The wifi password is on the fridge")
    await mem.add("My blue car needs an oil change in March")
    hits = await mem.search("what colour is my blue car")
    assert hits[0].text == "My blue car needs an oil change in March"
    assert all("wifi" not in h.text for h in hits)


async def test_memory_search_ignores_stopword_only_queries(db):
    mem = MemoryStore(db)
    await mem.add("something stored")
    assert await mem.search("what is the") == []


async def test_memory_delete(db):
    mem = MemoryStore(db)
    m = await mem.add("temp")
    assert await mem.delete(m.id) is True
    assert await mem.delete(m.id) is False
    assert await mem.count() == 0


# --------------------------------------------------------------------------- turns


async def test_turn_history_is_chronological_and_per_session(db):
    turns = TurnStore(db)
    for i in range(4):
        await turns.add(session_id="s1", agent_id="JARVIS", utterance=f"u{i}", speech="ok",
                        latency_ms=10, trace=[])
    await turns.add(session_id="other", agent_id="JARVIS", utterance="x", speech="ok",
                    latency_ms=10, trace=[])
    hist = await turns.history("s1", limit=3)
    assert [t.utterance for t in hist] == ["u1", "u2", "u3"]


async def test_agent_stats(db):
    turns = TurnStore(db)
    await turns.add(session_id="s", agent_id="SHURI", utterance="a", speech="b", latency_ms=100, trace=[])
    await turns.add(session_id="s", agent_id="SHURI", utterance="a", speech="b", latency_ms=300, trace=[])
    stats = await turns.stats()
    assert stats["SHURI"].turns == 2
    assert stats["SHURI"].avg_latency_ms == 200.0
    assert stats["SHURI"].last_active.tzinfo is not None


# ----------------------------------------------------------------------- mfa one-time state
#
# These prove the version compare-and-set gives single-winner on SQLITE -- the exact
# backend the review flagged, because its dialect emits no ``SELECT ... FOR UPDATE``. Each
# test forces the "both callers read the SAME state before either writes" interleaving
# (via a shared read or ``asyncio.gather``), so the guarantee comes from the checked
# ``rowcount``, not from scheduling luck or a lock the backend ignores.


async def _mfa_user(db) -> str:
    users = UserStore(db)
    user = await users.create("mfauser", await hash_password("pw correct horse battery"))
    return user.id


async def test_set_mfa_pending_refuses_once_enabled(db):
    """Finding #1: a stale enrollment write cannot clear an already-enabled factor."""
    users = UserStore(db)
    uid = await _mfa_user(db)
    assert await users.set_mfa_pending(uid, "SECRETAAAAAAAAAA") is True
    assert await users.enable_mfa(uid, ["r1"], expected_secret="SECRETAAAAAAAAAA",
                                  last_counter=1) is True
    # A later pending write (request that began while disabled) must be refused, and must
    # NOT reset mfa_enabled or wipe the secret/recovery state.
    assert await users.set_mfa_pending(uid, "LATEAAAAAAAAAAAA") is False
    row = await users.by_id(uid)
    assert row.mfa_enabled is True and row.mfa_secret == "SECRETAAAAAAAAAA"
    assert row.mfa_recovery_hashes == ["r1"]


async def test_enable_mfa_single_winner_on_shared_version(db):
    """Finding #2 (enable): two confirmations reading the SAME pending version -- only one
    enables and reveals its recovery set; the loser's stale-version UPDATE touches no row."""
    users = UserStore(db)
    uid = await _mfa_user(db)
    await users.set_mfa_pending(uid, "SHAREDSECRETAAAA")
    # Both callers read the same version before either writes.
    results = await asyncio.gather(
        users.enable_mfa(uid, ["setA"], expected_secret="SHAREDSECRETAAAA", last_counter=1),
        users.enable_mfa(uid, ["setB"], expected_secret="SHAREDSECRETAAAA", last_counter=1),
    )
    assert sum(bool(r) for r in results) == 1
    row = await users.by_id(uid)
    assert row.mfa_enabled is True
    assert row.mfa_recovery_hashes in (["setA"], ["setB"])  # one winner, never merged


async def test_recovery_consume_single_winner_on_shared_version(db):
    """Finding #2 (recovery): two redemptions of the same hash reading the same version --
    exactly one succeeds and exactly one hash is removed, on SQLite without FOR UPDATE."""
    users = UserStore(db)
    uid = await _mfa_user(db)
    await users.set_mfa_pending(uid, "SHAREDSECRETBBBB")
    await users.enable_mfa(uid, ["h1", "h2", "h3"], expected_secret="SHAREDSECRETBBBB",
                           last_counter=1)
    results = await asyncio.gather(
        users.consume_recovery_hash(uid, "h2"),
        users.consume_recovery_hash(uid, "h2"),
    )
    assert sum(bool(r) for r in results) == 1
    remaining = (await users.by_id(uid)).mfa_recovery_hashes
    assert sorted(remaining) == ["h1", "h3"]  # exactly one copy of h2 removed


async def test_enable_mfa_loses_to_a_concurrent_re_pending(db):
    """Version CAS also catches a swapped secret: a confirmation whose version was advanced
    by a concurrent ``set_mfa_pending`` fails closed rather than enabling on a stale proof."""
    users = UserStore(db)
    uid = await _mfa_user(db)
    await users.set_mfa_pending(uid, "FIRSTSECRETAAAAA")
    # A concurrent re-enrollment bumps the version and swaps the secret...
    await users.set_mfa_pending(uid, "SECONDSECRETAAAA")
    # ...so enabling on the first proof is refused (wrong secret / advanced version).
    assert await users.enable_mfa(uid, ["r"], expected_secret="FIRSTSECRETAAAAA",
                                  last_counter=1) is False
    assert (await users.by_id(uid)).mfa_enabled is False
