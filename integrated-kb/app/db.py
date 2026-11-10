"""PostgreSQL access; no implicit credentials or import-time connection."""
import os
from contextlib import contextmanager
import psycopg2
from psycopg2.extras import RealDictCursor


def get_conn():
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise RuntimeError('DATABASE_URL is required')
    return psycopg2.connect(dsn)


@contextmanager
def transaction():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql, params=(), one=False, conn=None):
    if conn is None:
        with transaction() as own:
            return query(sql, params, one, own)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


def execute(sql, params=(), conn=None):
    if conn is None:
        with transaction() as own:
            return execute(sql, params, own)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount
