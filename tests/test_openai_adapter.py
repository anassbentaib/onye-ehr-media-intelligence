import json

import httpx
import openai
import pytest

from ehr_media.summaries import openai_draft


def response_payload(content):
    return {"id": "resp_test", "object": "response", "created_at": 1,
            "status": "completed", "error": None, "incomplete_details": None,
            "model": "gpt-4.1-mini", "instructions": None, "metadata": {},
            "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "output": [{"type": "message", "id": "msg_test", "status": "completed", "role": "assistant", "content": content}]}


def install_client(monkeypatch, handler):
    client = openai.OpenAI(api_key="test-key-not-real", max_retries=0,
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: client)
    return client


def test_real_sdk_request_and_structured_response(monkeypatch):
    sources = [{"id": "r-1", "title": "X-ray", "date": "2026-10-15", "kind": "imaging", "text": "No evidence of pneumonia."}]
    expected = {"chief_concern": None, "key_diagnoses": [], "recent_media": [
        {"text": "No evidence of pneumonia.", "source_id": "r-1"}], "flagged_anomalies": []}
    def handler(request):
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        assert "untrusted" in payload["input"][0]["content"]
        return httpx.Response(200, json=response_payload([{"type": "output_text", "annotations": [], "text": json.dumps(expected)}]))
    client = install_client(monkeypatch, handler)
    try:
        assert openai_draft(sources, "gpt-4.1-mini").model_dump() == expected
    finally:
        client.close()


def test_refusal_fails_explicitly(monkeypatch):
    client = install_client(monkeypatch, lambda request: httpx.Response(200, json=response_payload([
        {"type": "refusal", "refusal": "Cannot summarize"}])))
    try:
        with pytest.raises(ValueError, match="refusal"):
            openai_draft([], "gpt-4.1-mini")
    finally:
        client.close()


def test_upstream_failure_does_not_become_demo(monkeypatch):
    client = install_client(monkeypatch, lambda request: httpx.Response(503, json={"error": {"message": "Unavailable"}}))
    try:
        with pytest.raises(openai.APIStatusError):
            openai_draft([], "gpt-4.1-mini")
    finally:
        client.close()


def test_missing_key_is_actionable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        openai_draft([], "gpt-4.1-mini")
