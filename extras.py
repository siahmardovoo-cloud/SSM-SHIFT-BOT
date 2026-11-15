"""Функсияҳои иловагӣ: авточавоб, навишти оҳиста, шӯхии /hask.

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
    for name in ("DB_PATH", "DB_FILE", "DATABASE", "DB_NAME", "DB"):
        v = getattr(config, name, None)
        if v and isinstance(v, (str, os.PathLike)):
            d = os.path.dirname(str(v))
            return os.path.join(d, "extras.db") if d else "extras.db"
    return "extras.db"


DB = _db_path()
MAX_BUTTONS = 6
PERIODS = [1, 6, 12, 24, 48, 168]  # соат
_lock = asyncio.Lock()
_FIELDS = {"ar_text", "ar_on", "ar_period", "typing", "ar_buttons"}


async def init() -> None:
    async with aiosqlite.connect(DB) as c:
        await c.execute(
            """CREATE TABLE IF NOT EXISTS extras (
                user_id INTEGER PRIMARY KEY,
                ar_text TEXT NOT NULL DEFAULT '',
                ar_on INTEGER NOT NULL DEFAULT 0,
                ar_period INTEGER NOT NULL DEFAULT 24,
                ar_buttons TEXT NOT NULL DEFAULT '[]',
                typing INTEGER NOT NULL DEFAULT 0
            )"""
        )
        await c.execute(
            """CREATE TABLE IF NOT EXISTS ar_log (
                owner_id INTEGER, chat_id INTEGER, ts INTEGER,
                PRIMARY KEY (owner_id, chat_id)
            )"""
        )
        await c.commit()


async def get(uid: int) -> dict:
    async with aiosqlite.connect(DB) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute("SELECT * FROM extras WHERE user_id=?", (uid,))
        row = await cur.fetchone()
    d = {"ar_text": "", "ar_on": 0, "ar_period": 24, "typing": 0, "buttons": []}
    if row:
        d["ar_text"] = row["ar_text"]
        d["ar_on"] = row["ar_on"]
        d["ar_period"] = row["ar_period"]
        d["typing"] = row["typing"]
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


async def claim_reply(owner_id: int, chat_id: int, period_hours: int) -> bool:
    """True, агар ба ин чат дар давраи охир ҷавоб надода бошем (ва ҳамон лаҳза қайд мекунад)."""
    now = int(time.time())
    async with _lock:
        async with aiosqlite.connect(DB) as c:
            cur = await c.execute(
                "SELECT ts FROM ar_log WHERE owner_id=? AND chat_id=?", (owner_id, chat_id)
            )
            row = await cur.fetchone()
            if row and now - row[0] < period_hours * 3600:
                return False
            await c.execute(
                "INSERT OR REPLACE INTO ar_log (owner_id, chat_id, ts) VALUES (?, ?, ?)",
                (owner_id, chat_id, now),
            )
            await c.commit()
    return True


# ---------- Ёрдамчиҳо ----------
def fmt_period(h: int) -> str:
    return f"{h} соат" if h <= 24 else f"{h // 24} рӯз"


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
