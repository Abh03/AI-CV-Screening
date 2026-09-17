import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from contextlib import contextmanager
from app.config import settings

# Pool maintains between 1 and 20 reusable connections
db_pool = ThreadedConnectionPool(
    minconn=1,
    maxconn=20,
    dsn=settings.DATABASE_URL
)

@contextmanager
def get_db_connection():
    conn = db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db_pool.putconn(conn)