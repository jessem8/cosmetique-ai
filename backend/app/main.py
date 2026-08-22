"""FastAPI entry point for the website API."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.rate_limit import limiter
from app.routers import auth, generations, products
from app.routers import generation_v2, product_locks


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Cosmetique AI",
    version="1.0.0",
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "Idempotency-Key",
    ],
    expose_headers=["X-Request-ID", "Content-Disposition"],
)


@app.exception_handler(RequestValidationError)
async def stable_generation_validation_error(
    request: Request, exc: RequestValidationError
):
    route = request.scope.get("route")
    if (
        request.method == "POST"
        and (
            getattr(route, "endpoint", None) is generations.create_generation
            or request.url.path.startswith("/api/v2/")
            or "/studio/v2/" in request.url.path
        )
    ):
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "INVALID_GENERATION_REQUEST",
                    "message": "La demande de génération est invalide.",
                }
            },
        )
    return await request_validation_exception_handler(request, exc)


@app.middleware("http")
async def secure_response_headers(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


API_PREFIX = "/api/v1"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(products.router, prefix=API_PREFIX)
app.include_router(generations.router, prefix=API_PREFIX)
# V2 has its own explicit prefix and never changes the V1 route contracts.
app.include_router(product_locks.router)
app.include_router(product_locks.alias_router)
app.include_router(product_locks.studio_router)
app.include_router(generation_v2.router)
app.include_router(generation_v2.studio_router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0"}
