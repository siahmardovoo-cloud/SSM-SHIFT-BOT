# Боти шрифтҳои зебо (Telegram)

Бот матнро ба 15 шрифт табдил медиҳад: дар худи бот, бо inline (`@bot матн`) ва бо Telegram Business (худкор, бо обуна).
Обуна: Telegram Stars ё корт (бо тасдиқи админ). Реферал: 5 хол = 1 моҳ.

## 1. Бот дар @BotFather
1. `/newbot` — токенро гиред.
2. `/setinline` — inline-ро фаъол кунед.
3. **Bot Settings → Business Mode** — фаъол кунед.

## 2. Railway
1. **New Project → Deploy from GitHub repo** — репозиторияро интихоб кунед.
2. **Variables**: `BOT_TOKEN`, `ADMIN_IDS`, `CARD_NUMBER`, `CARD_HOLDER` ва дигарҳоро аз `.env.example` илова кунед.
3. **Volume** илова кунед, Mount path: `/data`, ва `DB_PATH=/data/bot.db` гузоред
   (бе Volume базаи маълумот ҳангоми ҳар deploy нест мешавад).
4. Deploy. Дар Logs бояд `Started as @...` пайдо шавад.

## Фармонҳои админ
- `/stats` — омор
- `/grant USER_ID РӮЗ` — обуна додан

## Эзоҳ
- Шрифтҳо дар `fonts.py` ҳастанд.
- Каналҳои спонсор: `SPONSOR_CHANNELS=@kanal1,@kanal2`, бот дар онҳо админ бошад.
- Бот танҳо паёмҳои матнии соҳиби Business-аккаунтро иваз мекунад.
