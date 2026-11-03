import mysql.connector.pooling as pooling
from config import DB
_pool = None
def conn():
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(pool_name="sb", pool_size=8, **DB)
    return _pool.get_connection()
def q(sql, args=(), one=False):
    c = conn()
    try:
        cur = c.cursor(dictionary=True); cur.execute(sql, args); rows = cur.fetchall(); cur.close()
        return (rows[0] if rows else None) if one else rows
    finally:
        c.close()
