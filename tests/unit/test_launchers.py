"""Both launchers must build their runtime with the CURRENT API. A signature change once left
one of them calling BackendClient(url) and nothing in the suite noticed, because launchers are
only exercised by hand. This keeps them honest without needing a microphone or a screen."""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "jarvis-local-agent" / "src" / "jarvis_local_agent"


@pytest.mark.parametrize("module", ["__main__.py", "voice_app.py"])
def test_launchers_construct_backend_client_from_the_config_object(module):
    tree = ast.parse((SRC / module).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "BackendClient"]
    assert calls, f"{module} never builds a BackendClient"
    for call in calls:
        arg = call.args[0]
        assert "backend_url" not in ast.unparse(arg), f"{module}: BackendClient must receive the config, not a URL"


@pytest.mark.parametrize("module", ["__main__.py", "voice_app.py"])
def test_launchers_load_config_via_the_keyring_aware_loader(module):
    assert "RuntimeConfig.load()" in (SRC / module).read_text(encoding="utf-8")


def test_launcher_modules_import_cleanly():
    import jarvis_local_agent.__main__  # noqa: F401
    import jarvis_local_agent.voice_app  # noqa: F401
