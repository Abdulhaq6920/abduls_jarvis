"""Configuration safety and encryption at rest."""
import pytest
from cryptography.fernet import Fernet
from jarvis_backend.config import Settings
from jarvis_backend.crypto import DecryptionError, FieldCipher
from jarvis_backend.storage import MemoryStore
from pydantic import ValidationError
from sqlalchemy import text

STRONG = "Zq8!vN3#rT6@wY1$kP9%mB4^xC7&dF2*hG5"


def prod_settings(**overrides) -> Settings:
    base = dict(
        env="prod", jwt_secret=STRONG, data_key=Fernet.generate_key().decode(),
        database_url="postgresql+asyncpg://u:p@db/jarvis", redis_url="rediss://:pw@cache:6380/0",
        allowed_hosts="jarvis.example.com", public_origin="https://jarvis.example.com",
        metrics_token="m" * 12 + "Q7" * 8,
    )
    return Settings(**{**base, **overrides})


# ------------------------------------------------------------------------- config
def test_valid_production_config_boots():
    assert prod_settings().is_prod


@pytest.mark.parametrize("override, fragment", [
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"jwt_secret": "change-me-change-me-change-me-change-me"}, "placeholder"),
    ({"data_key": None}, "DATA_KEY"),
    ({"database_url": None}, "PostgreSQL"),
    ({"redis_url": None}, "Redis"),
    ({"allowed_hosts": "*"}, "ALLOWED_HOSTS"),
    ({"public_origin": "http://jarvis.example.com"}, "https"),
    ({"metrics_token": None}, "METRICS_TOKEN"),
])
def test_production_refuses_unsafe_config(override, fragment):
    with pytest.raises(ValidationError) as exc:
        prod_settings(**override)
    assert fragment.lower() in str(exc.value).lower()


def test_dev_allows_missing_secrets_but_reports_them():
    s = Settings(env="dev")
    codes = {f.code for f in s.findings()}
    assert {"jwt_secret_weak", "data_key_missing"} <= codes
    assert all(f.level == "warning" for f in s.findings())


def test_secrets_never_appear_in_repr_or_describe():
    s = prod_settings()
    for rendered in (repr(s), str(s), str(s.describe())):
        assert STRONG not in rendered
        assert "pw@cache" not in rendered
        assert "u:p@db" not in rendered


def test_postgres_url_normalised_for_asyncpg():
    s = Settings(env="dev", database_url="postgres://u:p@h:5432/d")
    assert s.effective_database_url == "postgresql+asyncpg://u:p@h:5432/d"
    assert not s.is_sqlite


def test_secrets_directory_is_read(tmp_path, monkeypatch):
    """Docker/Kubernetes secrets: one file per setting, named jarvis_<setting>."""
    (tmp_path / "jarvis_jwt_secret").write_text(STRONG)
    (tmp_path / "jarvis_database_url").write_text("postgresql+asyncpg://u:pw@db/jarvis")
    monkeypatch.chdir(tmp_path)  # no .env here
    s = Settings(_secrets_dir=str(tmp_path))
    assert s.secret("jwt_secret") == STRONG
    assert s.effective_database_url.endswith("@db/jarvis") and not s.is_sqlite


def test_environment_overrides_secret_files(tmp_path, monkeypatch):
    (tmp_path / "jarvis_jwt_secret").write_text("from-the-file-" + "a" * 30)
    monkeypatch.setenv("JARVIS_JWT_SECRET", STRONG)
    monkeypatch.chdir(tmp_path)
    assert Settings(_secrets_dir=str(tmp_path)).secret("jwt_secret") == STRONG


# ------------------------------------------------------------------------- crypto
def test_cipher_roundtrip_and_no_plaintext():
    c = FieldCipher([Fernet.generate_key().decode()])
    token = c.encrypt("my secret note")
    assert "my secret note" not in token and token.startswith("enc:v1:")
    assert c.decrypt(token) == "my secret note"


def test_cipher_key_rotation_keeps_old_data_readable():
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    legacy = FieldCipher([old]).encrypt("written under the old key")
    rotated = FieldCipher([new, old])  # newest first
    assert rotated.decrypt(legacy) == "written under the old key"
    assert rotated.decrypt(rotated.encrypt("x")) == "x"
    # New writes use the FIRST (newest) key: a cipher holding only the old key must be unable
    # to decrypt them. (Comparing ciphertext prefixes is wrong -- the "enc:v1:" tag plus
    # Fernet's constant version byte make the first bytes collide by design, which made this
    # assertion intermittently fail.)
    old_only = FieldCipher([old])
    with pytest.raises(DecryptionError):
        old_only.decrypt(rotated.encrypt("x"))


def test_cipher_rejects_tampering_and_wrong_key():
    c = FieldCipher([Fernet.generate_key().decode()])
    token = c.encrypt("hello")
    with pytest.raises(DecryptionError):
        c.decrypt(token[:-4] + "AAAA")
    with pytest.raises(DecryptionError):
        FieldCipher([Fernet.generate_key().decode()]).decrypt(token)


def test_legacy_plaintext_is_still_readable():
    assert FieldCipher([Fernet.generate_key().decode()]).decrypt("old plain value") == "old plain value"


async def test_database_holds_only_ciphertext(db):
    """The point of encryption at rest: reading the raw column reveals nothing."""
    await MemoryStore(db).add("my bank pin is 4921")
    async with db.session() as s:
        raw = (await s.execute(text("SELECT text FROM memories"))).scalar_one()
    assert "4921" not in raw and raw.startswith("enc:v1:")
    assert (await MemoryStore(db).search("bank pin"))[0].text == "my bank pin is 4921"
