"""The routing layer: explicit naming, LLM classification, and the keyword safety net.

The fallback chain under test is strict and never leaves the process:

    explicit "ask <agent> to ..."  ->  LLM classification  ->  keyword scoring  ->  JARVIS

No network: every LLM here is a fake whose ``complete`` returns a canned string (or raises).
"""
from typing import List

import pytest
from jarvis_backend.config import Settings
from jarvis_backend.core.llm import EchoLLM, LLMMessage
from jarvis_backend.core.router import KeywordRouter, LLMRouter, build_router
from jarvis_shared.agents import AgentId, default_agents

AGENTS = default_agents()


class FakeLLM:
    """Canned-reply provider. Records the prompts it was asked to complete."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: List[List[LLMMessage]] = []

    async def complete(self, messages: List[LLMMessage]) -> str:
        self.prompts.append(messages)
        return self.reply


class BoomLLM:
    async def complete(self, messages: List[LLMMessage]) -> str:
        raise RuntimeError("provider exploded")


# ------------------------------------------------------------------ explicit always wins
async def test_explicit_name_beats_the_model():
    llm = FakeLLM("TONY")
    decision = await LLMRouter(llm).route("Hey JARVIS, ask SHURI to research LangGraph", AGENTS)
    assert decision.agent_id == AgentId.SHURI and decision.explicit is True
    assert decision.task == "research LangGraph"
    assert llm.prompts == []  # a named agent is an instruction: the model is never consulted


# ------------------------------------------------------------------ natural-language routing
async def test_llm_routes_a_phrasing_the_keywords_miss():
    # "analyse" (British spelling) is not in VISION's routing_keywords, so the deterministic
    # router gives up and JARVIS answers. The LLM router closes exactly that gap.
    assert KeywordRouter().decide("analyse this data", AGENTS).agent_id == AgentId.JARVIS

    decision = await LLMRouter(FakeLLM("VISION")).route("analyse this data", AGENTS)
    assert decision.agent_id == AgentId.VISION and decision.explicit is False
    assert decision.task == "analyse this data" and "LLM routed" in decision.reason


async def test_reply_with_surrounding_words_and_punctuation_is_still_parsed():
    decision = await LLMRouter(FakeLLM("I'd route this to shuri.")).route("what's new in AI", AGENTS)
    assert decision.agent_id == AgentId.SHURI


async def test_multi_word_agent_id_is_parsed():
    decision = await LLMRouter(FakeLLM("Dr. Strange")).route("map out the next quarter", AGENTS)
    assert decision.agent_id == AgentId.DR_STRANGE


async def test_prompt_menu_is_derived_from_the_passed_in_agents():
    llm = FakeLLM("JARVIS")
    await LLMRouter(llm).route("say something", AGENTS)
    menu = llm.prompts[0][-1].content
    assert all(spec.id.value in menu for spec in AGENTS)   # new agents are picked up for free
    assert "say something" in menu


# ------------------------------------------------------------------ fallback to keywords
async def test_unusable_reply_falls_back_to_the_keyword_router():
    decision = await LLMRouter(FakeLLM("Sorry, I have no idea.")).route("open notepad", AGENTS)
    assert decision.agent_id == AgentId.FRIDAY and "keyword fallback" in decision.reason


@pytest.mark.parametrize("reply", ["ULTRON", "VISIONARY", "TONYS"])
async def test_an_agent_that_is_not_on_the_roster_falls_back_to_the_keyword_router(reply):
    # Only whole ids from the passed-in roster count, so a hallucinated or near-miss name
    # degrades to keywords instead of resolving to the wrong agent.
    decision = await LLMRouter(FakeLLM(reply)).route("check my inbox", AGENTS)
    assert decision.agent_id == AgentId.NATASHA and "keyword fallback" in decision.reason


async def test_empty_reply_falls_back_to_the_keyword_router():
    decision = await LLMRouter(FakeLLM("   ")).route("tell me a joke", AGENTS)
    assert decision.agent_id == AgentId.JARVIS and "keyword fallback" in decision.reason


async def test_a_raising_provider_never_propagates():
    decision = await LLMRouter(BoomLLM()).route("research agent security", AGENTS)
    assert decision.agent_id == AgentId.SHURI and "keyword fallback" in decision.reason


async def test_echo_llm_is_short_circuited_and_behaves_exactly_like_the_keyword_router():
    # EchoLLM echoes the prompt back and could never name an agent, so the dev/test default
    # must not even call it: routing stays deterministic offline.
    llm_router, keyword = LLMRouter(EchoLLM()), KeywordRouter()
    for utterance in ("open notepad", "ask TONY to check my project", "remember that my car is blue",
                      "schedule a daily report", "hello there", "Hey JARVIS, research tea"):
        llm_decision = await llm_router.route(utterance, AGENTS)
        keyword_decision = keyword.decide(utterance, AGENTS)
        assert llm_decision.agent_id == keyword_decision.agent_id, utterance
        assert llm_decision.explicit == keyword_decision.explicit, utterance
        assert llm_decision.task == keyword_decision.task, utterance


# ------------------------------------------------------------------ factory
def test_build_router_defaults_to_the_llm_router():
    assert isinstance(build_router(Settings(env="dev"), EchoLLM()), LLMRouter)


def test_build_router_honours_the_keyword_pin():
    assert isinstance(build_router(Settings(env="dev", router="keyword"), FakeLLM("TONY")),
                      KeywordRouter)
