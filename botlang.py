import asyncio
import os
import sqlite3
from contextlib import closing

from aiogram.types import InlineKeyboardMarkup

import extras

DB = os.path.join(os.path.dirname(extras.DB) or ".", "lang.db")
LANGS = ("tg", "ru")
_cache: dict[int, str] = {}

BTN_RU = {
    "⬅️ Баргаштан": "⬅️ Назад",
    "↩️ Бекор": "↩️ Отмена",
    "❌ Бекор": "❌ Отмена",
    "🔤 Шрифтҳо": "🔤 Шрифты",
    "💳 Пардохт": "💳 Оплата",
    "⚙️ Танзимот": "⚙️ Настройки",
    "🚫 Хомӯш кардан": "🚫 Выключить",
    "⬅️ Ба танзимот": "⬅️ В настройки",
    "✅ Санҷиш": "✅ Проверить",
}


def _init_sync() -> None:
    with closing(sqlite3.connect(DB)) as c:
        c.execute("CREATE TABLE IF NOT EXISTS lang (uid INTEGER PRIMARY KEY, lang TEXT NOT NULL)")
        c.commit()
        for uid, lang in c.execute("SELECT uid, lang FROM lang"):
            _cache[uid] = lang


def _set_sync(uid: int, lang: str) -> None:
    with closing(sqlite3.connect(DB)) as c:
        c.execute("INSERT OR REPLACE INTO lang (uid, lang) VALUES (?, ?)", (uid, lang))
        c.commit()


async def init() -> None:
    await asyncio.to_thread(_init_sync)


def get(uid):
    return _cache.get(uid, "tg") if uid else "tg"


async def set_lang(uid: int, lang: str) -> None:
    if lang not in LANGS:
        return
    _cache[uid] = lang
    await asyncio.to_thread(_set_sync, uid, lang)


def pick(uid, tg: str, ru: str) -> str:
    return ru if get(uid) == "ru" else tg


def localize(markup, uid):
    if markup is None or get(uid) != "ru":
        return markup
    rows = []
    for row in markup.inline_keyboard:
        rows.append([
            b.model_copy(update={"text": BTN_RU[b.text]}) if b.text in BTN_RU else b
            for b in row
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
