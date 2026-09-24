import logging
import json
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from app.config import settings

correlation_id = ContextVar("correlation_id", default=None)


class SafeJSONFormatter(logging.Formatter):
    def format(self, record):
        event = {"timestamp": datetime.now(timezone.utc).isoformat(),
                 "level": record.levelname, "logger": record.name,
                 "event": getattr(record, "event", "log"),
                 "correlation_id": correlation_id.get()}
        for field in ("route", "status", "latency_ms", "run_id", "stage", "count", "error_code"):
            value = getattr(record, field, None)
            if value is not None:
                event[field] = value
        return json.dumps(event, separators=(",", ":"))


def setup_logging() -> None:
    """
    Configures application-wide logging handlers and formatting.
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeJSONFormatter())
    logging.basicConfig(level=log_level, handlers=[handler], force=True)
    
    # Reduce noise from external HTTP client libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


logger = logging.getLogger("cv_screening")
