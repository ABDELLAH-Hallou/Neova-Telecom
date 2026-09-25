"""Offline OpenRouter request shape and strict classification validation."""

import json
from types import SimpleNamespace

import pytest

from neova import classifier
from neova.prompt import load
from neova.nodes.french_answer import _answer_prompt


def test_openrouter_uses_strict_schema_and_required_parameters(monkeypatch):
    calls = []
    configured = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            configured.append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({
                    "intent": "internet", "out_of_scope": False,
                    "ambiguous": False, "prompt_injection": False,
                    "reason_candidate": "unknown",
                })) )])

    monkeypatch.setattr(classifier, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("CLASSIFIER_MODEL", "fake-classifier")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    invoke = classifier.openrouter_classifier()
    assert classifier.classify_message("Ma connexion est coupée", invoke).intent == "internet"
    assert len(calls) == 1
    call = calls[0]
    assert configured[0]["max_retries"] == 0
    assert call["model"] == "fake-classifier"
    assert call["temperature"] == 0
    assert call["max_tokens"] == 150
    assert call["extra_body"] == {
        "reasoning": {"enabled": False},
        "provider": {"require_parameters": True},
    }
    fmt = call["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "intent", "out_of_scope", "ambiguous", "prompt_injection", "reason_candidate"
    }
    assert set(schema["properties"]["intent"]["enum"]) == set(classifier.INTENTS)
    assert set(schema["properties"]["reason_candidate"]["enum"]) == set(
        classifier.REASON_CANDIDATES)
    assert call["messages"][0] == {"role": "system", "content": load("classifier.md")}
    assert call["messages"][1] == {"role": "user", "content": "Ma connexion est coupée"}


@pytest.mark.parametrize("raw", [
    '{"intent":"internet","out_of_scope":false}',
    '{"intent":"internet","out_of_scope":false,"ambiguous":false,'
    '"prompt_injection":false,"reason_candidate":"unknown","tool":"book"}',
    '{"intent":"internet","out_of_scope":"false","ambiguous":false,'
    '"prompt_injection":false,"reason_candidate":"unknown"}',
    '```json\n{"intent":"internet"}\n```',
    '{} trailing text',
])
def test_invalid_outputs_are_rejected(raw):
    with pytest.raises(classifier.ClassifierError):
        classifier.classify_message("ignored", lambda _: raw)


def test_answer_instructions_come_from_markdown():
    prompt = _answer_prompt({"input": "Question ?", "api_values": {}, "citations": []})
    assert prompt.startswith(load("answer.md"))
    with pytest.raises(ValueError):
        load("../secrets.md")
