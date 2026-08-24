"""Provider contracts and a defensive CloseRouter HTTP adapter."""
from __future__ import annotations

import base64
import io
import json
import os
import secrets
import urllib.parse
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from PIL import Image, ImageDraw

from .contracts import (
    CostPreflight,
    ExecutionPlan,
    GenerationSpec,
    NormalizedImageResult,
    ProviderCapabilities,
    ProviderRequest,
    canonical_json,
    new_request_id,
    sha256_bytes,
)
from .image import canonicalize_image, encode_png


class ProviderError(RuntimeError):
    """Base class for provider errors; callers must not auto-fallback paid work."""


class ProviderPreflightError(ProviderError):
    pass


class ProviderProtocolError(ProviderError):
    pass


class HostNotAllowedError(ProviderError):
    pass


class Provider(Protocol):
    def discover(self) -> tuple[ProviderCapabilities, ...]:
        ...

    def preflight(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> CostPreflight:
        ...

    def plan(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> ExecutionPlan:
        ...

    def execute(self, plan: ExecutionPlan) -> NormalizedImageResult:
        ...


def _parse_capabilities(payload: Any, *, provider_name: str) -> tuple[ProviderCapabilities, ...]:
    if isinstance(payload, Mapping):
        entries = payload.get("data", payload.get("models", payload.get("providers", ())))
    else:
        entries = payload
    if isinstance(entries, Mapping):
        entries = [dict(value, id=key) if isinstance(value, Mapping) else {"id": key, "name": value} for key, value in entries.items()]
    output: list[ProviderCapabilities] = []
    for item in entries or ():
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, Mapping):
            continue
        model = str(item.get("id", item.get("name", item.get("model", "")))).strip()
        if not model:
            continue
        endpoints = item.get("endpoints", item.get("capabilities", ()))
        endpoint_names: set[str] = set()
        if isinstance(endpoints, Mapping):
            endpoint_names.update(str(key).casefold() for key, enabled in endpoints.items() if enabled)
        elif isinstance(endpoints, (list, tuple, set)):
            endpoint_names.update(str(value).casefold() for value in endpoints)
        elif isinstance(endpoints, str):
            endpoint_names.add(endpoints.casefold())
        supports_edit = bool(item.get("supports_edit", item.get("edit", False))) or any("edit" in value for value in endpoint_names)
        supports_inpainting = bool(item.get("supports_inpainting", item.get("inpainting", False))) or any("inpaint" in value for value in endpoint_names)
        supports_generation = bool(item.get("supports_generation", False)) or any(
            "image" in value and "generation" in value for value in endpoint_names
        )
        # CloseRouter's catalog also contains text/audio models.  A missing
        # image endpoint must never be interpreted as image-generation support.
        if not (supports_generation or supports_edit or supports_inpainting):
            continue
        deterministic = bool(item.get("deterministic_seed", item.get("supports_seed", False)))
        pricing = item.get("pricing")
        pricing_image = pricing.get("image") if isinstance(pricing, Mapping) else None
        try:
            cost = float(
                item.get(
                    "cost_usd",
                    item.get("price", item.get("estimated_cost_usd", pricing_image or 0.0)),
                )
                or 0.0
            )
        except (TypeError, ValueError):
            cost = 0.0
        image_endpoint = next(
            (value for value in endpoint_names if "image" in value),
            "images/generations" if supports_generation else "images/edits",
        )
        output.append(
            ProviderCapabilities(
                provider=provider_name,
                model=model,
                endpoint=str(item.get("endpoint", image_endpoint)),
                supports_generation=supports_generation,
                supports_edit=supports_edit,
                supports_inpainting=supports_inpainting,
                deterministic_seed=deterministic,
                max_width=int(item.get("max_width", 4096) or 4096),
                max_height=int(item.get("max_height", 4096) or 4096),
                max_pixels=int(item.get("max_pixels", 16_000_000) or 16_000_000),
                input_formats=tuple(str(value) for value in item.get("input_formats", ("image/png", "image/jpeg", "image/webp"))),
                output_formats=tuple(str(value) for value in item.get("output_formats", ("image/png", "image/jpeg", "image/webp"))),
                estimated_cost_usd=max(0.0, cost),
                raw=dict(item),
            )
        )
    return tuple(output)


class CloseRouterAdapter:
    """CloseRouter-compatible image provider.

    Authentication is injected only while executing a request.  The adapter
    never logs or serializes the key, prompts, or image bytes, and image edits
    use inline data URLs rather than external media URLs.
    """

    provider_name = "closerouter"
    default_base_url = "https://api.closerouter.dev/v1"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_provider: Callable[[], str | None] | None = None,
        transport: Any = None,
        timeout: float = 20.0,
        max_response_bytes: int = 32 * 1024 * 1024,
        allowed_hosts: Iterable[str] | None = None,
        max_request_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.base_url = (base_url or os.getenv("CLOSEROUTER_BASE_URL") or self.default_base_url).rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("CloseRouter base_url must contain an http(s) host")
        # Plain HTTP is permitted only for loopback/mock hosts, never for a
        # public provider endpoint.
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise HostNotAllowedError("CloseRouter requires HTTPS for public hosts")
        self._allowed_hosts = {host.casefold().rstrip(".") for host in (allowed_hosts or (parsed.hostname,))}
        self._allowed_hosts.add(parsed.hostname.casefold().rstrip("."))
        self._api_key = api_key
        self._api_key_provider = api_key_provider
        self._transport = transport
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self._client: Any = None
        self._capabilities: tuple[ProviderCapabilities, ...] | None = None
        self.last_request_id: str | None = None
        # Credit introspection is best-effort accounting, never a request gate.
        self.last_credits_before: float | None = None
        self.last_credits_after: float | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import httpx  # type: ignore
        except ImportError as exc:
            raise ProviderError("httpx is required for CloseRouterAdapter") from exc
        kwargs: dict[str, Any] = {"timeout": self.timeout, "follow_redirects": False}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        self._client = httpx.Client(**kwargs)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "CloseRouterAdapter":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _assert_allowed_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme not in {"https", "http"} or host not in self._allowed_hosts:
            raise HostNotAllowedError("provider URL is outside the configured host allowlist")
        if parsed.scheme != "https" and host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            raise HostNotAllowedError("provider URL must use HTTPS")

    def _url(self, path: str) -> str:
        url = f"{self.base_url}/{path.lstrip('/')}"
        self._assert_allowed_url(url)
        return url

    def _api_key_value(self) -> str | None:
        # A provider may be called by a server-side secret manager.  Resolve it
        # at execution time so discovery/planning objects cannot leak it.
        value = self._api_key_provider() if self._api_key_provider is not None else self._api_key
        if value is None:
            value = os.getenv("CLOSEROUTER_API_KEY")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _json_response(response: Any) -> Any:
        content = bytes(getattr(response, "content", b""))
        if len(content) > 8 * 1024 * 1024:
            raise ProviderProtocolError("provider JSON response exceeds the bound")
        try:
            return response.json()
        except Exception as exc:
            try:
                return json.loads(content.decode("utf-8"))
            except Exception as json_exc:
                raise ProviderProtocolError("provider returned invalid JSON") from json_exc

    def _request(self, request: ProviderRequest, *, authenticated: bool = True) -> Any:
        self._assert_allowed_url(request.url)
        headers = dict(request.headers)
        # Bound serialized request bodies before handing them to httpx.  This
        # is especially important for inline edit data URLs, which contain
        # private image bytes and must not create an unbounded upload.
        if request.json_body is not None:
            request_bytes = canonical_json(dict(request.json_body)).encode("utf-8")
            if len(request_bytes) > self.max_request_bytes:
                raise ProviderProtocolError("provider request exceeds the configured byte bound")
        elif request.data is not None:
            request_bytes = canonical_json(dict(request.data)).encode("utf-8")
            if len(request_bytes) > self.max_request_bytes:
                raise ProviderProtocolError("provider request exceeds the configured byte bound")
        if authenticated:
            key = self._api_key_value()
            if not key:
                raise ProviderPreflightError("CloseRouter API key is not configured server-side")
            headers["Authorization"] = f"Bearer {key}"
        try:
            response = self._get_client().request(
                request.method,
                request.url,
                headers=headers,
                json=dict(request.json_body) if request.json_body is not None else None,
                data=dict(request.data) if request.data is not None else None,
                files=dict(request.files) if request.files is not None else None,
            )
        except Exception as exc:
            raise ProviderError("provider request failed") from exc
        headers_obj = getattr(response, "headers", {})
        try:
            declared_length = int(headers_obj.get("content-length", "0") or 0)
        except (TypeError, ValueError):
            declared_length = 0
        if declared_length > self.max_response_bytes:
            raise ProviderProtocolError("provider response exceeds the configured byte bound")
        content = bytes(getattr(response, "content", b""))
        if len(content) > self.max_response_bytes:
            raise ProviderProtocolError("provider response exceeds the configured byte bound")
        status = int(getattr(response, "status_code", 0))
        if status < 200 or status >= 300:
            raise ProviderError(f"provider returned HTTP {status}")
        return response

    def discover(self) -> tuple[ProviderCapabilities, ...]:
        response = self._request(ProviderRequest("GET", self._url("models"), headers={"Accept": "application/json"}))
        capabilities = _parse_capabilities(self._json_response(response), provider_name=self.provider_name)
        if not capabilities:
            raise ProviderProtocolError("CloseRouter /models returned no usable image models")
        self._capabilities = capabilities
        return capabilities

    def _select_capability(self, spec: GenerationSpec, *, operation: str, model: str | None = None) -> ProviderCapabilities:
        capabilities = self._capabilities or self.discover()
        requested = model or spec.model
        candidates = [item for item in capabilities if not requested or item.model == requested]
        if operation in {"edit", "inpaint", "inpainting"}:
            candidates = [item for item in candidates if item.supports_edit or item.supports_inpainting]
        else:
            candidates = [item for item in candidates if item.supports_generation]
        if not candidates:
            raise ProviderPreflightError("no discovered CloseRouter model supports the requested operation")
        selected = candidates[0]
        if spec.width > selected.max_width or spec.height > selected.max_height or spec.width * spec.height > selected.max_pixels:
            raise ProviderPreflightError("requested image exceeds the discovered provider bounds")
        if spec.image_mime not in selected.input_formats and spec.image_bytes is not None:
            raise ProviderPreflightError("input image format is not supported by the discovered model")
        return selected

    def _credits(self) -> float | None:
        response = self._request(ProviderRequest("GET", self._url("credits"), headers={"Accept": "application/json"}))
        payload = self._json_response(response)
        if not isinstance(payload, Mapping):
            raise ProviderProtocolError("CloseRouter /credits returned an invalid payload")
        for key in ("total_credits", "credits", "balance", "remaining", "available"):
            if key in payload:
                try:
                    value = float(payload[key])
                except (TypeError, ValueError) as exc:
                    raise ProviderProtocolError("CloseRouter credits value is invalid") from exc
                if value < 0:
                    raise ProviderProtocolError("CloseRouter credits value is negative")
                return value
        # Some accounts expose a nested balance object.
        nested = payload.get("data")
        if isinstance(nested, Mapping):
            for key in ("total_credits", "credits", "balance", "remaining", "available"):
                if key in nested:
                    return float(nested[key])
        return None

    def preflight(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> CostPreflight:
        capability = self._select_capability(spec, operation=operation)
        estimated = capability.estimated_cost_usd
        if max_cost_usd is not None and estimated > max_cost_usd:
            return CostPreflight(False, estimated, reason="estimated cost exceeds caller budget", checked_at=datetime.now(timezone.utc).isoformat())
        # The server-side immutable budget is authoritative. Provider credit
        # endpoints are inconsistent across accounts and are therefore not a
        # prerequisite for submitting an otherwise valid allowlisted request.
        # Sample the balance only for optional accounting; never reject here.
        try:
            credits = self._credits()
        except ProviderError:
            credits = None
        self.last_credits_before = credits
        return CostPreflight(True, estimated, credits_available_usd=credits, checked_at=datetime.now(timezone.utc).isoformat())

    def plan(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> ExecutionPlan:
        capability = self._select_capability(spec, operation=operation)
        preflight = self.preflight(spec, operation=operation, max_cost_usd=max_cost_usd)
        if not preflight.allowed:
            raise ProviderPreflightError(preflight.reason or "provider cost preflight rejected the request")
        request_id = new_request_id()
        self.last_request_id = request_id
        metadata = dict(spec.metadata)
        metadata["provider_credits_before"] = preflight.credits_available_usd
        metadata["provider_estimated_cost_usd"] = preflight.estimated_cost_usd
        return ExecutionPlan(
            request_id=request_id,
            provider=self.provider_name,
            operation=operation,
            capabilities=capability,
            spec=GenerationSpec(
                prompt=spec.prompt,
                width=spec.width,
                height=spec.height,
                seed=spec.seed,
                model=capability.model,
                image_bytes=spec.image_bytes,
                image_mime=spec.image_mime,
                mask_bytes=spec.mask_bytes,
                negative_prompt=spec.negative_prompt,
                metadata=metadata,
            ),
            estimated_cost_usd=preflight.estimated_cost_usd,
        )

    @staticmethod
    def _data_url(value: bytes, mime: str) -> str:
        return f"data:{mime};base64,{base64.b64encode(value).decode('ascii')}"

    def build_request(self, plan: ExecutionPlan) -> ProviderRequest:
        spec = plan.spec
        operation = plan.operation.casefold()
        option_values = {
            "quality": ({"low", "medium", "high"}, spec.metadata.get("quality")),
            "input_fidelity": ({"low", "high"}, spec.metadata.get("input_fidelity")),
            "output_format": ({"png", "jpeg", "webp"}, spec.metadata.get("output_format")),
            "background": ({"auto", "opaque", "transparent"}, spec.metadata.get("background")),
            "resolution": ({"1k", "2k", "4k"}, spec.metadata.get("resolution")),
        }
        provider_options: dict[str, str] = {}
        for name, (allowed, value) in option_values.items():
            if value is None:
                continue
            normalized = str(value).casefold().strip()
            if normalized not in allowed:
                raise ProviderProtocolError(f"unsupported {name} option")
            provider_options[name] = normalized
        if operation in {"edit", "inpaint", "inpainting"}:
            if not spec.image_bytes:
                raise ProviderProtocolError("image edit requires source image bytes")
            fields: dict[str, Any] = {
                "model": plan.capabilities.model,
                "prompt": spec.prompt,
                "size": f"{spec.width}x{spec.height}",
            }
            if spec.negative_prompt:
                fields["negative_prompt"] = spec.negative_prompt
            if spec.seed is not None:
                fields["seed"] = str(spec.seed)
            fields.update(provider_options)
            fields["response_format"] = "b64_json"
            files: dict[str, Any] = {
                "image": ("image.png", spec.image_bytes, spec.image_mime),
            }
            if spec.mask_bytes is not None:
                files["mask"] = ("mask.png", spec.mask_bytes, "image/png")
            return ProviderRequest(
                method="POST",
                url=self._url("images/edits"),
                headers={"Accept": "application/json", "X-Request-ID": plan.request_id},
                data=fields,
                files=files,
            )
        body = {"model": plan.capabilities.model, "prompt": spec.prompt, "size": f"{spec.width}x{spec.height}", "n": 1}
        if spec.negative_prompt:
            body["negative_prompt"] = spec.negative_prompt
        if spec.seed is not None:
            body["seed"] = spec.seed
        body.update(provider_options)
        body["response_format"] = "b64_json"
        path = "images/generations"
        return ProviderRequest(
            method="POST",
            url=self._url(path),
            headers={"Accept": "application/json", "Content-Type": "application/json", "X-Request-ID": plan.request_id},
            json_body=body,
        )

    def _download_result_url(self, url: str) -> bytes:
        self._assert_allowed_url(url)
        response = self._request(ProviderRequest("GET", url, headers={"Accept": "image/*"}))
        payload = bytes(getattr(response, "content", b""))
        if not payload or len(payload) > self.max_response_bytes:
            raise ProviderProtocolError("provider image URL returned an invalid or oversized body")
        return payload

    def _normalize_response(self, response: Any, plan: ExecutionPlan) -> NormalizedImageResult:
        content_type = str(getattr(response, "headers", {}).get("content-type", "")).split(";", 1)[0].casefold()
        if content_type.startswith("image/"):
            raw_image = bytes(getattr(response, "content", b""))
            if not raw_image:
                raise ProviderProtocolError("provider returned an empty image body")
            payload: Mapping[str, Any] = {"data": [{"_raw_image": raw_image}]}
        else:
            payload = self._json_response(response)
        if not isinstance(payload, Mapping):
            raise ProviderProtocolError("provider image response is not an object")
        data = payload.get("data", payload.get("images", ()))
        if isinstance(data, Mapping):
            data = [data]
        if not isinstance(data, Sequence) or not data:
            raise ProviderProtocolError("provider image response has no image data")
        first = data[0]
        if isinstance(first, str):
            first = {"url": first}
        if not isinstance(first, Mapping):
            raise ProviderProtocolError("provider image item is invalid")
        source_url = None
        if first.get("_raw_image") is not None:
            image_bytes = bytes(first["_raw_image"])
        elif first.get("b64_json") or first.get("base64"):
            encoded = str(first.get("b64_json") or first.get("base64"))
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ProviderProtocolError("provider image base64 is invalid") from exc
        elif first.get("url"):
            source_url = str(first["url"])
            image_bytes = self._download_result_url(source_url)
        else:
            raise ProviderProtocolError("provider image item has neither base64 nor allowlisted URL")
        canonical = canonicalize_image(image_bytes)
        normalized = canonical.png_bytes
        usage = dict(
            payload.get("usage", {})
            if isinstance(payload.get("usage", {}), Mapping)
            else {}
        )
        provider_response_id = payload.get("id") or getattr(response, "headers", {}).get("x-request-id")
        usage.update(
            {
                "provider_request_id": provider_response_id,
                "client_request_id": plan.request_id,
                "credits_before": plan.spec.metadata.get("provider_credits_before"),
            }
        )
        cost = payload.get("cost_usd", payload.get("cost"))
        try:
            cost_value = float(cost) if cost is not None else plan.estimated_cost_usd
        except (TypeError, ValueError):
            cost_value = plan.estimated_cost_usd
        return NormalizedImageResult(
            image_bytes=normalized,
            mime_type="image/png",
            width=canonical.width,
            height=canonical.height,
            sha256=sha256_bytes(normalized),
            request_id=plan.request_id,
            provider=plan.provider,
            model=plan.capabilities.model,
            attempts=1,
            usage=usage,
            cost_usd=max(0.0, cost_value),
            deterministic_seed_claim=plan.capabilities.deterministic_seed,
            source_url=source_url,
        )

    def execute(self, plan: ExecutionPlan) -> NormalizedImageResult:
        request = self.build_request(plan)
        # Exactly one billable request.  Retries/fallbacks belong to a caller's
        # explicit policy and are intentionally not hidden here.
        response = self._request(request)
        result = self._normalize_response(response, plan)
        try:
            credits_after = self._credits()
        except ProviderError:
            credits_after = None
        self.last_credits_after = credits_after
        if credits_after is not None:
            usage = dict(result.usage)
            usage["credits_after"] = credits_after
            result = replace(result, usage=usage)
        return result


class DeterministicImageProvider:
    """Synthetic local provider used by CPU tests and offline demos."""

    provider_name = "deterministic-fake"

    def __init__(self, *, model: str = "synthetic-background-v1") -> None:
        self.capability = ProviderCapabilities(
            provider=self.provider_name,
            model=model,
            supports_generation=True,
            supports_edit=True,
            supports_inpainting=True,
            deterministic_seed=True,
            estimated_cost_usd=0.0,
        )

    def discover(self) -> tuple[ProviderCapabilities, ...]:
        return (self.capability,)

    def preflight(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> CostPreflight:
        if spec.width * spec.height > self.capability.max_pixels:
            return CostPreflight(False, 0.0, reason="image exceeds fake provider bounds")
        return CostPreflight(True, 0.0, credits_available_usd=None, reason=None, checked_at=datetime.now(timezone.utc).isoformat())

    def plan(self, spec: GenerationSpec, *, operation: str = "generation", max_cost_usd: float | None = None) -> ExecutionPlan:
        preflight = self.preflight(spec, operation=operation, max_cost_usd=max_cost_usd)
        if not preflight.allowed:
            raise ProviderPreflightError(preflight.reason or "fake provider preflight rejected request")
        return ExecutionPlan(new_request_id(), self.provider_name, operation, self.capability, spec, 0.0)

    def execute(self, plan: ExecutionPlan) -> NormalizedImageResult:
        # A subtle gradient makes tests able to prove that a remote/background
        # result was actually used while remaining perfectly reproducible.
        image = Image.new("RGBA", (plan.spec.width, plan.spec.height), (232, 239, 240, 255))
        draw = ImageDraw.Draw(image)
        seed = int(plan.spec.seed or 0) & 0xFFFFFFFF
        for y in range(plan.spec.height):
            value = (y * 17 + seed) % 48
            draw.line((0, y, plan.spec.width, y), fill=(220 + value // 4, 232 + value // 8, 234 + value // 10, 255))
        output = encode_png(image, mode="RGB")
        return NormalizedImageResult(
            image_bytes=output,
            mime_type="image/png",
            width=image.width,
            height=image.height,
            sha256=sha256_bytes(output),
            request_id=plan.request_id,
            provider=self.provider_name,
            model=self.capability.model,
            attempts=1,
            usage={"synthetic": True},
            cost_usd=0.0,
            deterministic_seed_claim=True,
        )


CloseRouterProvider = CloseRouterAdapter
CloseRouterImageProvider = CloseRouterAdapter
ImageProvider = Provider


__all__ = [
    "CloseRouterAdapter",
    "CloseRouterProvider",
    "CloseRouterImageProvider",
    "DeterministicImageProvider",
    "HostNotAllowedError",
    "Provider",
    "ImageProvider",
    "ProviderError",
    "ProviderPreflightError",
    "ProviderProtocolError",
]
