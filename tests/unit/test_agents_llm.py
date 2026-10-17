"""An LLM-backed reasoning agent speaks its provider's output and emits no tool requests."""
from typing import List

from jarvis_backend.agents.runtime import AgentServices, build_agent
from jarvis_backend.core.llm import LLMMessage
from jarvis_shared.agents import AgentId, default_agents

SENTINEL = "This is the model's analysis of the data."


class FakeLLM:
    """Minimal provider: records the messages it saw and returns a known sentinel."""

    def __init__(self) -> None:
        self.seen: List[LLMMessage] = []

    async def complete(self, messages: List[LLMMessage]) -> str:
        self.seen = messages
        return SENTINEL


def _spec(agent_id: AgentId):
    return next(s for s in default_agents() if s.id == agent_id)


async def test_llm_agent_speaks_the_provider_output_and_requests_no_tools():
    fake = FakeLLM()
    agent = build_agent(_spec(AgentId.VISION), AgentServices(memory=None, llm=fake))

    outcome = await agent.handle("analyse this", {"allowed_paths": ["/tmp/project"], "memories": ["likes tea"]})

    assert outcome.speech == SENTINEL
    assert outcome.tool_requests == []
    # The persona and the user task both reached the provider.
    assert fake.seen[0].role == "system" and "VISION" in fake.seen[0].content
    assert fake.seen[-1].role == "user" and fake.seen[-1].content == "analyse this"


async def test_security_gatekeeper_reasons_without_faking_approvals():
    fake = FakeLLM()
    agent = build_agent(_spec(AgentId.NICK_FURY), AgentServices(memory=None, llm=fake))

    outcome = await agent.handle("is deleting this folder safe?", {})

    assert outcome.speech == SENTINEL and outcome.tool_requests == []
