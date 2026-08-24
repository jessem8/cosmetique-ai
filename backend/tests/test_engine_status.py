from types import SimpleNamespace

from app.routers import generation_v2


class _Runtime:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def health(self):
        return SimpleNamespace(
            ready=True,
            inference_ready=True,
            gpu={"available": True},
            runtime_id="private-ai-runtime",
            phases={
                "container": True,
                "cuda": True,
                "models": True,
                "inference": True,
            },
            engine_mode="native-sam2",
        )


def test_studio_engine_status_projects_private_runtime_health(monkeypatch):
    monkeypatch.setattr(
        generation_v2,
        "AIRuntimeClient",
        lambda **kwargs: _Runtime(),
    )

    payload = generation_v2.get_studio_engine_status(SimpleNamespace())

    assert payload == {
        "status": "ready",
        "ready": True,
        "message": "Runtime GPU prêt pour l'extraction.",
        "runtime": "private-ai-runtime",
        "engine_mode": "native-sam2",
        "gpu": "CUDA disponible",
        "models": ["Grounding DINO", "SAM2.1"],
        "phases": {
            "container": True,
            "cuda": True,
            "models": True,
            "inference": True,
        },
    }
