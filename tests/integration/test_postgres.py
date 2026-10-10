"""The storage layer against a REAL PostgreSQL server (not SQLite).

Skipped automatically when no Postgres is available. Uses the bundled ``pgserver`` package
(or set JARVIS_TEST_POSTGRES_URL to point at your own server) so these run without Docker.
"""
import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from jarvis_backend.config import Settings
from jarvis_backend.crypto import FieldCipher, set_cipher
from jarvis_backend.storage import ApprovalStore, AuditLog, Database, MemoryStore, TurnStore


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
