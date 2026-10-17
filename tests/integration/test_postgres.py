"""The storage layer against a REAL PostgreSQL server (not SQLite).

Skipped automatically when no Postgres is available. Uses the bundled ``pgserver`` package
(or set JARVIS_TEST_POSTGRES_URL to point at your own server) so these run without Docker.
"""
import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.config import Settings
from jarvis_backend.crypto import FieldCipher, set_cipher
from jarvis_backend.storage import (
    ApprovalStore,
    AuditLog,
    Database,
    MemoryStore,
    TurnStore,
    UserStore,
)


@pytest.fixture(scope="module")
def pg_url(tmp_path_factory):
    explicit = os.environ.get("JARVIS_TEST_POSTGRES_URL")
    if explicit:
        yield explicit
        return
    pgserver = pytest.importorskip("pgserver")
    try:
        server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="stop")
    except Exception as exc:  # no binary for this platform
        pytest.skip(f"cannot start bundled postgres: {exc}")
    uri = server.get_uri()  # postgresql://postgres:@/postgres?host=/tmp/... (unix socket) or tcp
    yield uri
    server.cleanup()


@pytest.fixture
async def pg(pg_url):
    set_cipher(FieldCipher.from_setting(Fernet.generate_key().decode()))
    settings = Settings(env="dev", database_url=pg_url, database_ssl="disable",
                        db_pool_size=5, db_max_overflow=10)
    assert not settings.is_sqlite, settings.effective_database_url
    db = Database(settings)
    async with db.engine.begin() as conn:  # clean slate per test
        from jarvis_backend.storage.models import Base
        await conn.run_sync(Base.metadata.drop_all)
    await db.create_schema()
    yield db
    await db.dispose()


async def test_connects_and_reports_postgresql(pg):
    assert pg.engine.dialect.name == "postgresql" and await pg.ping()


async def test_audit_chain_survives_concurrent_writers_across_separate_connections(pg):
    """Two AuditLog instances == two API replicas: only the Postgres advisory lock stops a fork."""
    replica_a, replica_b = AuditLog(pg), AuditLog(pg)
    await asyncio.gather(*[(replica_a if i % 2 else replica_b).record("evt", n=i) for i in range(40)])
    result = await replica_a.verify()
    assert result.ok and result.checked == 40, result


async def test_approval_claim_has_exactly_one_winner_on_postgres(pg):
    store = ApprovalStore(pg)
    item = await store.create("TONY", "run git status", "RESTRICTED", {"a": 1})
    await store.resolve(item.id, True, by="admin")
    wins = await asyncio.gather(*[ApprovalStore(pg).claim(item.id) for _ in range(12)])
    assert sum(w is not None for w in wins) == 1


async def test_encrypted_columns_roundtrip_and_are_ciphertext_in_postgres(pg):
    from sqlalchemy import text
    await MemoryStore(pg).add("the safe combination is 31-4-15")
    async with pg.session() as s:
        raw = (await s.execute(text("SELECT text FROM memories"))).scalar_one()
    assert "31-4-15" not in raw and raw.startswith("enc:v1:")
    assert (await MemoryStore(pg).search("safe combination"))[0].text.endswith("31-4-15")


async def test_timestamps_are_timezone_aware_and_stats_aggregate(pg):
    turns = TurnStore(pg)
    await turns.add(session_id="s", agent_id="SHURI", utterance="a", speech="b", latency_ms=50, trace=[])
    await turns.add(session_id="s", agent_id="SHURI", utterance="a", speech="b", latency_ms=150, trace=[])
    stats = (await turns.stats())["SHURI"]
    assert stats.turns == 2 and stats.avg_latency_ms == 100.0 and stats.last_active.tzinfo is not None


# ------------------------------------------------------------------ mfa one-time state
# True parallel transactions on real Postgres (separate UserStore instances == separate
# connections) prove the version compare-and-set yields a single winner, complementing the
# SQLite proofs in test_storage.py. These do not rely on FOR UPDATE.


async def _pg_mfa_user(pg) -> str:
    user = await UserStore(pg).create("mfauser", await hash_password("pw correct horse battery"))
    return user.id


async def test_enable_mfa_has_exactly_one_winner_on_postgres(pg):
    """Two concurrent confirmations across separate connections: only one enables MFA and
    reveals its recovery set -- the version CAS, not a lock SQLite would ignore, is what
    guarantees it."""
    uid = await _pg_mfa_user(pg)
    await UserStore(pg).set_mfa_pending(uid, "SHAREDSECRETAAAA")
    results = await asyncio.gather(
        UserStore(pg).enable_mfa(uid, ["setA"], expected_secret="SHAREDSECRETAAAA", last_counter=1),
        UserStore(pg).enable_mfa(uid, ["setB"], expected_secret="SHAREDSECRETAAAA", last_counter=1),
    )
    assert sum(bool(r) for r in results) == 1
    row = await UserStore(pg).by_id(uid)
    assert row.mfa_enabled is True and row.mfa_recovery_hashes in (["setA"], ["setB"])


async def test_recovery_consume_has_exactly_one_winner_on_postgres(pg):
    """Two concurrent redemptions of the same recovery hash across separate connections:
    exactly one succeeds and exactly one hash is removed (single-use under real concurrency)."""
    uid = await _pg_mfa_user(pg)
    await UserStore(pg).set_mfa_pending(uid, "SHAREDSECRETBBBB")
    await UserStore(pg).enable_mfa(uid, ["h1", "h2", "h3"], expected_secret="SHAREDSECRETBBBB",
                                   last_counter=1)
    results = await asyncio.gather(
        UserStore(pg).consume_recovery_hash(uid, "h2"),
        UserStore(pg).consume_recovery_hash(uid, "h2"),
    )
    assert sum(bool(r) for r in results) == 1
    remaining = (await UserStore(pg).by_id(uid)).mfa_recovery_hashes
    assert sorted(remaining) == ["h1", "h3"]


async def test_stale_enrollment_cannot_disable_enabled_mfa_on_postgres(pg):
    """A late pending write across a separate connection must be refused once MFA is on, so
    it cannot clear a just-activated factor."""
    uid = await _pg_mfa_user(pg)
    await UserStore(pg).set_mfa_pending(uid, "SECRETAAAAAAAAAA")
    await UserStore(pg).enable_mfa(uid, ["r1"], expected_secret="SECRETAAAAAAAAAA", last_counter=1)
    assert await UserStore(pg).set_mfa_pending(uid, "LATEAAAAAAAAAAAA") is False
    row = await UserStore(pg).by_id(uid)
    assert row.mfa_enabled is True and row.mfa_secret == "SECRETAAAAAAAAAA"
    assert row.mfa_recovery_hashes == ["r1"]


async def test_stale_disable_cannot_clear_a_rotated_factor_on_postgres(pg):
    """Finding #1 on real Postgres: a disable that authenticated generation G must not clear
    a replacement G+n enrolled in between. Clearing is bound to the proved state VERSION, so
    the stale request affects 0 rows and the replacement survives.

        A: authenticate generation G -> reads mfa_state_version == v_g
        B: disable G, enroll+enable the replacement across a SEPARATE connection
        A: disable_mfa(expected_version=v_g) -- refused; replacement stays enabled
    """
    uid = await _pg_mfa_user(pg)
    await UserStore(pg).set_mfa_pending(uid, "SECRETGGGGGGGGGG")
    await UserStore(pg).enable_mfa(uid, ["g1"], expected_secret="SECRETGGGGGGGGGG", last_counter=1)
    v_g = (await UserStore(pg).by_id(uid)).mfa_state_version
    # Request B (separate connection) rotates the factor to a replacement generation.
    assert await UserStore(pg).disable_mfa(uid, expected_version=v_g) is True
    await UserStore(pg).set_mfa_pending(uid, "SECRETNNNNNNNNNN")
    await UserStore(pg).enable_mfa(uid, ["n1"], expected_secret="SECRETNNNNNNNNNN", last_counter=1)
    # Request A's delayed clear, bound to the retired version v_g, must lose.
    assert await UserStore(pg).disable_mfa(uid, expected_version=v_g) is False
    row = await UserStore(pg).by_id(uid)
    assert row.mfa_enabled is True and row.mfa_secret == "SECRETNNNNNNNNNN"
    assert row.mfa_recovery_hashes == ["n1"]
