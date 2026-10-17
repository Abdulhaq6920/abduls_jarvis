"""An LLM-backed reasoning agent speaks its provider's output and emits no tool requests."""
from typing import List

from jarvis_backend.agents.runtime import AgentServices, build_agent
from jarvis_backend.core.llm import LLMMessage
from jarvis_backend.core.search import DisabledSearch, SearchResult
from jarvis_shared.agents import AgentId, default_agents

SENTINEL = "This is the model's analysis of the data."


class FakeLLM:
    """Minimal provider: records the messages it saw and returns a known sentinel."""

    def __init__(self) -> None:
        self.seen: List[LLMMessage] = []

    async def complete(self, messages: List[LLMMessage]) -> str:
        self.seen = messages
        return SENTINEL


class FakeSearch:
    """Minimal provider: records the query and returns fixed results."""

    def __init__(self, results: List[SearchResult]) -> None:
        self._results = results
        self.query = None

    async def search(self, query: str, *, limit: int = 5) -> List[SearchResult]:
        self.query = query
        return self._results[:limit]


def _spec(agent_id: AgentId):
    return next(s for s in default_agents() if s.id == agent_id)


async def test_llm_agent_speaks_the_provider_output_and_requests_no_tools():
    fake = FakeLLM()
    agent = build_agent(_spec(AgentId.VISION),
                        AgentServices(memory=None, llm=fake, search=DisabledSearch()))

    outcome = await agent.handle("analyse this", {"allowed_paths": ["/tmp/project"], "memories": ["likes tea"]})

    assert outcome.speech == SENTINEL
    assert outcome.tool_requests == []
    # The persona and the user task both reached the provider.
    assert fake.seen[0].role == "system" and "VISION" in fake.seen[0].content
    assert fake.seen[-1].role == "user" and fake.seen[-1].content == "analyse this"


async def test_security_gatekeeper_reasons_without_faking_approvals():
    fake = FakeLLM()
    agent = build_agent(_spec(AgentId.NICK_FURY),
                        AgentServices(memory=None, llm=fake, search=DisabledSearch()))

    outcome = await agent.handle("is deleting this folder safe?", {})

    assert outcome.speech == SENTINEL and outcome.tool_requests == []


async def test_shuri_synthesises_search_results_via_the_llm_and_requests_no_tools():
    fake_llm = FakeLLM()
    results = [
        SearchResult(title="LangGraph overview", url="https://example.com/langgraph",
                     snippet="LangGraph builds stateful multi-agent apps."),
        SearchResult(title="Agent security", url="https://example.com/security",
                     snippet="Agents must gate every action behind approval."),
    ]
    fake_search = FakeSearch(results)
    agent = build_agent(_spec(AgentId.SHURI),
                        AgentServices(memory=None, llm=fake_llm, search=fake_search))

    outcome = await agent.handle("research LangGraph agent security", {})

    # SHURI speaks the LLM synthesis and NEVER emits a tool request (read-only research).
    assert outcome.speech == SENTINEL
    assert outcome.tool_requests == []
    assert fake_search.query == "LangGraph agent security"
    # The persona, the topic and each result's title + url reached the provider.
    system, user = fake_llm.seen[0], fake_llm.seen[-1]
    assert system.role == "system" and "SHURI" in system.content
    assert user.role == "user" and "LangGraph agent security" in user.content
    for r in results:
        assert r.title in user.content and r.url in user.content


async def test_shuri_stays_honest_when_search_is_disabled():
    fake_llm = FakeLLM()
    agent = build_agent(_spec(AgentId.SHURI),
                        AgentServices(memory=None, llm=fake_llm, search=DisabledSearch()))

    outcome = await agent.handle("research agent security", {})

    # Empty results => honest wording, no fabricated finding/URL, no LLM synthesis, no tools.
    assert outcome.tool_requests == []
    assert outcome.speech != SENTINEL
    assert "nothing" in outcome.speech.lower() or "not configured" in outcome.speech.lower()
    assert "http" not in outcome.speech.lower()
