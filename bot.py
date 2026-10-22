import os
import re
import json
import asyncio
import hashlib
import logging
import threading
import html
from datetime import datetime, timezone, timedelta

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
# Публикации сторонних источников требуют проверки по официальным оповещениям.

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma")  # резерв для будущей публикации в канал
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL", "60")))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
DATA_DIR = os.getenv("DATA_DIR", ".")
MSK = timezone(timedelta(hours=3))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DANGER_IMAGE = os.path.join(BASE_DIR, "danger.jpg")
ALL_CLEAR_IMAGE = os.path.join(BASE_DIR, "all_clear.jpg")

SOURCES = [
    ("radar_rschs (проверка публикаций)", "https://t.me/s/radar_rschs", True),
    ("locatorru (дополнительный источник)", "https://t.me/s/locatorru", False),
    ("bplarussiaru (дополнительный источник)", "https://t.me/s/bplarussiaru", False),
]
OFFICIAL_SOURCES = [
    ("МЧС по Костромской области", "https://44.mchs.gov.ru/"),
    ("Администрация Костромской области", "https://adm44.ru/"),
]

TERRITORY_TERMS = [
    "костром", "нерехт", "бую", "буй", "волгоречен", "галич", "шарь",
    "манту", "чухлом", "макарьев", "солигалич", "кологрив", "нея",
    "антропов", "боговаров", "кадый", "красное-на-волге", "красносель",
    "межев", "октябрьск", "островск", "павин", "парфеньев", "поназырев",
    "пышуг", "сусанин", "судислав", "вохом", "варнав", "ветлуг"
]
REGION_RE = re.compile(r"костром\w*\s+(област|регион)", re.I)

ALERT_RE = re.compile(
    r"беспилотн\w*\s+опасност|опасност\w*\s+по\s+бпла|"
    r"угроз\w*\s+беспилот|угроз\w*\s+бпла|"
    r"внимани\w*.{0,30}бпла|тревог\w*.{0,30}(?:бпла|беспилот)|"
    r"бпла.{0,40}(?:внимани|угроз|опасност|тревог)|"
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
DANGER_RE = re.compile(
    r"(?:опасност\w*\s+по\s+бпла|беспилотн\w*\s+опасност|красн\w*\s+уров)",
    re.I
)
THREAT_RE = re.compile(r"(?:угроз\w*\s+(?:беспилот|бпла)|бпла.{0,30}угроз)", re.I | re.S)
ATTENTION_RE = re.compile(r"(?:внимани\w*.{0,30}бпла|бпла.{0,30}внимани)", re.I | re.S)

ROCKET_RE = re.compile(
    r"ракетн\w*.{0,50}(?:опасност|угроз|тревог|объявлен)|"
    r"(?:опасност|угроз|тревог).{0,50}ракетн\w*",
    re.I | re.S
)
ROCKET_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}ракетн\w*|"
    r"ракетн\w*.{0,70}"
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)",
    re.I | re.S
)
ROCKET_NEGATION_RE = re.compile(
    r"ракетн\w*.{0,50}(?:не\s+(?:объявлен\w*|введен\w*|подтвержден\w*)|"
    r"отсутствует|нет)|"
    r"(?:нет|не\s+(?:объявлен\w*|введен\w*|подтвержден\w*)).{0,50}ракетн\w*",
    re.I | re.S
)

TERRITORY_ALIASES = {
    "г. Кострома": ["город кострома", "г. кострома", "кострома"],
    "г. Буй": ["город буй", "г. буй"],
    "г. Волгореченск": ["город волгореченск", "г. волгореченск", "волгореченск"],
    "г. Галич": ["город галич", "г. галич"],
    "г. Кологрив": ["город кологрив", "г. кологрив"],
    "г. Макарьев": ["город макарьев", "г. макарьев"],
    "г. Мантурово": ["город мантурово", "г. мантурово", "мантурово"],
    "г. Нея": ["город нея", "г. нея"],
    "г. Нерехта": ["город нерехта", "г. нерехта", "нерехта"],
    "г. Солигалич": ["город солигалич", "г. солигалич"],
    "г. Шарья": ["город шарья", "г. шарья", "шарья"],
    "Антроповский округ": ["антроповский район", "антроповский округ"],
    "Боговаровский округ": ["боговаровский район", "боговаровский округ", "боговаров"],
    "Варнавинский округ": ["варнавинский район", "варнавинский округ", "варнав"],
    "Вохомский округ": ["вохомский район", "вохомский округ"],
    "Галичский район": ["галичский район", "галичский округ"],
    "Кадыйский округ": ["кадыйский район", "кадыйский округ"],
    "Кологривский округ": ["кологривский район", "кологривский округ"],
    "Костромской район": ["костромской район", "костромской округ"],
    "Красносельский район": ["красносельский район", "красносельский округ"],
    "Макарьевский округ": ["макарьевский район", "макарьевский округ"],
    "Мантуровский округ": ["мантуровский район", "мантуровский округ"],
    "Межевской округ": ["межевской район", "межевской округ"],
    "Нейский округ": ["нейский район", "нейский округ"],
    "Нерехтский район": ["нерехтский район", "нерехтский округ"],
    "Октябрьский округ": ["октябрьский район", "октябрьский округ"],
    "Островский район": ["островский район", "островский округ"],
    "Павинский округ": ["павинский район", "павинский округ"],
    "Парфеньевский округ": ["парфеньевский район", "парфеньевский округ"],
    "Поназыревский округ": ["поназыревский район", "поназыревский округ"],
    "Пыщугский округ": ["пыщугский район", "пыщугский округ"],
    "Солигаличский район": ["солигаличский район", "солигаличский округ"],
    "Судиславский округ": ["судиславский район", "судиславский округ"],
    "Сусанинский район": ["сусанинский район", "сусанинский округ"],
    "Чухломский район": ["чухломский район", "чухломский округ"],
    "Шарьинский район": ["шарьинский район", "шарьинский округ"],
}

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
KOSTROMA_LAT = 57.7679
KOSTROMA_LON = 40.9269

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
for key, default in {
    "seen": {}, "subscribers": [], "last_errors": {}, "initialized": {}
}.items():
    state.setdefault(key, default)
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

def extract_territories(text):
    value = normalize(text)
    if re.search(
        r"(?:на\s+)?(?:всей\s+)?территории\s+костромской\s+области|"
        r"по\s+всей\s+костромской\s+области|по\s+костромской\s+области|"
        r"во\s+всей\s+костромской\s+области", value
    ):
        return ["Костромская область"]

    found = []
    entries = sorted(
        TERRITORY_ALIASES.items(),
        key=lambda item: max((len(x) for x in item[1]), default=0),
        reverse=True
    )
    for display_name, aliases in entries:
        for alias in aliases:
            alias_norm = normalize(alias)
            pattern = r"(?<!\w)" + re.escape(alias_norm) + r"(?!\w)"
            if re.search(pattern, value) and display_name not in found:
                found.append(display_name)
                break
    if found:
        return found
    if mentions_kostroma(text):
        return ["Костромская область"]
    return []

def classify_rocket(text):
    value = normalize(text)
    # Отбой проверяется раньше объявления опасности.
    if ROCKET_CANCEL_RE.search(value):
        return "rocket_cancel", "Отбой ракетной опасности"
    if ROCKET_NEGATION_RE.search(value):
        return None
    if ROCKET_RE.search(value):
        return "rocket_danger", "Ракетная опасность"
    return None

def classify_uav(text):
    value = normalize(text)
    if CANCEL_RE.search(value) or "отбой беспилотной опасности" in value:
        return "cancel", "Отбой опасности по БПЛА"
    if not ALERT_RE.search(value):
        return None
    if DANGER_RE.search(value) or "беспилотной опасности" in value:
        return "danger", "Опасность по БПЛА"
    if THREAT_RE.search(value):
        return "threat", "Угроза по БПЛА"
    if ATTENTION_RE.search(value):
        return "attention", "Внимание: БПЛА"
    return None

def classify(text):
    # Совместимость с существующими вызовами.
    return classify_rocket(text) or classify_uav(text)

def weather_code_text(code):
    mapping = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
        3: "пасмурно", 45: "туман", 48: "изморозевый туман",
        51: "слабая морось", 53: "морось", 55: "сильная морось",
        56: "слабая ледяная морось", 57: "сильная ледяная морось",
        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
        66: "слабый ледяной дождь", 67: "сильный ледяной дождь",
        71: "небольшой снег", 73: "снег", 75: "сильный снег",
        77: "снежные зёрна", 80: "ливневый дождь", 81: "ливень",
        82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
        95: "гроза", 96: "гроза с небольшим градом", 99: "гроза с градом"
    }
    return mapping.get(code, "условия не определены")

async def get_weather_text():
    params = {
        "latitude": KOSTROMA_LAT, "longitude": KOSTROMA_LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": 2, "timezone": "Europe/Moscow"
    }
    response = await asyncio.to_thread(
        lambda: requests.get(WEATHER_URL, params=params, timeout=REQUEST_TIMEOUT)
    )
    response.raise_for_status()
    data = response.json()
    current, daily = data["current"], data["daily"]
    today, tomorrow = daily["time"][0], daily["time"][1]
    return (
        "🌦 <b>Погода — Кострома</b>\n"
        f"Сейчас: {current['temperature_2m']:+.1f} °C, {weather_code_text(current['weather_code'])}\n"
        f"Ощущается как: {current['apparent_temperature']:+.1f} °C\n"
        f"Влажность: {current['relative_humidity_2m']}%, ветер: {current['wind_speed_10m']} км/ч\n\n"
        f"Сегодня ({today}): {weather_code_text(daily['weather_code'][0])}, "
        f"{daily['temperature_2m_min'][0]:+.0f}…{daily['temperature_2m_max'][0]:+.0f} °C, "
        f"вероятность осадков до {daily['precipitation_probability_max'][0]}%\n"
        f"Завтра ({tomorrow}): {weather_code_text(daily['weather_code'][1])}, "
        f"{daily['temperature_2m_min'][1]:+.0f}…{daily['temperature_2m_max'][1]:+.0f} °C, "
        f"вероятность осадков до {daily['precipitation_probability_max'][1]}%\n\n"
        "Источник: Open-Meteo. Прогноз может обновляться."
    )

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
        if text:
            digest = hashlib.sha256((post_id + "|" + text).encode("utf-8")).hexdigest()
            posts.append({"key": digest, "text": text[:5000], "url": url})
    return posts[-30:]

def event_message(kind, label, source, url, published_text):
    stamp = now_msk().strftime("%d.%m.%Y, %H:%M МСК")
    territories = extract_territories(published_text) or ["Костромская область"]
    territory_text = "\n".join(f"• {html.escape(name)}" for name in territories)
    is_rocket = "ракет" in (label or "").lower()

    if is_rocket and kind == "rocket_cancel":
        title = "🟢 Отбой ракетной опасности"
        category = "Ракетная опасность — отбой"
    elif is_rocket:
        title = "🔴 Ракетная опасность"
        category = "Ракетная опасность"
    elif kind == "cancel":
        title = "🟢 Отбой опасности по БПЛА"
        category = "БПЛА"
    elif kind == "threat":
        title = "🟠 Угроза по БПЛА"
        category = "БПЛА"
    elif kind == "attention":
        title = "🟡 Внимание: БПЛА"
        category = "БПЛА"
    else:
        title = "🔴 Опасность по БПЛА"
        category = "БПЛА"

    return (
        "<b>🛰 UAV ALERT</b>\n"
        "📍 <b>Регион:</b> Костромская область\n\n"
        f"<b>{title}</b>\n"
        f"Категория: {category}\n"
        f"🕒 <b>Время обнаружения ботом:</b> {stamp}\n\n"
        f"📍 <b>Территории, упомянутые в публикации:</b>\n{territory_text}\n\n"
        "<i>Это автоматически сформированная сводка по стороннему сообщению. "
        "Проверьте актуальность по официальным оповещениям; публикация не является "
        "самостоятельным подтверждением угрозы.</i>\n\n"
        f"Источник: <a href=\"{html.escape(url, quote=True)}\">{html.escape(source)}</a>"
    )

async def send_event(application, kind, label, source, url, text):
    message = event_message(kind, label, source, url, text)
    is_cancel = kind in ("cancel", "rocket_cancel")
    image_path = ALL_CLEAR_IMAGE if is_cancel else DANGER_IMAGE
    recipients = set(subscribers)
    recipients.add(ADMIN_ID)

    for chat_id in sorted(recipients):
        try:
            if os.path.isfile(image_path):
                if len(message) <= 1024:
                    with open(image_path, "rb") as photo:
                        await application.bot.send_photo(
                            chat_id=chat_id, photo=photo, caption=message,
                            parse_mode=ParseMode.HTML
                        )
                else:
                    with open(image_path, "rb") as photo:
                        await application.bot.send_photo(
                            chat_id=chat_id, photo=photo,
                            caption="🛰 UAV ALERT — Костромская область"
                        )
                    await application.bot.send_message(
                        chat_id=chat_id, text=message, parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
            else:
                await application.bot.send_message(
                    chat_id=chat_id, text=message, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
        except TelegramError:
            logger.exception("Не удалось отправить уведомление chat_id=%s", chat_id)
        except OSError:
            logger.exception("Не удалось прочитать изображение: %s", image_path)

async def regional_alert_status():
    found = {"uav": None, "rocket": None}
    errors = []
    for name, url, _ in SOURCES:
        try:
            posts = await asyncio.to_thread(source_posts, url)
            for post in reversed(posts):
                text = post["text"]
                if not mentions_kostroma(text):
                    continue
                if found["rocket"] is None:
                    rocket_result = classify_rocket(text)
                    if rocket_result:
                        found["rocket"] = (rocket_result[1], name, post["url"])
                if found["uav"] is None:
                    result = classify_uav(text)
                    if result:
                        found["uav"] = (result[1], name, post["url"])
        except Exception as exc:
            errors.append(name)
            logger.warning("Не удалось проверить статус по %s: %s", name, exc)

    lines = ["🛰 <b>Статус оповещений — Костромская область</b>"]
    if found["uav"]:
        label, name, url = found["uav"]
        lines.append(
            f"БПЛА: <b>{html.escape(label)}</b> — последнее найденное сообщение: "
            f"<a href=\"{html.escape(url, quote=True)}\">{html.escape(name)}</a>."
        )
    else:
        lines.append("БПЛА: активное состояние не установлено по доступным публикациям.")
    if found["rocket"]:
        label, name, url = found["rocket"]
        lines.append(
            f"Ракетная опасность: <b>{html.escape(label)}</b> — последнее найденное сообщение: "
            f"<a href=\"{html.escape(url, quote=True)}\">{html.escape(name)}</a>."
        )
    else:
        lines.append("Ракетная опасность: активное состояние не установлено по доступным публикациям.")
    if errors:
        lines.append("Не удалось проверить: " + ", ".join(errors) + ".")
    lines.append(
        "\n⚠️ Это автоматическая сводка по найденным публикациям Telegram, "
        "а не подтверждение текущей официальной обстановки. Отсутствие сообщения "
        "не означает отсутствие угрозы. Проверяйте официальные оповещения МЧС и властей."
    )
    return "\n".join(lines)

async def monitor_source(application, name, url, is_review_source):
    posts = await asyncio.to_thread(source_posts, url)
    seen_map = state.setdefault("seen", {})
    source_seen = set(seen_map.get(url, []))
    initialized = bool(state.setdefault("initialized", {}).get(url, False))

    # Первый запуск только запоминает имеющиеся публикации, не рассылая архив.
    if not initialized:
        seen_map[url] = [p["key"] for p in posts][-300:]
        state["initialized"][url] = True
        state["last_errors"].pop(url, None)
        return

    for post in posts:
        if post["key"] in source_seen:
            continue
        source_seen.add(post["key"])
        text = post["text"]
        if not mentions_kostroma(text):
            continue

        result = classify_rocket(text) or classify_uav(text)
        if not result:
            continue

        kind, label = result
        await send_event(application, kind, label, name, post["url"], text)

    # Сортируем ключи для сохранения предсказуемого состояния.
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
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "🛰 UAV ALERT — информационный сервис Костромской области.\n\n"
        "Бот проверяет публичные сообщения выбранных источников, если процесс запущен. "
        "Сообщения сторонних каналов требуют сверки с официальными оповещениями. "
        "Бот не является официальной системой 112.\n\n"
        "Команды: /weather — погода; /status — состояние мониторинга; "
        "/sources — источники; /health — диагностика администратора.\n\n"
        "Нажми «Подписаться», чтобы получать уведомления.",
        reply_markup=keyboard()
    )
    try:
        await update.effective_message.reply_text(
            await regional_alert_status(), parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception as exc:
        logger.warning("Не удалось сформировать статус при /start: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Не удалось получить сводку. Проверь официальные оповещения МЧС и органов власти."
        )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.effective_message:
        return
    subscribers.add(user.id)
    save_state()
    await update.effective_message.reply_text("🔔 Подписка включена.", reply_markup=keyboard())

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not update.effective_message:
        return
    subscribers.discard(user.id)
    save_state()
    await update.effective_message.reply_text("🔕 Подписка отключена.", reply_markup=keyboard())

def sources_text():
    lines = ["📡 Источники мониторинга", ""]
    lines.extend(f"• {name}: {url.replace('/s/', '/')}" for name, url, _ in SOURCES)
    lines.extend(["", "Официальные ресурсы для сверки:",
                  *[f"• {name}: {url}" for name, url in OFFICIAL_SOURCES],
                  "", "При первом запуске бот запоминает старые посты и не рассылает архив.",
                  "Сообщения из Telegram-каналов не подтверждают официальный статус тревоги."])
    return "\n".join(lines)

def status_text():
    errors = state.get("last_errors", {})
    lines = [
        "🩺 UAV ALERT — статус",
        f"Последний цикл: {state.get('last_scan') or 'ещё не завершён'}",
        f"Интервал проверки: {CHECK_INTERVAL} сек.",
        f"Подписчиков: {len(subscribers)}",
        f"Источников: {len(SOURCES)}", ""
    ]
    for name, url, _ in SOURCES:
        lines.append(f"• {name}: {'ошибка — ' + errors[url] if url in errors else 'без последней ошибки'}")
    return "\n".join(lines)

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.effective_message.reply_text(
            await get_weather_text(), parse_mode=ParseMode.HTML
        )
    except Exception as exc:
        logger.warning("Не удалось получить прогноз погоды: %s", exc)
        await update.effective_message.reply_text(
            "Не удалось получить прогноз погоды для Костромы. Попробуй позже."
        )

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
    # Бот не читает SMS с телефона. Администратор может передать текст вручную.
    message, user = update.effective_message, update.effective_user
    if not message or not user or user.id != ADMIN_ID or not message.text:
        return
    text = message.text.strip()
    result = classify_rocket(text) or classify_uav(text)
    if not result or not mentions_kostroma(text):
        return
    await message.reply_text(
        "📩 Текст распознан как возможное предупреждение/отбой. "
        "Это не подтверждает подлинность отправителя или актуальность сообщения. "
        "Проверь официальный первоисточник."
    )
    kind, label = result
    await send_event(
        context.application, kind, label,
        "Текст, переданный администратором",
        "https://44.mchs.gov.ru/", text
    )

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
        if query.message:
            await query.message.reply_text(text, reply_markup=keyboard(), disable_web_page_preview=True)

async def post_init(application: Application):
    global monitor_task
    await application.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Статус мониторинга"),
        BotCommand("weather", "Погода в Костроме"),
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
    return jsonify({
        "status": "ok", "last_scan": state.get("last_scan"),
        "sources": len(SOURCES), "errors": state.get("last_errors", {})
    })

def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Укажи BOT_TOKEN в переменных окружения хостинга.")
    threading.Thread(target=run_flask, daemon=True, name="uav-alert-health").start()
    application = (
        Application.builder().token(BOT_TOKEN)
        .post_init(post_init).post_shutdown(post_shutdown).build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("sources", history_sources))
    application.add_handler(CommandHandler("health", admin_health))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, incoming_sms))
    application.add_handler(CallbackQueryHandler(callback))
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
