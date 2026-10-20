import os
import re
import json
import time
import html
import asyncio
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# UAV ALERT — гражданский информационный бот.
# Мониторятся только формулировки предупреждений/отбоя для Костромской области.
# Сведения о маршрутах, координатах и местах обнаружения БПЛА не извлекаются.
# Сообщения сторонних Telegram-каналов — дополнительный сигнал, не официальное подтверждение.

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma")
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL", "60")))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
DATA_DIR = os.getenv("DATA_DIR", ".")
MSK = timezone(timedelta(hours=3))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCES = [
    ("radar_rschs (проверка публикаций)", "https://t.me/s/radar_rschs", True),
    ("locatorru (дополнительный источник)", "https://t.me/s/locatorru", False),
    ("bplarussiaru (дополнительный источник)", "https://t.me/s/bplarussiaru", False),
]
OFFICIAL_SOURCES = [
    ("МЧС по Костромской области", "https://44.mchs.gov.ru/"),
    ("Администрация Костромской области", "https://adm44.ru/"),
]

# Территориальный фильтр по названиям региона, городов и муниципальных районов/округов.
TERRITORY_TERMS = [
    "костром", "нерехт", "бую", "буй", "волгоречен", "галич", "шарь", "манту",
    "чухлом", "макарьев", "солигалич", "кологрив", "нея", "антропов",
    "боговаров", "кадый", "красное-на-волге", "красносель", "межев",
    "октябрьск", "островск", "павин", "парфеньев", "понaзырев", "поназырев",
    "пышуг", "сусанин", "судислав", "вохом", "варнав", "ветлуг"
]
REGION_RE = re.compile(r"костром\w*\s+(област|регион)", re.I)
ALERT_RE = re.compile(
    r"беспилотн\w*\s+опасност|опасност\w*\s+по\s+бпла|"
    r"угроз\w*\s+беспилот|угроз\w*\s+бпла|"
    r"внимани\w*.{0,30}бпла|бпла.{0,40}(?:внимани|угроз|опасност)|"
    r"опасност\w*.{0,30}(?:беспилот|бпла)|"
    r"беспилотн\w*.{0,30}(?:угроз|опасност|внимани)",
    re.I | re.S
)
CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}(?:беспилотн|бпла|опасност|угроз)|"
    r"(?:беспилотн|бпла|опасност|угроз).{0,70}(?:отбой|отмен[аы]|снят[аоы]?)",
    re.I | re.S
)
DANGER_RE = re.compile(r"(?:опасност\w*\s+по\s+бпла|беспилотн\w*\s+опасност|красн\w*\s+уров)", re.I)
THREAT_RE = re.compile(r"(?:угроз\w*\s+(?:беспилот|бпла)|бпла.{0,30}угроз)", re.I | re.S)
ATTENTION_RE = re.compile(r"(?:внимани\w*.{0,30}бпла|бпла.{0,30}внимани)", re.I | re.S)

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "uav_alert_state.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "uav_alert_subscribers.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("uav-alert")

def now_msk():
    return datetime.now(MSK)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default

def save_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

state = load_json(STATE_FILE, {
    "seen": {}, "subscribers": [], "last_scan": None, "last_errors": {},
    "last_event": None, "initialized": {}
})
if not isinstance(state, dict):
    state = {}
state.setdefault("seen", {})
state.setdefault("subscribers", [])
state.setdefault("last_errors", {})
state.setdefault("initialized", {})
subscribers = set(load_json(SUBSCRIBERS_FILE, state.get("subscribers", [])))
monitor_task = None

def save_state():
    state["subscribers"] = sorted(subscribers)
    save_json(STATE_FILE, state)
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))

def normalize(text):
    return re.sub(r"\s+", " ", (text or "").casefold().replace("ё", "е")).strip()

def mentions_kostroma(text):
    value = normalize(text)
    if REGION_RE.search(value):
        return True
    return any(term in value for term in TERRITORY_TERMS)

def classify(text):
    value = normalize(text)
    # Отбой проверяется первым, чтобы сообщение с цитатой не стало активной тревогой.
    if CANCEL_RE.search(value) or "отбой беспилотной опасности" in value:
        return "cancel", "🟢 ОТБОЙ БЕСПИЛОТНОЙ ОПАСНОСТИ"
    if not ALERT_RE.search(value):
        return None
    if DANGER_RE.search(value):
        return "danger", "🔴 ОПАСНОСТЬ ПО БПЛА"
    if THREAT_RE.search(value):
        return "threat", "🟠 УГРОЗА ПО БПЛА"
    if ATTENTION_RE.search(value):
        return "attention", "🟡 ВНИМАНИЕ: БПЛА"
    # Общее явное сообщение о беспилотной опасности — трактуем как опасность,
    # но источник и время показываем для самостоятельной проверки.
    if "беспилотной опасности" in value:
        return "danger", "🔴 ОПАСНОСТЬ ПО БПЛА"
    return None

def source_posts(source_url):
    response = requests.get(
        source_url, timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0 (compatible; UAV-ALERT-civil-info/1.0)"}
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts = []
    for node in soup.select(".tgme_widget_message"):
        text_node = node.select_one(".tgme_widget_message_text")
        if not text_node:
            continue
        text = text_node.get_text(" ", strip=True)
        link_node = node.select_one("a.tgme_widget_message_date")
        url = link_node.get("href", "") if link_node else source_url
        post_id = node.get("data-post", "") or url
        if not text:
            continue
        digest = hashlib.sha256((post_id + "|" + text).encode("utf-8")).hexdigest()
        posts.append({"key": digest, "text": text[:5000], "url": url})
    return posts[-30:]

def event_message(kind, label, source, url, published_text):
    stamp = now_msk().strftime("%d.%m.%Y %H:%M МСК")
    source_safe = html.escape(source)
    url_safe = html.escape(url, quote=True)
    excerpt = html.escape(re.sub(r"\s+", " ", published_text)[:650])
    trust_note = (
        "Источник не является официальным подтверждением. Сверьте сообщение "
        "с актуальными оповещениями МЧС и органов власти."
        if "дополнительный источник" in source.lower() or "radar_rschs" in source.lower()
        else "Информационное сообщение; следуйте указаниям уполномоченных служб."
    )
    return (
        "<b>🛰 UAV ALERT — Костромская область</b>\n\n"
        f"<b>{label}</b>\n"
        f"Время получения: {stamp}\n"
        f"Источник: <a href=\"{url_safe}\">{source_safe}</a>\n\n"
        f"<i>Фрагмент публикации:</i> {excerpt}\n\n"
        f"<i>{html.escape(trust_note)}</i>"
    )

async def send_event(application, kind, label, source, url, text):
    message = event_message(kind, label, source, url, text)
    recipients = set(subscribers)
    recipients.add(ADMIN_ID)
    for chat_id in sorted(recipients):
        try:
            await application.bot.send_message(
                chat_id=chat_id, text=message, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except TelegramError:
            logger.exception("Не удалось отправить уведомление chat_id=%s", chat_id)

async def monitor_source(application, name, url, is_review_source):
    posts = await asyncio.to_thread(source_posts, url)
    seen_map = state.setdefault("seen", {})
    source_seen = set(seen_map.get(url, []))
    initialized = bool(state.setdefault("initialized", {}).get(url, False))
    if not initialized:
        # При первом запуске только запоминаем старые публикации, чтобы не рассылать архив.
        seen_map[url] = [p["key"] for p in posts][-300:]
        state["initialized"][url] = True
        state["last_errors"].pop(url, None)
        return
    for post in posts:
        if post["key"] in source_seen:
            continue
        source_seen.add(post["key"])
        text = post["text"]
        # Только предупреждения/отбой, относящиеся к Костромской области.
        if not mentions_kostroma(text):
            continue
        result = classify(text)
        if not result:
            continue
        kind, label = result
        # Сообщения сторонних каналов отправляются как требующие проверки,
        # но не изменяют внутренний "официальный" статус и не публикуются в канал.
        await send_event(application, kind, label, name, post["url"], text)
    seen_map[url] = list(source_seen)[-300:]
    state["last_errors"].pop(url, None)

async def monitor_loop(application):
    logger.info("Круглосуточный мониторинг запущен; интервал %s сек.", CHECK_INTERVAL)
    while True:
        for name, url, review in SOURCES:
            try:
                await monitor_source(application, name, url, review)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state.setdefault("last_errors", {})[url] = str(exc)[:250]
                logger.warning("Источник %s недоступен: %s", name, exc)
        state["last_scan"] = now_msk().isoformat(timespec="seconds")
        save_state()
        await asyncio.sleep(CHECK_INTERVAL)

def keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"),
         InlineKeyboardButton("🔕 Отписаться", callback_data="unsubscribe")],
        [InlineKeyboardButton("📡 Источники", callback_data="sources"),
         InlineKeyboardButton("ℹ️ Статус", callback_data="status")],
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            "🛰 UAV ALERT — информационный сервис Костромской области.\n\n"
            "Бот проверяет публичные сообщения выбранных источников круглосуточно, "
            "если процесс запущен. Сообщения сторонних каналов требуют сверки "
            "с официальными оповещениями. Бот не является официальной системой 112.\n\n"
            "Нажми «Подписаться», чтобы получать уведомления.",
            reply_markup=keyboard()
        )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    subscribers.add(user.id)
    save_state()
    await update.effective_message.reply_text("🔔 Подписка включена.", reply_markup=keyboard())

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    subscribers.discard(user.id)
    save_state()
    await update.effective_message.reply_text("🔕 Подписка отключена.", reply_markup=keyboard())

def sources_text():
    lines = ["📡 Источники мониторинга", ""]
    lines.extend(f"• {name}: {url.replace('/s/', '/')}" for name, url, _ in SOURCES)
    lines.extend([
        "", "Официальные ресурсы для сверки:",
        *[f"• {name}: {url}" for name, url in OFFICIAL_SOURCES],
        "", "При первом запуске бот запоминает старые посты и не рассылает архив.",
        "Сообщения из Telegram-каналов не подтверждают официальный статус тревоги."
    ])
    return "\n".join(lines)

def status_text():
    errors = state.get("last_errors", {})
    lines = [
        "🩺 UAV ALERT — статус",
        f"Последний цикл: {state.get('last_scan') or 'ещё не завершён'}",
        f"Интервал проверки: {CHECK_INTERVAL} сек.",
        f"Подписчиков: {len(subscribers)}",
        f"Источников: {len(SOURCES)}",
        ""
    ]
    for name, url, _ in SOURCES:
        lines.append(f"• {name}: {'ошибка — ' + errors[url] if url in errors else 'без последней ошибки'}")
    return "\n".join(lines)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(status_text())

async def history_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(sources_text(), disable_web_page_preview=True)

async def admin_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.effective_message.reply_text("⛔ Команда только для администратора.")
        return
    await update.effective_message.reply_text(status_text())

async def incoming_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # SMS пересылается боту вручную или через отдельную интеграцию.
    # Обычные SMS телефонной сети бот напрямую не получает.
    message = update.effective_message
    user = update.effective_user
    if not message or not user or user.id != ADMIN_ID or not message.text:
        return
    text = message.text.strip()
    normalized = normalize(text)
    result = classify(text)
    if not result or not mentions_kostroma(text):
        return
    kind, label = result
    await message.reply_text(
        "📩 Распознан текст, похожий на предупреждение/отбой.\n"
        "Это не подтверждает подлинность отправителя и актуальность сообщения. "
        "Проверь официальный первоисточник перед публикацией."
    )
    await send_event(context.application, kind, label, "SMS/текст, переданный администратором",
                     "https://44.mchs.gov.ru/", text)

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "subscribe":
        subscribers.add(query.from_user.id)
        save_state()
        text = "🔔 Подписка включена."
    elif query.data == "unsubscribe":
        subscribers.discard(query.from_user.id)
        save_state()
        text = "🔕 Подписка отключена."
    elif query.data == "sources":
        text = sources_text()
    else:
        text = status_text()
    try:
        await query.edit_message_text(text, reply_markup=keyboard(), disable_web_page_preview=True)
    except BadRequest:
        await query.message.reply_text(text, reply_markup=keyboard(), disable_web_page_preview=True)

async def post_init(application: Application):
    global monitor_task
    await application.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Статус мониторинга"),
        BotCommand("sources", "Список источников"),
        BotCommand("health", "Диагностика администратора"),
    ])
    monitor_task = asyncio.create_task(monitor_loop(application))

async def post_shutdown(application: Application):
    global monitor_task
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

app = Flask(__name__)

@app.get("/")
def index():
    return jsonify({"service": "UAV ALERT", "status": "running", "last_scan": state.get("last_scan")})

@app.get("/status")
def health():
    return jsonify({"status": "ok", "last_scan": state.get("last_scan"),
                    "sources": len(SOURCES), "errors": state.get("last_errors", {})})

def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в переменных окружения хостинга.")
    threading.Thread(target=run_flask, daemon=True, name="uav-alert-health").start()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("sources", history_sources))
    application.add_handler(CommandHandler("health", admin_health))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_sms))
    application.add_handler(CallbackQueryHandler(callback))
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
