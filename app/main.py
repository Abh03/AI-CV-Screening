import time
import re
from uuid import uuid4
from pathlib import Path
from sqlalchemy import text
from redis.asyncio import Redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.endpoints import router as screening_router
from app.api.campaigns import router as campaign_router
from app.config import settings
from app.core.logging import setup_logging, logger, correlation_id
from app.models.database import engine

# Initialize application logging
setup_logging()
settings.validate_production()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Multi-stage deterministic + LLM hybrid candidate screening engine."
)


@app.exception_handler(RequestValidationError)
async def invalid_request_handler(request: Request, exc: RequestValidationError):
    # Raw input may contain non-JSON numbers or CV PII. Return locations and reasons,
    # not the original input or exception objects from validator contexts.
    return JSONResponse(status_code=422, content={
        "detail": [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
    })

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
    incoming = request.headers.get("x-correlation-id", "")
    request_id = incoming if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", incoming) else uuid4().hex
    token = correlation_id.set(request_id)
    try:
        return await _handle_request(request, call_next, start_time, request_id)
    finally:
        correlation_id.reset(token)


async def _handle_request(request, call_next, start_time, request_id):
    if request.url.path == "/api/v1/screening/run-pdf":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > (settings.PDF_MAX_BYTES * 4 // 3 + 65536):
            response = JSONResponse(status_code=413, content={"status": "failure", "code": "PDF_TOO_LARGE"})
            response.headers["X-Correlation-ID"] = request_id
            return response
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time-Sec"] = f"{process_time:.4f}"
    response.headers["X-Correlation-ID"] = request_id
    route = request.scope.get("route")
    logger.info("request", extra={"event": "http_request", "route": route.path if route else "unmatched",
                                  "status": response.status_code, "latency_ms": round(process_time * 1000, 2)})
    return response

app.include_router(screening_router)
app.include_router(campaign_router)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }


@app.get("/ready")
async def readiness_check():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
            if revision != "f8137b4a2c91":
                raise RuntimeError("migration pending")
        redis = Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        try:
            await redis.ping()
        finally:
            await redis.aclose()
        if settings.ENVIRONMENT.lower() == "production":
            for model_path in ("/opt/models/embedding", "/opt/models/reranker"):
                if not Path(model_path).is_dir():
                    raise RuntimeError("model missing")
        return {"status": "ready"}
    except Exception:
        logger.warning("readiness", extra={"event": "readiness_failed", "error_code": "DEPENDENCY_UNAVAILABLE"})
        return JSONResponse(status_code=503, content={"status": "unavailable"})
