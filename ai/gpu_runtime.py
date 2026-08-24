"""Adaptive private product-extraction runtime.

The runtime uses native Grounding DINO + SAM2 only on capable GPUs. On
Pascal-class or otherwise unsupported GPUs it uses the CPU ONNX U2Net fallback.
No background-generation pipeline is loaded.

Runtime lifecycle is explicit so health probes never confuse a live container
with an inference-ready model process.
"""
from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class RuntimeReadiness:
    """Observable readiness gates for the private runtime."""

    container_up: bool = True
    cuda_ready: bool = False
    models_loading: bool = False
    inference_ready: bool = False
    engine_mode: str = "unavailable"
    reason: str | None = None

    @property
    def status(self) -> str:
        if not self.container_up:
            return "container_down"
        if self.models_loading:
            return "models_loading"
        if self.inference_ready:
            return "inference_ready"
        if not self.cuda_ready:
            return "cuda_unavailable"
        return "inference_unavailable"

    def payload(self) -> dict[str, Any]:
        return {
            "service": "campaign-studio-ai-runtime",
            "status": self.status,
            "container_up": self.container_up,
            "cuda_ready": self.cuda_ready,
            "models_loading": self.models_loading,
            "inference_ready": self.inference_ready,
            "engine_mode": self.engine_mode,
            "model_cache": _env("MODEL_CACHE_ROOT", "/models"),
            "artifact_root": _env("ARTIFACT_ROOT", "/artifacts"),
            "config_audit": {
                "HF_TOKEN": "SET" if _env("HF_TOKEN") else "UNSET",
                "AI_SERVICE_TOKEN": "SET" if _env("AI_SERVICE_TOKEN") else "UNSET",
                "BACKGROUND_GENERATION": "DISABLED",
                "GPU_RUNTIME_MODE": _env("GPU_RUNTIME_MODE", "auto"),
            },
            "reason": self.reason,
        }


class GpuRuntime:
    """Load the real GPU service once and expose a private ASGI app.

    Native model loading is synchronous work, so production starts it in a
    daemon thread after the health endpoint is reachable. This keeps the
    platform responsive and lets the UI report models_loading honestly.
    Tests can pass eager=False to keep model loading disabled.
    """

    def __init__(self, *, service: Any | None = None, eager: bool | None = None) -> None:
        self.state = RuntimeReadiness()
        self.service = service
        self.service_app: Any | None = None
        self._load_lock = Lock()
        self._load_thread: Thread | None = None
        self.auto_load = eager is not False
        should_eager = eager if eager is not None else _env("GPU_RUNTIME_EAGER_LOAD", "0") in {"1", "true", "yes"}
        if service is not None:
            self.state.cuda_ready = True
            self.state.inference_ready = True
            self.state.engine_mode = "test"
            self._mount_service(service)
        elif should_eager:
            self.load()

    def start_background_load(self) -> bool:
        """Start one model load without blocking the ASGI event loop."""

        with self._load_lock:
            if self.state.inference_ready or self.state.models_loading:
                return False
            self.state.models_loading = True
            self._load_thread = Thread(
                target=self.load,
                name="product-extraction-model-loader",
                daemon=True,
            )
            self._load_thread.start()
            return True

    @staticmethod
    def _cuda_probe() -> tuple[bool, str | None]:
        """Verify CUDA and one real tensor op on the target Blackwell GPU."""

        try:
            import torch
        except ImportError:
            return False, "PyTorch is unavailable"
        try:
            if not torch.cuda.is_available():
                return False, "CUDA is unavailable"
            capability = tuple(torch.cuda.get_device_capability())
            required = tuple(
                int(part)
                for part in _env("GPU_RUNTIME_REQUIRED_COMPUTE", "12.0").split(".", 1)
            )
            if capability != required:
                return False, f"unsupported CUDA capability {capability}; expected {required}"
            probe = torch.ones((1,), device="cuda", dtype=torch.float16)
            torch.cuda.synchronize()
            if float(probe.item()) != 1.0:
                return False, "CUDA tensor probe returned an invalid value"
            return True, None
        except Exception as exc:
            return False, f"CUDA probe failed: {type(exc).__name__}"

    @staticmethod
    def _cuda_available() -> bool:
        ready, _ = GpuRuntime._cuda_probe()
        return ready

    def load(self) -> None:
        """Resolve persistent model snapshots and construct the V2 service.

        The compatibility import is lazy and confined to this function. No
        notebook, tunnel, or interactive startup path is executed.
        """

        self.state.models_loading = True
        self.state.reason = None
        mode = _env("GPU_RUNTIME_MODE", "auto").casefold()
        self.state.cuda_ready, cuda_reason = self._cuda_probe()

        token = _env("AI_SERVICE_TOKEN")
        if not token:
            self.state.models_loading = False
            self.state.inference_ready = False
            self.state.reason = "AI_SERVICE_TOKEN is required"
            return

        try:
            from ai.gpu_service import build_cpu_service, build_service, create_app

            if mode not in {"auto", "native-sam2", "cpu-u2net"}:
                raise RuntimeError("GPU_RUNTIME_MODE must be auto, native-sam2, or cpu-u2net")
            use_cpu = mode == "cpu-u2net" or (mode == "auto" and not self.state.cuda_ready)
            if use_cpu:
                self.service = build_cpu_service(cache_dir=_env("MODEL_CACHE_ROOT", "/models"))
                self.state.engine_mode = "cpu-u2net"
                self.state.reason = cuda_reason or "CPU extraction mode selected"
            else:
                self.service = build_service(
                    hf_token=_env("HF_TOKEN") or None,
                    cache_dir=_env("MODEL_CACHE_ROOT", "/models"),
                )
                self.state.engine_mode = "native-sam2"
            self.service_app = create_app(self.service)
            self.state.inference_ready = True
        except Exception as exc:
            self.service = None
            self.service_app = None
            self.state.inference_ready = False
            self.state.engine_mode = "unavailable"
            self.state.reason = f"{type(exc).__name__}: {str(exc)}"[:500]
        finally:
            self.state.models_loading = False

    def _mount_service(self, service: Any) -> None:
        try:
            from ai.gpu_service import create_app

            self.service_app = create_app(service)
        except Exception as exc:
            self.service_app = None
            self.state.inference_ready = False
            self.state.reason = f"{type(exc).__name__}: {str(exc)}"[:500]


def _authorized(request: Request) -> bool:
    expected = _env("AI_SERVICE_TOKEN")
    supplied = request.headers.get("authorization", "")
    if not expected or not supplied.startswith("Bearer "):
        return False
    return hmac.compare_digest(supplied, f"Bearer {expected}")


def create_app(*, runtime: GpuRuntime | None = None, service: Any | None = None, eager: bool | None = None) -> FastAPI:
    """Build the private app; service is injectable for CPU contract tests."""

    runtime = runtime or GpuRuntime(service=service, eager=eager)
    app = FastAPI(title="Campaign Studio GPU Runtime", version="2.0.0")

    @app.middleware("http")
    async def private_v2_boundary(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/v2/") and not _authorized(request):
            return JSONResponse(
                {"detail": "AI service bearer authentication failed"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        if runtime.auto_load:
            runtime.start_background_load()
        payload = runtime.state.payload()
        payload["authenticated"] = False
        return payload

    @app.get("/v2/health")
    def health(request: Request) -> Response:
        # Health is deliberately authenticated just like every V2 operation.
        if not _authorized(request):
            return JSONResponse(
                {"detail": "AI service bearer authentication failed"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if runtime.auto_load:
            runtime.start_background_load()
        payload = runtime.state.payload()
        return JSONResponse(payload, status_code=200 if runtime.state.inference_ready else 503)

    async def runtime_proxy(scope: Any, receive: Any, send: Any) -> None:
        """Dispatch to the loaded extraction service without remounting routes."""

        if runtime.service_app is None:
            response = JSONResponse(
                {
                    "detail": "GPU inference runtime is not ready",
                    "path": scope.get("path", ""),
                },
                status_code=503,
            )
            await response(scope, receive, send)
            return
        await runtime.service_app(scope, receive, send)

    # The proxy reads runtime.service_app for every request, so a background
    # model load can publish the real Product Lock routes after startup.
    app.mount("/", runtime_proxy)

    return app


runtime = GpuRuntime()
app = create_app(runtime=runtime)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ai.gpu_runtime:app", host="0.0.0.0", port=int(_env("PORT", "8000")), access_log=False)


__all__ = ["GpuRuntime", "RuntimeReadiness", "app", "create_app", "runtime"]
