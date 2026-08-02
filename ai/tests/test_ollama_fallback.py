from __future__ import annotations

import sys
import types

import pytest

from ai import colab_cosmetic_poster_pipeline as pipeline


def _valid_response() -> dict[str, object]:
    return {
        "brand": "Rexona",
        "product_name": "Shower Fresh",
        "category": "deodorant",
        "titre": "Fraîcheur au quotidien",
        "sous_titre": "Une sensation propre et légère.",
        "bullets": [],
        "cta": "Découvrir",
        "hashtags": ["#Rexona"],
        "_meta": {
            "ocr_text": "Rexona Shower Fresh",
            "source": "ocr+metadata",
        },
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("localhost:11434", "http://127.0.0.1:11434"),
        ("0.0.0.0:11434", "http://127.0.0.1:11434"),
        ("http://0.0.0.0:11434", "http://127.0.0.1:11434"),
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
    ],
)
def test_ollama_host_is_a_valid_client_url(raw: str, expected: str) -> None:
    assert pipeline.normalize_ollama_host(raw) == expected


def test_copy_tries_configured_fallback_models(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, host: str) -> None:
            assert host == pipeline.OLLAMA_HOST

        def chat(self, *, model: str, **_: object) -> object:
            calls.append(model)
            if model == "primary":
                raise RuntimeError("primary model unavailable")
            return types.SimpleNamespace(
                message=types.SimpleNamespace(content='{"brand":"Rexona","product_name":"Shower Fresh","category":"deodorant","titre":"Fraîcheur au quotidien","sous_titre":"Une sensation propre et légère.","bullets":[],"cta":"Découvrir","hashtags":["#Rexona"],"_meta":{"ocr_text":"Rexona Shower Fresh","source":"ocr+metadata"}}')
            )

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(pipeline, "OLLAMA_MODEL", "primary")
    monkeypatch.setattr(pipeline, "OLLAMA_FALLBACK_MODELS", ("fallback-qwen", "fallback-mistral"))

    result = pipeline.generate_marketing_copy(
        {"text": "Rexona Shower Fresh"},
        {"brand": "Rexona", "product_name": "Shower Fresh", "category": "deodorant"},
    )

    assert result["brand"] == "Rexona"
    assert calls == ["primary", "primary", "fallback-qwen"]


def test_copy_error_reports_models_attempted(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, host: str) -> None:
            pass

        def chat(self, *, model: str, **_: object) -> object:
            raise RuntimeError(f"{model} unavailable")

    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(pipeline, "OLLAMA_MODEL", "primary")
    monkeypatch.setattr(pipeline, "OLLAMA_FALLBACK_MODELS", ("fallback-qwen",))

    with pytest.raises(pipeline.PipelineError, match="primary.*fallback-qwen"):
        pipeline.generate_marketing_copy(
            {"text": "Rexona Shower Fresh"},
            {"brand": "Rexona", "product_name": "Shower Fresh", "category": "deodorant"},
        )
