import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # на Railway .env лозим нест
    pass


def _ints(value: str) -> list[int]:
    return [int(x) for x in value.replace(" ", "").split(",") if x.isdigit()]


def _strs(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN муайян нашудааст (Variables дар Railway ё файли .env)")

# Админҳо: ID-и рақамии Telegram, бо вергул ҷудо карда шавад
ADMIN_IDS = _ints(os.getenv("ADMIN_IDS", ""))

# Базаи маълумот. Дар Railway Volume-ро ба /data васл кунед ва DB_PATH=/data/bot.db гузоред
DB_PATH = os.getenv("DB_PATH", "bot.db")

# Каналҳои спонсор (ихтиёрӣ): "@kanal1,@kanal2". Бот бояд дар онҳо админ бошад
SPONSOR_CHANNELS = _strs(os.getenv("SPONSOR_CHANNELS", ""))

# Нарх ва муддат
PRICE_SOMONI = int(os.getenv("PRICE_SOMONI", "5"))
STARS_PRICE = int(os.getenv("STARS_PRICE", "40"))
SUB_DAYS = int(os.getenv("SUB_DAYS", "30"))

# Пардохт бо корт
CARD_BANK = os.getenv("CARD_BANK", "Душанбе Сити")
CARD_NUMBER = os.getenv("CARD_NUMBER", "")
CARD_HOLDER = os.getenv("CARD_HOLDER", "")

# Реферал: чанд хол барои 1 моҳи ройгон
REF_TARGET = int(os.getenv("REF_TARGET", "5"))

# Пайвандҳо
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "")  # масалан: your_username
LESSONS_URL = os.getenv("LESSONS_URL", "")  # пайванд ба канал ё видео-дарс
WELCOME_VIDEO = os.getenv("WELCOME_VIDEO", "")  # file_id ё URL-и видео (ихтиёрӣ)

# Дар main() пур мешавад
BOT_USERNAME = ""
