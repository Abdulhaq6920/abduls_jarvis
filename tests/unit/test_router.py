"""The routing layer: explicit naming, LLM classification, and the keyword safety net.

The fallback chain under test is strict and never leaves the process:

    explicit "ask <agent> to ..."  ->  LLM classification  ->  keyword scoring  ->  JARVIS

No network: every LLM here is a fake whose ``complete`` returns a canned string (or raises).
"""
from typing import List

import pytest
from jarvis_backend.config import Settings
from jarvis_backend.core.llm import FALLBACK_REPLY, EchoLLM, LLMMessage
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


async def test_a_reply_that_answers_then_explains_itself_is_parsed():
    # The prompt asks for the bare id, but a chatty model answers and then justifies it.
    # Leading with the id is the highest-confidence shape, so it is taken as the answer.
    decision = await LLMRouter(FakeLLM("VISION - best suited to analyse data")).route(
        "analyse this spreadsheet", AGENTS)
    assert decision.agent_id == AgentId.VISION


async def test_prompt_menu_is_derived_from_the_passed_in_agents():
    llm = FakeLLM("JARVIS")
    await LLMRouter(llm).route("say something", AGENTS)
    menu = llm.prompts[0][-1].content
    assert all(spec.id.value in menu for spec in AGENTS)   # new agents are picked up for free
    assert "say something" in menu
    # EVERY routing keyword is listed, not a truncated head of the list: NATASHA's seventh
    # keyword ("meeting") is one the keyword fallback matches, so hiding it from the model
    # would make the two layers disagree about that agent's lane.
    for spec in AGENTS:
        assert all(keyword in menu for keyword in spec.routing_keywords), spec.id


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


@pytest.mark.parametrize("reply", [
    "The JARVIS router would send this to VISION",  # a preamble naming another agent
    "Not TONY, VISION should handle it",            # a negation
    "Either VISION or SHURI could do this",         # a genuine hedge
])
async def test_a_reply_naming_several_agents_is_refused_not_guessed(reply):
    # Picking the earliest (or latest) id in such a reply misroutes AND looks like a
    # successful parse, which is worse than no model at all: here the keyword router gets
    # "open notepad" right. A reply naming more than one agent is ambiguous, so the
    # deterministic router decides and the trace says so.
    decision = await LLMRouter(FakeLLM(reply)).route("open notepad", AGENTS)
    assert decision.agent_id == AgentId.FRIDAY and "keyword fallback" in decision.reason


async def test_the_providers_own_offline_sentence_is_read_as_unavailable():
    # OpenAICompatibleLLM never raises: on a transport error it returns this exact sentence.
    # An outage must be diagnosed as "unavailable", not as a model that answered badly.
    llm = FakeLLM(FALLBACK_REPLY)
    decision = await LLMRouter(llm).route("check my inbox", AGENTS)
    assert decision.agent_id == AgentId.NATASHA
    assert "language model unavailable" in decision.reason


@pytest.mark.parametrize("utterance", ["", "   ", "Hey JARVIS"])
async def test_an_empty_request_never_reaches_the_provider(utterance):
    # A bare wake word or a blank STT result strips to "": there is nothing to classify, and
    # prompting the model with it would hand a blank task to whichever agent it named.
    llm = FakeLLM("VISION")
    decision = await LLMRouter(llm).route(utterance, AGENTS)
    assert decision.agent_id == AgentId.JARVIS and decision.task == ""
    assert llm.prompts == []


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
        # Including the reason: it is shown in the PLAN stage of every request a default
        # install makes, so the short-circuit adds no fallback annotation to it.
        assert llm_decision.reason == keyword_decision.reason, utterance


# ------------------------------------------------------------------ factory
def test_build_router_defaults_to_the_llm_router():
    assert isinstance(build_router(Settings(env="dev"), EchoLLM()), LLMRouter)


def test_build_router_honours_the_keyword_pin():
    assert isinstance(build_router(Settings(env="dev", router="keyword"), FakeLLM("TONY")),
                      KeywordRouter)


# --------------------------------------------------------------- reply parsing (regression)
# A reply naming SEVERAL agents is ambiguous. Resolving it by "first id wins" (or last)
# silently misroutes while still looking like a success, so these must fall back instead.
@pytest.mark.parametrize("reply, expected", [
    # Expected shape: the bare id, optionally with punctuation or a trailing explanation.
    ("VISION", AgentId.VISION),
    ("vision.", AgentId.VISION),
    ("  VISION  ", AgentId.VISION),
    ("DR_STRANGE", AgentId.DR_STRANGE),
    ("Dr. Strange", AgentId.DR_STRANGE),
    ("nick fury", AgentId.NICK_FURY),
    ("VISION - best suited to analyse data", AgentId.VISION),
    # Tolerated: exactly one roster id somewhere in a chatty reply.
    ("Agent: NICK FURY", AgentId.NICK_FURY),
    ("I'd route this to SHURI", AgentId.SHURI),
    # Ambiguous: more than one agent named -> refuse to guess.
    ("The JARVIS router would send this to VISION", None),
    ("Not TONY, VISION should analyse it", None),
    # Unknown / empty.
    ("ULTRON", None),
    ("VISIONARY", None),
    ("", None),
    ("   ", None),
])
def test_parse_agent_resolves_one_agent_or_refuses(reply, expected):
    from jarvis_backend.core.router import _parse_agent

    assert _parse_agent(reply, AGENTS) == expected


async def test_ambiguous_reply_falls_back_to_keyword_routing():
    """An ambiguous reply must route by keyword, and the PLAN reason must say so -- not
    report a confident 'LLM routed to ...' for an agent the model never settled on."""
    router = LLMRouter(FakeLLM("Not TONY, VISION should analyse it"), KeywordRouter())
    decision = await router.route("check my git repo status", AGENTS)
    assert decision.agent_id == AgentId.TONY       # keyword scoring, not the reply order
    assert "keyword fallback" in decision.reason


async def test_blank_utterance_skips_the_model_call():
    """A bare wake word strips to "". There is nothing to classify, so the provider must
    not be called at all (an empty request would route to an arbitrary specialist)."""
    llm = FakeLLM("VISION")
    router = LLMRouter(llm, KeywordRouter())
    decision = await router.route("Hey JARVIS", AGENTS)
    assert decision.agent_id == AgentId.JARVIS
    assert "keyword fallback" in decision.reason
    assert llm.prompts == []                      # no model call was made
