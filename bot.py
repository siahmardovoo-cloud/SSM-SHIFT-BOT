import asyncio
import html
import logging
import time
from datetime import datetime
from urllib.parse import quote

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
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
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import fonts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fontbot")

router = Router()

# Entity-ҳое, ки бо иваз кардани шрифт вайрон мешаванд — чунин паёмҳоро дастнорас мегузорем
SKIP_ENTITIES = {
    "url", "text_link", "mention", "text_mention", "email", "phone_number",
    "bot_command", "custom_emoji", "code", "pre",
}


class PayStates(StatesGroup):
    proof = State()


# ====================== Ёрдамчиҳо ======================
def btn(text: str, data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=data)


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


BACK = btn("⬅️ Баргаштан", "menu")


def fmt_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def is_active(user) -> bool:
    return bool(user) and (user["sub_until"] or 0) > time.time()


def menu_kb() -> InlineKeyboardMarkup:
    support = (
        btn("📞 Дастгирӣ", url=f"https://t.me/{config.SUPPORT_USERNAME.lstrip('@')}")
        if config.SUPPORT_USERNAME
        else btn("📞 Дастгирӣ", "no_support")
    )
    lessons = (
        btn("🎓 Дарсҳо", url=config.LESSONS_URL)
        if config.LESSONS_URL
        else btn("🎓 Дарсҳо", "no_lessons")
    )
    return kb([
        [btn("🔤 Шрифтҳо", "fonts"), btn("⚙️ Танзимот", "settings")],
        [btn("👤 Кабинет", "cabinet"), btn("📋 Маълумот", "info")],
        [btn("💳 Пардохт", "pay"), btn("👥 Реферал", "ref")],
        [btn("🔌 Пайваст кардан", "connect"), support],
        [lessons],
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
    rows = [[btn(f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}")] for ch in missing]
    rows.append([btn("✅ Санҷиш", "check_sub")])
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
            await message.answer_video(config.WELCOME_VIDEO, caption=text, reply_markup=menu_kb())
            return
        except Exception as e:  # noqa: BLE001
            log.warning("welcome video failed: %s", e)
    await message.answer(text, reply_markup=menu_kb())


@router.callback_query(F.data.in_({"menu", "check_sub"}))
async def cb_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, welcome_text(cb.from_user.full_name), menu_kb())


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
    b = InlineKeyboardBuilder()
    for fid, _, _ in fonts.FONTS:
        b.button(text=f"✅ {fid}" if fid == current else str(fid), callback_data=f"font:{fid}")
    b.adjust(4, 4, 4, 3)
    b.row(btn("🚫 Хомӯш кардан", "font:0"))
    b.row(BACK)
    return b.as_markup()


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
@router.callback_query(F.data.in_({"settings", "toggle_auto"}))
async def cb_settings(cb: CallbackQuery) -> None:
    if cb.data == "toggle_auto":
        await db.toggle_auto(cb.from_user.id)
    user = await db.get_user(cb.from_user.id)
    text = (
        "⚙️ <b>Танзимот</b>\n\n"
        f"🔄 Табдили худкор дар Business: {'✅ фаъол' if user['auto'] else '❌ хомӯш'}\n"
        f"🔤 Шрифти ҳозира: <b>{fonts.font_name(user['font'])}</b>"
    )
    markup = kb([
        [btn("🔄 Фаъол / хомӯш кардан", "toggle_auto")],
        [btn("🔤 Шрифтҳо", "fonts")],
        [BACK],
    ])
    await show(cb, text, markup)


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
    await show(cb, text, kb([[btn("💳 Пардохт", "pay"), btn("⚙️ Танзимот", "settings")], [BACK]]))


@router.callback_query(F.data == "info")
async def cb_info(cb: CallbackQuery) -> None:
    text = (
        "📋 <b>Бот чӣ кор карда метавонад?</b>\n\n"
        "• Матни шуморо ба 15 шрифти зебо табдил медиҳад.\n"
        "• Режими inline: дар ҳар чат <code>@{bot} матн</code> нависед ва шрифтро интихоб кунед.\n"
        "• Telegram Business: паёмҳои шумо дар чатҳо худкор бо шрифти интихобшуда иваз мешаванд "
        "(бо обуна).\n"
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
        "ℹ️ Паёмҳои дорои линк, @ва кодро бот дастнорас мегузорад."
    )
    await show(cb, text, kb([[btn("💳 Пардохт", "pay")], [BACK]]))


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
    await show(cb, text, kb([[btn("📤 Фиристодан ба дӯст", url=share)], [BACK]]))
