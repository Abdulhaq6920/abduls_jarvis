"""Persistence layer: audit chain integrity, approval state machine, memory, stats."""
import asyncio

from jarvis_backend.storage import ApprovalStore, AuditLog, MemoryStore, TurnStore
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
