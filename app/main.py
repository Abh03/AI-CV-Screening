import time
from fastapi import FastAPI, Request
from app.api.endpoints import router as screening_router
from app.config import settings
from app.core.logging import setup_logging, logger

# Initialize application logging
setup_logging()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Multi-stage deterministic + LLM hybrid candidate screening engine."
)

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.perf_counter()
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