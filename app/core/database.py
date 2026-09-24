"""Synchronous diagnostics only; application persistence uses async SQLAlchemy."""
from contextlib import contextmanager
import psycopg2
from sqlalchemy.engine import make_url
from app.config import settings


@contextmanager
def get_db_connection():
    url = make_url(settings.DATABASE_URL)
    if url.get_backend_name() != "postgresql":
        raise ValueError("PostgreSQL is required for infrastructure diagnostics")
    dsn = url.set(drivername="postgresql").render_as_string(hide_password=False)
    conn = psycopg2.connect(dsn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
