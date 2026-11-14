import os
import time

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT,
    username TEXT,
    font INTEGER NOT NULL DEFAULT 2,
    auto INTEGER NOT NULL DEFAULT 1,
    sub_until INTEGER NOT NULL DEFAULT 0,
    referrer_id INTEGER,
    ref_credited INTEGER NOT NULL DEFAULT 0,
    points INTEGER NOT NULL DEFAULT 0,
    invited INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS business_connections (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    file_id TEXT,
    created_at INTEGER NOT NULL
);
"""


async def init() -> None:
    folder = os.path.dirname(config.DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    async with aiosqlite.connect(config.DB_PATH) as conn:
        await conn.executescript(SCHEMA)
        await conn.commit()


async def execute(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(config.DB_PATH) as conn:
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.lastrowid


async def fetchone(sql: str, params: tuple = ()):
    async with aiosqlite.connect(config.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


# ---------- Корбарон ----------
async def ensure_user(uid: int, name: str, username: str | None, referrer: int | None) -> None:
    row = await fetchone("SELECT id FROM users WHERE id=?", (uid,))
    if row is None:
        await execute(
            "INSERT INTO users (id, name, username, referrer_id, created_at) VALUES (?,?,?,?,?)",
            (uid, name, username, referrer, int(time.time())),
        )
    else:
        await execute("UPDATE users SET name=?, username=? WHERE id=?", (name, username, uid))


async def get_user(uid: int):
    return await fetchone("SELECT * FROM users WHERE id=?", (uid,))


async def set_font(uid: int, font_id: int) -> None:
    await execute("UPDATE users SET font=? WHERE id=?", (font_id, uid))


async def toggle_auto(uid: int) -> None:
    await execute("UPDATE users SET auto = 1 - auto WHERE id=?", (uid,))


async def extend_sub(uid: int, days: int) -> int:
    user = await get_user(uid)
    base = max(int(time.time()), (user["sub_until"] if user else 0) or 0)
    until = base + days * 86400
    await execute("UPDATE users SET sub_until=? WHERE id=?", (until, uid))
    return until


# ---------- Реферал ----------
async def credit_referral(uid: int):
    """Як маротиба ба даъваткунанда хол медиҳад. Бармегардонад: (referrer_id, rewarded) ё None."""
    user = await get_user(uid)
    if not user or not user["referrer_id"] or user["ref_credited"]:
        return None
    await execute("UPDATE users SET ref_credited=1 WHERE id=?", (uid,))
    ref_id = user["referrer_id"]
    if not await get_user(ref_id):
        return None
    await execute("UPDATE users SET points=points+1, invited=invited+1 WHERE id=?", (ref_id,))
    ref = await get_user(ref_id)
    rewarded = False
    if ref["points"] >= config.REF_TARGET:
        await execute("UPDATE users SET points=points-? WHERE id=?", (config.REF_TARGET, ref_id))
        await extend_sub(ref_id, 30)
        rewarded = True
    return ref_id, rewarded


# ---------- Business ----------
async def save_connection(conn_id: str, user_id: int, enabled: bool) -> None:
    await execute(
        "INSERT OR REPLACE INTO business_connections (id, user_id, enabled) VALUES (?,?,?)",
        (conn_id, user_id, int(enabled)),
    )


async def get_connection(conn_id: str):
    return await fetchone("SELECT * FROM business_connections WHERE id=?", (conn_id,))


async def has_connection(user_id: int) -> bool:
    row = await fetchone(
        "SELECT 1 FROM business_connections WHERE user_id=? AND enabled=1", (user_id,)
    )
    return row is not None


# ---------- Пардохтҳо ----------
async def add_payment(uid: int, method: str, status: str, file_id: str | None = None) -> int:
    return await execute(
        "INSERT INTO payments (user_id, method, status, file_id, created_at) VALUES (?,?,?,?,?)",
        (uid, method, status, file_id, int(time.time())),
    )


async def get_payment(pid: int):
    return await fetchone("SELECT * FROM payments WHERE id=?", (pid,))


async def set_payment_status(pid: int, status: str) -> None:
    await execute("UPDATE payments SET status=? WHERE id=?", (status, pid))


async def stats() -> dict:
    now = int(time.time())
    users = await fetchone("SELECT COUNT(*) c FROM users")
    active = await fetchone("SELECT COUNT(*) c FROM users WHERE sub_until>?", (now,))
    conns = await fetchone("SELECT COUNT(*) c FROM business_connections WHERE enabled=1")
    return {"users": users["c"], "active": active["c"], "connections": conns["c"]}
