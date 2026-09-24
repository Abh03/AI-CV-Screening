import pytest

pytestmark = pytest.mark.infrastructure

import redis
from app.core.database import get_db_connection
from app.config import settings

def test_postgres_connection():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            result = cur.fetchone()
            assert result[0] == 1

def test_pgvector_installed():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            result = cur.fetchone()
            assert result is not None
            assert result[0] == 'vector'

def test_redis_connection():
    r = redis.Redis.from_url(settings.REDIS_URL)
    assert r.ping() is True