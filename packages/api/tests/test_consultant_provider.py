"""No outbound network: exercise the real httpx adapter with bounded transport responses."""

import json

import httpx
import pytest
from pydantic import ValidationError
from starlette.requests import Request

from src.consultant.contracts import ContextArgs, Fault, Proposal
from src.consultant.provider import INPUT_TOKENS, Provider
from src.consultant.router import network


async def test_real_adapter_uses_exact_profile_no_retry_and_bounded_body():
    received = []

    async def transport(request):
        received.append(request)
        if len(received) == 1:
            return httpx.Response(500, json={"secret": "upstream-private"})
        return httpx.Response(200, content=b"x" * 65537)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), follow_redirects=False
    ) as client:
        provider = Provider(client, "fixture-only-key")
        with pytest.raises(Fault, match="provider_unavailable"):
            await provider.response([{"role": "user", "content": "fixture"}], False)
        assert len(received) == 1
        payload = json.loads(received[0].content)
        assert payload["model"] == "gpt-6-luna"
        assert payload["reasoning"] == {"effort": "high"}
        assert payload["store"] is False
        assert payload["max_output_tokens"] == 4096
        assert payload["include"] == ["reasoning.encrypted_content"]
        assert payload["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
        assert "metadata" not in payload
        with pytest.raises(Fault, match="invalid_answer"):
            await provider.response([], True)
        with pytest.raises(Fault, match="context_too_large"):
            await provider.response([{"role": "user", "content": "x" * INPUT_TOKENS}], False)
        assert len(received) == 2  # oversized input never reached the HTTP channel


async def test_embedding_dimension_and_nonfinite_output_are_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"embedding": [1.0]}]})
        )
    ) as client:
        with pytest.raises(Fault, match="invalid_answer"):
            await Provider(client, "fixture-only").embedding("product")


def test_forwarded_identity_requires_explicit_trusted_peer():
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(consultant_trusted_proxy_peers=[]))
    scope = {
        "type": "http",
        "client": ("192.0.2.1", 321),
        "headers": [(b"x-real-ip", b"198.51.100.1"), (b"x-forwarded-for", b"203.0.113.1")],
        "app": app,
    }
    assert network(Request(scope)) == "192.0.2.1"
    app.state.consultant_trusted_proxy_peers = ["192.0.2.0/24"]
    assert network(Request(scope)) == "198.51.100.1"
    scope["headers"] = [(b"x-real-ip", b"198.51.100.1, 203.0.113.1")]
    assert network(Request(scope)) == "unknown"


@pytest.mark.parametrize("status", [None, "uncertain", 1])
def test_final_proposal_requires_explicit_supported_status(status):
    proposal = {
        "answer": "Уточните вопрос.",
        "source_ids": [],
        "product_ids": [],
        "open_product_id": None,
    }
    if status is not None:
        proposal["status"] = status
    with pytest.raises(ValidationError):
        Proposal.model_validate(proposal)


def test_context_tool_requires_both_bounded_fields():
    with pytest.raises(ValidationError):
        ContextArgs.model_validate({"product_ids": []})
