"""LLMService: on/off switch, model fallback, daily budget, metrics hook, and log hygiene."""
import logging
from datetime import UTC, datetime, timedelta

import pytest
from jarvis_backend.config import Settings
from jarvis_backend.llm.budget import TokenBudget
from jarvis_backend.llm.service import LLMService, build_llm_service
from jarvis_backend.llm.types import ChatMessage, LLMError

from tests.llm_fakes import llm_error, resp

MODELS = {"fast": "m/fast", "default": "m/default", "reasoning": "m/reason"}
MSG = [ChatMessage("user", "hi")]


class Provider:
    name = "fake"

    def __init__(self, *script):
        self.script, self.models = list(script), []

    async def chat(self, **kwargs):
        self.models.append(kwargs["model"])
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self):
        return None


class MemoryKV:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return (self.data[key], datetime.now(UTC)) if key in self.data else None

    async def put(self, key, value):
        self.data[key] = value


class BrokenKV:
    async def get(self, key):
        raise RuntimeError("db down")

    put = get


async def test_disabled_service_raises_a_clear_error():
    svc = LLMService(provider=None, models=MODELS)
    assert not svc.enabled
    with pytest.raises(LLMError) as raised:
        await svc.complete(messages=MSG)
    assert raised.value.kind == "disabled"
    assert (await svc.status())["enabled"] is False


async def test_role_selects_the_model():
    p = Provider(resp("a"), resp("b"))
    svc = LLMService(provider=p, models=MODELS)
    await svc.complete(role="fast", messages=MSG)
    await svc.complete(role="reasoning", messages=MSG)
    assert p.models == ["m/fast", "m/reason"]


async def test_host_failures_fall_back_to_the_fallback_model():
    p = Provider(llm_error("unavailable"), resp("from fallback"))
    svc = LLMService(provider=p, models=MODELS, fallback_model="m/backup")
    assert (await svc.complete(messages=MSG)).content == "from fallback"
    assert p.models == ["m/default", "m/backup"]


@pytest.mark.parametrize("kind", ["credits", "auth", "bad_request"])
async def test_errors_that_any_model_would_hit_are_not_retried_elsewhere(kind):
    p = Provider(llm_error(kind))
    svc = LLMService(provider=p, models=MODELS, fallback_model="m/backup")
    with pytest.raises(LLMError):
        await svc.complete(messages=MSG)
    assert p.models == ["m/default"]


async def test_both_models_failing_raises_the_last_error():
    p = Provider(llm_error("timeout"), llm_error("unavailable"))
    svc = LLMService(provider=p, models=MODELS, fallback_model="m/backup")
    with pytest.raises(LLMError) as raised:
        await svc.complete(messages=MSG)
    assert raised.value.kind == "unavailable" and svc.failures == 2


async def test_max_tokens_is_capped_by_the_service():
    seen = {}

    class Spy(Provider):
        async def chat(self, **kwargs):
            seen.update(kwargs)
            return resp("ok")

    await LLMService(provider=Spy(), models=MODELS, max_tokens=100).complete(messages=MSG, max_tokens=99_999)
    assert seen["max_tokens"] == 100


# ------------------------------------------------------------------------ budget
async def test_daily_budget_blocks_once_spent_and_resets_next_day():
    clock = [datetime(2026, 1, 1, 12, tzinfo=UTC)]
    kv = MemoryKV()
    budget = TokenBudget(kv, limit=30, clock=lambda: clock[0])
    p = Provider(resp("a", tokens=20), resp("b", tokens=20), resp("c", tokens=20))
    svc = LLMService(provider=p, models=MODELS, budget=budget)

    await svc.complete(messages=MSG)            # 20 used
    await svc.complete(messages=MSG)            # 40 used (soft cap: checked before the call)
    with pytest.raises(LLMError) as raised:
        await svc.complete(messages=MSG)
    assert raised.value.kind == "budget"
    assert kv.data["llm_usage:20260101"] == {"tokens": 40}

    clock[0] += timedelta(days=1)
    assert (await svc.complete(messages=MSG)).content == "c"
    snap = await budget.snapshot()
    assert snap["used"] == 20 and snap["day"] == "20260102"


async def test_budget_survives_a_restart_by_reading_the_store():
    day = datetime(2026, 1, 1, tzinfo=UTC)
    kv = MemoryKV()
    kv.data["llm_usage:20260101"] = {"tokens": 500}
    budget = TokenBudget(kv, limit=500, clock=lambda: day)
    with pytest.raises(LLMError):
        await budget.check()


async def test_unlimited_budget_never_blocks():
    budget = TokenBudget(MemoryKV(), limit=0)
    await budget.record(10**9)
    await budget.check()


async def test_budget_keeps_working_in_memory_if_the_store_is_down():
    budget = TokenBudget(BrokenKV(), limit=10)
    await budget.record(10)
    with pytest.raises(LLMError):
        await budget.check()


# ------------------------------------------------------- metrics hook and log hygiene
async def test_metrics_hook_gets_a_record_and_cannot_break_a_call():
    records = []
    svc = LLMService(provider=Provider(resp("a", tokens=15), llm_error("timeout")), models=MODELS)
    svc.on_call = records.append
    await svc.complete(messages=MSG, agent="SHURI")
    with pytest.raises(LLMError):
        await svc.complete(messages=MSG)
    assert [(r.outcome, r.agent) for r in records] == [("ok", "SHURI"), ("timeout", "-")]
    assert records[0].prompt_tokens == 10 and records[0].completion_tokens == 5

    svc2 = LLMService(provider=Provider(resp("fine")), models=MODELS)
    svc2.on_call = lambda r: 1 / 0
    assert (await svc2.complete(messages=MSG)).content == "fine"


async def test_prompts_and_completions_never_reach_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    secret_prompt, secret_reply = "MY-SECRET-PROMPT-XYZ", "MY-SECRET-REPLY-XYZ"
    svc = LLMService(provider=Provider(resp(secret_reply), llm_error("unavailable")), models=MODELS)
    await svc.complete(messages=[ChatMessage("user", secret_prompt)])
    with pytest.raises(LLMError):
        await svc.complete(messages=[ChatMessage("user", secret_prompt)])
    assert secret_prompt not in caplog.text and secret_reply not in caplog.text


# -------------------------------------------------------------------- construction
def test_service_is_inert_without_a_key_or_when_switched_off():
    assert not build_llm_service(Settings(), None).enabled
    assert not build_llm_service(Settings(hf_token="hf_x" * 8, llm_enabled=False), None).enabled


async def test_service_is_active_with_a_key_and_status_never_exposes_it():
    key = "hf_abcdefghijklmnopqrstuvwxyz012345"
    svc = build_llm_service(Settings(hf_token=key), None)
    assert svc.enabled
    status = await svc.status()
    assert key not in repr(status) and status["models"]["default"].startswith("openai/")
    await svc.aclose()


def test_settings_never_print_the_key():
    s = Settings(hf_token="hf_abcdefghijklmnopqrstuvwxyz012345")
    assert "hf_abcdefghijklmnop" not in repr(s) and "hf_abcdefghijklmnop" not in str(s.describe())
    assert s.llm_active and s.describe()["llm"] == "on"


def test_insecure_model_endpoint_is_flagged():
    s = Settings(hf_token="hf_abcdefghijklmnopqrstuvwxyz012345", llm_base_url="http://models.example.com/v1")
    assert any(f.code == "llm_base_url_insecure" for f in s.findings())
