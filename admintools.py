"""Ёрдамчиҳои панели админ: омор, рӯйхат, ҷустуҷӯ, backup/restore.

Бе тағйир додани db.py кор мекунад: ҷадвали корбаронро худаш пайдо мекунад
(ҷадвале, ки сутунҳои id ва sub_until дорад).
"""
import asyncio
import csv
import io
import os
import sqlite3
import tempfile
import time

import aiosqlite

import config
import db

_NAMES = ("DB_PATH", "DB_FILE", "DATABASE", "DB_NAME", "DB")
_table: str | None = None


def db_path() -> str:
    for mod in (db, config):
        for n in _NAMES:
            v = getattr(mod, n, None)
            if v and isinstance(v, (str, os.PathLike)):
                return str(v)
    return "bot.db"


async def user_table() -> str | None:
    global _table
    if _table:
        return _table
    async with aiosqlite.connect(db_path()) as c:
        cur = await c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        names = [r[0] for r in await cur.fetchall()]
        for t in names:
            cur = await c.execute(f'PRAGMA table_info("{t}")')
            cols = {r[1] for r in await cur.fetchall()}
            if {"id", "sub_until"} <= cols:
                _table = t
                return t
    return None


async def counts() -> dict | None:
    t = await user_table()
    if not t:
        return None
    now = int(time.time())
    async with aiosqlite.connect(db_path()) as c:
        cur = await c.execute(
            f'SELECT COUNT(*), '
            f'SUM(CASE WHEN sub_until > ? THEN 1 ELSE 0 END), '
            f'SUM(CASE WHEN sub_until > 0 AND sub_until <= ? THEN 1 ELSE 0 END) FROM "{t}"',
            (now, now),
        )
        total, active, expired = await cur.fetchone()
    return {"total": total or 0, "active": active or 0, "expired": expired or 0}


async def list_users(kind: str, page: int, per: int = 8):
    t = await user_table()
    if not t:
        return [], 0
    now = int(time.time())
    where, params = {
        "active": ("sub_until > ?", (now,)),
        "expired": ("sub_until > 0 AND sub_until <= ?", (now,)),
        "all": ("1 = 1", ()),
    }[kind]
    async with aiosqlite.connect(db_path()) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {where}', params)
        total = (await cur.fetchone())[0]
        cur = await c.execute(
            f'SELECT id, name, username, sub_until FROM "{t}" WHERE {where} '
            f"ORDER BY sub_until DESC, id DESC LIMIT ? OFFSET ?",
            (*params, per, page * per),
        )
        rows = await cur.fetchall()
    return rows, total


async def find(q: str, limit: int = 8):
    t = await user_table()
    if not t:
        return []
    q = q.strip()
    async with aiosqlite.connect(db_path()) as c:
        c.row_factory = aiosqlite.Row
        if q.lstrip("-").isdigit():
            cur = await c.execute(f'SELECT id, name, username, sub_until FROM "{t}" WHERE id=?', (int(q),))
        else:
            like = f"%{q.lstrip('@')}%"
            cur = await c.execute(
                f'SELECT id, name, username, sub_until FROM "{t}" WHERE username LIKE ? OR name LIKE ? LIMIT ?',
                (like, like, limit),
            )
        return await cur.fetchall()


async def set_sub(uid: int, ts: int) -> None:
    t = await user_table()
    if not t:
        return
    async with aiosqlite.connect(db_path()) as c:
        await c.execute(f'UPDATE "{t}" SET sub_until=? WHERE id=?', (ts, uid))
        await c.commit()


async def all_ids() -> list[int]:
    t = await user_table()
    if not t:
        return []
    async with aiosqlite.connect(db_path()) as c:
        cur = await c.execute(f'SELECT id FROM "{t}"')
        return [r[0] for r in await cur.fetchall()]


async def export_csv() -> bytes:
    t = await user_table()
    if not t:
        return b""
    async with aiosqlite.connect(db_path()) as c:
        cur = await c.execute(f'SELECT * FROM "{t}"')
        head = [d[0] for d in cur.description]
        rows = await cur.fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(head)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


# ---------- Backup / Restore ----------
def _snapshot_sync(path: str) -> bytes:
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = dst = None
    try:
        src = sqlite3.connect(path, timeout=15)
        dst = sqlite3.connect(tmp)
        src.backup(dst)
        dst.close()
        dst = None
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        for cn in (src, dst):
            if cn:
                cn.close()
        if os.path.exists(tmp):
            os.remove(tmp)


async def snapshot(path: str | None = None) -> bytes:
    return await asyncio.to_thread(_snapshot_sync, path or db_path())


def _restore_sync(data: bytes, extras_path: str):
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = dst = None
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        src = sqlite3.connect(tmp)
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("файл вайрон аст")
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        target, kind, count = None, None, 0
        for t in tables:
            cols = {r[1] for r in src.execute(f'PRAGMA table_info("{t}")')}
            if {"id", "sub_until"} <= cols:
                target, kind = db_path(), "корбарон ва обунаҳо"
                count = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                break
            if t == "extras" and "user_id" in cols:
                target, kind = extras_path, "танзимоти авточавоб"
                count = src.execute('SELECT COUNT(*) FROM "extras"').fetchone()[0]
        if not target:
            raise ValueError("ин файли базаи ин бот нест")
        dst = sqlite3.connect(target, timeout=20)
        src.backup(dst)
        return kind, count
    finally:
        for cn in (src, dst):
            if cn:
                cn.close()
        if os.path.exists(tmp):
            os.remove(tmp)


async def restore(data: bytes, extras_path: str):
    global _table
    res = await asyncio.to_thread(_restore_sync, data, extras_path)
    _table = None
    return res
