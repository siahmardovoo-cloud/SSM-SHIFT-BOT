import asyncio
import html
import io
import json
import logging
import os
import re
import socket
import time
from datetime import datetime
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    BusinessConnection,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import admintools
import config
import db
import extras
import fonts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fontbot")

router = Router()

# Entity-ҳое, ки бо иваз кардани шрифт вайрон мешаванд — чунин паёмҳоро дастнорас мегузорем
SKIP_ENTITIES = {
    "url", "text_link", "mention", "text_mention", "email", "phone_number",
    "bot_command", "custom_emoji", "code", "pre",
}

# Оё версияи aiogram рангҳои тугмаро (style) дастгирӣ мекунад?
# Агар не — тугмаҳо бе ранг кор мекунанд, бот намеафтад.
HAS_STYLE = "style" in InlineKeyboardButton.model_fields
if not HAS_STYLE:
    log.warning("aiogram кӯҳна аст: рангҳои тугма хомӯшанд. Барои ранг: pip install -U aiogram")


class PayStates(StatesGroup):
    proof = State()


class ARStates(StatesGroup):
    text = State()
    btn = State()
    away = State()


# ====================== Ёрдамчиҳо ======================
def btn(
    text: str,
    data: str | None = None,
    url: str | None = None,
    style: str | None = None,
) -> InlineKeyboardButton:
    """style: 'primary' (кабуд), 'success' (сабз), 'danger' (сурх)."""
    extra = {"style": style} if (style and HAS_STYLE) else {}
    if url:
        return InlineKeyboardButton(text=text, url=url, **extra)
    return InlineKeyboardButton(text=text, callback_data=data, **extra)


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


BACK = btn("⬅️ Баргаштан", "menu", style="danger")


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def is_active(user) -> bool:
    return bool(user) and (user["sub_until"] or 0) > time.time()


def menu_kb(uid: int | None = None) -> InlineKeyboardMarkup:
    support = (
        btn("📞 Дастгирӣ", url=f"https://t.me/{config.SUPPORT_USERNAME.lstrip('@')}", style="success")
        if config.SUPPORT_USERNAME
        else btn("📞 Дастгирӣ", "no_support", style="success")
    )
    lessons = (
        btn("🎓 Дарсҳо", url=config.LESSONS_URL, style="danger")
        if config.LESSONS_URL
        else btn("🎓 Дарсҳо", "no_lessons", style="danger")
    )
    return kb([
        [btn("🔤 Шрифтҳо", "fonts", style="primary"), btn("⚙️ Танзимот", "settings", style="primary")],
        [btn("👤 Кабинет", "cabinet", style="primary"), btn("📋 Маълумот", "info", style="primary")],
        [btn("💳 Пардохт", "pay", style="success"), btn("👥 Реферал", "ref", style="success")],
        [btn("🔌 Пайваст кардан", "connect", style="success"), support],
        [lessons, btn("🛠 Панели Админ", "adm", style="danger")] if (uid in config.ADMIN_IDS) else [lessons],
    ])


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Паёми мавҷударо иваз мекунад (барои callback) ё паёми нав мефиристад."""
    if isinstance(event, CallbackQuery):
        msg = event.message
        try:
            await msg.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                try:
                    await msg.delete()
                except TelegramBadRequest:
                    pass
                await msg.answer(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            log.warning("admin %s: %s", admin_id, e)


# ====================== Каналҳои спонсор ======================
async def missing_sponsors(bot: Bot, user_id: int) -> list[str]:
    missing = []
    for ch in config.SPONSOR_CHANNELS:
        try:
            member = await bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:  # noqa: BLE001  (бот дар канал админ нест ва ғ.)
            log.warning("sponsor check %s failed: %s", ch, e)
    return missing


def sponsor_kb(missing: list[str]) -> InlineKeyboardMarkup:
    rows = [[btn(f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}", style="primary")] for ch in missing]
    rows.append([btn("✅ Санҷиш", "check_sub", style="success")])
    return kb(rows)


SPONSOR_TEXT = "📢 Барои истифода аз бот аввал ба каналҳои зерин обуна шавед, баъд «✅ Санҷиш»-ро пахш кунед."


class Gate(BaseMiddleware):
    """Корбарро сабт мекунад, спонсорҳоро месанҷад ва реферал хол медиҳад."""

    async def __call__(self, handler, event, data):
        bot: Bot = data["bot"]
        referrer = None
        if isinstance(event, Message):
            if event.chat.type != "private" or event.successful_payment or not event.from_user:
                return await handler(event, data)
            user = event.from_user
            if event.text and event.text.startswith("/start"):
                parts = event.text.split(maxsplit=1)
                if len(parts) == 2 and parts[1].isdigit() and int(parts[1]) != user.id:
                    referrer = int(parts[1])
        elif isinstance(event, CallbackQuery):
            user = event.from_user
        else:
            return await handler(event, data)

        await db.ensure_user(user.id, user.full_name, user.username, referrer)

        if config.SPONSOR_CHANNELS and user.id not in config.ADMIN_IDS:
            missing = await missing_sponsors(bot, user.id)
            if missing:
                if isinstance(event, CallbackQuery):
                    await event.answer("Ҳанӯз ба ҳама каналҳо обуна нашудаед", show_alert=True)
                    if event.data != "check_sub":
                        await event.message.answer(SPONSOR_TEXT, reply_markup=sponsor_kb(missing))
                else:
                    await event.answer(SPONSOR_TEXT, reply_markup=sponsor_kb(missing))
                return

        result = await db.credit_referral(user.id)
        if result:
            ref_id, rewarded = result
            try:
                await bot.send_message(ref_id, "🎉 Дӯсти шумо ба бот ҳамроҳ шуд: +1 хол ⭐")
                if rewarded:
                    await bot.send_message(
                        ref_id,
                        f"🎁 Табрик! {config.REF_TARGET} хол ҷамъ шуд — обунаи {30} рӯза фаъол шуд.",
                    )
            except Exception:  # noqa: BLE001
                pass
            if rewarded:
                await notify_admins(
                    bot, f"🎁 Корбар <code>{ref_id}</code> {config.REF_TARGET} хол ҷамъ кард — тӯҳфаи Telegram фиристед."
                )
        return await handler(event, data)


router.message.outer_middleware(Gate())
router.callback_query.outer_middleware(Gate())


# ====================== Меню ======================
def welcome_text(name: str) -> str:
    return (
        f"👋 Хуш омадед, <b>{html.escape(name)}</b>!\n\n"
        "Ин бот матни шуморо ба шрифтҳои зебо табдил медиҳад — "
        "дар худи бот, дар ҳама ҷо бо режими inline ва дар чатҳои шумо бо Telegram Business.\n\n"
        "✨ Аз менюи поён интихоб кунед:"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = welcome_text(message.from_user.full_name)
    if config.WELCOME_VIDEO:
        try:
            await message.answer_video(config.WELCOME_VIDEO, caption=text, reply_markup=menu_kb(message.from_user.id))
            return
        except Exception as e:  # noqa: BLE001
            log.warning("welcome video failed: %s", e)
    await message.answer(text, reply_markup=menu_kb(message.from_user.id))


@router.callback_query(F.data.in_({"menu", "check_sub"}))
async def cb_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, welcome_text(cb.from_user.full_name), menu_kb(cb.from_user.id))


@router.callback_query(F.data.in_({"no_support", "no_lessons"}))
async def cb_not_set(cb: CallbackQuery) -> None:
    await cb.answer("Ин бахш ҳанӯз танзим нашудааст", show_alert=True)


# ====================== Шрифтҳо ======================
def fonts_text(current: int) -> str:
    lines = ["🔤 <b>Интихоби шрифт</b>\n"]
    for fid, name, _ in fonts.FONTS:
        sample = html.escape(fonts.apply_font(fid, fonts.SAMPLE))
        mark = "✅ " if fid == current else ""
        lines.append(f"{mark}{fid} · {name}: {sample}")
    lines.append(f"\nШрифти ҳозира: <b>{fonts.font_name(current)}</b>")
    lines.append(f"💡 Дар ҳама ҷо: @{config.BOT_USERNAME} матн")
    return "\n".join(lines)


def fonts_kb(current: int) -> InlineKeyboardMarkup:
    ids = [f[0] for f in fonts.FONTS]
    buttons = [
        btn(
            f"✅ {fid}" if fid == current else str(fid),
            f"font:{fid}",
            style="success" if fid == current else "primary",
        )
        for fid in ids
    ]
    rows: list[list[InlineKeyboardButton]] = []
    i = 0
    for size in (4, 4, 4, 3):
        chunk = buttons[i:i + size]
        if chunk:
            rows.append(chunk)
        i += size
    if i < len(buttons):  # агар шрифти иловагӣ илова шавад
        for j in range(i, len(buttons), 4):
            rows.append(buttons[j:j + 4])
    rows.append([btn("🚫 Хомӯш кардан", "font:0", style="danger")])
    rows.append([BACK])
    return kb(rows)


@router.callback_query(F.data == "fonts")
async def cb_fonts(cb: CallbackQuery) -> None:
    user = await db.get_user(cb.from_user.id)
    await show(cb, fonts_text(user["font"]), fonts_kb(user["font"]))


@router.callback_query(F.data.startswith("font:"))
async def cb_font_pick(cb: CallbackQuery) -> None:
    fid = int(cb.data.split(":")[1])
    if fid != 0 and fid not in {f[0] for f in fonts.FONTS}:
        return await cb.answer()
    await db.set_font(cb.from_user.id, fid)
    await show(cb, fonts_text(fid), fonts_kb(fid))


# ====================== Танзимот / Кабинет / Маълумот ======================
def has_access(user_id: int, user) -> bool:
    return user_id in config.ADMIN_IDS or is_active(user)


async def locked_page(cb: CallbackQuery) -> bool:
    """Агар обуна нест — саҳифаи қулфро нишон медиҳад ва True бармегардонад."""
    user = await db.get_user(cb.from_user.id)
    if has_access(cb.from_user.id, user):
        return False
    text = (
        "🔒 <b>Ин бахш барои обунахандагон аст</b>\n\n"
        "Аввал обуна харед — баъд табдили худкор дар Telegram Business, "
        "авточавоб ва дигар имкониятҳо кушода мешаванд."
    )
    await show(cb, text, kb([[btn("💳 Пардохт", "pay", style="success")], [BACK]]))
    return True


def onoff(v) -> str:
    return "✅" if v else "❌"


@router.callback_query(
    F.data.in_({"settings", "toggle_auto", "toggle_typing", "toggle_del", "toggle_edit", "toggle_groups"})
)
async def cb_settings(cb: CallbackQuery) -> None:
    if await locked_page(cb):
        return
    uid = cb.from_user.id
    flips = {
        "toggle_typing": "typing",
        "toggle_del": "notify_del",
        "toggle_edit": "notify_edit",
        "toggle_groups": "groups",
    }
    if cb.data == "toggle_auto":
        await db.toggle_auto(uid)
    elif cb.data in flips:
        ex = await extras.get(uid)
        key = flips[cb.data]
        await extras.save(uid, **{key: 0 if ex[key] else 1})
    user = await db.get_user(uid)
    ex = await extras.get(uid)
    ar_state = "🟢 фаъол" if (ex["ar_on"] and (ex["ar_text"] or ex["away_text"])) else "🔴 хомӯш"
    text = (
        "⚙️ <b>Танзимот</b>\n\n"
        f"🔄 Табдили худкор дар Business: {'✅ фаъол' if user['auto'] else '❌ хомӯш'}\n"
        f"⌨️ Навишти оҳиста: {'✅ фаъол' if ex['typing'] else '❌ хомӯш'}\n"
        f"🗑 Удалит (паёми нестшуда): {'✅ фаъол' if ex['notify_del'] else '❌ хомӯш'}\n"
        f"✏️ Таҳрир (паёми таҳриршуда): {'✅ фаъол' if ex['notify_edit'] else '❌ хомӯш'}\n"
        f"👥 Шрифт дар гурӯҳҳо: {'✅ фаъол' if ex['groups'] else '❌ хомӯш'}\n"
        f"🤖 Авточавоб: {ar_state}\n"
        f"🔤 Шрифти ҳозира: <b>{fonts.font_name(user['font'])}</b>\n\n"
        "ℹ️ <i>Удалит/Таҳрир: вақте шахси дигар дар чатҳои Business паёмро нест ё таҳрир кунад, "
        "бот матни онро ба шумо мефиристад (танҳо аз лаҳзаи фаъол кардан).\n"
        "Дар гурӯҳ: /f матн — бот матнро бо шрифти шумо мефиристад.</i>"
    )

    def tgl(label: str, cb_data: str, on) -> InlineKeyboardButton:
        return btn(f"{label} {onoff(on)}", cb_data, style="success" if on else "danger")

    markup = kb([
        [tgl("🔄 Табдили худкор", "toggle_auto", user["auto"])],
        [tgl("⌨️ Навишти оҳиста", "toggle_typing", ex["typing"])],
        [tgl("🗑 Удалит", "toggle_del", ex["notify_del"]), tgl("✏️ Таҳрир", "toggle_edit", ex["notify_edit"])],
        [tgl("👥 Шрифт дар гурӯҳҳо", "toggle_groups", ex["groups"])],
        [btn("🤖 Авточавоб", "ar", style="primary"), btn("🔤 Шрифтҳо", "fonts", style="primary")],
        [BACK],
    ])
    await show(cb, text, markup)


# ====================== Авточавоб ======================
AR_HELP = (
    "✍️ <b>Матни авточавоб</b>\n\n"
    "Матнеро нависед, ки вақте шахси дигар ба шумо менависад, бот ба ӯ мефиристад. Масалан:\n"
    "<code>Салом! Ман ҳоло бандам, баъдтар ҷавоб медиҳам 🤖</code>\n\n"
    "• <code>{name}</code> — номи нависанда\n"
    "• Эмодзиҳои premium қабул мешаванд ✨\n"
    "• Охирашро бот худаш илова мекунад: <i>Навишташуда тавассути боти @BOTUSER</i>"
)


async def ar_menu(event, uid: int) -> None:
    ex = await extras.get(uid)
    has_text = bool(ex["ar_text"])
    has_away = bool(ex["away_text"])
    on = bool(ex["ar_on"]) and (has_text or has_away)
    text = (
        "🤖 <b>Авточавоб</b>\n\n"
        "Вақте шахси дигар ба шумо менависад, бот ба ӯ худкор ҷавоб медиҳад.\n\n"
        f"✍️ Матн: {'✅ гузошта шудааст' if has_text else '❌ нест'}\n"
        f"🕒 Матни «дар шабака нест»: {'✅ гузошта шудааст' if has_away else '❌ нест'}\n"
        f"🌙 Вақти «дар шабака нест»: {extras.fmt_slot(ex['away_slot'])}\n"
        f"🔘 Тугмаҳо: {len(ex['buttons'])}/{extras.MAX_BUTTONS}\n"
        f"🔄 Режим: {'🟢 фаъол' if on else '🔴 хомӯш'}\n"
        f"⏰ Такрор: ҳар {extras.fmt_period(ex['ar_period'])} (ба як шахс)"
    )
    markup = kb([
        [btn(f"✍️ Матни авточавоб {onoff(has_text)}", "ar_text", style="primary")],
        [btn(f"🕒 Матни «дар шабака нест» {onoff(has_away)}", "ar_away", style="primary")],
        [btn(f"🔘 Тугмаҳои матн ({len(ex['buttons'])})", "ar_btns", style="success")],
        [
            btn(f"🔄 Режим: {'🟢 Фаъол' if on else '🔴 Хомӯш'}", "ar_toggle", style="success" if on else "danger"),
            btn(f"⏰ Такрор: {extras.fmt_period(ex['ar_period'])}", "ar_period", style="primary"),
        ],
        [btn(f"🌙 Вақти «дар шабака нест»: {extras.fmt_slot(ex['away_slot'])}", "ar_slot", style="primary")],
        [btn("⬅️ Ба танзимот", "settings", style="danger")],
    ])
    await show(event, text, markup)


@router.callback_query(F.data == "ar")
async def cb_ar(cb: CallbackQuery, state: FSMContext) -> None:
    if await locked_page(cb):
        return
    await state.clear()
    await ar_menu(cb, cb.from_user.id)


@router.callback_query(F.data == "ar_toggle")
async def cb_ar_toggle(cb: CallbackQuery) -> None:
    if await locked_page(cb):
        return
    ex = await extras.get(cb.from_user.id)
    if not ex["ar_text"]:
        return await cb.answer("Аввал матни авточавобро гузоред", show_alert=True)
    await extras.save(cb.from_user.id, ar_on=0 if ex["ar_on"] else 1)
    await ar_menu(cb, cb.from_user.id)


@router.callback_query(F.data == "ar_period")
async def cb_ar_period(cb: CallbackQuery) -> None:
    if await locked_page(cb):
        return
    ex = await extras.get(cb.from_user.id)
    cur = ex["ar_period"]
    nxt = next((p for p in extras.PERIODS if p > cur), extras.PERIODS[0])
    await extras.save(cb.from_user.id, ar_period=nxt)
    await ar_menu(cb, cb.from_user.id)


@router.callback_query(F.data == "ar_text")
async def cb_ar_text(cb: CallbackQuery, state: FSMContext) -> None:
    if await locked_page(cb):
        return
    await state.set_state(ARStates.text)
    text = AR_HELP.replace("@BOTUSER", f"@{config.BOT_USERNAME}")
    await show(cb, text, kb([
        [btn("🗑 Хомӯш кардан", "ar_clear", style="danger")],
        [btn("↩️ Бекор", "ar", style="primary")],
    ]))


@router.callback_query(F.data == "ar_clear")
async def cb_ar_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await extras.save(cb.from_user.id, ar_text="", ar_on=0)
    await ar_menu(cb, cb.from_user.id)


@router.message(ARStates.text, F.text)
async def ar_save_text(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await message.answer("✍️ Матни оддӣ нависед (на фармон) ё «↩️ Бекор»-ро пахш кунед.")
    if len(message.text) > 700:
        return await message.answer("⚠️ Матн аз 700 ҳарф зиёд аст. Кӯтоҳтар нависед.")
    await extras.save(message.from_user.id, ar_text=message.html_text, ar_on=1)
    await state.clear()
    await message.answer("✅ Матни авточавоб захира шуд ва фаъол гардид.")
    await ar_menu(message, message.from_user.id)


@router.message(ARStates.text)
async def ar_text_wrong(message: Message) -> None:
    await message.answer("✍️ Лутфан матн нависед.")


AWAY_HELP = (
    "🕒 <b>Матни «дар шабака нест»</b>\n\n"
    "Ин матн дар вақти «дар шабака нест» (вақтро дар меню интихоб мекунед) "
    "ба ҷои авточавоби оддӣ ба касе, ки менависад, фиристода мешавад. Масалан:\n"
    "<code>Салом, {name}! Ҳозир хобам, саҳар ҷавоб медиҳам 😴</code>\n\n"
    "• <code>{name}</code> — номи нависанда\n"
    "• Эмодзиҳои premium қабул мешаванд ✨\n"
    "• Охирашро бот худаш илова мекунад: <i>Навишташуда тавассути боти @BOTUSER</i>"
)


@router.callback_query(F.data == "ar_away")
async def cb_ar_away(cb: CallbackQuery, state: FSMContext) -> None:
    if await locked_page(cb):
        return
    await state.set_state(ARStates.away)
    text = AWAY_HELP.replace("@BOTUSER", f"@{config.BOT_USERNAME}")
    await show(cb, text, kb([
        [btn("🗑 Хомӯш кардан", "away_clear", style="danger")],
        [btn("↩️ Бекор", "ar", style="primary")],
    ]))


@router.callback_query(F.data == "away_clear")
async def cb_away_clear(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await extras.save(cb.from_user.id, away_text="")
    await ar_menu(cb, cb.from_user.id)


@router.message(ARStates.away, F.text)
async def ar_save_away(message: Message, state: FSMContext) -> None:
    if message.text.startswith("/"):
        return await message.answer("✍️ Матни оддӣ нависед (на фармон) ё «↩️ Бекор»-ро пахш кунед.")
    if len(message.text) > 700:
        return await message.answer("⚠️ Матн аз 700 ҳарф зиёд аст. Кӯтоҳтар нависед.")
    await extras.save(message.from_user.id, away_text=message.html_text, ar_on=1)
    await state.clear()
    await message.answer("✅ Матни «дар шабака нест» захира шуд ва фаъол гардид.")
    await ar_menu(message, message.from_user.id)


@router.message(ARStates.away)
async def ar_away_wrong(message: Message) -> None:
    await message.answer("✍️ Лутфан матн нависед.")


@router.callback_query(F.data == "ar_slot")
async def cb_ar_slot(cb: CallbackQuery) -> None:
    if await locked_page(cb):
        return
    ex = await extras.get(cb.from_user.id)
    await extras.save(cb.from_user.id, away_slot=(ex["away_slot"] + 1) % len(extras.AWAY_SLOTS))
    await ar_menu(cb, cb.from_user.id)


async def ar_btns_menu(event, uid: int) -> None:
    ex = await extras.get(uid)
    lines = [f"🔘 <b>Тугмаҳои матн</b> ({len(ex['buttons'])}/{extras.MAX_BUTTONS})\n"]
    lines.append("Ин тугмаҳо дар зери паёми авточавоб пайдо мешаванд.\n")
    for i, (label, url) in enumerate(ex["buttons"], 1):
        lines.append(f"{i}. {html.escape(label)} → {html.escape(url)}")
    rows = [[btn(f"🗑 {label}", f"ar_bdel:{i}", style="danger")] for i, (label, _) in enumerate(ex["buttons"])]
    if len(ex["buttons"]) < extras.MAX_BUTTONS:
        rows.append([btn("➕ Тугма илова кардан", "ar_badd", style="success")])
    rows.append([btn("⬅️ Баргаштан", "ar", style="danger")])
    await show(event, "\n".join(lines), kb(rows))


@router.callback_query(F.data == "ar_btns")
async def cb_ar_btns(cb: CallbackQuery, state: FSMContext) -> None:
    if await locked_page(cb):
        return
    await state.clear()
    await ar_btns_menu(cb, cb.from_user.id)


@router.callback_query(F.data == "ar_badd")
async def cb_ar_badd(cb: CallbackQuery, state: FSMContext) -> None:
    if await locked_page(cb):
        return
    await state.set_state(ARStates.btn)
    text = (
        "➕ <b>Тугмаи нав</b>\n\n"
        "Ном ва линкро бо хати «|» ҷудо карда нависед:\n"
        "<code>Канал | https://t.me/username</code>"
    )
    await show(cb, text, kb([[btn("↩️ Бекор", "ar_btns", style="primary")]]))


@router.message(ARStates.btn, F.text)
async def ar_save_btn(message: Message, state: FSMContext) -> None:
    parsed = extras.parse_button(message.text)
    if not parsed:
        return await message.answer(
            "⚠️ Дуруст нест. Ин тавр нависед:\n<code>Канал | https://t.me/username</code>"
        )
    ex = await extras.get(message.from_user.id)
    if len(ex["buttons"]) >= extras.MAX_BUTTONS:
        await state.clear()
        return await message.answer("⚠️ Аллакай 6 тугма ҳаст.")
    await extras.save(message.from_user.id, buttons=ex["buttons"] + [parsed])
    await state.clear()
    await message.answer("✅ Тугма илова шуд.")
    await ar_btns_menu(message, message.from_user.id)


@router.message(ARStates.btn)
async def ar_btn_wrong(message: Message) -> None:
    await message.answer("➕ Матн нависед: <code>Ном | линк</code>")


@router.callback_query(F.data.startswith("ar_bdel:"))
async def cb_ar_bdel(cb: CallbackQuery) -> None:
    if await locked_page(cb):
        return
    idx = int(cb.data.split(":")[1])
    ex = await extras.get(cb.from_user.id)
    btns = ex["buttons"]
    if 0 <= idx < len(btns):
        btns.pop(idx)
        await extras.save(cb.from_user.id, buttons=btns)
    await ar_btns_menu(cb, cb.from_user.id)


@router.callback_query(F.data == "cabinet")
async def cb_cabinet(cb: CallbackQuery) -> None:
    u = await db.get_user(cb.from_user.id)
    username = f"@{u['username']}" if u["username"] else "—"
    sub = f"✅ то {fmt_date(u['sub_until'])}" if is_active(u) else "❌ нест"
    business = "✅ пайваст" if await db.has_connection(u["id"]) else "❌ пайваст нест"
    text = (
        "👤 <b>Кабинети шахсии шумо</b>\n\n"
        f"🪪 Ном: {html.escape(u['name'] or '—')}\n"
        f"🔗 Юзернейм: {html.escape(username)}\n"
        f"🆔 ID: <code>{u['id']}</code>\n"
        f"⏳ Обуна: {sub}\n"
        f"🔤 Шрифт: {fonts.font_name(u['font'])}\n"
        f"💼 Business: {business}\n"
        f"👥 Рефералҳо: {u['invited']} | ⭐ Холҳо: {u['points']}/{config.REF_TARGET}"
    )
    await show(cb, text, kb([
        [btn("💳 Пардохт", "pay", style="success"), btn("⚙️ Танзимот", "settings", style="primary")],
        [BACK],
    ]))


@router.callback_query(F.data == "info")
async def cb_info(cb: CallbackQuery) -> None:
    text = (
        "📋 <b>Бот чӣ кор карда метавонад?</b>\n\n"
        "• Матни шуморо ба 15 шрифти зебо табдил медиҳад.\n"
        "• Режими inline: дар ҳар чат <code>@{bot} матн</code> нависед ва шрифтро интихоб кунед.\n"
        "• Telegram Business: паёмҳои шумо дар чатҳо худкор бо шрифти интихобшуда иваз мешаванд "
        "(бо обуна).\n"
        "• Авточавоб: ба касе, ки ба шумо менависад, бот худкор ҷавоб медиҳад.\n"
        "• Фармонҳо дар чат: /shrift1…/shrift15 — шрифт, /shrift0 — хомӯш, /hask — шӯхӣ.\n"
        "• Системаи реферал: {t} дӯст = 1 моҳи ройгон."
    ).format(bot=config.BOT_USERNAME, t=config.REF_TARGET)
    await show(cb, text, kb([[BACK]]))


@router.callback_query(F.data == "connect")
async def cb_connect(cb: CallbackQuery) -> None:
    text = (
        "🔌 <b>Тарзи пайваст кардани бот ба Business</b>\n\n"
        "1. Telegram → <b>Танзимот</b> → <b>Telegram Business</b> → <b>Чат-ботҳо</b>.\n"
        f"2. Номи ботро нависед: <code>@{config.BOT_USERNAME}</code> ва интихоб кунед.\n"
        "3. Чатҳоро интихоб кунед (масалан «Ҳамаи чатҳои шахсӣ»).\n"
        "4. Иҷозатҳо: <b>Паёмҳоро хондан</b> ва <b>Ба паёмҳо ҷавоб додан</b> / "
        "<b>Таҳрир</b> фаъол бошанд.\n"
        "5. Обунаро фаъол кунед (💳 Пардохт) — ва паёмҳо худкор иваз мешаванд ✅\n\n"
        "💡 Дар ҳар чат: <code>/shrift5</code> — шрифти 5, <code>/shrift0</code> — хомӯш, <code>/hask</code> — шӯхӣ.\n\n"
        "ℹ️ Паёмҳои дорои линк, @ва кодро бот дастнорас мегузорад."
    )
    await show(cb, text, kb([[btn("💳 Пардохт", "pay", style="success")], [BACK]]))


# ====================== Реферал ======================
@router.callback_query(F.data == "ref")
async def cb_ref(cb: CallbackQuery) -> None:
    u = await db.get_user(cb.from_user.id)
    link = f"https://t.me/{config.BOT_USERNAME}?start={u['id']}"
    text = (
        "👥 <b>Системаи Реферал</b>\n\n"
        "Дӯстонро бо линки худ ба бот даъват кунед. Вақте дӯстатон ба бот медарояд"
        f"{' ва ба каналҳои спонсор обуна мешавад' if config.SPONSOR_CHANNELS else ''} — "
        "шумо 1 хол мегиред.\n"
        f"⭐ Ба {config.REF_TARGET} хол расид — <b>обунаи 1 моҳа + тӯҳфаи Telegram</b> 🎁\n\n"
        f"🔗 Линк: <code>{link}</code>\n"
        f"📊 Даъватшуда: {u['invited']} нафар\n"
        f"⭐ Холҳо: {u['points']} / {config.REF_TARGET}"
    )
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('Боти шрифтҳои зебо 🔤')}"
    await show(cb, text, kb([[btn("📤 Фиристодан ба дӯст", url=share, style="primary")], [BACK]]))


# ====================== Пардохт ======================
@router.callback_query(F.data == "pay")
async def cb_pay(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    u = await db.get_user(cb.from_user.id)
    now_state = f"✅ то {fmt_date(u['sub_until'])}" if is_active(u) else "❌ нест"
    text = (
        f"💳 <b>Пардохти обунаи моҳона</b>\n\n"
        f"💰 Нарх: {config.PRICE_SOMONI} сомонӣ ё {config.STARS_PRICE} Stars ⭐\n"
        f"⏳ Ҳозира: {now_state}\n\n"
        "⭐ <b>Старс</b> — пардохт ва фаъолшавӣ дар ҳамон лаҳза.\n"
        f"🏦 <b>{html.escape(config.CARD_BANK)}</b> — ба корт гузаронед ва чекро фиристед."
    )
    await show(cb, text, kb([
        [
            btn(f"⭐ Старс ({config.STARS_PRICE} ⭐)", "pay_stars", style="success"),
            btn(f"🏦 {config.CARD_BANK}", "pay_card", style="success"),
        ],
        [BACK],
    ]))


@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(cb: CallbackQuery, bot: Bot) -> None:
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title="Обунаи 1 моҳа",
        description="Табдили худкори шрифт дар Telegram Business",
        payload=f"sub:{config.SUB_DAYS}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Обуна", amount=config.STARS_PRICE)],
    )
    await cb.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message) -> None:
    uid = message.from_user.id
    until = await db.extend_sub(uid, config.SUB_DAYS)
    await db.add_payment(uid, "stars", "approved")
    await message.answer(
        f"✅ Пардохт қабул шуд! Обуна то <b>{fmt_date(until)}</b> фаъол аст.", reply_markup=menu_kb(uid)
    )


@router.callback_query(F.data == "pay_card")
async def cb_pay_card(cb: CallbackQuery, state: FSMContext) -> None:
    if not config.CARD_NUMBER:
        return await cb.answer("Пардохт бо корт ҳанӯз танзим нашудааст", show_alert=True)
    await state.set_state(PayStates.proof)
    holder = f"\n👤 Ном: {html.escape(config.CARD_HOLDER)}" if config.CARD_HOLDER else ""
    text = (
        f"🏦 <b>{html.escape(config.CARD_BANK)}</b>\n\n"
        f"Рақам: <code>{html.escape(config.CARD_NUMBER)}</code>{holder}\n"
        f"Маблағ: <b>{config.PRICE_SOMONI} сомонӣ</b>\n\n"
        "Пас аз гузаронидан скриншоти чекро ҳамин ҷо фиристед 📸"
    )
    await show(cb, text, kb([[BACK]]))


@router.message(PayStates.proof, F.photo)
async def on_proof(message: Message, state: FSMContext, bot: Bot) -> None:
    file_id = message.photo[-1].file_id
    pid = await db.add_payment(message.from_user.id, "card", "pending", file_id)
    await state.clear()
    await message.answer("⏳ Чек фиристода шуд. Админ ба наздикӣ санҷида, обунаро фаъол мекунад.")
    caption = (
        f"💳 Пардохти нав #{pid}\n"
        f"Корбар: {html.escape(message.from_user.full_name)} (<code>{message.from_user.id}</code>)\n"
        f"Маблағ: {config.PRICE_SOMONI} сомонӣ"
    )
    markup = kb([[
        btn("✅ Тасдиқ", f"appr:{pid}", style="success"),
        btn("❌ Рад", f"rej:{pid}", style="danger"),
    ]])
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=caption, reply_markup=markup)
        except Exception as e:  # noqa: BLE001
            log.warning("admin %s: %s", admin_id, e)


@router.message(PayStates.proof)
async def on_proof_wrong(message: Message) -> None:
    await message.answer("📸 Лутфан скриншоти чекро ҳамчун расм фиристед.")


@router.callback_query(F.data.regexp(r"^(appr|rej):\d+$"))
async def on_admin_decision(cb: CallbackQuery, bot: Bot) -> None:
    if cb.from_user.id not in config.ADMIN_IDS:
        return await cb.answer("Танҳо барои админ", show_alert=True)
    action, pid_s = cb.data.split(":")
    payment = await db.get_payment(int(pid_s))
    if not payment or payment["status"] != "pending":
        return await cb.answer("Ин пардохт аллакай коркард шудааст", show_alert=True)
    uid = payment["user_id"]
    if action == "appr":
        until = await db.extend_sub(uid, config.SUB_DAYS)
        await db.set_payment_status(payment["id"], "approved")
        await bot.send_message(uid, f"✅ Пардохт тасдиқ шуд! Обуна то <b>{fmt_date(until)}</b> фаъол аст.")
        result = "✅ Тасдиқ шуд"
    else:
        await db.set_payment_status(payment["id"], "rejected")
        await bot.send_message(uid, "❌ Пардохт рад шуд. Агар хато бошад, ба дастгирӣ нависед.")
        result = "❌ Рад шуд"
    try:
        await cb.message.edit_caption(caption=f"{cb.message.caption}\n\n{result}", reply_markup=None)
    except TelegramBadRequest:
        pass
    await cb.answer(result)


# ====================== Панели Админ ======================
class AdminStates(StatesGroup):
    find = State()
    bc = State()
    restore = State()


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


async def adm_guard(cb: CallbackQuery) -> bool:
    if is_admin(cb.from_user.id):
        return True
    await cb.answer("Танҳо барои админ", show_alert=True)
    return False


async def adm_panel(event) -> None:
    c = await admintools.counts()
    if c is None:
        stats = "⚠️ Ҷадвали корбарон ёфт нашуд."
    else:
        stats = (
            f"👥 Корбарон: <b>{c['total']}</b>\n"
            f"✅ Обунаи фаъол: <b>{c['active']}</b>\n"
            f"⌛ Обунаи гузашта: <b>{c['expired']}</b>"
        )
    text = (
        "🛠 <b>Панели Админ</b>\n\n"
        f"{stats}\n\n"
        f"💾 Файли база: <code>{html.escape(admintools.db_path())}</code>"
    )
    markup = kb([
        [btn("✅ Обунахандагон", "adm_l:active:0", style="success"), btn("🔎 Ёфтани корбар", "adm_find", style="primary")],
        [btn("📣 Паёми умумӣ", "adm_bc", style="primary"), btn("📄 Рӯйхат (CSV)", "adm_csv", style="primary")],
        [btn("💾 Backup", "adm_bk", style="success"), btn("♻️ Барқарорсозӣ", "adm_rs", style="danger")],
        [BACK],
    ])
    await show(event, text, markup)


@router.callback_query(F.data == "adm")
async def cb_adm(cb: CallbackQuery, state: FSMContext) -> None:
    if not await adm_guard(cb):
        return
    await state.clear()
    await adm_panel(cb)


KINDS = {"active": "✅ Обунахандагон", "expired": "⌛ Обунаи гузашта", "all": "👥 Ҳамаи корбарон"}


def user_label(r) -> str:
    mark = "✅" if (r["sub_until"] or 0) > time.time() else "▫️"
    name = (r["name"] or "—")[:20]
    uname = f" @{r['username']}" if r["username"] else ""
    return f"{mark} {name}{uname}"[:60]


@router.callback_query(F.data.startswith("adm_l:"))
async def cb_adm_list(cb: CallbackQuery) -> None:
    if not await adm_guard(cb):
        return
    _, kind, page_s = cb.data.split(":")
    if kind not in KINDS:
        return await cb.answer()
    page = max(0, int(page_s))
    per = 8
    rows, total = await admintools.list_users(kind, page, per)
    pages = max(1, (total + per - 1) // per)
    if page >= pages:
        page = pages - 1
        rows, total = await admintools.list_users(kind, page, per)
    text = f"<b>{KINDS[kind]}</b> — {total} нафар (саҳ. {page + 1}/{pages})"
    if not rows:
        text += "\n\nХолӣ аст."
    buttons = [[btn(user_label(r), f"adm_u:{r['id']}", style="primary")] for r in rows]
    nav = []
    if page > 0:
        nav.append(btn("◀️", f"adm_l:{kind}:{page - 1}"))
    if page + 1 < pages:
        nav.append(btn("▶️", f"adm_l:{kind}:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        btn("✅ Фаъол", "adm_l:active:0", style="success"),
        btn("⌛ Гузашта", "adm_l:expired:0", style="danger"),
        btn("👥 Ҳама", "adm_l:all:0", style="primary"),
    ])
    buttons.append([btn("⬅️ Панел", "adm", style="danger")])
    await show(cb, text, kb(buttons))


async def adm_card(event, uid: int, notice: str = "") -> None:
    u = await db.get_user(uid)
    if not u:
        if isinstance(event, CallbackQuery):
            return await event.answer("Корбар ёфт нашуд", show_alert=True)
        return await event.answer("Корбар ёфт нашуд")
    username = f"@{u['username']}" if u["username"] else "—"
    sub = f"✅ то {fmt_date(u['sub_until'])}" if is_active(u) else "❌ нест"
    text = (
        (f"{notice}\n\n" if notice else "")
        + f"👤 <b>{html.escape(u['name'] or '—')}</b> ({html.escape(username)})\n"
        f"🆔 <code>{u['id']}</code>\n"
        f"⏳ Обуна: {sub}\n"
        f"🔤 Шрифт: {fonts.font_name(u['font'])}\n"
        f"👥 Рефералҳо: {u['invited']} | ⭐ Холҳо: {u['points']}"
    )
    markup = kb([
        [
            btn("🎁 +30 рӯз", f"adm_g:{uid}:30", style="success"),
            btn("🎁 +90 рӯз", f"adm_g:{uid}:90", style="success"),
            btn("🎁 +365", f"adm_g:{uid}:365", style="success"),
        ],
        [btn("🚫 Бекор кардани обуна", f"adm_r:{uid}", style="danger")],
        [btn("⬅️ Рӯйхат", "adm_l:active:0", style="primary"), btn("🛠 Панел", "adm", style="danger")],
    ])
    await show(event, text, markup)


@router.callback_query(F.data.regexp(r"^adm_u:\d+$"))
async def cb_adm_user(cb: CallbackQuery) -> None:
    if not await adm_guard(cb):
        return
    await adm_card(cb, int(cb.data.split(":")[1]))


@router.callback_query(F.data.regexp(r"^adm_g:\d+:\d+$"))
async def cb_adm_grant(cb: CallbackQuery, bot: Bot) -> None:
    if not await adm_guard(cb):
        return
    _, uid_s, days_s = cb.data.split(":")
    uid, days = int(uid_s), int(days_s)
    if not await db.get_user(uid):
        return await cb.answer("Корбар ёфт нашуд", show_alert=True)
    until = await db.extend_sub(uid, days)
    try:
        await bot.send_message(uid, f"🎁 Ба шумо обуна дода шуд — то <b>{fmt_date(until)}</b>.")
    except Exception:  # noqa: BLE001
        pass
    await adm_card(cb, uid, f"✅ +{days} рӯз дода шуд.")


@router.callback_query(F.data.regexp(r"^adm_r:\d+$"))
async def cb_adm_revoke(cb: CallbackQuery) -> None:
    if not await adm_guard(cb):
        return
    uid = int(cb.data.split(":")[1])
    await show(cb, f"🚫 Обунаи корбари <code>{uid}</code> бекор карда шавад?", kb([
        [btn("✅ Ҳа, бекор кун", f"adm_ry:{uid}", style="danger")],
        [btn("↩️ Не", f"adm_u:{uid}", style="primary")],
    ]))


@router.callback_query(F.data.regexp(r"^adm_ry:\d+$"))
async def cb_adm_revoke_yes(cb: CallbackQuery) -> None:
    if not await adm_guard(cb):
        return
    uid = int(cb.data.split(":")[1])
    await admintools.set_sub(uid, 0)
    await adm_card(cb, uid, "🚫 Обуна бекор шуд.")


# ----- Ёфтани корбар -----
@router.callback_query(F.data == "adm_find")
async def cb_adm_find(cb: CallbackQuery, state: FSMContext) -> None:
    if not await adm_guard(cb):
        return
    await state.set_state(AdminStates.find)
    await show(cb, "🔎 ID, @юзернейм ё номи корбарро нависед:", kb([[btn("↩️ Бекор", "adm", style="primary")]]))


@router.message(AdminStates.find, F.text)
async def adm_find_msg(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    rows = await admintools.find(message.text)
    if not rows:
        return await message.answer("Ёфт нашуд. Дигар хел нависед ё «↩️ Бекор»-ро пахш кунед.")
    await state.clear()
    if len(rows) == 1:
        return await adm_card(message, rows[0]["id"])
    buttons = [[btn(user_label(r), f"adm_u:{r['id']}", style="primary")] for r in rows]
    buttons.append([btn("🛠 Панел", "adm", style="danger")])
    await message.answer(f"Ёфт шуд: {len(rows)} нафар", reply_markup=kb(buttons))


# ----- Паёми умумӣ -----
@router.callback_query(F.data == "adm_bc")
async def cb_adm_bc(cb: CallbackQuery, state: FSMContext) -> None:
    if not await adm_guard(cb):
        return
    c = await admintools.counts()
    await state.set_state(AdminStates.bc)
    total = c["total"] if c else 0
    await show(
        cb,
        f"📣 <b>Паёми умумӣ</b>\n\nПаёмро нависед (матн, расм ё видео). Он ба <b>{total}</b> корбар фиристода мешавад.",
        kb([[btn("↩️ Бекор", "adm", style="primary")]]),
    )


@router.message(AdminStates.bc)
async def adm_bc_msg(message: Message, state: FSMContext) -> None:
    if not is_admin(message.from_user.id):
        return
    await state.update_data(chat=message.chat.id, mid=message.message_id)
    await message.reply(
        "Ин паёмро ба ҳамаи корбарон мефиристед?",
        reply_markup=kb([[
            btn("✅ Фиристодан", "adm_bc_go", style="success"),
            btn("❌ Бекор", "adm", style="danger"),
        ]]),
    )


async def run_broadcast(bot: Bot, admin_id: int, from_chat: int, mid: int) -> None:
    ids = await admintools.all_ids()
    ok = blocked = failed = 0
    for uid in ids:
        for _ in range(2):
            try:
                await bot.copy_message(uid, from_chat, mid)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except TelegramForbiddenError:
                blocked += 1
                break
            except Exception:  # noqa: BLE001
                failed += 1
                break
        else:
            failed += 1
        await asyncio.sleep(0.05)
    try:
        await bot.send_message(
            admin_id, f"📣 Тамом шуд!\n✅ Расид: {ok}\n🚫 Бот баста кардаанд: {blocked}\n⚠️ Хато: {failed}"
        )
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data == "adm_bc_go")
async def cb_adm_bc_go(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not await adm_guard(cb):
        return
    data = await state.get_data()
    await state.clear()
    if not data.get("mid"):
        return await cb.answer("Паём ёфт нашуд", show_alert=True)
    try:
        await cb.message.edit_text("📣 Фиристодан оғоз шуд. Вақте тамом шавад, натиҷа меояд.")
    except TelegramBadRequest:
        pass
    asyncio.create_task(run_broadcast(bot, cb.from_user.id, data["chat"], data["mid"]))
    await cb.answer()


# ----- CSV -----
@router.callback_query(F.data == "adm_csv")
async def cb_adm_csv(cb: CallbackQuery, bot: Bot) -> None:
    if not await adm_guard(cb):
        return
    data = await admintools.export_csv()
    if not data:
        return await cb.answer("Ҷадвал ёфт нашуд", show_alert=True)
    await bot.send_document(cb.from_user.id, BufferedInputFile(data, filename="users.csv"), caption="📄 Рӯйхати корбарон")
    await cb.answer()


# ----- Backup / Барқарорсозӣ -----
async def send_backups(bot: Bot, chat_id: int, auto: bool = False) -> bool:
    c = await admintools.counts()
    if auto and (not c or c["total"] == 0):
        return False  # базаи холиро ҳамчун backup намефиристем
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    cap = (
        f"💾 Backup — {stamp}\n👥 {c['total']} корбар, ✅ {c['active']} обуна"
        if c else f"💾 Backup — {stamp}"
    )
    data = await admintools.snapshot(admintools.db_path())
    await bot.send_document(chat_id, BufferedInputFile(data, filename=f"bot_{stamp}.db"), caption=cap)
    if os.path.exists(extras.DB):
        edata = await admintools.snapshot(extras.DB)
        await bot.send_document(
            chat_id, BufferedInputFile(edata, filename=f"extras_{stamp}.db"), caption="💾 Танзимоти авточавоб"
        )
    return True


@router.callback_query(F.data == "adm_bk")
async def cb_adm_backup(cb: CallbackQuery, bot: Bot) -> None:
    if not await adm_guard(cb):
        return
    await cb.answer("Backup фиристода мешавад…")
    try:
        await send_backups(bot, cb.from_user.id)
    except Exception as e:  # noqa: BLE001
        log.warning("backup failed: %s", e)
        await bot.send_message(cb.from_user.id, f"⚠️ Backup нашуд: {html.escape(str(e))}")


@router.callback_query(F.data == "adm_rs")
async def cb_adm_restore(cb: CallbackQuery, state: FSMContext) -> None:
    if not await adm_guard(cb):
        return
    await state.set_state(AdminStates.restore)
    text = (
        "♻️ <b>Барқарорсозӣ</b>\n\n"
        "Файли backup-ро (<code>bot_….db</code>) ба ин ҷо фиристед ё ҷавоб диҳед (forward кунед).\n"
        "⚠️ Он маълумоти ҳозираро иваз мекунад.\n\n"
        "Ду файл бошад (<code>extras_….db</code> низ) — онро ҳам фиристед."
    )
    await show(cb, text, kb([[btn("✅ Тамом", "adm", style="success")], [btn("↩️ Бекор", "adm", style="danger")]]))


@router.message(AdminStates.restore, F.document)
async def adm_restore_doc(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return
    doc = message.document
    if doc.file_size and doc.file_size > 40_000_000:
        return await message.answer("⚠️ Файл хеле калон аст.")
    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        kind, n = await admintools.restore(buf.getvalue(), extras.DB)
        if kind == "танзимоти авточавоб":
            await extras.init()
    except Exception as e:  # noqa: BLE001
        return await message.answer(f"⚠️ Барқарор нашуд: {html.escape(str(e))}")
    await message.answer(
        f"✅ Барқарор шуд: {kind} — {n} сабт.\nАгар файли дигар ҳаст, фиристед; вагарна «Тамом»-ро пахш кунед.",
        reply_markup=kb([[btn("✅ Тамом", "adm", style="success")]]),
    )


@router.message(AdminStates.restore)
async def adm_restore_wrong(message: Message) -> None:
    await message.answer("📎 Файли .db-ро ҳамчун файл (document) фиристед.")


async def auto_backup(bot: Bot) -> None:
    """Ҳар 6 соат backup ба админҳо мефиристад (то маълумот ҳеҷ гоҳ гум нашавад)."""
    await asyncio.sleep(300)
    while True:
        for admin_id in config.ADMIN_IDS:
            try:
                await send_backups(bot, admin_id, auto=True)
            except Exception as e:  # noqa: BLE001
                log.warning("auto backup failed: %s", e)
        await asyncio.sleep(6 * 3600)


async def startup_check(bot: Bot) -> None:
    """Агар база хол бошад (масалан пас аз навсозӣ) — ба админ хабар медиҳад."""
    try:
        c = await admintools.counts()
        if c is not None and c["total"] == 0:
            await notify_admins(
                bot,
                "⚠️ Базаи маълумот ҳоло хол аст (0 корбар).\n"
                "Агар ин пас аз навсозӣ бошад, охирин backup-ро барқарор кунед:\n"
                "🛠 Панели Админ → ♻️ Барқарорсозӣ.",
            )
    except Exception as e:  # noqa: BLE001
        log.warning("startup check failed: %s", e)


# ====================== Админ-фармонҳо ======================
@router.message(Command("stats"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_stats(message: Message) -> None:
    s = await db.stats()
    await message.answer(
        f"📊 Корбарон: {s['users']}\n✅ Обунаи фаъол: {s['active']}\n💼 Business пайваст: {s['connections']}"
    )


@router.message(Command("grant"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_grant(message: Message, command: CommandObject, bot: Bot) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        return await message.answer("Истифода: /grant user_id days")
    uid, days = int(parts[0]), int(parts[1])
    if not await db.get_user(uid):
        return await message.answer("Корбар ёфт нашуд")
    until = await db.extend_sub(uid, days)
    await message.answer(f"✅ Обуна то {fmt_date(until)}")
    try:
        await bot.send_message(uid, f"🎁 Ба шумо обуна дода шуд — то <b>{fmt_date(until)}</b>.")
    except Exception:  # noqa: BLE001
        pass


# ====================== Business-режим ======================
@router.business_connection()
async def on_business_connection(connection: BusinessConnection, bot: Bot) -> None:
    await db.save_connection(connection.id, connection.user.id, connection.is_enabled)
    text = (
        f"🔌 Бот ба аккаунти шумо пайваст шуд! (@{config.BOT_USERNAME})\n"
        "/shrift1 … /shrift15 — шрифтро иваз кардан, /shrift0 — хомӯш кардан\n"
        "/hask — шӯхӣ"
        if connection.is_enabled
        else "ℹ️ Бот аз Telegram Business ҷудо шуд."
    )
    try:
        await bot.send_message(connection.user_chat_id, text, parse_mode=None)
    except Exception as e:  # noqa: BLE001
        log.warning("business notify failed: %s", e)


async def biz_edit(bot: Bot, message: Message, conn_id: str, text: str) -> bool:
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            business_connection_id=conn_id,
            parse_mode=None,
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("business edit failed: %s", e)
        return False


async def biz_owner(bot: Bot, conn_id: str) -> int | None:
    conn = await db.get_connection(conn_id)
    if conn is not None:
        return conn["user_id"]
    try:
        c = await bot.get_business_connection(conn_id)
    except Exception as e:  # noqa: BLE001
        log.warning("get_business_connection failed: %s", e)
        return None
    await db.save_connection(c.id, c.user.id, c.is_enabled)
    return c.user.id


async def handle_incoming(bot: Bot, message: Message, owner_id: int, conn_id: str) -> None:
    """Паёми шахси дигар ба соҳиби аккаунт: кэш (Удалит/Таҳрир) + авточавоб."""
    sender = message.from_user
    if not sender or sender.is_bot:
        return
    user = await db.get_user(owner_id)
    if not user or not has_access(owner_id, user):
        return
    ex = await extras.get(owner_id)

    if ex["notify_del"] or ex["notify_edit"]:
        await extras.cache_put(
            owner_id, message.chat.id, message.message_id, sender.full_name, message.text or ""
        )

    if not ex["ar_on"]:
        return
    if ex["away_text"] and extras.is_away_now(ex["away_slot"]):
        kind, body = "away", ex["away_text"]
    elif ex["ar_text"]:
        kind, body = "ar", ex["ar_text"]
    else:
        return
    if not await extras.claim_reply(owner_id, message.chat.id, ex["ar_period"], kind):
        return

    name = sender.first_name or sender.full_name or ""
    markup = kb([[btn(label, url=url, style="primary")] for label, url in ex["buttons"]]) if ex["buttons"] else None
    text = extras.render_autoreply(body, name, config.BOT_USERNAME)
    try:
        await bot.send_message(
            chat_id=message.chat.id, text=text, business_connection_id=conn_id, reply_markup=markup
        )
    except Exception as e:  # noqa: BLE001
        log.warning("autoreply failed, retry plain: %s", e)
        try:  # масалан эмодзии premium иҷозат набошад
            await bot.send_message(
                chat_id=message.chat.id,
                text=extras.strip_tags(text),
                business_connection_id=conn_id,
                reply_markup=markup,
            )
        except Exception as e2:  # noqa: BLE001
            log.warning("autoreply plain failed: %s", e2)


@router.business_message(F.text)
async def on_business_message(message: Message, bot: Bot) -> None:
    conn_id = message.business_connection_id
    owner_id = await biz_owner(bot, conn_id)
    if owner_id is None or not message.from_user:
        return

    # Паёми шахси дигар -> кэш + авточавоб
    if message.from_user.id != owner_id:
        await handle_incoming(bot, message, owner_id, conn_id)
        return

    # Аз ин ҷо: паёми худи соҳиби аккаунт
    user = await db.get_user(owner_id)
    if not user or not has_access(owner_id, user):
        return

    text = message.text
    low = text.strip().lower()

    # /shrift1 … /shrift15, /shrift0
    m = re.fullmatch(r"/shrift\s*(\d{1,2})", low)
    if m:
        fid = int(m.group(1))
        if fid != 0 and fid not in {f[0] for f in fonts.FONTS}:
            await biz_edit(bot, message, conn_id, f"⚠️ Чунин шрифт нест. Аз 1 то {len(fonts.FONTS)} интихоб кунед.")
            return
        await db.set_font(owner_id, fid)
        label = "хомӯш шуд" if fid == 0 else fonts.font_name(fid)
        await biz_edit(bot, message, conn_id, f"✅ Шрифт: {label}")
        return

    # /hask — шӯхии бе зарар
    if low == "/hask":
        frames = extras.hask_frames(config.BOT_USERNAME)
        for i, fr in enumerate(frames):
            await biz_edit(bot, message, conn_id, fr)
            if i < len(frames) - 1:
                await asyncio.sleep(1.4)
        return

    if text.startswith("/"):
        return
    if not user["font"] or not user["auto"]:
        return
    if any(e.type in SKIP_ENTITIES for e in (message.entities or [])):
        return

    new_text = fonts.apply_font(user["font"], text)
    if new_text == text:
        return

    # Навишти оҳиста: матн ҳарф ба ҳарф пайдо мешавад
    ex = await extras.get(owner_id)
    if ex["typing"]:
        for fr in extras.typing_frames(new_text):
            if not await biz_edit(bot, message, conn_id, fr):
                break
            await asyncio.sleep(0.45)
    await biz_edit(bot, message, conn_id, new_text)


def _short(s: str, n: int = 900) -> str:
    s = s or ""
    return html.escape(s if len(s) <= n else s[:n] + "…")


# Удалит: паёми нестшуда (бо ҳифзи эҳтиёт, агар aiogram кӯҳна бошад, бот намеафтад)
if hasattr(router, "deleted_business_messages"):

    @router.deleted_business_messages()
    async def on_business_deleted(event, bot: Bot) -> None:
        owner_id = await biz_owner(bot, event.business_connection_id)
        if owner_id is None:
            return
        ex = await extras.get(owner_id)
        if not ex["notify_del"]:
            return
        user = await db.get_user(owner_id)
        if not user or not has_access(owner_id, user):
            return
        for _mid, name, text in await extras.cache_pop(owner_id, event.chat.id, list(event.message_ids)):
            try:
                await bot.send_message(
                    owner_id,
                    f"🗑 <b>{html.escape(name)}</b> паёмро нест кард:\n<blockquote>{_short(text)}</blockquote>",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("deleted notify failed: %s", e)


# Таҳрир: паёми таҳриршуда
if hasattr(router, "edited_business_message"):

    @router.edited_business_message(F.text)
    async def on_business_edited(message: Message, bot: Bot) -> None:
        owner_id = await biz_owner(bot, message.business_connection_id)
        if owner_id is None or not message.from_user or message.from_user.id == owner_id:
            return
        ex = await extras.get(owner_id)
        if not ex["notify_edit"]:
            return
        user = await db.get_user(owner_id)
        if not user or not has_access(owner_id, user):
            return
        old = await extras.cache_get(owner_id, message.chat.id, message.message_id)
        await extras.cache_put(
            owner_id, message.chat.id, message.message_id, message.from_user.full_name, message.text
        )
        if not old or old[1] == message.text:
            return
        try:
            await bot.send_message(
                owner_id,
                f"✏️ <b>{html.escape(message.from_user.full_name)}</b> паёмро таҳрир кард:\n\n"
                f"Пеш:\n<blockquote>{_short(old[1])}</blockquote>\n"
                f"Ҳозир:\n<blockquote>{_short(message.text)}</blockquote>",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("edited notify failed: %s", e)


# ====================== Inline-режим ======================
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
    results = []
    for fid, name, _ in fonts.FONTS:
        styled = fonts.apply_font(fid, text)
        results.append(
            InlineQueryResultArticle(
                id=str(fid),
                title=f"{fid} · {name}",
                description=styled[:80],
                input_message_content=InputTextMessageContent(message_text=styled, parse_mode=None),
            )
        )
    await query.answer(results, cache_time=1, is_personal=True)


# ====================== Шрифт дар гурӯҳҳо ======================
GROUP_CMD = re.compile(r"^/(?:f|shrift)(?:@\w+)?\s+(.+)$", re.I | re.S)


@router.message(F.text, F.chat.type.in_({"group", "supergroup"}))
async def group_font(message: Message) -> None:
    """Дар гурӯҳ: /f матн — бот матнро бо шрифти шумо мефиристад."""
    m = GROUP_CMD.match(message.text.strip())
    if not m or not message.from_user:
        return
    uid = message.from_user.id
    user = await db.get_user(uid)
    if not user:
        return await message.reply(f"Аввал ботро оғоз кунед: @{config.BOT_USERNAME}")
    if not has_access(uid, user):
        return await message.reply("🔒 Ин имконият барои обунахандагон аст.")
    ex = await extras.get(uid)
    if not ex["groups"]:
        return await message.reply("Шрифт дар гурӯҳҳо дар Танзимот хомӯш аст.")
    if not user["font"]:
        return await message.reply(f"Аввал шрифтро интихоб кунед: @{config.BOT_USERNAME} → «🔤 Шрифтҳо»")
    styled = fonts.apply_font(user["font"], m.group(1))
    await message.answer(f"👤 {message.from_user.full_name}:\n{styled}", parse_mode=None)
    try:  # агар бот иҷозати нест кардан дошта бошад, фармони аслиро нест мекунад
        await message.delete()
    except Exception:  # noqa: BLE001
        pass


# ====================== Табдил дар худи бот ======================
@router.message(F.text, F.chat.type == "private", StateFilter(None))
async def convert_private(message: Message) -> None:
    if message.text.startswith("/"):
        return
    user = await db.get_user(message.from_user.id)
    if not user or not user["font"]:
        return await message.answer("Аввал шрифтро интихоб кунед: /start → «🔤 Шрифтҳо»")
    await message.answer(fonts.apply_font(user["font"], message.text), parse_mode=None)


# ====================== Оғоз ======================
def make_session() -> AiohttpSession:
    """Сессия бо IPv4 ва пайвастагии нав барои ҳар дархост (ҳал барои Request timeout дар Railway)."""
    session = AiohttpSession(timeout=60)
    try:
        session._connector_init["family"] = socket.AF_INET
        session._connector_init["force_close"] = True
    except Exception as e:  # noqa: BLE001
        log.warning("session tune failed: %s", e)
    return session


async def main() -> None:
    await db.init()
    await extras.init()
    bot = Bot(
        token=config.BOT_TOKEN,
        session=make_session(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Агар Telegram дар оғоз ҷавоб надиҳад, бот намеафтад, балки такрор мекунад
    while True:
        try:
            me = await bot.get_me()
            break
        except Exception as e:  # noqa: BLE001
            log.warning("get_me failed, retry in 5s: %s", e)
            await asyncio.sleep(5)

    config.BOT_USERNAME = me.username
    log.info("Started as @%s", me.username)
    asyncio.create_task(auto_backup(bot))
    asyncio.create_task(startup_check(bot))

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dp.start_polling(
                bot,
                allowed_updates=dp.resolve_used_update_types(),
                polling_timeout=20,
            )
            break
        except Exception as e:  # noqa: BLE001
            log.warning("polling crashed, restart in 5s: %s", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
