import os
import re
import json
import html
import asyncio
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest, RetryAfter
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
except ValueError:
    ADMIN_ID = 1421675956
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(15, int(os.getenv("CHECK_INTERVAL", "30")))
HISTORY_WINDOW_HOURS = max(1, int(os.getenv("HISTORY_WINDOW_HOURS", "5")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
SOURCE_LIMIT = max(30, int(os.getenv("SOURCE_LIMIT", "100")))
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
# Канал, куда автоматически публикуются подтверждённые изменения статуса БПЛА/МВШ.
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip() or "@RADAR_Kostroma"
MSK = timezone(timedelta(hours=3))
UTC = timezone.utc

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("uav_alert")
monitor_task = None
monitor_lock = None

# ============================================================
# FILES / JSON
# ============================================================
def now_msk():
    return datetime.now(MSK)


def json_path(filename):
    return os.path.join(DATA_DIR, filename)


def parse_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)
        return result.astimezone(MSK)
    except (ValueError, TypeError, OverflowError):
        return None


def format_time(value):
    parsed = parse_datetime(value)
    return parsed.strftime("%d.%m.%Y %H:%M МСК") if parsed else "неизвестно"


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def save_json(filename, value):
    temp = filename + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(temp, filename)
    except OSError:
        logger.exception("Не удалось сохранить %s", filename)
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass

STATE_FILE = json_path("state.json")
HISTORY_FILE = json_path("history.json")
SUBSCRIBERS_FILE = json_path("subscribers.json")
SENT_POSTS_FILE = json_path("sent_posts.json")
NOTIFICATION_EVENTS_FILE = json_path("notification_events.json")
SOURCE_STATE_FILE = json_path("source_state.json")

DEFAULT_STATE = {
    "uav_level": 0, "uav_cycle": 0, "rocket_active": False, "rocket_cycle": 0,
    "active_locations": [], "last_uav_time": None, "last_uav_source": None,
    "last_uav_post": None, "last_uav_post_key": None, "last_uav_post_date": None,
    "last_uav_post_text": None,
    "last_uav_event_type": None, "last_uav_notification_key": None,
    "last_rocket_time": None, "last_rocket_source": None, "last_rocket_post": None,
    "last_rocket_post_key": None, "last_rocket_post_date": None,
    "last_rocket_event_type": None, "last_rocket_notification_key": None,
    "startup_completed": False, "last_scan_time": None, "last_scan_count": 0,
}
state = load_json(STATE_FILE, DEFAULT_STATE.copy())
if not isinstance(state, dict):
    state = DEFAULT_STATE.copy()
for k, v in DEFAULT_STATE.items():
    state.setdefault(k, v)
history = load_json(HISTORY_FILE, [])
if not isinstance(history, list):
    history = []
raw_subscribers = load_json(SUBSCRIBERS_FILE, [])
if isinstance(raw_subscribers, dict):
    raw_subscribers = raw_subscribers.get("subscribers", [])
subscribers = set()
if isinstance(raw_subscribers, list):
    for item in raw_subscribers:
        try:
            subscribers.add(int(item))
        except (ValueError, TypeError):
            pass
sent_posts = load_json(SENT_POSTS_FILE, {})
if not isinstance(sent_posts, dict):
    sent_posts = {}
notification_events = load_json(NOTIFICATION_EVENTS_FILE, {})
if not isinstance(notification_events, dict):
    notification_events = {}
source_state = load_json(SOURCE_STATE_FILE, {})
if not isinstance(source_state, dict):
    source_state = {}
CHANNEL_POSTS_FILE = json_path("channel_posts.json")
channel_posts = load_json(CHANNEL_POSTS_FILE, {})
if not isinstance(channel_posts, dict):
    channel_posts = {}


def save_state(): save_json(STATE_FILE, state)
def save_history():
    global history
    history = history[-500:]
    save_json(HISTORY_FILE, history)
def save_subscribers(): save_json(SUBSCRIBERS_FILE, sorted(subscribers))
def save_sent_posts(): save_json(SENT_POSTS_FILE, sent_posts)
def save_source_state(): save_json(SOURCE_STATE_FILE, source_state)
def save_channel_posts():
    if len(channel_posts) > 3000:
        ordered = sorted(channel_posts.items(), key=lambda x: str(x[1].get("attempted_at", "")))
        for old_key, _ in ordered[:-2500]:
            channel_posts.pop(old_key, None)
    save_json(CHANNEL_POSTS_FILE, channel_posts)
def save_notification_events():
    if len(notification_events) > 3000:
        ordered = sorted(notification_events.items(), key=lambda x: str(x[1].get("attempted_at", "")))
        for old_key, _ in ordered[:-2500]:
            notification_events.pop(old_key, None)
    save_json(NOTIFICATION_EVENTS_FILE, notification_events)

logger.info("Загружено подписчиков: %s", len(subscribers))

# ============================================================
# SOURCES
# ============================================================
SOURCES = {
    "locator": {"name": "Locator", "url": "https://t.me/s/locatorru", "channel": "@locatorru"},
    "monitoring": {"name": "Russia Monitoring Radar BPLA", "url": "https://t.me/s/russiamonitoring_radar_bpla", "channel": "@russiamonitoring_radar_bpla"},
    "radar": {"name": "Radar Russia", "url": "https://t.me/s/radarrussiia", "channel": "@radarrussiia"},
    "bpla": {"name": "BPLA Russia", "url": "https://t.me/s/bplarussiaru", "channel": "@bplarussiaru"},
}

# ============================================================
# TEXT / TERRITORY FILTER
# ============================================================
def normalize_text(value):
    value = (value or "").replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", value).strip().lower()

# Deliberately narrow: only explicit Kostroma / Kostroma Oblast mentions pass.
KOSTROMA_RE = re.compile(r"\b(?:костромск\w*\s+област\w*|костром\w*)\b", re.I)
LOCATION_PATTERNS = [
    (r"\bкостромск\w*\s+област\w*\b", "Костромская область"),
    (r"\bкостром\w*\b", "Кострома"),
    (r"\bнерехт\w*\b", "Нерехта"), (r"\bбу(?:й|я|е|ю|ем)\b", "Буй"),
    (r"\bволгореченск\w*\b", "Волгореченск"), (r"\bгалич\w*\b", "Галич"),
    (r"\bшарь\w*\b", "Шарья"), (r"\bмантуров\w*\b", "Мантурово"),
    (r"\bчухлом\w*\b", "Чухлома"), (r"\bмакарьев\w*\b", "Макарьев"),
    (r"\bсолигалич\w*\b", "Солигалич"), (r"\bкологрив\w*\b", "Кологрив"),
    (r"\bне[яе]\b", "Нея"), (r"\bкостромск\w*\s+район\w*\b", "Костромской район"),
    (r"\bнерехтск\w*\s+район\w*\b", "Нерехтский район"),
    (r"\bшарьинск\w*\s+район\w*\b", "Шарьинский район"),
]


def find_locations(text):
    value = normalize_text(text)
    found = []
    for pattern, label in LOCATION_PATTERNS:
        if re.search(pattern, value) and label not in found:
            found.append(label)
    if "Костромская область" in found:
        return ["Костромская область"]
    return found

# ============================================================
# EVENT DETECTION
# ============================================================
UAV_CONTEXT_RE = re.compile(
    r"(?:\bбпла\b|\bбеспилот\w*\b|\bдрон\w*\b|\bбезэкипажн\w*\b|"
    r"\bмвш\b|\bмрш\b|\bмрлс\b|\bмрл\b|"
    r"\bмалоразмерн\w*\s+воздушн\w*\s+шар\w*\b|"
    r"\bвоздушн\w*\s+шар\w*\s+с\s+аппаратурой\b|"
    r"\bшар(?:ы|ов|ами)?\s+с\s+аппаратурой\b|\bна\s+шарах\b|"
    r"\bвоздушн\w*\s+цел\w*\b|\bлетательн\w*\s+аппарат\w*\b)", re.I)
ROCKET_CONTEXT_RE = re.compile(
    r"(?:\bракетн\w*\s+опасност\w*\b|\bракетн\w*\s+тревог\w*\b|"
    r"\bугроз\w*\s+ракетн\w*\b|\bракет\w*\s+опасност\w*\b)", re.I)


def contains_uav(text): return bool(UAV_CONTEXT_RE.search(normalize_text(text)))
def contains_rocket(text): return bool(ROCKET_CONTEXT_RE.search(normalize_text(text)))

CANCEL_RE = re.compile(
    r"\b(?:отбой|отмен\w*|снят\w*|снята|снято|прекращен\w*|"
    r"опасност\w*\s+больше\s+нет|угроз\w*\s+больше\s+нет|"
    r"больше\s+не\s+актуальн\w*)\b", re.I)
PERSIST_RE = re.compile(r"\b(?:сохраня\w*|действу\w*|продолжа\w*|остает\w*|остаетс\w*)\b", re.I)


def detect_cancel(text):
    return bool(CANCEL_RE.search(normalize_text(text)))


def detect_rocket_cancel(text):
    value = normalize_text(text)
    return detect_cancel(value) and bool(re.search(r"\bракет\w*\b", value))


def detect_uav_cancel(text):
    value = normalize_text(text)
    if not detect_cancel(value) or not contains_uav(value):
        return False
    # A general cancellation word is not enough if the same text says the threat continues.
    if PERSIST_RE.search(value) and not re.search(r"\b(?:отбой|отмен\w*|снят\w*|прекращен\w*)\b", value):
        return False
    if detect_rocket_cancel(value) and not re.search(r"\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш)\b.{0,100}\b(?:отбой|отмен\w*|снят\w*)\b", value):
        return False
    return True


def detect_uav_level(text):
    value = normalize_text(text)
    if detect_uav_cancel(value):
        return 0
    if not contains_uav(value):
        return 0
    # Do not interpret an old event mentioned in a cancellation / retrospective report as active.
    if detect_cancel(value):
        return 0
    # Require an explicit status phrase near the UAV context, not just unrelated words elsewhere.
    patterns = [
        (3, r"\b(?:опасност\w*|объявлен\w*\s+опасност\w*|красн\w*\s+уровен\w*|тревог\w*)\b.{0,100}\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b"),
        (3, r"\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b.{0,100}\b(?:опасност\w*|тревог\w*)\b"),
        (2, r"\b(?:угроз\w*|оранжев\w*\s+уровен\w*)\b.{0,100}\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b"),
        (2, r"\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b.{0,100}\bугроз\w*\b"),
        (1, r"\b(?:внимание|желт\w*\s+уровен\w*)\b.{0,100}\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b"),
        (1, r"\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b.{0,100}\bвнимание\b"),
    ]
    for level, pattern in patterns:
        if re.search(pattern, value):
            return level
    return 0


def detect_rocket(text):
    value = normalize_text(text)
    return contains_rocket(value) and not detect_rocket_cancel(value)

# ============================================================
# FETCH POSTS
# ============================================================
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def fetch_source(source_key, source_info):
    response = requests.get(source_info["url"], headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts = []
    for node in soup.select(".tgme_widget_message"):
        data_post = node.get("data-post", "")
        if "/" not in data_post:
            continue
        try:
            post_id = int(data_post.rsplit("/", 1)[1])
        except (ValueError, IndexError):
            continue
        text_node = node.select_one(".tgme_widget_message_text")
        text = text_node.get_text(" ", strip=True) if text_node else ""
        time_node = node.select_one("time[datetime]")
        published = parse_datetime(time_node.get("datetime") if time_node else None)
        posts.append({
            "key": f"{source_key}:{post_id}", "source": source_key,
            "source_name": source_info["name"], "channel": source_info["channel"],
            "post_id": post_id, "text": text,
            "date": published.isoformat(timespec="seconds") if published else None,
            "url": f"https://t.me/{data_post}",
        })
    posts.sort(key=lambda p: (parse_datetime(p.get("date")) or datetime.min.replace(tzinfo=MSK), int(p.get("post_id", 0))))
    return posts[-SOURCE_LIMIT:]


def mark_post_processed(post):
    key = post.get("key")
    if not key:
        return
    sent_posts[key] = {"processed_at": now_msk().isoformat(timespec="seconds"),
                       "date": post.get("date"), "source": post.get("source"), "url": post.get("url")}
    if len(sent_posts) > 3000:
        ordered = sorted(sent_posts.items(), key=lambda x: str(x[1].get("processed_at", "")))
        for old_key, _ in ordered[:len(sent_posts) - 2500]:
            sent_posts.pop(old_key, None)

# ============================================================
# NOTIFICATION KEYS / HISTORY
# ============================================================
def notification_key(event_type, level, cycle, post):
    raw = "|".join([event_type, str(level), str(cycle), str(post.get("key", ""))])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def add_history(event_type, title, post=None):
    post = post or {}
    history.append({"time": now_msk().isoformat(timespec="seconds"), "event": event_type,
                    "title": title, "source": post.get("source_name"), "url": post.get("url"),
                    "post_time": post.get("date")})
    save_history()

# ============================================================
# MESSAGE BUILDERS
# ============================================================
def level_title(level):
    return {0: "🟢 Опасность по БПЛА не объявлена", 1: "🟡 Внимание по БПЛА",
            2: "🟠 Угроза по БПЛА", 3: "🔴 Опасность по БПЛА"}.get(level, "Статус БПЛА неизвестен")


def source_line(post):
    if not post:
        return "Источник: автоматическая проверка UAV ALERT"
    name = html.escape(str(post.get("source_name", "Telegram")))
    url_raw = str(post.get("url", ""))
    published = format_time(post.get("date"))
    if url_raw:
        return f'Источник: <a href="{html.escape(url_raw, quote=True)}">{name}</a>\nПубликация: {published}'
    return f"Источник: {name}\nПубликация: {published}"


def uav_category_label(post):
    """Возвращает категорию по тексту источника для точного текста пуша и поста."""
    value = normalize_text((post or {}).get("text", ""))
    mrsh = bool(re.search(r"\bмрш\b|малоразмерн\w*\s+разведывательн\w*\s+воздушн\w*\s+шар\w*", value))
    mwsh = bool(re.search(
        r"\bмвш\b|малоразмерн\w*\s+воздушн\w*\s+шар\w*|"
        r"воздушн\w*\s+шар\w*\s+с\s+аппаратурой|\bна\s+шарах\b", value
    ))
    bpla = bool(re.search(r"\bбпла\b|беспилот\w*|\bдрон\w*|безэкипажн\w*", value))
    labels = []
    if bpla:
        labels.append("БПЛА")
    if mwsh:
        labels.append("МВШ — малоразмерные воздушные шары")
    if mrsh:
        labels.append("МРШ — малоразмерные разведывательные воздушные шары")
    if labels:
        return " / ".join(labels)
    return "БПЛА"


def uav_level_title_for_post(level, post):
    category = uav_category_label(post)
    titles = {
        1: f"🟡 Внимание по {category}",
        2: f"🟠 Угроза по {category}",
        3: f"🔴 Опасность по {category}",
    }
    return titles.get(level, f"🟢 Статус по {category}")


def build_uav_push(level, locations, post):
    territory = ", ".join(locations) if locations else "Кострома / Костромская область"
    return (f"<b>{uav_level_title_for_post(level, post)}</b>\n\nТерритория: {html.escape(territory)}\n\n{source_line(post)}\n\n"
            "Информация автоматически собрана из публичных сообщений. Она не заменяет официальные оповещения.")


def build_uav_cancel_push(post):
    category = html.escape(uav_category_label(post))
    return (f"<b>🟢 Отбой по опасности: {category}</b>\n\nВ сообщении обнаружена информация об отбое. "
            "Сверяйтесь с официальными оповещениями региона.\n\n" + source_line(post))


def build_uav_channel_post(level, locations, post):
    """Готовый HTML-пост для канала; время публикации источника и ссылка сохраняются."""
    territory = ", ".join(locations) if locations else "Костромская область"
    category = html.escape(uav_category_label(post))
    return (
        "🛰 <b>UAV ALERT</b>\n"
        f"📍 Регион: {html.escape(territory)}\n\n"
        f"<b>{uav_level_title_for_post(level, post)}</b>\n"
        f"Категория: {category}\n"
        f"Время обнаружения: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}\n\n"
        f"{source_line(post)}\n\n"
        "<i>Информация собрана из публичного сообщения и не заменяет официальные оповещения.</i>"
    )


def build_uav_cancel_channel_post(post):
    territory = "Костромская область"
    category = html.escape(uav_category_label(post))
    return (
        "🛰 <b>UAV ALERT</b>\n"
        f"📍 Регион: {territory}\n\n"
        f"🟢 <b>Отбой по опасности: {category}</b>\n"
        f"Время обнаружения сообщения: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}\n\n"
        f"{source_line(post)}\n\n"
        "<i>Сверяйтесь с официальными оповещениями региона.</i>"
    )


def build_rocket_push(active, post):
    title = "🟥 Ракетная опасность" if active else "🟢 Отбой ракетной опасности"
    note = "Следуйте официальным инструкциям экстренных служб." if active else "Сверяйтесь с официальными оповещениями региона."
    return f"<b>{title}</b>\n\n{note}\n\n{source_line(post)}"

# ============================================================
# PUSH DELIVERY (remember each successful recipient)
# ============================================================
async def send_push(application, message, event_type, level=0, cycle=0, post=None):
    post = post or {}
    key = notification_key(event_type, level, cycle, post)
    record = notification_events.get(key, {})
    delivered_users = set()
    try:
        delivered_users = {int(x) for x in record.get("delivered_users", [])}
    except (TypeError, ValueError):
        delivered_users = set()
    if record.get("sent"):
        logger.info("Уведомление %s уже доставлено.", event_type)
        return int(record.get("sent_count", 0))
    if not subscribers:
        notification_events[key] = {"sent": False, "sent_count": 0, "delivered_users": [],
                                    "attempted_at": now_msk().isoformat(timespec="seconds"),
                                    "event": event_type, "post": post.get("key"), "error": "Нет подписчиков"}
        save_notification_events()
        logger.warning("Push %s не отправлен: нет подписчиков.", event_type)
        return 0
    dead_users = []
    errors = []
    for user_id in list(subscribers):
        if user_id in delivered_users:
            continue
        delivered = False
        for attempt in range(2):
            try:
                await application.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML,
                                                   disable_web_page_preview=True)
                delivered_users.add(user_id)
                delivered = True
                break
            except RetryAfter as exc:
                if attempt == 0:
                    await asyncio.sleep(float(getattr(exc, "retry_after", 2)))
                    continue
                errors.append(f"{user_id}: RetryAfter")
            except Forbidden:
                dead_users.append(user_id)
                errors.append(f"{user_id}: Forbidden")
                break
            except (BadRequest, TelegramError) as exc:
                errors.append(f"{user_id}: {str(exc)[:180]}")
                logger.warning("Не удалось отправить push пользователю %s: %s", user_id, exc)
                break
        if not delivered and user_id not in dead_users:
            logger.warning("Уведомление пока не доставлено пользователю %s", user_id)
    for user_id in dead_users:
        subscribers.discard(user_id)
    if dead_users:
        save_subscribers()
    failed_count = sum(1 for user_id in subscribers if user_id not in delivered_users)
    notification_events[key] = {
        "sent": failed_count == 0 and len(subscribers) > 0,
        "sent_count": len(delivered_users.intersection(subscribers)),
        "failed_count": failed_count,
        "delivered_users": sorted(delivered_users),
        "attempted_at": now_msk().isoformat(timespec="seconds"),
        "event": event_type, "post": post.get("key"), "error": "; ".join(errors[:10]) if errors else None,
    }
    save_notification_events()
    logger.info("Push %s: доставлено %s; осталось %s", event_type, len(delivered_users.intersection(subscribers)), failed_count)
    return len(delivered_users.intersection(subscribers))


async def publish_to_channel(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Публикует событие в канал один раз; неуспешная публикация повторяется при следующей проверке."""
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    record = channel_posts.get(key, {})
    if record.get("sent"):
        logger.info("Публикация %s уже отправлена в канал %s.", event_type, CHANNEL_ID)
        return True
    if not CHANNEL_ID:
        logger.error("Публикация пропущена: не задан CHANNEL_ID.")
        return False
    try:
        sent_message = await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        channel_posts[key] = {
            "sent": True,
            "channel": CHANNEL_ID,
            "message_id": getattr(sent_message, "message_id", None),
            "event": event_type,
            "level": level,
            "cycle": cycle,
            "source_post": post.get("key"),
            "attempted_at": now_msk().isoformat(timespec="seconds"),
            "error": None,
        }
        save_channel_posts()
        logger.info("Событие %s опубликовано в канале %s.", event_type, CHANNEL_ID)
        return True
    except TelegramError as exc:
        channel_posts[key] = {
            "sent": False,
            "channel": CHANNEL_ID,
            "event": event_type,
            "level": level,
            "cycle": cycle,
            "source_post": post.get("key"),
            "attempted_at": now_msk().isoformat(timespec="seconds"),
            "error": str(exc)[:500],
        }
        save_channel_posts()
        logger.exception("Не удалось опубликовать %s в канале %s. Проверь права администратора бота.", event_type, CHANNEL_ID)
        return False


async def retry_last_uav_notification(application):
    event_type = state.get("last_uav_event_type")
    key = state.get("last_uav_notification_key")
    if not event_type or not key:
        return 0
    previous = notification_events.get(key, {})
    post = {
        "key": state.get("last_uav_post_key") or "",
        "source_name": state.get("last_uav_source"),
        "url": state.get("last_uav_post"),
        "date": state.get("last_uav_post_date"),
        "text": state.get("last_uav_post_text") or "",
    }
    cycle = int(state.get("uav_cycle", 0))
    if event_type == "uav_cancel":
        message, level = build_uav_cancel_push(post), 0
        channel_message = build_uav_cancel_channel_post(post)
    else:
        level = int(state.get("uav_level", 0))
        message = build_uav_push(level, state.get("active_locations") or [], post)
        channel_message = build_uav_channel_post(level, state.get("active_locations") or [], post)
    if not previous.get("sent"):
        await send_push(application, message, event_type, level, cycle, post)
    await publish_to_channel(application, channel_message, event_type, level, cycle, post, key_override=key)
    return int(notification_events.get(key, {}).get("sent_count", 0))


async def send_current_status_to_user(application, user_id):
    level = int(state.get("uav_level", 0))
    if level > 0:
        post = {"key": state.get("last_uav_post_key") or "", "source_name": state.get("last_uav_source") or "Автоматическая проверка",
                "url": state.get("last_uav_post") or "", "date": state.get("last_uav_post_date") or state.get("last_uav_time")}
        try:
            await application.bot.send_message(chat_id=user_id, text=build_uav_push(level, state.get("active_locations") or [], post),
                                               parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramError:
            logger.exception("Не удалось отправить текущий статус БПЛА пользователю %s", user_id)
    if state.get("rocket_active"):
        post = {"key": state.get("last_rocket_post_key") or "", "source_name": state.get("last_rocket_source") or "Автоматическая проверка",
                "url": state.get("last_rocket_post") or "", "date": state.get("last_rocket_post_date") or state.get("last_rocket_time")}
        try:
            await application.bot.send_message(chat_id=user_id, text=build_rocket_push(True, post), parse_mode=ParseMode.HTML,
                                               disable_web_page_preview=True)
        except TelegramError:
            logger.exception("Не удалось отправить текущий ракетный статус пользователю %s", user_id)

# ============================================================
# APPLY EVENTS
# ============================================================
async def apply_uav_event(application, new_level, locations, post):
    old_level = int(state.get("uav_level", 0))
    old_locations = list(state.get("active_locations") or [])
    locations = locations or ["Костромская область"]
    if new_level <= 0:
        if old_level <= 0:
            logger.info("Отбой найден, но активного статуса БПЛА нет — пуш не отправляем.")
            return False
        state["uav_level"] = 0
        state["active_locations"] = []
        state["uav_cycle"] = int(state.get("uav_cycle", 0)) + 1
        event_type, level = "uav_cancel", 0
    else:
        if old_level == new_level and set(old_locations) == set(locations):
            logger.info("Статус не изменился; повторный пуш не отправляем.")
            await retry_last_uav_notification(application)
            return False
        if old_level == 0:
            state["uav_cycle"] = int(state.get("uav_cycle", 0)) + 1
        state["uav_level"] = new_level
        state["active_locations"] = locations
        event_type, level = ("uav_active" if old_level == 0 else "uav_update"), new_level
    cycle = int(state.get("uav_cycle", 0))
    state["last_uav_time"] = now_msk().isoformat(timespec="seconds")
    state["last_uav_source"] = post.get("source_name")
    state["last_uav_post"] = post.get("url")
    state["last_uav_post_key"] = post.get("key")
    state["last_uav_post_date"] = post.get("date")
    state["last_uav_post_text"] = post.get("text", "")
    state["last_uav_event_type"] = event_type
    state["last_uav_notification_key"] = notification_key(event_type, level, cycle, post)
    save_state()
    title = "Отбой по опасности БПЛА" if level == 0 else level_title(level)
    add_history(event_type, title, post)
    message = build_uav_cancel_push(post) if level == 0 else build_uav_push(level, locations, post)
    await send_push(application, message, event_type, level, cycle, post)
    channel_message = build_uav_cancel_channel_post(post) if level == 0 else build_uav_channel_post(level, locations, post)
    await publish_to_channel(
        application, channel_message, event_type, level, cycle, post,
        key_override=state["last_uav_notification_key"],
    )
    return True


async def apply_rocket_event(application, active, post):
    old_active = bool(state.get("rocket_active", False))
    if old_active == active:
        logger.info("Ракетный статус не изменился; повторный пуш не отправляем.")
        key = state.get("last_rocket_notification_key")
        if key and not notification_events.get(key, {}).get("sent"):
            old_post = {"key": state.get("last_rocket_post_key") or post.get("key", ""),
                        "source_name": state.get("last_rocket_source") or post.get("source_name"),
                        "url": state.get("last_rocket_post") or post.get("url"),
                        "date": state.get("last_rocket_post_date") or post.get("date")}
            typ = state.get("last_rocket_event_type") or ("rocket_active" if active else "rocket_cancel")
            await send_push(application, build_rocket_push(active, old_post), typ, 1 if active else 0,
                            int(state.get("rocket_cycle", 0)), old_post)
        return False
    state["rocket_active"] = active
    if active:
        state["rocket_cycle"] = int(state.get("rocket_cycle", 0)) + 1
    event_type = "rocket_active" if active else "rocket_cancel"
    cycle = int(state.get("rocket_cycle", 0))
    state["last_rocket_time"] = now_msk().isoformat(timespec="seconds")
    state["last_rocket_source"] = post.get("source_name")
    state["last_rocket_post"] = post.get("url")
    state["last_rocket_post_key"] = post.get("key")
    state["last_rocket_post_date"] = post.get("date")
    state["last_rocket_event_type"] = event_type
    state["last_rocket_notification_key"] = notification_key(event_type, 1 if active else 0, cycle, post)
    save_state()
    add_history(event_type, "Ракетная опасность" if active else "Отбой ракетной опасности", post)
    await send_push(application, build_rocket_push(active, post), event_type, 1 if active else 0, cycle, post)
    return True

# ============================================================
# POST PARSER — KOSTROMA FILTER APPLIES TO UAV AND ROCKET EVENTS
# ============================================================
def get_post_event(post, inherited_locations=None):
    text = post.get("text", "")
    value = normalize_text(text)
    # Strict regional filter: no explicit Kostroma / Kostroma Oblast mention => no event.
    # District/city-only mentions inside Kostroma region can be added here if desired.
    if not KOSTROMA_RE.search(value):
        return {"locations": [], "uav_event": "none", "uav_level": 0, "rocket_event": "none"}
    locations = find_locations(text) or ["Костромская область"]
    uav_event, uav_level = "none", 0
    if contains_uav(text):
        if detect_uav_cancel(text):
            uav_event = "cancel"
        else:
            uav_level = detect_uav_level(text)
            if uav_level > 0:
                uav_event = "active"
    rocket_event = "cancel" if detect_rocket_cancel(text) else ("active" if detect_rocket(text) else "none")
    return {"locations": locations, "uav_event": uav_event, "uav_level": uav_level, "rocket_event": rocket_event}

# ============================================================
# MONITORING — PROCESS ONLY NEW POSTS; NO REPEATED SPAM
# ============================================================
async def check_sources(application):
    global monitor_lock
    if monitor_lock is None:
        monitor_lock = asyncio.Lock()
    if monitor_lock.locked():
        logger.warning("Предыдущая проверка ещё выполняется.")
        return
    async with monitor_lock:
        collected = []
        for source_key, source_info in SOURCES.items():
            try:
                posts = await asyncio.to_thread(fetch_source, source_key, source_info)
                for post in posts:
                    published = parse_datetime(post.get("date"))
                    if published is None:
                        continue
                    age = now_msk() - published
                    if timedelta(0) <= age <= timedelta(hours=HISTORY_WINDOW_HOURS):
                        collected.append(post)
            except requests.RequestException:
                logger.exception("Ошибка запроса к источнику %s", source_key)
            except Exception:
                logger.exception("Ошибка обработки источника %s", source_key)
        unique = {p.get("key"): p for p in collected if p.get("key")}
        collected = sorted(unique.values(), key=lambda p: (parse_datetime(p.get("date")) or datetime.min.replace(tzinfo=MSK), int(p.get("post_id", 0)), p.get("source", "")))
        state["last_scan_time"] = now_msk().isoformat(timespec="seconds")
        state["last_scan_count"] = len(collected)
        save_state()
        logger.info("Найдено публикаций в окне: %s; новых: %s", len(collected), sum(1 for p in collected if p.get("key") not in sent_posts))

        # On first startup, scan recent posts to determine current state but send at most the latest
        # valid regional event for each event family. Mark all seen posts afterwards.
        first_scan = not bool(state.get("startup_completed"))
        candidates = collected if first_scan else [p for p in collected if p.get("key") not in sent_posts]
        latest_uav = None
        latest_rocket = None
        for post in candidates:
            event = get_post_event(post)
            if event["uav_event"] != "none":
                latest_uav = (post, event)
                logger.info("Региональное событие БПЛА: %s level=%s source=%s text=%s", event["uav_event"], event["uav_level"], post.get("source_name"), post.get("text", "")[:180])
            if event["rocket_event"] != "none":
                latest_rocket = (post, event)

        if latest_uav:
            post, event = latest_uav
            if event["uav_event"] == "cancel":
                if int(state.get("uav_level", 0)) > 0:
                    await apply_uav_event(application, 0, event["locations"], post)
                else:
                    logger.info("Региональный отбой найден, активного статуса нет; пуш не отправляем.")
            else:
                await apply_uav_event(application, int(event["uav_level"]), event["locations"], post)
        elif state.get("last_uav_event_type") and state.get("last_uav_notification_key"):
            # Повторяем неудачную доставку пуша или публикации в канале,
            # включая событие отбоя, когда текущий уровень уже равен нулю.
            await retry_last_uav_notification(application)

        if latest_rocket:
            post, event = latest_rocket
            await apply_rocket_event(application, event["rocket_event"] == "active", post)

        # Mark every collected post as seen, including irrelevant/out-of-region posts.
        # This prevents repeatedly analysing the same unrelated posts every 30 seconds.
        for post in collected:
            mark_post_processed(post)
        save_sent_posts()
        for post in collected:
            source = post.get("source", "")
            previous = source_state.get(source, {})
            try:
                previous_id = int(previous.get("last_post_id", 0))
            except (ValueError, TypeError):
                previous_id = 0
            source_state[source] = {"last_post_id": max(previous_id, int(post.get("post_id", 0))),
                                    "last_scan_time": now_msk().isoformat(timespec="seconds")}
        save_source_state()
        state["startup_completed"] = True
        save_state()


async def monitor_loop(application):
    logger.info("Мониторинг запущен. Интервал %s сек.; окно %s ч.", CHECK_INTERVAL, HISTORY_WINDOW_HOURS)
    while True:
        try:
            await check_sources(application)
        except asyncio.CancelledError:
            logger.info("Мониторинг остановлен.")
            raise
        except Exception:
            logger.exception("Ошибка цикла мониторинга.")
        await asyncio.sleep(CHECK_INTERVAL)

# ============================================================
# KEYBOARD / STATUS / HISTORY
# ============================================================
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"), InlineKeyboardButton("🔕 Отписаться", callback_data="unsubscribe")],
        [InlineKeyboardButton("📊 Статус", callback_data="status"), InlineKeyboardButton("📚 История", callback_data="history")],
        [InlineKeyboardButton("📡 Источники", callback_data="sources"), InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])


def format_status():
    level = int(state.get("uav_level", 0))
    locations = state.get("active_locations") or ["Костромская область"]
    rocket = "🟥 Активна" if state.get("rocket_active") else "🟢 Не объявлена"
    return ("<b>UAV ALERT — текущий статус</b>\n\n" + html.escape(level_title(level)) +
            f"\nТерритория: {html.escape(', '.join(locations))}\nПоследнее изменение: {format_time(state.get('last_uav_time'))}\n\n" +
            f"<b>Ракетная опасность:</b> {rocket}\nПоследняя проверка: {format_time(state.get('last_scan_time'))}\n" +
            f"Публикаций в окне: {int(state.get('last_scan_count', 0))}\n\n" +
            "<i>Автоматический мониторинг публичных сообщений; не заменяет официальные оповещения.</i>")


def format_history(limit=10):
    if not history:
        return "<b>📚 История событий</b>\n\nСобытий пока нет."
    lines = ["<b>📚 Последние события UAV ALERT</b>", ""]
    for item in reversed(history[-limit:]):
        lines.append(f"• <b>{html.escape(str(item.get('title', item.get('event', 'Событие'))))}</b>")
        lines.append(f"  {format_time(item.get('time'))}")
        if item.get("source"):
            lines.append(f"  Источник: {html.escape(str(item['source']))}")
        if item.get("url"):
            lines.append(f'  <a href="{html.escape(str(item["url"]), quote=True)}">Открыть публикацию</a>')
        lines.append("")
    return "\n".join(lines)


def format_sources():
    lines = ["<b>📡 Источники мониторинга</b>", ""]
    for info in SOURCES.values():
        lines.append(f'<a href="{html.escape(info["url"], quote=True)}">{html.escape(info["name"])}</a>')
    lines += ["", "Фильтр событий: только публикации с явным упоминанием Костромы или Костромской области.",
              "Источники публичные и могут содержать неполную или неподтверждённую информацию."]
    return "\n".join(lines)


def format_stats():
    return ("<b>📊 Статистика UAV ALERT</b>\n\n" + f"Подписчиков: {len(subscribers)}\nЗаписей истории: {len(history)}\n" +
            f"Обработанных публикаций: {len(sent_posts)}\nСобытий уведомлений: {len(notification_events)}\n" +
            f"Окно анализа: {HISTORY_WINDOW_HOURS} ч.\nИнтервал проверки: {CHECK_INTERVAL} сек.")

# ============================================================
# COMMANDS / CALLBACKS
# ============================================================
def is_admin(update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


async def start_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("🚨 <b>UAV ALERT</b>\nГражданский информационный сервис мониторинга публичных сообщений о БПЛА в Костроме и Костромской области.\n\nНажми «Подписаться», чтобы получать уведомления. Бот не заменяет официальные оповещения.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)


async def subscribe_user(application, user_id):
    was_subscribed = user_id in subscribers
    subscribers.add(user_id)
    save_subscribers()
    logger.info("Подписка пользователя %s; всего %s", user_id, len(subscribers))
    if not was_subscribed:
        await send_current_status_to_user(application, user_id)


async def subscribe_command(update, context):
    user, message = update.effective_user, update.effective_message
    if user and message:
        await subscribe_user(context.application, user.id)
        await message.reply_text("🔔 Подписка включена. Ты будешь получать уведомления UAV ALERT.", reply_markup=main_keyboard())


async def unsubscribe_command(update, context):
    user, message = update.effective_user, update.effective_message
    if user and message:
        subscribers.discard(user.id)
        save_subscribers()
        await message.reply_text("🔕 Подписка отключена.", reply_markup=main_keyboard())


async def status_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(format_status(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)


async def history_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(format_history(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)


async def sources_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(format_sources(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)


async def stats_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(format_stats(), parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def test_command(update, context):
    if not update.effective_message:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return
    await update.effective_message.reply_text("✅ Тестовая проверка Telegram-бота прошла успешно.", reply_markup=main_keyboard())


async def admin_command(update, context):
    if not update.effective_message:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return
    await update.effective_message.reply_text("<b>Админ-панель UAV ALERT</b>\n\n" +
        f"Подписчиков: {len(subscribers)}\nСтатус БПЛА: {html.escape(level_title(int(state.get('uav_level', 0))))}\n" +
        f"Ракетная опасность: {'активна' if state.get('rocket_active') else 'не объявлена'}\nПоследняя проверка: {format_time(state.get('last_scan_time'))}",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def help_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("<b>Команды UAV ALERT</b>\n\n/start — главное меню\n/subscribe — подписаться\n/unsubscribe — отписаться\n/status — текущий статус\n/history — история событий\n/sources — источники\n/stats — статистика\n/test — тест (администратор)\n/admin — админ-информация\n/help — помощь\n\nБот использует публичные сообщения и не заменяет официальные оповещения.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def callback_handler(update, context):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user, data = query.from_user, query.data or ""
    if data == "subscribe":
        await subscribe_user(context.application, user.id)
        text = "🔔 Подписка включена."
    elif data == "unsubscribe":
        subscribers.discard(user.id)
        save_subscribers()
        text = "🔕 Подписка отключена."
    elif data == "status": text = format_status()
    elif data == "history": text = format_history()
    elif data == "sources": text = format_sources()
    elif data == "help": text = "<b>UAV ALERT</b>\n\nИспользуй кнопки или команды /status, /history, /sources, /subscribe и /unsubscribe.\nФильтр — Кострома и Костромская область. Бот не заменяет официальные оповещения."
    else: text = "Неизвестная команда."
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            logger.warning("Не удалось обновить меню: %s", exc)

# ============================================================
# FLASK HEALTH ENDPOINTS
# ============================================================
app = Flask(__name__)

@app.get("/")
def index():
    return jsonify({"service": "UAV ALERT", "status": "running", "monitor_interval_seconds": CHECK_INTERVAL,
                    "history_window_hours": HISTORY_WINDOW_HOURS, "subscribers": len(subscribers),
                    "channel_id": CHANNEL_ID, "last_scan_time": state.get("last_scan_time")}), 200

@app.get("/status")
def health_status():
    return jsonify({"status": "ok", "uav_level": state.get("uav_level", 0),
                    "rocket_active": state.get("rocket_active", False), "last_scan_time": state.get("last_scan_time"),
                    "last_scan_count": state.get("last_scan_count", 0)}), 200


def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ============================================================
# APPLICATION LIFECYCLE / MAIN
# ============================================================
async def post_init(application):
    global monitor_task
    commands = [BotCommand("start", "Главное меню"), BotCommand("subscribe", "Подписаться"),
                BotCommand("unsubscribe", "Отписаться"), BotCommand("status", "Текущий статус"),
                BotCommand("history", "История событий"), BotCommand("sources", "Источники"),
                BotCommand("stats", "Статистика"), BotCommand("test", "Тест уведомления"),
                BotCommand("admin", "Админ-информация"), BotCommand("help", "Помощь")]
    try:
        await application.bot.set_my_commands(commands)
    except TelegramError:
        logger.exception("Не удалось установить команды Telegram.")
    monitor_task = asyncio.create_task(monitor_loop(application), name="uav-alert-monitor")
    logger.info("UAV ALERT готов к работе.")


async def post_shutdown(application):
    global monitor_task
    logger.info("UAV ALERT завершает работу.")
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Ошибка при остановке мониторинга.")
    monitor_task = None


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Добавь токен бота в Environment на Render.")
    threading.Thread(target=run_flask, name="uav-alert-flask", daemon=True).start()
    application = (Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build())
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Запускаем Telegram polling.")
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
