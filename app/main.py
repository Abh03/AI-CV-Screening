import time
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.endpoints import router as screening_router
from app.config import settings
from app.core.logging import setup_logging, logger
from app.core.security import validate_encryption_configuration

# Initialize application logging
setup_logging()
if settings.ENVIRONMENT.lower() == "production":
    validate_encryption_configuration()

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
    if request.url.path == "/api/v1/screening/run-pdf":
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > (settings.PDF_MAX_BYTES * 4 // 3 + 65536):
            return JSONResponse(status_code=413, content={"status": "failure", "code": "PDF_TOO_LARGE"})
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time-Sec"] = f"{process_time:.4f}"
    logger.info(f"Path: {request.url.path} | Status: {response.status_code} | Latency: {process_time:.4f}s")
    return response

app.include_router(screening_router)


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT
    }
