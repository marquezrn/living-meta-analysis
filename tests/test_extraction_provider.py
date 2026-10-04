"""SDK adapter tests with an in-process fake runner, never live API requests."""

from types import SimpleNamespace

import pytest

from livingmeta.agents.provider import AgentsCaller
from livingmeta.domain import ExtractionBatch


@pytest.mark.asyncio
async def test_missing_key_does_not_reserve_or_send(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reservations = []
    caller = AgentsCaller(before_call=lambda *args: reservations.append(args), after_call=lambda *args: None)
    with pytest.raises(RuntimeError, match="no paid extraction"):
        await caller("test", "Synthetic instructions", "Synthetic input", ExtractionBatch)
    assert reservations == []


@pytest.mark.asyncio
async def test_sdk_tools_disabled_and_actual_usage_settled(monkeypatch):
    from agents import Runner
    events = []
    async def run(agent, *, input, max_turns, run_config):
        assert not agent.tools and not agent.mcp_servers
        assert max_turns == 1 and run_config.tracing_disabled
        assert agent.model_settings.retry.max_retries == 0
        return SimpleNamespace(final_output=ExtractionBatch(eligible=False, eligibility_reason="Synthetic review"),
                               context_wrapper=SimpleNamespace(usage=SimpleNamespace(input_tokens=123, output_tokens=34)))
    monkeypatch.setattr(Runner, "run", run)
    caller = AgentsCaller(api_key="synthetic-offline-key", before_call=lambda *args: "reservation",
                          after_call=lambda *args: events.append(args))
    output = await caller("test", "Synthetic instructions", "Synthetic input", ExtractionBatch)
    assert not output.eligible
    assert events == [("reservation", 123, 34)]


@pytest.mark.asyncio
async def test_unknown_charge_settles_reservation_instead_of_releasing(monkeypatch):
    from agents import Runner
    events, released, reserved = [], [], []
    async def run(*args, **kwargs):
        raise RuntimeError("Synthetic connection failure with unknown charge")
    monkeypatch.setattr(Runner, "run", run)
    def reserve(*args):
        reserved.append(args)
        return "reservation"
    caller = AgentsCaller(api_key="synthetic-offline-key", before_call=reserve, after_call=lambda *args: events.append(args),
                          release_call=lambda *args: released.append(args), max_output_tokens=100)
    with pytest.raises(RuntimeError):
        await caller("test", "Synthetic instructions", "Synthetic input", ExtractionBatch)
    assert not released
    assert events == [("reservation", None, None)]
