"""Функсияҳои иловагӣ: авточавоб, «дар шабака нест», навишти оҳиста, Удалит/Таҳрир, /hask.

Маълумот дар файли алоҳидаи extras.db захира мешавад (дар ҳамон папкае, ки db.py-и шумо),
барои ҳамин db.py-и шумо тағйир намеёбад.
"""
import asyncio
import json
import os
import re
import time

import aiosqlite

import config


def _db_path() -> str:
    try:
        import db as _db  # файли базаи асосӣ ҳар ҷое бошад, extras.db ҳамон ҷо мешавад
    except Exception:  # noqa: BLE001
        _db = None
    for mod in (_db, config):
        if mod is None:
            continue
        for name in ("DB_PATH", "DB_FILE", "DATABASE", "DB_NAME", "DB"):
            v = getattr(mod, name, None)
            if v and isinstance(v, (str, os.PathLike)):
                d = os.path.dirname(str(v))
                return os.path.join(d, "extras.db") if d else "extras.db"
    return "extras.db"


DB = _db_path()
MAX_BUTTONS = 6
PERIODS = [1, 6, 12, 24, 48, 168]  # соат
# Вақти «дар шабака нест» (соати оғоз, соати анҷом)
AWAY_SLOTS = [(22, 8), (23, 7), (0, 9), (18, 9), (20, 6), (9, 18)]
# Фарқи вақт аз UTC (Тоҷикистон = +5). Дар config.py метавон TZ_OFFSET гузошт.
TZ_OFFSET = getattr(config, "TZ_OFFSET", 5)

_lock = asyncio.Lock()
_cache_puts = 0

_COLUMNS = {
    "ar_text": "TEXT NOT NULL DEFAULT ''",
    "ar_on": "INTEGER NOT NULL DEFAULT 0",
    "ar_period": "INTEGER NOT NULL DEFAULT 24",
    "ar_buttons": "TEXT NOT NULL DEFAULT '[]'",
    "typing": "INTEGER NOT NULL DEFAULT 0",
    "away_text": "TEXT NOT NULL DEFAULT ''",
    "away_slot": "INTEGER NOT NULL DEFAULT 0",
    "notify_del": "INTEGER NOT NULL DEFAULT 0",
    "notify_edit": "INTEGER NOT NULL DEFAULT 0",
    "groups": "INTEGER NOT NULL DEFAULT 1",
}
_FIELDS = set(_COLUMNS)


async def init() -> None:
    async with aiosqlite.connect(DB) as c:
        await c.execute("CREATE TABLE IF NOT EXISTS extras (user_id INTEGER PRIMARY KEY)")
        cur = await c.execute("PRAGMA table_info(extras)")
        have = {r[1] for r in await cur.fetchall()}
        for col, ddl in _COLUMNS.items():
            if col not in have:
                await c.execute(f"ALTER TABLE extras ADD COLUMN {col} {ddl}")
        await c.execute(
            """CREATE TABLE IF NOT EXISTS reply_log (
                owner_id INTEGER, chat_id INTEGER, kind TEXT, ts INTEGER,
                PRIMARY KEY (owner_id, chat_id, kind)
            )"""
        )
        await c.execute(
            """CREATE TABLE IF NOT EXISTS msg_cache (
                owner_id INTEGER, chat_id INTEGER, msg_id INTEGER,
                name TEXT, text TEXT, ts INTEGER,
                PRIMARY KEY (owner_id, chat_id, msg_id)
            )"""
        )
        await c.commit()


async def get(uid: int) -> dict:
    async with aiosqlite.connect(DB) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute("SELECT * FROM extras WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    d = {
        "ar_text": "", "ar_on": 0, "ar_period": 24, "typing": 0, "buttons": [],
        "away_text": "", "away_slot": 0, "notify_del": 0, "notify_edit": 0, "groups": 1,
    }
    if row:
        for k in ("ar_text", "ar_on", "ar_period", "typing", "away_text", "away_slot",
                  "notify_del", "notify_edit", "groups"):
            d[k] = row[k]
        try:
            d["buttons"] = [(str(a), str(b)) for a, b in json.loads(row["ar_buttons"])]
        except Exception:  # noqa: BLE001
            d["buttons"] = []
    return d


async def save(uid: int, **fields) -> None:
    if "buttons" in fields:
        fields["ar_buttons"] = json.dumps(fields.pop("buttons"), ensure_ascii=False)
    fields = {k: v for k, v in fields.items() if k in _FIELDS}
    async with aiosqlite.connect(DB) as c:
        await c.execute("INSERT OR IGNORE INTO extras (user_id) VALUES (?)", (uid,))
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            await c.execute(f"UPDATE extras SET {cols} WHERE user_id=?", (*fields.values(), uid))
        await c.commit()


async def claim_reply(owner_id: int, chat_id: int, period_hours: int, kind: str = "ar") -> bool:
    """True, агар ба ин чат дар давраи охир ҷавоб надода бошем (ва ҳамон лаҳза қайд мекунад)."""
    now = int(time.time())
    async with _lock:
        async with aiosqlite.connect(DB) as c:
            cur = await c.execute(
                "SELECT ts FROM reply_log WHERE owner_id=? AND chat_id=? AND kind=?",
                (owner_id, chat_id, kind),
            )
            row = await cur.fetchone()
            if row and now - row[0] < period_hours * 3600:
                return False
            await c.execute(
                "INSERT OR REPLACE INTO reply_log (owner_id, chat_id, kind, ts) VALUES (?, ?, ?, ?)",
                (owner_id, chat_id, kind, now),
            )
            await c.commit()
    return True


# ---------- Кэши паёмҳо (барои Удалит / Таҳрир) ----------
async def cache_put(owner_id: int, chat_id: int, msg_id: int, name: str, text: str) -> None:
    global _cache_puts
    now = int(time.time())
    async with aiosqlite.connect(DB) as c:
        await c.execute(
            "INSERT OR REPLACE INTO msg_cache (owner_id, chat_id, msg_id, name, text, ts) VALUES (?,?,?,?,?,?)",
            (owner_id, chat_id, msg_id, name, text, now),
        )
        _cache_puts += 1
        if _cache_puts % 200 == 0:  # паёмҳои аз 3 рӯз кӯҳнатарро тоза мекунем
            await c.execute("DELETE FROM msg_cache WHERE ts < ?", (now - 3 * 86400,))
        await c.commit()


async def cache_get(owner_id: int, chat_id: int, msg_id: int):
    async with aiosqlite.connect(DB) as c:
        cur = await c.execute(
            "SELECT name, text FROM msg_cache WHERE owner_id=? AND chat_id=? AND msg_id=?",
            (owner_id, chat_id, msg_id),
        )
        row = await cur.fetchone()
    return (row[0], row[1]) if row else None


async def cache_pop(owner_id: int, chat_id: int, msg_ids: list[int]) -> list[tuple[int, str, str]]:
    out = []
    async with aiosqlite.connect(DB) as c:
        for mid in msg_ids:
            cur = await c.execute(
                "SELECT name, text FROM msg_cache WHERE owner_id=? AND chat_id=? AND msg_id=?",
                (owner_id, chat_id, mid),
            )
            row = await cur.fetchone()
            if row:
                out.append((mid, row[0], row[1]))
                await c.execute(
                    "DELETE FROM msg_cache WHERE owner_id=? AND chat_id=? AND msg_id=?",
                    (owner_id, chat_id, mid),
                )
        await c.commit()
    return out


# ---------- Ёрдамчиҳо ----------
def fmt_period(h: int) -> str:
    return f"{h} соат" if h <= 24 else f"{h // 24} рӯз"


def fmt_slot(slot: int) -> str:
    a, b = AWAY_SLOTS[slot % len(AWAY_SLOTS)]
    return f"{a:02d}:00–{b:02d}:00"


def is_away_now(slot: int) -> bool:
    a, b = AWAY_SLOTS[slot % len(AWAY_SLOTS)]
    h = time.gmtime(time.time() + TZ_OFFSET * 3600).tm_hour
    return (a <= h < b) if a < b else (h >= a or h < b)


def parse_button(s: str):
    """'Ном | https://...' -> (ном, линк) ё None."""
    if "|" not in s:
        return None
    label, url = (x.strip() for x in s.split("|", 1))
    if url.startswith("@"):
        url = "https://t.me/" + url[1:]
    elif url.startswith("t.me/"):
        url = "https://" + url
    if not label or len(label) > 32 or not re.match(r"^https?://\S+$", url):
        return None
    return label, url


def render_autoreply(html_text: str, name: str, bot_username: str) -> str:
    import html as _h

    body = html_text.replace("{name}", _h.escape(name))
    return f"{body}\n\n✍️ <i>Навишташуда тавассути боти @{bot_username}</i>"


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def typing_frames(text: str, steps: int = 6) -> list[str]:
    """Кадрҳои «навишти оҳиста»: матн ҳарф ба ҳарф бо курсори ▌."""
    n = len(text)
    if n < 4:
        return []
    steps = min(steps, n - 1)
    cuts = sorted({max(1, round(n * k / (steps + 1))) for k in range(1, steps + 1)})
    return [text[:c].rstrip() + "▌" for c in cuts if text[:c].strip()]


def _bar(p: int) -> str:
    filled = round(p / 10)
    return "█" * filled + "░" * (10 - filled) + f" {p}%"


def hask_frames(bot_username: str) -> list[str]:
    """Шӯхии бе зарар: «ҳак шудан»-и сохта, дар охир ошкор мешавад, ки шӯхӣ буд."""
    return [
        "🖥 HASK v4.0 | 🎯\n──────────\n📡 Ҷустуҷӯи дастгоҳ...\n" + _bar(6),
        "🖥 HASK v4.0 | 🎯\n──────────\n🔑 Парол ёфт шуд ✅\n🖼 Галерея: 1007 акс ёфт шуд\n" + _bar(47),
        "🖥 HASK v4.0 | 🎯\n──────────\n🔋 Батарея: 4% ⚠️\n🌡 Ҳарорат: 63°C 🔥\n" + _bar(89),
        "☠️☠️ ДАСТГОҲИ ШУМО ҲАК ШУД ☠️☠️\n\n🩸 Ҳамаи маълумот гирифта шуд.\nНусхабардорӣ ба охир мерасад...",
        "😂😂😂 ШӮХӢ БУД!\n\nҲеҷ чиз ҳак нашуд ва ҳеҷ маълумоте нагирифта шуд. Ором бош 😊\n\n"
        f"✍️ Навишташуда тавассути боти @{bot_username}",
    ]
