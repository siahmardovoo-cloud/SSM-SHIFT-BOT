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
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import config
import db
import fontui
import fonts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fontbot")

router = Router()
fontui.register(router)

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
        f"• Матни шуморо ба {len(fonts.FONTS)} шрифти зебо табдил медиҳад.\n"
        f"• Режими inline: дар ҳар чат <code>@{config.BOT_USERNAME} матн</code> нависед ва шрифтро интихоб кунед.\n"
        "• Telegram Business: паёмҳои шумо дар чатҳо худкор бо шрифти интихобшуда иваз мешаванд "
        "(бо обуна).\n"
        f"• Системаи реферал: {config.REF_TARGET} дӯст = 1 моҳи ройгон."
    )
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
        [btn(f"⭐ Старс ({config.STARS_PRICE} ⭐)", "pay_stars"), btn(f"🏦 {config.CARD_BANK}", "pay_card")],
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
        f"✅ Пардохт қабул шуд! Обуна то <b>{fmt_date(until)}</b> фаъол аст.", reply_markup=menu_kb()
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
    markup = kb([[btn("✅ Тасдиқ", f"appr:{pid}"), btn("❌ Рад", f"rej:{pid}")]])
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
        "✅ Бот ба Telegram Business пайваст шуд!"
        if connection.is_enabled
        else "ℹ️ Бот аз Telegram Business ҷудо шуд."
    )
    try:
        await bot.send_message(connection.user_chat_id, text)
    except Exception as e:  # noqa: BLE001
        log.warning("business notify failed: %s", e)


@router.business_message(F.text)
async def on_business_message(message: Message, bot: Bot) -> None:
    conn_id = message.business_connection_id
    conn = await db.get_connection(conn_id)
    if conn is None:
        try:
            c = await bot.get_business_connection(conn_id)
        except Exception as e:  # noqa: BLE001
            log.warning("get_business_connection failed: %s", e)
            return
        await db.save_connection(c.id, c.user.id, c.is_enabled)
        owner_id = c.user.id
    else:
        owner_id = conn["user_id"]

    # Танҳо паёмҳои худи соҳиби аккаунт иваз мешаванд
    if not message.from_user or message.from_user.id != owner_id:
        return

    user = await db.get_user(owner_id)
    if not user or not user["font"] or not user["auto"] or not is_active(user):
        return

    text = message.text
    if text.startswith("/"):
        return
    if any(e.type in SKIP_ENTITIES for e in (message.entities or [])):
        return

    new_text = fonts.apply_font(user["font"], text)
    if new_text == text:
        return
    try:
        await bot.edit_message_text(
            text=new_text,
            chat_id=message.chat.id,
            message_id=message.message_id,
            business_connection_id=conn_id,
            parse_mode=None,
        )
    except TelegramBadRequest as e:
        log.warning("business edit failed: %s", e)


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
async def main() -> None:
    await db.init()
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    config.BOT_USERNAME = me.username
    log.info("Started as @%s", me.username)

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
