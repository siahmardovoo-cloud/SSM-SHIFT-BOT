"""Интихоби шрифт бо саҳифаҳо (барои 65 шрифт) ва inline-режим."""
import html

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import fonts

PAGE_SIZE = 15
INLINE_PAGE = 50  # Telegram дар як ҷавоб на бештар аз 50 натиҷа қабул мекунад


def pages_count() -> int:
    return (len(fonts.FONTS) + PAGE_SIZE - 1) // PAGE_SIZE


def fonts_text(current: int, page: int) -> str:
    start = page * PAGE_SIZE
    lines = [f"🔤 <b>Интихоби шрифт</b> ({page + 1}/{pages_count()})\n"]
    for fid, name, _ in fonts.FONTS[start:start + PAGE_SIZE]:
        sample = html.escape(fonts.apply_font(fid, fonts.SAMPLE))
        mark = "✅ " if fid == current else ""
        lines.append(f"{mark}{fid} · {name}: {sample}")
    lines.append(f"\nШрифти ҳозира: <b>{fonts.font_name(current)}</b>")
    lines.append(f"💡 Дар ҳама ҷо: @{config.BOT_USERNAME} матн")
    return "\n".join(lines)


def fonts_kb(current: int, page: int) -> InlineKeyboardMarkup:
    total = pages_count()
    start = page * PAGE_SIZE
    b = InlineKeyboardBuilder()
    for fid, _, _ in fonts.FONTS[start:start + PAGE_SIZE]:
        b.button(text=f"✅ {fid}" if fid == current else str(fid), callback_data=f"font:{fid}:{page}")
    b.adjust(5)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"fonts:p:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"fonts:p:{page + 1}"))
    b.row(*nav)
    b.row(InlineKeyboardButton(text="🚫 Хомӯш кардан", callback_data=f"font:0:{page}"))
    b.row(InlineKeyboardButton(text="⬅️ Баргаштан", callback_data="menu"))
    return b.as_markup()


async def _show(cb: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            try:
                await cb.message.delete()
            except TelegramBadRequest:
                pass
            await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


def register(router: Router) -> None:
    @router.callback_query(F.data.regexp(r"^fonts(:p:\d+)?$"))
    async def cb_fonts(cb: CallbackQuery) -> None:
        page = int(cb.data.split(":")[2]) if ":p:" in cb.data else 0
        page = max(0, min(page, pages_count() - 1))
        user = await db.get_user(cb.from_user.id)
        await _show(cb, fonts_text(user["font"], page), fonts_kb(user["font"], page))

    @router.callback_query(F.data.regexp(r"^font:\d+:\d+$"))
    async def cb_font_pick(cb: CallbackQuery) -> None:
        _, fid_s, page_s = cb.data.split(":")
        fid, page = int(fid_s), int(page_s)
        if fid != 0 and fid not in {f[0] for f in fonts.FONTS}:
            return await cb.answer()
        page = max(0, min(page, pages_count() - 1))
        await db.set_font(cb.from_user.id, fid)
        await _show(cb, fonts_text(fid, page), fonts_kb(fid, page))

    @router.callback_query(F.data == "noop")
    async def cb_noop(cb: CallbackQuery) -> None:
        await cb.answer()

    @router.inline_query()
    async def on_inline(query: InlineQuery) -> None:
        text = query.query.strip()
        if not text:
            hint = InlineQueryResultArticle(
                id="hint",
                title="Матн нависед",
                description=f"@{config.BOT_USERNAME} Салом дунё",
                input_message_content=InputTextMessageContent(message_text=f"@{config.BOT_USERNAME}"),
            )
            return await query.answer([hint], cache_time=1, is_personal=True)
        offset = int(query.offset) if query.offset and query.offset.isdigit() else 0
        chunk = fonts.FONTS[offset:offset + INLINE_PAGE]
        results = []
        for fid, name, _ in chunk:
            styled = fonts.apply_font(fid, text)
            results.append(
                InlineQueryResultArticle(
                    id=str(fid),
                    title=f"{fid} · {name}",
                    description=styled[:80],
                    input_message_content=InputTextMessageContent(message_text=styled, parse_mode=None),
                )
            )
        nxt = str(offset + INLINE_PAGE) if offset + INLINE_PAGE < len(fonts.FONTS) else ""
        await query.answer(results, cache_time=1, is_personal=True, next_offset=nxt)
