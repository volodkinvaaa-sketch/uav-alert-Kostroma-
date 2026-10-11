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

# ========================= CONFIG =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
def env_int(name, default, minimum=None):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value) if minimum is not None else value

ADMIN_ID = env_int("ADMIN_ID", 1421675956)
PORT = env_int("PORT", 10000, 1)
CHECK_INTERVAL = env_int("CHECK_INTERVAL", 30, 15)
HISTORY_WINDOW_HOURS = env_int("HISTORY_WINDOW_HOURS", 5, 1)
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 15, 5)
SOURCE_LIMIT = env_int("SOURCE_LIMIT", 100, 30)
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip() or "@RADAR_Kostroma"
MSK = timezone(timedelta(hours=3))
UTC = timezone.utc
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("uav_alert")
monitor_task = None
monitor_lock = None

# ========================= STORAGE =========================
def now_msk():
    return datetime.now(MSK)

def file_path(name):
    return os.path.join(DATA_DIR, name)

def parse_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(MSK)
    except (ValueError, TypeError, OverflowError):
        return None

def format_time(value):
    dt = parse_datetime(value)
    return dt.strftime("%d.%m.%Y %H:%M МСК") if dt else "неизвестно"

def load_json(name, default):
    try:
        with open(file_path(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, TypeError):
        return default

def save_json(name, value):
    path = file_path(name)
    temp = path + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        os.replace(temp, path)
    except OSError:
        logger.exception("Не удалось сохранить %s", name)
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass

DEFAULT_STATE = {
    "uav_level": 0, "uav_cycle": 0, "rocket_active": False, "rocket_cycle": 0,
    "active_locations": [], "uav_territories": {}, "last_uav_time": None,
    "last_uav_source": None, "last_uav_post": None, "last_uav_post_key": None,
    "last_uav_post_date": None, "last_uav_post_text": None, "last_uav_event_type": None,
    "last_uav_notification_key": None, "last_rocket_time": None, "last_rocket_source": None,
    "last_rocket_post": None, "last_rocket_post_key": None, "last_rocket_post_date": None,
    "last_rocket_event_type": None, "last_rocket_notification_key": None,
    "startup_completed": False, "last_scan_time": None, "last_scan_count": 0,
}
state = load_json("state.json", DEFAULT_STATE.copy())
if not isinstance(state, dict):
    state = DEFAULT_STATE.copy()
for key, value in DEFAULT_STATE.items():
    state.setdefault(key, value)
if not isinstance(state.get("uav_territories"), dict):
    state["uav_territories"] = {}
history = load_json("history.json", [])
if not isinstance(history, list):
    history = []
raw_subscribers = load_json("subscribers.json", [])
if isinstance(raw_subscribers, dict):
    raw_subscribers = raw_subscribers.get("subscribers", [])
subscribers = set()
for item in raw_subscribers if isinstance(raw_subscribers, list) else []:
    try:
        subscribers.add(int(item))
    except (ValueError, TypeError):
        pass
sent_posts = load_json("sent_posts.json", {})
notification_events = load_json("notification_events.json", {})
source_state = load_json("source_state.json", {})
channel_posts = load_json("channel_posts.json", {})
admin_drafts = load_json("admin_drafts.json", {})
for var_name in ("sent_posts", "notification_events", "source_state", "channel_posts", "admin_drafts"):
    if not isinstance(globals()[var_name], dict):
        globals()[var_name] = {}

def save_state(): save_json("state.json", state)
def save_history():
    del history[:-500]
    save_json("history.json", history)
def save_subscribers(): save_json("subscribers.json", sorted(subscribers))
def save_sent_posts():
    if len(sent_posts) > 3000:
        for key, _ in sorted(sent_posts.items(), key=lambda x: str(x[1].get("processed_at", "")))[:-2500]:
            sent_posts.pop(key, None)
    save_json("sent_posts.json", sent_posts)
def prune_records(records, limit=3000, keep=2500):
    if len(records) > limit:
        for key, _ in sorted(records.items(), key=lambda x: str(x[1].get("attempted_at", "")))[:-keep]:
            records.pop(key, None)
def save_notification_events():
    prune_records(notification_events)
    save_json("notification_events.json", notification_events)
def save_source_state(): save_json("source_state.json", source_state)
def save_channel_posts():
    prune_records(channel_posts)
    save_json("channel_posts.json", channel_posts)
def save_admin_drafts():
    prune_records(admin_drafts)
    save_json("admin_drafts.json", admin_drafts)

# ========================= SOURCES =========================
SOURCES = {
    "locator": {"name": "Локатор России", "url": "https://t.me/s/locatorru", "channel": "@locatorru"},
    "monitoring": {"name": "Russia Monitoring Radar BPLA", "url": "https://t.me/s/russiamonitoring_radar_bpla", "channel": "@russiamonitoring_radar_bpla"},
    "radar": {"name": "Radar Russia", "url": "https://t.me/s/radarrussiia", "channel": "@radarrussiia"},
    "bpla": {"name": "BPLA Russia", "url": "https://t.me/s/bplarussiaru", "channel": "@bplarussiaru"},
}

# ========================= TERRITORY FILTER =========================
def normalize_text(value):
    value = (value or "").replace("\xa0", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", value).strip().lower()

KOSTROMA_RE = re.compile(r"\b(?:костромск\w*\s+област\w*|костром\w*)\b", re.I)
CITY_PATTERNS = [
    (r"\bкостром(?:а|ы|е|у|ой|ою)\b", "Кострома"), (r"\bнерехт\w*\b", "Нерехта"),
    (r"\bбу(?:й|я|е|ю|ем)\b", "Буй"), (r"\bволгореченск\w*\b", "Волгореченск"),
    (r"\bгалич\w*\b", "Галич"), (r"\bшарь\w*\b", "Шарья"),
    (r"\bмантуров\w*\b", "Мантурово"), (r"\bчухлом\w*\b", "Чухлома"),
    (r"\bмакарьев\w*\b", "Макарьев"), (r"\bсолигалич\w*\b", "Солигалич"),
    (r"\bкологрив\w*\b", "Кологрив"), (r"\bне[яе]\b", "Нея"),
]
AREA_STEMS = [
    ("антропов", "Антроповский"), ("буй", "Буйский"), ("вохом", "Вохомский"),
    ("варнав", "Варнавинский"), ("ветлуж", "Ветлужский"), ("галич", "Галичский"),
    ("кадый", "Кадыйский"), ("кологрив", "Кологривский"), ("костром", "Костромской"),
    ("красносель", "Красносельский"), ("макарьев", "Макарьевский"), ("межев", "Межевской"),
    ("мантуров", "Мантуровский"), ("ней", "Нейский"), ("нерехт", "Нерехтский"),
    ("октябрь", "Октябрьский"), ("остров", "Островский"), ("павин", "Павинский"),
    ("парфеньев", "Парфеньевский"), ("поназыр", "Поназыревский"), ("пыщуг", "Пыщугский"),
    ("солигалич", "Солигаличский"), ("сусанин", "Сусанинский"), ("чухлом", "Чухломский"),
    ("шарь", "Шарьинский"), ("судислав", "Судиславский"),
]
AREA_PATTERNS = []
SHORT_MO_PATTERNS = []
for stem, adjective in AREA_STEMS:
    AREA_PATTERNS.extend([
        (rf"\b{stem}\w*\s+район\w*", f"Район: {adjective} район"),
        (rf"\b{stem}\w*\s+(?:муниципальн\w*\s+)?округ\w*", f"Муниципальный округ: {adjective} муниципальный округ"),
    ])
    SHORT_MO_PATTERNS.append((rf"\b{stem}\w*\s+(?:мо|м\.\s*о\.)\b", f"Муниципальный округ: {adjective} муниципальный округ"))

def find_locations(text):
    value = normalize_text(text)
    found, area_spans = [], []
    for pattern, label in AREA_PATTERNS + SHORT_MO_PATTERNS:
        for match in re.finditer(pattern, value):
            area_spans.append((match.start(), match.end()))
            if label not in found:
                found.append(label)
    for pattern, city in CITY_PATTERNS:
        for match in re.finditer(pattern, value):
            if any(match.start() < end and match.end() > start for start, end in area_spans):
                continue
            label = f"Город: {city}"
            if label not in found:
                found.append(label)
    if re.search(r"\bкостромск\w*\s+област\w*\b", value) and "Костромская область" not in found:
        found.append("Костромская область")
    return found

def has_kostroma_territory(text):
    return bool(KOSTROMA_RE.search(normalize_text(text)) or find_locations(text))

def display_locations(locations):
    clean = []
    for item in locations or []:
        item = str(item).strip()
        for prefix in ("Город: ", "Район: ", "Муниципальный округ: "):
            if item.startswith(prefix):
                item = item[len(prefix):]
                break
        if item and item not in clean:
            clean.append(item)
    return clean

# ========================= EVENT DETECTION =========================
UAV_CONTEXT_RE = re.compile(
    r"(?:\bбпла\b|\bбеспилот\w*\b|\bдрон\w*\b|\bбезэкипажн\w*\b|\bмвш\b|\bмрш\b|\bмрлс\b|\bмрл\b|"
    r"\bмалоразмерн\w*\s+воздушн\w*\s+шар\w*\b|\bмалоразмерн\w*\s+разведывательн\w*\s+воздушн\w*\s+шар\w*\b|"
    r"\bвоздушн\w*\s+шар\w*\s+с\s+аппаратурой\b|\bна\s+шарах\b|\bвоздушн\w*\s+цел\w*\b|\bлетательн\w*\s+аппарат\w*\b)", re.I)
ROCKET_CONTEXT_RE = re.compile(r"(?:\bракетн\w*\s+опасност\w*\b|\bракетн\w*\s+тревог\w*\b|\bугроз\w*\s+ракетн\w*\b|\bракет\w*\s+опасност\w*\b)", re.I)
CANCEL_RE = re.compile(r"\b(?:отбой|отмен\w*|снят\w*|снята|снято|прекращен\w*|опасност\w*\s+больше\s+нет|угроз\w*\s+больше\s+нет|больше\s+не\s+актуальн\w*)\b", re.I)
PERSIST_RE = re.compile(r"\b(?:сохраня\w*|действу\w*|продолжа\w*|остает\w*|остается|остаются)\b", re.I)

def contains_uav(text): return bool(UAV_CONTEXT_RE.search(normalize_text(text)))
def contains_rocket(text): return bool(ROCKET_CONTEXT_RE.search(normalize_text(text)))
def detect_cancel(text): return bool(CANCEL_RE.search(normalize_text(text)))
def detect_rocket_cancel(text):
    value = normalize_text(text)
    return contains_rocket(value) and detect_cancel(value)
def detect_uav_cancel(text):
    value = normalize_text(text)
    if not contains_uav(value) or not detect_cancel(value):
        return False
    # Отбой только ракетной опасности не должен снимать статус БПЛА.
    if contains_rocket(value) and not re.search(r"\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b.{0,120}\b(?:отбой|отмен\w*|снят\w*|прекращен\w*)\b|\b(?:отбой|отмен\w*|снят\w*|прекращен\w*).{0,120}\b(?:бпла|беспилот\w*|дрон\w*|мвш|мрш|мрлс|мрл)\b", value):
        return False
    return True

def detect_uav_level(text):
    value = normalize_text(text)
    if not contains_uav(value) or detect_cancel(value):
        return 0
    ctx = r"(?:бпла|беспилот\w*|дрон\w*|безэкипажн\w*|мвш|мрш|мрлс|мрл|воздушн\w*\s+шар\w*|летательн\w*\s+аппарат\w*)"
    patterns = [
        (3, rf"\b(?:опасност\w*|объявлен\w*\s+опасност\w*|красн\w*\s+уровен\w*|тревог\w*)\b.{{0,120}}\b{ctx}"),
        (3, rf"\b{ctx}\b.{{0,120}}\b(?:опасност\w*|тревог\w*)\b"),
        (2, rf"\b(?:угроз\w*|оранжев\w*\s+уровен\w*)\b.{{0,120}}\b{ctx}"),
        (2, rf"\b{ctx}\b.{{0,120}}\bугроз\w*\b"),
        (1, rf"\b(?:внимание|желт\w*\s+уровен\w*)\b.{{0,120}}\b{ctx}"),
        (1, rf"\b{ctx}\b.{{0,120}}\bвнимание\b"),
    ]
    for level, pattern in patterns:
        if re.search(pattern, value):
            return level
    return 0

def detect_rocket(text):
    value = normalize_text(text)
    return contains_rocket(value) and not detect_rocket_cancel(value)

# ========================= SOURCE SCRAPING =========================
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
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
    posts.sort(key=lambda p: (parse_datetime(p.get("date")) or datetime.min.replace(tzinfo=MSK), p["post_id"]))
    return posts[-SOURCE_LIMIT:]

def mark_post_processed(post):
    if post.get("key"):
        sent_posts[post["key"]] = {"processed_at": now_msk().isoformat(timespec="seconds"),
                                   "date": post.get("date"), "source": post.get("source"), "url": post.get("url")}

def notification_key(event_type, level, cycle, post):
    raw = "|".join([event_type, str(level), str(cycle), str(post.get("key", ""))])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def add_history(event_type, title, post=None):
    post = post or {}
    history.append({"time": now_msk().isoformat(timespec="seconds"), "event": event_type,
                    "title": title, "source": post.get("source_name"), "url": post.get("url"), "post_time": post.get("date")})
    del history[:-500]
    save_history()

# ========================= MESSAGE BUILDERS =========================
def level_title(level):
    return {0: "🟢 Опасность по БПЛА не объявлена", 1: "🟡 Внимание по БПЛА",
            2: "🟠 Угроза по БПЛА", 3: "🔴 Опасность по БПЛА"}.get(level, "Статус БПЛА неизвестен")

def source_line(post):
    if not post:
        return "Источник: автоматическая проверка UAV ALERT"
    name = html.escape(str(post.get("source_name") or "Telegram"))
    url = str(post.get("url") or "")
    if url:
        return f'Источник: <a href="{html.escape(url, quote=True)}">{name}</a>\nПубликация: {format_time(post.get("date"))}'
    return f"Источник: {name}\nПубликация: {format_time(post.get('date'))}"

def uav_category_label(post):
    value = normalize_text((post or {}).get("text", ""))
    mrsh = bool(re.search(r"\bмрш\b|малоразмерн\w*\s+разведывательн\w*\s+воздушн\w*\s+шар\w*", value))
    mvsh = bool(re.search(r"\bмвш\b|малоразмерн\w*\s+воздушн\w*\s+шар\w*|воздушн\w*\s+шар\w*\s+с\s+аппаратурой|\bна\s+шарах\b", value))
    bpla = bool(re.search(r"\bбпла\b|беспилот\w*|\bдрон\w*|безэкипажн\w*", value))
    labels = []
    if bpla: labels.append("БПЛА")
    if mvsh: labels.append("МВШ — малоразмерные воздушные шары")
    if mrsh: labels.append("МРШ — малоразмерные разведывательные воздушные шары")
    return " / ".join(labels) if labels else "БПЛА"

def uav_level_title_for_post(level, post):
    cat = uav_category_label(post)
    return {1: f"🟡 Внимание по {cat}", 2: f"🟠 Угроза по {cat}", 3: f"🔴 Опасность по {cat}"}.get(level, f"🟢 Статус по {cat}")

def build_uav_push(level, locations, post):
    territory = ", ".join(display_locations(locations)) or "Костромская область"
    return (f"<b>{uav_level_title_for_post(level, post)}</b>\n\nТерритории: {html.escape(territory)}\n\n"
            f"{source_line(post)}\n\nИнформация собрана из публичных сообщений. Она не заменяет официальные оповещения.")

def build_uav_cancel_push(post, locations=None):
    cat = html.escape(uav_category_label(post))
    territory = ", ".join(display_locations(locations or []))
    line = f"\nТерритории: {html.escape(territory)}\n" if territory else "\n"
    return f"<b>🟢 Отбой по опасности: {cat}</b>{line}\nОбнаружена информация об отбое. Сверяйтесь с официальными оповещениями региона.\n\n{source_line(post)}"

def build_uav_channel_post(level, locations, post):
    territory = ", ".join(display_locations(locations)) or "Костромская область"
    return ("🛰 <b>UAV ALERT</b>\n" f"📍 Территории: {html.escape(territory)}\n\n"
            f"<b>{uav_level_title_for_post(level, post)}</b>\nКатегория: {html.escape(uav_category_label(post))}\n"
            f"Время обработки: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}\n\n{source_line(post)}\n\n"
            "<i>Информация собрана из публичного сообщения и не заменяет официальные оповещения.</i>")

def build_uav_cancel_channel_post(post, locations=None):
    territory = ", ".join(display_locations(locations or [])) or "Костромская область"
    return ("🛰 <b>UAV ALERT</b>\n" f"📍 Территории: {html.escape(territory)}\n\n"
            f"🟢 <b>Отбой по опасности: {html.escape(uav_category_label(post))}</b>\n"
            f"Время обработки: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}\n\n{source_line(post)}\n\n"
            "<i>Сверяйтесь с официальными оповещениями региона.</i>")

def build_uav_admin_draft(level, locations, post):
    territory = ", ".join(display_locations(locations or [])) or "Костромская область"
    category = uav_category_label(post or {})
    if level <= 0:
        headline, detail = f"🟢 Отбой по опасности: {category}", "Обнаружена информация об отбое. Сверяйтесь с официальными оповещениями региона."
    else:
        headline, detail = uav_level_title_for_post(level, post or {}), f"Категория: {category}"
    lines = ["🛰 UAV ALERT", f"📍 Территории: {territory}", "", headline, detail,
             f"Время обработки: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}", "",
             f"📡 Источник: {(post or {}).get('source_name') or 'публичный источник'}"]
    if (post or {}).get("url"):
        lines.append(f"Ссылка на источник: {post['url']}")
    lines.extend(["", "Информация собрана из публичного сообщения и не заменяет официальные оповещения."])
    return "\n".join(lines)

def build_rocket_push(active, post):
    title = "🟥 Ракетная опасность" if active else "🟢 Отбой ракетной опасности"
    note = "Следуйте официальным инструкциям экстренных служб." if active else "Сверяйтесь с официальными оповещениями региона."
    return f"<b>{title}</b>\n\n{note}\n\n{source_line(post)}"

# ========================= DELIVERY =========================
async def send_push(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    record = notification_events.get(key, {})
    if record.get("sent"):
        return int(record.get("sent_count", 0))
    delivered = set()
    try:
        delivered = {int(x) for x in record.get("delivered_users", [])}
    except (ValueError, TypeError):
        pass
    if not subscribers:
        notification_events[key] = {"sent": True, "sent_count": 0, "delivered_users": [],
            "attempted_at": now_msk().isoformat(timespec="seconds"), "event": event_type,
            "post": post.get("key"), "error": "На момент события подписчиков не было."}
        save_notification_events()
        return 0
    dead, errors = [], []
    for user_id in list(subscribers):
        if user_id in delivered:
            continue
        for attempt in range(2):
            try:
                await application.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.HTML,
                                                   disable_web_page_preview=True)
                delivered.add(user_id)
                break
            except RetryAfter as exc:
                delay = getattr(exc, "retry_after", 2)
                if hasattr(delay, "total_seconds"): delay = delay.total_seconds()
                if attempt == 0:
                    await asyncio.sleep(max(1.0, float(delay)))
                    continue
                errors.append(f"{user_id}: RetryAfter")
            except Forbidden:
                dead.append(user_id)
                break
            except (BadRequest, TelegramError) as exc:
                errors.append(f"{user_id}: {str(exc)[:120]}")
                logger.warning("Ошибка отправки уведомления %s: %s", user_id, exc)
                break
    for user_id in dead: subscribers.discard(user_id)
    if dead: save_subscribers()
    remaining = sum(1 for uid in subscribers if uid not in delivered)
    notification_events[key] = {"sent": remaining == 0, "sent_count": len(delivered.intersection(subscribers)),
        "failed_count": remaining, "delivered_users": sorted(delivered),
        "attempted_at": now_msk().isoformat(timespec="seconds"), "event": event_type,
        "post": post.get("key"), "error": "; ".join(errors[:10]) if errors else None}
    save_notification_events()
    return len(delivered.intersection(subscribers))

async def send_admin_post_draft(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Отправляет только сам черновик — без вводной фразы перед постом."""
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    if admin_drafts.get(key, {}).get("sent"):
        return True
    try:
        await application.bot.send_message(chat_id=ADMIN_ID, text=message, disable_web_page_preview=True)
        admin_drafts[key] = {"sent": True, "admin_id": ADMIN_ID, "event": event_type, "level": level,
            "cycle": cycle, "source_post": post.get("key"), "attempted_at": now_msk().isoformat(timespec="seconds"), "error": None}
        save_admin_drafts()
        return True
    except TelegramError as exc:
        admin_drafts[key] = {"sent": False, "admin_id": ADMIN_ID, "event": event_type, "level": level,
            "cycle": cycle, "source_post": post.get("key"), "attempted_at": now_msk().isoformat(timespec="seconds"),
            "error": str(exc)[:500]}
        save_admin_drafts()
        logger.exception("Не удалось отправить черновик администратору. Открой бота и нажми /start.")
        return False

async def publish_to_channel(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Оставлена для отдельного ручного сценария; мониторинг её не вызывает."""
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    if channel_posts.get(key, {}).get("sent"):
        return True
    try:
        sent = await application.bot.send_message(chat_id=CHANNEL_ID, text=message, parse_mode=ParseMode.HTML,
                                                  disable_web_page_preview=True)
        channel_posts[key] = {"sent": True, "channel": CHANNEL_ID, "message_id": getattr(sent, "message_id", None),
            "event": event_type, "level": level, "cycle": cycle, "source_post": post.get("key"),
            "attempted_at": now_msk().isoformat(timespec="seconds"), "error": None}
        save_channel_posts()
        return True
    except TelegramError as exc:
        channel_posts[key] = {"sent": False, "channel": CHANNEL_ID, "event": event_type, "level": level,
            "cycle": cycle, "source_post": post.get("key"), "attempted_at": now_msk().isoformat(timespec="seconds"),
            "error": str(exc)[:500]}
        save_channel_posts()
        logger.exception("Не удалось опубликовать в канале.")
        return False

# ========================= UAV / ROCKET STATE =========================
def sync_uav_summary():
    territories = state.get("uav_territories") or {}
    active = [(name, item) for name, item in territories.items() if int(item.get("level", 0)) > 0]
    state["uav_level"] = max((int(item.get("level", 0)) for _, item in active), default=0)
    state["active_locations"] = [name for name, _ in sorted(active, key=lambda pair: pair[0])]
    if active:
        latest = max(active, key=lambda pair: str(pair[1].get("last_time") or ""))[1]
        state["last_uav_time"] = latest.get("last_time") or state.get("last_uav_time")

async def apply_uav_event(application, new_level, locations, post, event_kind=None):
    """Хранит статусы территорий раздельно, но отправляет одно событие на исходную публикацию."""
    post = post or {}
    territories = state.setdefault("uav_territories", {})
    if not isinstance(territories, dict):
        territories = {}
        state["uav_territories"] = territories
    if not territories and int(state.get("uav_level", 0)) > 0:
        for name in state.get("active_locations") or ["Костромская область"]:
            territories[name] = {"level": int(state["uav_level"]), "cycle": int(state.get("uav_cycle", 0)),
                "last_time": state.get("last_uav_time"), "last_source": state.get("last_uav_source"),
                "last_post_url": state.get("last_uav_post"), "last_post_key": state.get("last_uav_post_key"),
                "last_post_date": state.get("last_uav_post_date"), "last_post_text": state.get("last_uav_post_text") or ""}
    locations = list(dict.fromkeys(locations or ["Костромская область"]))
    now = now_msk().isoformat(timespec="seconds")
    cancel = int(new_level) <= 0
    if cancel:
        if "Костромская область" in locations:
            targets = [name for name, item in territories.items() if int(item.get("level", 0)) > 0]
        else:
            targets = [name for name in locations if name in territories and int(territories[name].get("level", 0)) > 0]
        if not targets:
            logger.info("Отбой найден, но активного статуса для территорий нет.")
            return False
        for name in targets:
            item = territories[name]
            item.update({"level": 0, "cycle": int(item.get("cycle", 0)) + 1, "last_time": now,
                "last_source": post.get("source_name"), "last_post_url": post.get("url"),
                "last_post_key": post.get("key"), "last_post_date": post.get("date"), "last_post_text": post.get("text", "")})
    else:
        targets = []
        for name in locations:
            item = territories.setdefault(name, {"level": 0, "cycle": 0})
            old_level = int(item.get("level", 0))
            if old_level == int(new_level):
                continue
            if old_level == 0:
                item["cycle"] = int(item.get("cycle", 0)) + 1
            item.update({"level": int(new_level), "last_time": now, "last_source": post.get("source_name"),
                "last_post_url": post.get("url"), "last_post_key": post.get("key"),
                "last_post_date": post.get("date"), "last_post_text": post.get("text", "")})
            targets.append(name)
        if not targets:
            logger.info("Статус %s не изменился для территорий %s", new_level, locations)
            return False
    event_type = "uav_cancel" if cancel else ("uav_active" if any(int(territories[n].get("level", 0)) == int(new_level) for n in targets) else "uav_update")
    cycle = max((int(territories[n].get("cycle", 0)) for n in targets), default=int(state.get("uav_cycle", 0)))
    key = notification_key(event_type, int(new_level), cycle, post)
    state.update({"uav_cycle": max(int(state.get("uav_cycle", 0)), cycle), "last_uav_time": now,
        "last_uav_source": post.get("source_name"), "last_uav_post": post.get("url"),
        "last_uav_post_key": post.get("key"), "last_uav_post_date": post.get("date"),
        "last_uav_post_text": post.get("text", ""), "last_uav_event_type": event_type,
        "last_uav_notification_key": key})
    for name in targets:
        territories[name]["last_event_type"] = event_type
        territories[name]["last_notification_key"] = key
    sync_uav_summary()
    save_state()
    title = "Отбой по опасности БПЛА" if cancel else uav_level_title_for_post(int(new_level), post)
    add_history(event_type, f"{title} — {', '.join(display_locations(targets))}", post)
    message = build_uav_cancel_push(post, targets) if cancel else build_uav_push(int(new_level), targets, post)
    draft = build_uav_admin_draft(0 if cancel else int(new_level), targets, post)
    await send_push(application, message, event_type, int(new_level), cycle, post, key_override=key)
    await send_admin_post_draft(application, draft, event_type, int(new_level), cycle, post, key_override=key)
    return True

async def retry_last_uav_notification(application):
    key = state.get("last_uav_notification_key")
    event_type = state.get("last_uav_event_type")
    if not key or not event_type:
        return 0
    post = {"key": state.get("last_uav_post_key") or "", "source_name": state.get("last_uav_source") or "Публичный источник",
            "url": state.get("last_uav_post") or "", "date": state.get("last_uav_post_date"),
            "text": state.get("last_uav_post_text") or ""}
    locations = state.get("active_locations") or ["Костромская область"]
    level, cycle = int(state.get("uav_level", 0)), int(state.get("uav_cycle", 0))
    msg = build_uav_cancel_push(post, locations) if event_type == "uav_cancel" else build_uav_push(level, locations, post)
    if not notification_events.get(key, {}).get("sent"):
        await send_push(application, msg, event_type, level, cycle, post, key_override=key)
    if not admin_drafts.get(key, {}).get("sent"):
        await send_admin_post_draft(application, build_uav_admin_draft(level, locations, post), event_type, level, cycle, post, key_override=key)
    return int(notification_events.get(key, {}).get("sent_count", 0))

async def send_current_status_to_user(application, user_id):
    territories = state.get("uav_territories") or {}
    active = [(name, item) for name, item in territories.items() if int(item.get("level", 0)) > 0]
    if active:
        lines = ["<b>Текущие статусы UAV ALERT</b>", ""]
        for name, item in sorted(active, key=lambda pair: pair[0]):
            post = {"text": item.get("last_post_text") or ""}
            lines.append(f"{html.escape(uav_level_title_for_post(int(item.get('level', 0)), post))}\nТерритория: {html.escape(', '.join(display_locations([name])))}")
        lines.extend(["", "Информация из публичных сообщений; не заменяет официальные оповещения."])
        try:
            await application.bot.send_message(chat_id=user_id, text="\n\n".join(lines), parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.exception("Не удалось отправить текущий статус пользователю %s", user_id)
    elif int(state.get("uav_level", 0)) > 0:
        post = {"source_name": state.get("last_uav_source"), "url": state.get("last_uav_post"),
                "date": state.get("last_uav_post_date"), "text": state.get("last_uav_post_text") or ""}
        try:
            await application.bot.send_message(chat_id=user_id,
                text=build_uav_push(int(state["uav_level"]), state.get("active_locations") or ["Костромская область"], post),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramError:
            logger.exception("Не удалось отправить статус пользователю %s", user_id)
    if state.get("rocket_active"):
        post = {"source_name": state.get("last_rocket_source"), "url": state.get("last_rocket_post"),
                "date": state.get("last_rocket_post_date")}
        try:
            await application.bot.send_message(chat_id=user_id, text=build_rocket_push(True, post), parse_mode=ParseMode.HTML)
        except TelegramError:
            logger.exception("Не удалось отправить ракетный статус пользователю %s", user_id)

async def apply_rocket_event(application, active, post):
    if bool(state.get("rocket_active", False)) == bool(active):
        return False
    state["rocket_active"] = bool(active)
    if active:
        state["rocket_cycle"] = int(state.get("rocket_cycle", 0)) + 1
    event_type = "rocket_active" if active else "rocket_cancel"
    cycle = int(state.get("rocket_cycle", 0))
    state["last_rocket_time"] = now_msk().isoformat(timespec="seconds")
    state["last_rocket_source"], state["last_rocket_post"] = post.get("source_name"), post.get("url")
    state["last_rocket_post_key"], state["last_rocket_post_date"] = post.get("key"), post.get("date")
    state["last_rocket_event_type"] = event_type
    key = notification_key(event_type, 1 if active else 0, cycle, post)
    state["last_rocket_notification_key"] = key
    save_state()
    add_history(event_type, "Ракетная опасность" if active else "Отбой ракетной опасности", post)
    await send_push(application, build_rocket_push(active, post), event_type, 1 if active else 0, cycle, post, key_override=key)
    return True

# ========================= PARSING / MONITOR LOOP =========================
def get_post_event(post, inherited_locations=None):
    text = post.get("text", "")
    if not has_kostroma_territory(text):
        return {"locations": [], "uav_event": "none", "uav_level": 0, "rocket_event": "none"}
    locations = find_locations(text) or (inherited_locations or ["Костромская область"])
    uav_event, level = "none", 0
    if contains_uav(text):
        if detect_uav_cancel(text):
            uav_event = "cancel"
        else:
            level = detect_uav_level(text)
            if level:
                uav_event = "active"
    rocket_event = "cancel" if detect_rocket_cancel(text) else ("active" if detect_rocket(text) else "none")
    return {"locations": locations, "uav_event": uav_event, "uav_level": level, "rocket_event": rocket_event}

async def check_sources(application):
    global monitor_lock
    if monitor_lock is None:
        monitor_lock = asyncio.Lock()
    if monitor_lock.locked():
        logger.warning("Предыдущая проверка ещё выполняется.")
        return
    async with monitor_lock:
        collected = []
        for source_key, info in SOURCES.items():
            try:
                posts = await asyncio.to_thread(fetch_source, source_key, info)
                for post in posts:
                    published = parse_datetime(post.get("date"))
                    if published is not None and timedelta(0) <= now_msk() - published <= timedelta(hours=HISTORY_WINDOW_HOURS):
                        collected.append(post)
            except requests.RequestException:
                logger.exception("Ошибка запроса к источнику %s", source_key)
            except Exception:
                logger.exception("Ошибка обработки источника %s", source_key)
        collected = sorted({p["key"]: p for p in collected if p.get("key")}.values(),
            key=lambda p: (parse_datetime(p.get("date")) or datetime.min.replace(tzinfo=MSK), int(p.get("post_id", 0)), p.get("source", "")))
        state["last_scan_time"] = now_msk().isoformat(timespec="seconds")
        state["last_scan_count"] = len(collected)
        save_state()
        first_scan = not bool(state.get("startup_completed"))
        candidates = collected if first_scan else [p for p in collected if p.get("key") not in sent_posts]
        logger.info("Публикаций в окне: %s; новых для обработки: %s", len(collected), len(candidates))
        # Обрабатываем сообщения по времени, не объединяя отмену и новый статус в один итоговый снимок.
        for post in candidates:
            event = get_post_event(post)
            if event["uav_event"] == "active":
                await apply_uav_event(application, event["uav_level"], event["locations"], post)
            elif event["uav_event"] == "cancel":
                await apply_uav_event(application, 0, event["locations"], post)
            if event["rocket_event"] == "active":
                await apply_rocket_event(application, True, post)
            elif event["rocket_event"] == "cancel":
                await apply_rocket_event(application, False, post)
        if not candidates:
            await retry_last_uav_notification(application)
        for post in collected:
            mark_post_processed(post)
        save_sent_posts()
        for post in collected:
            source = post.get("source", "")
            old = source_state.get(source, {})
            try: old_id = int(old.get("last_post_id", 0))
            except (ValueError, TypeError): old_id = 0
            source_state[source] = {"last_post_id": max(old_id, int(post.get("post_id", 0))),
                                    "last_scan_time": now_msk().isoformat(timespec="seconds")}
        save_source_state()
        state["startup_completed"] = True
        save_state()

async def monitor_loop(application):
    logger.info("Мониторинг запущен: интервал %s сек., окно %s ч.", CHECK_INTERVAL, HISTORY_WINDOW_HOURS)
    while True:
        try:
            await check_sources(application)
        except asyncio.CancelledError:
            logger.info("Мониторинг остановлен.")
            raise
        except Exception:
            logger.exception("Ошибка цикла мониторинга.")
        await asyncio.sleep(CHECK_INTERVAL)

# ========================= UI / STATUS =========================
def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"), InlineKeyboardButton("🔕 Отписаться", callback_data="unsubscribe")],
        [InlineKeyboardButton("📊 Статус", callback_data="status"), InlineKeyboardButton("📚 История", callback_data="history")],
        [InlineKeyboardButton("📡 Источники", callback_data="sources"), InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])

def format_status():
    level = int(state.get("uav_level", 0))
    locations = state.get("active_locations") or []
    rocket = "🟥 Активна" if state.get("rocket_active") else "🟢 Не объявлена"
    territory_text = ", ".join(display_locations(locations)) if locations else "активных территорий нет"
    return ("<b>UAV ALERT — текущий статус</b>\n\n" + html.escape(level_title(level)) +
        f"\nАктивные территории: {html.escape(territory_text)}\nПоследнее изменение: {format_time(state.get('last_uav_time'))}\n\n" +
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
        if item.get("source"): lines.append(f"  Источник: {html.escape(str(item['source']))}")
        if item.get("url"): lines.append(f'  <a href="{html.escape(str(item["url"]), quote=True)}">Открыть публикацию</a>')
        lines.append("")
    return "\n".join(lines)

def format_sources():
    lines = ["<b>📡 Источники мониторинга</b>", ""]
    for info in SOURCES.values():
        lines.append(f'<a href="{html.escape(info["url"], quote=True)}">{html.escape(info["name"])}</a>')
    lines.extend(["", "Фильтр: Кострома и Костромская область, включая города и районы.",
                  "Источники публичные и могут содержать неполную или неподтверждённую информацию."])
    return "\n".join(lines)

def format_stats():
    return ("<b>📊 Статистика UAV ALERT</b>\n\n" f"Подписчиков: {len(subscribers)}\nЗаписей истории: {len(history)}\n"
            f"Обработанных публикаций: {len(sent_posts)}\nСобытий уведомлений: {len(notification_events)}\n"
            f"Окно анализа: {HISTORY_WINDOW_HOURS} ч.\nИнтервал проверки: {CHECK_INTERVAL} сек.")

def is_admin(update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)

async def start_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("🚨 <b>UAV ALERT</b>\nГражданский информационный сервис мониторинга публичных сообщений о БПЛА в Костроме и Костромской области.\n\nНажми «Подписаться», чтобы получать уведомления. Бот не заменяет официальные оповещения.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)

async def subscribe_user(application, user_id):
    new_sub = user_id not in subscribers
    subscribers.add(user_id)
    save_subscribers()
    if new_sub:
        await send_current_status_to_user(application, user_id)

async def subscribe_command(update, context):
    if update.effective_user and update.effective_message:
        await subscribe_user(context.application, update.effective_user.id)
        await update.effective_message.reply_text("🔔 Подписка включена. Ты будешь получать уведомления UAV ALERT.", reply_markup=main_keyboard())

async def unsubscribe_command(update, context):
    if update.effective_user and update.effective_message:
        subscribers.discard(update.effective_user.id)
        save_subscribers()
        await update.effective_message.reply_text("🔕 Подписка отключена.", reply_markup=main_keyboard())

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
    if not update.effective_message: return
    if not is_admin(update):
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return
    await update.effective_message.reply_text("✅ Тестовая проверка Telegram-бота прошла успешно.", reply_markup=main_keyboard())

async def admin_command(update, context):
    if not update.effective_message: return
    if not is_admin(update):
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return
    await update.effective_message.reply_text("<b>Админ-панель UAV ALERT</b>\n\n"
        f"Подписчиков: {len(subscribers)}\nСтатус БПЛА: {html.escape(level_title(int(state.get('uav_level', 0))))}\n"
        f"Ракетная опасность: {'активна' if state.get('rocket_active') else 'не объявлена'}\n"
        f"Последняя проверка: {format_time(state.get('last_scan_time'))}", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())

async def help_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("<b>Команды UAV ALERT</b>\n\n/start — главное меню\n/subscribe — подписаться\n/unsubscribe — отписаться\n/status — текущий статус\n/history — история событий\n/sources — источники\n/stats — статистика\n/test — тест (администратор)\n/admin — админ-информация\n/help — помощь\n\nБот использует публичные сообщения и не заменяет официальные оповещения.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())

async def callback_handler(update, context):
    query = update.callback_query
    if not query: return
    await query.answer()
    user, data = query.from_user, query.data or ""
    if data == "subscribe":
        await subscribe_user(context.application, user.id); text = "🔔 Подписка включена."
    elif data == "unsubscribe":
        subscribers.discard(user.id); save_subscribers(); text = "🔕 Подписка отключена."
    elif data == "status": text = format_status()
    elif data == "history": text = format_history()
    elif data == "sources": text = format_sources()
    elif data == "help": text = "<b>UAV ALERT</b>\n\nИспользуй кнопки или команды /status, /history, /sources, /subscribe и /unsubscribe. Бот не заменяет официальные оповещения."
    else: text = "Неизвестная команда."
    try:
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)
    except BadRequest as exc:
        if "Message is not modified" not in str(exc): logger.warning("Не удалось обновить меню: %s", exc)

# ========================= FLASK HEALTH CHECK =========================
app = Flask(__name__)
@app.get("/")
def index():
    return jsonify({"service": "UAV ALERT", "status": "running", "monitor_interval_seconds": CHECK_INTERVAL,
        "history_window_hours": HISTORY_WINDOW_HOURS, "subscribers": len(subscribers), "channel_id": CHANNEL_ID,
        "publication_mode": "admin_dm_draft", "last_scan_time": state.get("last_scan_time")}), 200

@app.get("/status")
def health_status():
    return jsonify({"status": "ok", "uav_level": state.get("uav_level", 0), "rocket_active": state.get("rocket_active", False),
        "last_scan_time": state.get("last_scan_time"), "last_scan_count": state.get("last_scan_count", 0)}), 200

def run_flask():
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)

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
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try: await monitor_task
        except asyncio.CancelledError: pass
        except Exception: logger.exception("Ошибка при остановке мониторинга.")
    monitor_task = None

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN. Добавь токен в Environment на Render.")
    threading.Thread(target=run_flask, name="uav-alert-flask", daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()
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
