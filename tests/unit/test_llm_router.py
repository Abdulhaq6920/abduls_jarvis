"""LLM router: cheap-first, strict JSON, and always able to fall back to keywords."""
import pytest
from jarvis_backend.core.llm_router import LLMRouter, parse_choice
from jarvis_shared.agents import AgentId, default_agents

from tests.llm_fakes import ScriptedLLM, llm_error, resp

AGENTS = default_agents()
VALID = {a.id.value for a in AGENTS}


@pytest.mark.parametrize("text,expected", [
    ('{"agent": "SHURI", "confidence": 0.9, "reason": "research"}', (AgentId.SHURI, 0.9)),
    ('Sure! {"agent":"dr strange","confidence":0.7,"reason":"plan"} done', (AgentId.DR_STRANGE, 0.7)),
    ('{"agent": "DR. STRANGE", "confidence": 1, "reason": ""}', (AgentId.DR_STRANGE, 1.0)),
])
def test_parses_a_valid_choice(text, expected):
    agent, confidence, _ = parse_choice(text, VALID)
    assert (agent, confidence) == expected


@pytest.mark.parametrize("text", [
    "", "no json here", "{broken", '{"agent": "ROOT", "confidence": 0.9}',
    '{"agent": "SHURI", "confidence": 7}', '{"agent": "SHURI", "confidence": "high"}', '{"agent": null}', "[1,2]",
])
def test_rejects_unusable_choices(text):
    assert parse_choice(text, VALID) is None


async def test_explicit_requests_never_cost_a_model_call():
    llm = ScriptedLLM()
    d = await LLMRouter(llm).aroute("hey jarvis ask tony to check my project", AGENTS)
    assert d.agent_id == AgentId.TONY and d.explicit and llm.calls == []


async def test_a_clear_keyword_match_is_trusted_without_a_model_call():
    llm = ScriptedLLM()
    d = await LLMRouter(llm).aroute("check my email and calendar", AGENTS)   # 2 NATASHA keywords
    assert d.agent_id == AgentId.NATASHA and llm.calls == []


async def test_the_fast_model_resolves_ambiguous_requests():
    llm = ScriptedLLM(resp('{"agent": "VISION", "confidence": 0.82, "reason": "needs analysis"}'))
    d = await LLMRouter(llm).aroute("what do you make of these quarterly numbers", AGENTS)
    assert d.agent_id == AgentId.VISION and not d.explicit and "VISION" in d.reason
    call = llm.calls[0]
    assert call["role"] == "fast" and call["temperature"] == 0.0 and call["agent"] == "ROUTER"
    assert "quarterly numbers" in call["messages"][1].content


@pytest.mark.parametrize("step", [
    resp("I think maybe SHURI?"),                                              # not JSON
    resp('{"agent": "SHURI", "confidence": 0.2, "reason": "unsure"}'),         # low confidence
    resp('{"agent": "NOBODY", "confidence": 0.99, "reason": "x"}'),            # unknown agent
    llm_error("timeout"), llm_error("credits"), llm_error("circuit_open"),     # model unavailable
])
async def test_any_problem_falls_back_to_the_keyword_decision(step):
    d = await LLMRouter(ScriptedLLM(step)).aroute("tell me something interesting", AGENTS)
    assert d.agent_id == AgentId.JARVIS and not d.explicit


async def test_disabled_model_uses_keywords_only():
    class Off(ScriptedLLM):
        enabled = False

    llm = Off()
    d = await LLMRouter(llm).aroute("tell me something interesting", AGENTS)
    assert d.agent_id == AgentId.JARVIS and llm.calls == []


def test_sync_route_never_calls_a_model():
    llm = ScriptedLLM()
    d = LLMRouter(llm).route("open the file", AGENTS)
    assert d.agent_id == AgentId.FRIDAY and llm.calls == []
