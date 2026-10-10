"""Static checks on the deployment files.

Docker is not available in every environment, so these tests cannot prove an image builds or
a stack starts. What they CAN do is catch the mistakes that don't need a daemon to detect:
broken YAML, secrets in the wrong place, services on the wrong network, a missing file.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = compose["services"]


def test_compose_defines_the_expected_services():
    assert {"migrate", "api", "worker", "nginx", "postgres", "redis"} <= set(SERVICES)


def test_only_nginx_publishes_a_port():
    published = {name for name, svc in SERVICES.items() if svc.get("ports")}
    assert published == {"nginx"}


def test_data_stores_are_not_reachable_from_the_edge_network():
    for name in ("postgres", "redis"):
        assert SERVICES[name]["networks"] == ["data"], name
    assert compose["networks"]["data"]["internal"] is True


def test_worker_is_never_scaled_and_api_does_not_run_the_scheduler():
    assert SERVICES["worker"]["deploy"]["replicas"] == 1
    assert SERVICES["api"]["environment"]["JARVIS_RUN_SCHEDULER"] == "false"


def test_no_secret_value_is_in_the_compose_file_or_environment():
    text = (ROOT / "docker-compose.yml").read_text()
    assert not re.search(r"(?i)(password|secret|token|key)\s*:\s*[\"']?[A-Za-z0-9+/=_-]{16,}", text)
    for name, svc in SERVICES.items():
        env = svc.get("environment") or {}
        for key, value in (env.items() if isinstance(env, dict) else []):
            assert not re.search(r"(?i)(secret|password|token|_key)$", key) or "FILE" in key or "SECRETS_DIR" in key, (name, key)


def test_every_declared_secret_is_a_file_and_every_reference_resolves():
    declared = set(compose["secrets"])
    for svc in SERVICES.values():
        for ref in svc.get("secrets", []):
            assert (ref if isinstance(ref, str) else ref["source"]) in declared
    for spec in compose["secrets"].values():
        assert spec["file"].startswith("./secrets/")


def test_secret_file_names_match_what_the_settings_loader_expects():
    """pydantic-settings reads <secrets_dir>/<env_prefix><field> -> jarvis_jwt_secret etc."""
    from jarvis_backend.config import Settings
    fields = set(Settings.model_fields)
    for name in compose["secrets"]:
        if name.startswith("jarvis_"):
            assert name.removeprefix("jarvis_") in fields, name


@pytest.mark.parametrize("name", ["api", "worker", "migrate"])
def test_backend_containers_are_locked_down(name):
    svc = SERVICES[name]
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]


def test_dockerfile_is_non_root_multistage_and_pinned():
    text = (ROOT / "docker" / "Dockerfile").read_text()
    assert text.count("FROM ") == 2 and "USER 10001:10001" in text
    assert "-c constraints.txt" in text and "|| true" not in text
    assert "COPY .env" not in text and ".env" not in re.findall(r"COPY\s+(\S+)", text)


def test_constraints_are_exact_pins():
    lines = [ln for ln in (ROOT / "constraints.txt").read_text().splitlines() if ln.strip()]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][^\s]*", ln) for ln in lines)


def test_every_file_the_compose_stack_mounts_exists():
    assert (ROOT / "docker" / "nginx.conf").exists() and (ROOT / "docker" / "Dockerfile").exists()
    for svc in SERVICES.values():
        for volume in svc.get("volumes", []):
            if isinstance(volume, str) and volume.startswith("./"):
                assert (ROOT / volume.split(":")[0]).exists(), volume


def test_nginx_config_has_no_dangling_includes_and_hides_internals():
    conf = (ROOT / "docker" / "nginx.conf").read_text()
    includes = re.findall(r"include\s+(\S+);", conf)
    assert all(i.startswith("/etc/nginx/") for i in includes), includes   # nothing that won't exist
    assert "server_tokens off" in conf and "location = /metrics" in conf
    assert conf.count("{") == conf.count("}")
    assert "X-Forwarded-For $remote_addr" in conf and "$proxy_add_x_forwarded_for" not in conf


def test_lowmem_profile_only_shrinks_resources_and_never_weakens_security():
    low = yaml.safe_load((ROOT / "docker-compose.lowmem.yml").read_text())
    assert low["services"]["api"]["deploy"]["replicas"] == 1
    # It may only touch sizing: no ports, networks, secrets, read_only, cap_*, or security_opt.
    forbidden = {"ports", "networks", "secrets", "read_only", "cap_drop", "cap_add", "security_opt",
                 "privileged", "volumes", "user", "image"}
    for name, svc in low["services"].items():
        assert name in SERVICES, name
        assert not forbidden & set(svc), (name, forbidden & set(svc))
    # And it must not be the auto-loaded override file, or it would apply to real deployments.
    assert not (ROOT / "docker-compose.override.yml").exists()


def test_backend_package_ships_the_dashboard():
    """The React app is built into the backend package; the build output is generated, not committed."""
    assert (ROOT / "jarvis-web/index.html").exists() and (ROOT / "jarvis-web/package-lock.json").exists()
    vite = (ROOT / "jarvis-web/vite.config.ts").read_text()
    assert "../jarvis-backend/src/jarvis_backend/web" in vite and "base: '/ui/'" in vite
    assert "assetsInlineLimit: 0" in vite  # the CSP forbids data: URLs for fonts/assets
    assert "web/**/*" in (ROOT / "jarvis-backend/pyproject.toml").read_text()
    assert "jarvis-backend/src/jarvis_backend/web/" in (ROOT / ".gitignore").read_text()


def test_image_builds_the_dashboard_with_node_and_ships_none_of_it():
    dockerfile = (ROOT / "docker/Dockerfile").read_text()
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert any(" AS web" in s and "node:" in s for s in stages)
    runtime = dockerfile.split("AS runtime", 1)[1]
    assert "node" not in runtime.lower().replace("nologin", "")  # Node never reaches the runtime image
    assert "npm ci" in dockerfile  # reproducible: installs exactly what the lockfile pins


def test_dashboard_never_loosens_the_csp_or_injects_html():
    """The strict CSP (no inline script/style) is the XSS backstop; the source must not fight it."""
    index = (ROOT / "jarvis-web/index.html").read_text()
    assert "<style" not in index and "style=" not in index
    assert not [line for line in index.splitlines() if "<script" in line and "src=" not in line]
    sources = [p for p in (ROOT / "jarvis-web/src").rglob("*") if p.suffix in {".ts", ".tsx"} and ".test." not in p.name]
    assert sources, "dashboard sources missing"
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in text and ".innerHTML" not in text, path
        assert "localStorage.setItem('jarvis-token" not in text, path
