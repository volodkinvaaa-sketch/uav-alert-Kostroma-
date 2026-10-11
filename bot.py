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
def env_int(name, default, minimum=None):
    """Безопасно читает целочисленную переменную окружения."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logging.warning("Некорректное значение %s; используется %s", name, default)
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = env_int("ADMIN_ID", 1421675956, 1)
PORT = env_int("PORT", 10000, 1)
# Интервал сканирования по умолчанию — 3 секунды. Можно изменить через переменную окружения.
CHECK_INTERVAL = env_int("CHECK_INTERVAL", 3, 3)
# Анализируем публикации только за последние 5 часов.
HISTORY_WINDOW_HOURS = env_int("HISTORY_WINDOW_HOURS", 5, 1)
# Не повторяем неудачную доставку на каждом цикле: пауза снижает риск ограничений Telegram.
RETRY_INTERVAL_SECONDS = env_int("RETRY_INTERVAL_SECONDS", 30, 10)
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 15, 5)
SOURCE_LIMIT = env_int("SOURCE_LIMIT", 100, 30)
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
# Целевой канал для готовых постов. Бот присылает черновик администратору в личку,
# а публикация в @RADAR_Kostroma выполняется вручную после проверки.
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
    """Загружает путь, уже собранный через json_path(); битый JSON не роняет запуск."""
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
    "active_locations": [], "uav_territories": {}, "last_uav_time": None, "last_uav_source": None,
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
if not isinstance(state.get("uav_territories"), dict):
    state["uav_territories"] = {}
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
ADMIN_DRAFTS_FILE = json_path("admin_drafts.json")
admin_drafts = load_json(ADMIN_DRAFTS_FILE, {})
if not isinstance(admin_drafts, dict):
    admin_drafts = {}


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
def save_admin_drafts():
    if len(admin_drafts) > 3000:
        ordered = sorted(admin_drafts.items(), key=lambda x: str(x[1].get("attempted_at", "")))
        for old_key, _ in ordered[:-2500]:
            admin_drafts.pop(old_key, None)
    save_json(ADMIN_DRAFTS_FILE, admin_drafts)
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

# Территориальный фильтр Костромской области.
# Формат: (регулярное выражение, подпись). Сначала более конкретные названия,
# чтобы город и район/округ с похожим названием распознавались отдельно.
KOSTROMA_RE = re.compile(r"\b(?:костромск\w*\s+област\w*|костром\w*)\b", re.I)

CITY_PATTERNS = [
    (r"\bкостром(?:а|ы|е|у|ой|ою)\b", "Кострома"),
    (r"\bнерехт\w*\b", "Нерехта"),
    (r"\bбу(?:й|я|е|ю|ем)\b", "Буй"),
    (r"\bволгореченск\w*\b", "Волгореченск"),
    (r"\bгалич\w*\b", "Галич"),
    (r"\bшарь\w*\b", "Шарья"),
    (r"\bмантуров\w*\b", "Мантурово"),
    (r"\bчухлом\w*\b", "Чухлома"),
    (r"\bмакарьев\w*\b", "Макарьев"),
    (r"\bсолигалич\w*\b", "Солигалич"),
    (r"\bкологрив\w*\b", "Кологрив"),
    (r"\bне[яе]\b", "Нея"),
]

# Районы и муниципальные округа области. Оба варианта названия поддерживаются:
# «Нейский район», «в Нейском муниципальном округе», «Кадыйский МО» и т. п.
AREA_STEMS = [
    (r"антропов", "Антроповский"),
    (r"буй", "Буйский"),
    (r"вохом", "Вохомский"),
    (r"варнав", "Варнавинский"),
    (r"ветлуж", "Ветлужский"),
    (r"галич", "Галичский"),
    (r"кадый", "Кадыйский"),
    (r"кологрив", "Кологривский"),
    (r"костром", "Костромской"),
    (r"красносель", "Красносельский"),
    (r"макарьев", "Макарьевский"),
    (r"межев", "Межевской"),
    (r"мантуров", "Мантуровский"),
    (r"ней", "Нейский"),
    (r"нерехт", "Нерехтский"),
    (r"октябрь", "Октябрьский"),
    (r"остров", "Островский"),
    (r"павин", "Павинский"),
    (r"парфеньев", "Парфеньевский"),
    (r"поназыр", "Поназыревский"),
    (r"пыщуг", "Пыщугский"),
    (r"солигалич", "Солигаличский"),
    (r"сусанин", "Сусанинский"),
    (r"чухлом", "Чухломский"),
    (r"шарь", "Шарьинский"),
    (r"судислав", "Судиславский"),
]

AREA_PATTERNS = []
for stem, adjective in AREA_STEMS:
    # Название с явным словом «район».
    AREA_PATTERNS.append((
        rf"\b{stem}\w*\s+район\w*",
        f"Район: {adjective} район",
    ))
    # Муниципальные округа, включая краткую форму «Нейский округ».
    AREA_PATTERNS.append((
        rf"\b{stem}\w*\s+(?:муниципальн\w*\s+)?округ\w*",
        f"Муниципальный округ: {adjective} муниципальный округ",
    ))

# Сокращённые формы «Нейский МО» и названия в родительном/предложном падеже.
SHORT_MO_PATTERNS = [
    (rf"\b{stem}\w*\s+(?:мо|м\.\s*о\.)\b", f"Муниципальный округ: {adj} муниципальный округ")
    for stem, adj in AREA_STEMS
]


def find_locations(text):
    """Возвращает только распознанные территории, сохраняя тип каждой территории."""
    value = normalize_text(text)
    found = []

    # Сначала районы/округа: они важнее совпадения по названию города с тем же корнем.
    for patterns in (AREA_PATTERNS, SHORT_MO_PATTERNS):
        for pattern, label in patterns:
            if re.search(pattern, value) and label not in found:
                found.append(label)

    # Город с тем же корнем, что и район, учитываем только если в тексте он явно назван
    # как город/городской округ или встречается без слова «район/округ» рядом.
    for pattern, city in CITY_PATTERNS:
        matches = list(re.finditer(pattern, value))
        for match in matches:
            before = value[max(0, match.start() - 35):match.start()]
            after = value[match.end():match.end() + 35]
            nearby = before + " " + after
            matched_word = match.group(0)
            is_area_adjective = bool(re.search(
                r"(?:ск|цк|нск|вск|евск|инск|овск|чск|ичск|тск|омск)"
                r"(?:ом|ого|ому|ий|ая|ое|ие|их|ими|ую|ым|ыми|ой|ую)?$",
                matched_word,
            ))
            if is_area_adjective and re.search(r"(?:район\w*|муниципальн\w*\s+округ\w*|округ\w*)", nearby):
                continue
            label = f"Город: {city}"
            if label not in found:
                found.append(label)

    # Если нет конкретной территории, но регион прямо указан, сохраняем общий статус.
    if not found and re.search(r"\bкостромск\w*\s+област\w*\b", value):
        found.append("Костромская область")
    # Упоминание области не должно скрывать конкретный найденный город/район.
    return found


def has_kostroma_territory(text):
    """Регион проходит фильтр по прямому упоминанию области/Костромы или её территории."""
    value = normalize_text(text)
    if KOSTROMA_RE.search(value):
        return True
    return bool(find_locations(value))

# ============================================================
# EVENT DETECTION
# ============================================================
UAV_CONTEXT_RE = re.compile(
    r"(?:\bбпла\b|\bбеспилот\w*\b|\bдрон\w*\b|\bбезэкипажн\w*\b|"
    r"\bмвш\b|\bмрш\b|\bмрлс\b|\bмрл\b|"
    r"\bмалоразмерн\w*\s+разведывательн\w*\s+воздушн\w*\s+шар\w*\b|"
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


CANCEL_NEGATION_RE = re.compile(
    r"\b(?:не\s+(?:снят\w*|отмен\w*|прекращен\w*|объявлен\w*|подтвержден\w*)|"
    r"отбоя\s+(?:нет|не\s+было|не\s+объявлен\w*)|"
    r"опасност\w*\s+не\s+(?:снят\w*|отменен\w*|прекращен\w*)|"
    r"угроз\w*\s+не\s+(?:снят\w*|отменен\w*|прекращен\w*))\b", re.I)


def detect_cancel(text):
    value = normalize_text(text)
    # Не сбрасываем статус по сообщениям вроде «опасность не снята» или «отбоя нет».
    if CANCEL_NEGATION_RE.search(value):
        return False
    return bool(CANCEL_RE.search(value))


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


def build_uav_cancel_push(post, locations=None):
    category = html.escape(uav_category_label(post))
    territory = ", ".join(locations or [])
    territory_line = f"\nТерритория: {html.escape(territory)}\n" if territory else "\n"
    return (f"<b>🟢 Отбой по опасности: {category}</b>" + territory_line +
            "\nВ сообщении обнаружена информация об отбое. Сверяйтесь с официальными оповещениями региона.\n\n" + source_line(post))


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
async def send_push(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Отправляет push и сохраняет точный текст для повторной доставки после ошибки/перезапуска."""
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    record = notification_events.get(key, {})
    try:
        delivered_users = {int(x) for x in record.get("delivered_users", [])}
    except (TypeError, ValueError):
        delivered_users = set()
    if record.get("sent"):
        logger.info("Уведомление %s уже доставлено.", event_type)
        return int(record.get("sent_count", 0))

    # Записываем сообщение до отправки. При следующей проверке повторяем именно этот текст.
    record.update({
        "sent": False, "message": message, "event": event_type,
        "level": int(level), "cycle": int(cycle), "post_data": post,
        "post": post.get("key"), "key": key,
        "attempted_at": now_msk().isoformat(timespec="seconds"),
        "delivered_users": sorted(delivered_users),
    })
    notification_events[key] = record
    save_notification_events()

    if not subscribers:
        record.update({"sent": False, "sent_count": len(delivered_users), "failed_count": 0,
                       "error": "Нет подписчиков; событие сохранено для повторной проверки."})
        notification_events[key] = record
        save_notification_events()
        logger.warning("Push %s пока не отправлен: нет подписчиков.", event_type)
        return len(delivered_users)

    dead_users = []
    errors = []
    for user_id in list(subscribers):
        if user_id in delivered_users:
            continue
        delivered = False
        for attempt in range(2):
            try:
                await application.bot.send_message(
                    chat_id=user_id, text=message, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                delivered_users.add(user_id)
                delivered = True
                break
            except RetryAfter as exc:
                if attempt == 0:
                    retry_delay = getattr(exc, "retry_after", 2)
                    if hasattr(retry_delay, "total_seconds"):
                        retry_delay = retry_delay.total_seconds()
                    await asyncio.sleep(max(1.0, float(retry_delay)))
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
        "event": event_type, "level": int(level), "cycle": int(cycle),
        "post": post.get("key"), "post_data": post, "message": message, "key": key,
        "error": "; ".join(errors[:10]) if errors else None,
    }
    save_notification_events()
    logger.info("Push %s: доставлено %s; осталось %s", event_type,
                len(delivered_users.intersection(subscribers)), failed_count)
    return len(delivered_users.intersection(subscribers))

def build_uav_admin_draft(level, locations, post):
    """Формирует простой текст поста, который удобно скопировать в @RADAR_Kostroma."""
    post = post or {}
    territory = ", ".join(locations) if locations else "Костромская область"
    category = uav_category_label(post)
    detected_at = now_msk().strftime("%d.%m.%Y %H:%M МСК")
    source_name = str(post.get("source_name") or "публичный источник")
    source_url = str(post.get("url") or "")
    if level <= 0:
        headline = f"🟢 Отбой по опасности: {category}"
        detail = "По опубликованному сообщению обнаружена информация об отбое. Сверяйтесь с официальными оповещениями региона."
    else:
        headline = uav_level_title_for_post(level, post)
        detail = f"Категория: {category}"
    lines = ["🛰 UAV ALERT", "📍 Регион: Костромская область"]
    city_items = [item.split(": ", 1)[1] for item in (locations or []) if item.startswith("Город: ")]
    district_items = [item.split(": ", 1)[1] for item in (locations or []) if item.startswith("Район: ")]
    municipal_items = [item.split(": ", 1)[1] for item in (locations or []) if item.startswith("Муниципальный округ: ")]
    other_items = [item for item in (locations or []) if not item.startswith(("Город: ", "Район: ", "Муниципальный округ: ")) and item != "Костромская область"]
    if city_items:
        lines.append("🏙 Город: " + ", ".join(city_items))
    if district_items:
        lines.append("🗺 Район: " + ", ".join(district_items))
    if municipal_items:
        lines.append("🏛 Муниципальный округ: " + ", ".join(municipal_items))
    if other_items:
        lines.append("📍 Территория: " + ", ".join(other_items))
    lines.extend(["", headline])
    if level > 0:
        lines.append(detail)
    else:
        lines.append(detail)
    lines.extend([
        f"Время обнаружения: {detected_at}",
        "",
        f"Источник: {source_name}",
    ])
    if source_url:
        lines.append(f"Ссылка на источник: {source_url}")
    lines.extend([
        "",
        "Информация собрана из публичного сообщения и не заменяет официальные оповещения.",
    ])
    return "\n".join(lines)


async def send_admin_post_draft(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Присылает администратору черновик; сохраняет текст для безопасного повтора."""
    post = post or {}
    key = key_override or notification_key(event_type, level, cycle, post)
    record = admin_drafts.get(key, {})
    if record.get("sent"):
        logger.info("Черновик события %s уже отправлен администратору.", event_type)
        return True
    record.update({"sent": False, "admin_id": ADMIN_ID, "event": event_type,
                   "level": int(level), "cycle": int(cycle), "source_post": post.get("key"),
                   "message": message, "post_data": post, "key": key,
                   "attempted_at": now_msk().isoformat(timespec="seconds")})
    admin_drafts[key] = record
    save_admin_drafts()
    try:
        # Администратору отправляем только сам текст поста, без служебной шапки.
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            disable_web_page_preview=True,
        )
        record.update({"sent": True, "error": None,
                       "attempted_at": now_msk().isoformat(timespec="seconds")})
        admin_drafts[key] = record
        save_admin_drafts()
        logger.info("Готовый пост по событию %s отправлен администратору %s.", event_type, ADMIN_ID)
        return True
    except TelegramError as exc:
        record.update({"sent": False, "error": str(exc)[:500],
                       "attempted_at": now_msk().isoformat(timespec="seconds")})
        admin_drafts[key] = record
        save_admin_drafts()
        logger.exception("Не удалось прислать черновик администратору. Открой бота в Telegram и нажми /start.")
        return False

async def publish_to_channel(application, message, event_type, level=0, cycle=0, post=None, key_override=None):
    """Публикует событие в канал один раз и сохраняет текст неудачной публикации для повтора."""
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
            chat_id=CHANNEL_ID, text=message, parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        channel_posts[key] = {
            "sent": True, "channel": CHANNEL_ID,
            "message_id": getattr(sent_message, "message_id", None),
            "event": event_type, "level": int(level), "cycle": int(cycle),
            "source_post": post.get("key"), "message": message, "post_data": post,
            "key": key, "attempted_at": now_msk().isoformat(timespec="seconds"), "error": None,
        }
        save_channel_posts()
        logger.info("Событие %s опубликовано в канале %s.", event_type, CHANNEL_ID)
        return True
    except TelegramError as exc:
        channel_posts[key] = {
            "sent": False, "channel": CHANNEL_ID, "event": event_type,
            "level": int(level), "cycle": int(cycle), "source_post": post.get("key"),
            "message": message, "post_data": post, "key": key,
            "attempted_at": now_msk().isoformat(timespec="seconds"), "error": str(exc)[:500],
        }
        save_channel_posts()
        logger.exception("Не удалось опубликовать %s в канале %s. Проверь права администратора бота.", event_type, CHANNEL_ID)
        return False

def retry_is_due(record):
    """Проверяет, прошло ли достаточно времени с последней попытки доставки."""
    attempted = parse_datetime(record.get("attempted_at")) if isinstance(record, dict) else None
    if attempted is None:
        return True
    return (now_msk() - attempted).total_seconds() >= RETRY_INTERVAL_SECONDS


async def retry_pending_notifications(application):
    """Повторяет все недоставленные push, черновики администратора и публикации канала с паузой."""
    total_delivered = 0
    for key, record in list(notification_events.items()):
        if not isinstance(record, dict) or record.get("sent") or not retry_is_due(record):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            # Старые записи, созданные до сохранения текста, безопасно восстановить нельзя.
            continue
        post = record.get("post_data") or {"key": record.get("post") or ""}
        total_delivered += await send_push(
            application, message, event_type, int(record.get("level", 0)),
            int(record.get("cycle", 0)), post, key_override=key,
        )

    for key, record in list(admin_drafts.items()):
        if not isinstance(record, dict) or record.get("sent") or not retry_is_due(record):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            continue
        post = record.get("post_data") or {"key": record.get("source_post") or ""}
        await send_admin_post_draft(
            application, message, event_type, int(record.get("level", 0)),
            int(record.get("cycle", 0)), post, key_override=key,
        )

    for key, record in list(channel_posts.items()):
        if not isinstance(record, dict) or record.get("sent") or not retry_is_due(record):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            continue
        post = record.get("post_data") or {"key": record.get("source_post") or ""}
        await publish_to_channel(
            application, message, event_type, int(record.get("level", 0)),
            int(record.get("cycle", 0)), post, key_override=key,
        )
    return total_delivered


async def retry_last_uav_notification(application):
    """Совместимость со старым названием функции; повторяет все ожидающие отправки."""
    return await retry_pending_notifications(application)

async def send_current_status_to_user(application, user_id):
    territories = state.get("uav_territories") or {}
    active = [(name, item) for name, item in territories.items() if int(item.get("level", 0)) > 0]
    if active:
        for territory, item in sorted(active, key=lambda pair: pair[0]):
            post = {"key": item.get("last_post_key") or "", "source_name": item.get("last_source") or "Автоматическая проверка",
                    "url": item.get("last_post_url") or "", "date": item.get("last_post_date") or item.get("last_time"),
                    "text": item.get("last_post_text") or ""}
            try:
                await application.bot.send_message(
                    chat_id=user_id,
                    text=build_uav_push(int(item.get("level", 0)), [territory], post),
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                )
            except TelegramError:
                logger.exception("Не удалось отправить текущий статус БПЛА пользователю %s", user_id)
                break
    elif int(state.get("uav_level", 0)) > 0:
        # Backwards compatibility for older saved state.
        post = {"key": state.get("last_uav_post_key") or "", "source_name": state.get("last_uav_source") or "Автоматическая проверка",
                "url": state.get("last_uav_post") or "", "date": state.get("last_uav_post_date") or state.get("last_uav_time"),
                "text": state.get("last_uav_post_text") or ""}
        try:
            await application.bot.send_message(chat_id=user_id,
                text=build_uav_push(int(state.get("uav_level", 0)), state.get("active_locations") or ["Костромская область"], post),
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
def sync_uav_summary():
    """Пересчитывает общий статус из независимых статусов городов/районов/округов."""
    territories = state.get("uav_territories") or {}
    active = [(name, item) for name, item in territories.items() if int(item.get("level", 0)) > 0]
    state["uav_level"] = max((int(item.get("level", 0)) for _, item in active), default=0)
    state["active_locations"] = [name for name, _ in sorted(active, key=lambda pair: pair[0])]
    if active:
        latest = max((item for _, item in active), key=lambda item: str(item.get("last_time") or ""))
        state["last_uav_time"] = latest.get("last_time") or state.get("last_uav_time")


async def apply_uav_event(application, new_level, locations, post):
    """Обновляет статусы территорий и отправляет одно общее уведомление на событие."""
    if not isinstance(state.get("uav_territories"), dict):
        state["uav_territories"] = {}
    territories = state["uav_territories"]

    # Миграция активного статуса из старой версии, где хранился один общий уровень.
    if not territories and int(state.get("uav_level", 0)) > 0:
        legacy_locations = state.get("active_locations") or ["Костромская область"]
        for legacy_name in legacy_locations:
            territories[legacy_name] = {
                "level": int(state.get("uav_level", 0)), "cycle": int(state.get("uav_cycle", 0)),
                "last_time": state.get("last_uav_time"), "last_source": state.get("last_uav_source"),
                "last_post_url": state.get("last_uav_post"), "last_post_key": state.get("last_uav_post_key"),
                "last_post_date": state.get("last_uav_post_date"), "last_post_text": state.get("last_uav_post_text") or "",
                "last_event_type": state.get("last_uav_event_type"),
                "last_notification_key": state.get("last_uav_notification_key"),
            }

    locations = list(dict.fromkeys(locations or ["Костромская область"]))
    now = now_msk().isoformat(timespec="seconds")
    changed_locations = []
    cycles = []
    event_type = "uav_cancel" if new_level <= 0 else None

    if new_level <= 0:
        # Общий отбой по области очищает все активные территории; локальный — только названные.
        if "Костромская область" in locations:
            targets = [name for name, item in territories.items() if int(item.get("level", 0)) > 0]
        else:
            targets = [name for name in locations if name in territories and int(territories[name].get("level", 0)) > 0]
        if not targets:
            logger.info("Отбой найден, но для указанной территории активного статуса нет.")
            await retry_pending_notifications(application)
            return False

        for territory in targets:
            item = territories.setdefault(territory, {})
            cycle = int(item.get("cycle", 0)) + 1
            item.update({"level": 0, "cycle": cycle, "last_time": now,
                         "last_source": post.get("source_name"), "last_post_url": post.get("url"),
                         "last_post_key": post.get("key"), "last_post_date": post.get("date"),
                         "last_post_text": post.get("text", ""), "last_event_type": "uav_cancel"})
            cycles.append(cycle)
            changed_locations.append(territory)
            add_history("uav_cancel", f"Отбой по опасности БПЛА — {territory}", post)
    else:
        for territory in locations:
            item = territories.setdefault(territory, {"level": 0, "cycle": 0})
            old_level = int(item.get("level", 0))
            if old_level == int(new_level):
                logger.info("Статус %s по территории %s не изменился.", new_level, territory)
                continue
            cycle = int(item.get("cycle", 0))
            if old_level == 0:
                cycle += 1
            item_event_type = "uav_active" if old_level == 0 else "uav_update"
            event_type = item_event_type if event_type is None else event_type
            item.update({"level": int(new_level), "cycle": cycle, "last_time": now,
                         "last_source": post.get("source_name"), "last_post_url": post.get("url"),
                         "last_post_key": post.get("key"), "last_post_date": post.get("date"),
                         "last_post_text": post.get("text", ""), "last_event_type": item_event_type})
            cycles.append(cycle)
            changed_locations.append(territory)
            add_history(item_event_type, f"{level_title(int(new_level))} — {territory}", post)

    sync_uav_summary()
    # Legacy summary fields remain available for health endpoints and old clients.
    state["last_uav_time"] = now
    state["last_uav_source"] = post.get("source_name")
    state["last_uav_post"] = post.get("url")
    state["last_uav_post_key"] = post.get("key")
    state["last_uav_post_date"] = post.get("date")
    state["last_uav_post_text"] = post.get("text", "")
    save_state()

    if not changed_locations:
        await retry_pending_notifications(application)
        return False

    # Все территории из одного исходного сообщения объединяются в один push и один пост администратору.
    combined_post = dict(post)
    combined_post["key"] = f"{post.get('key', '')}|territories:{'|'.join(sorted(changed_locations))}"
    combined_cycle = max(cycles, default=0)
    if new_level <= 0:
        event_type = "uav_cancel"
        message = build_uav_cancel_push(post, changed_locations)
        draft = build_uav_admin_draft(0, changed_locations, post)
        message_level = 0
    else:
        event_type = event_type or "uav_active"
        message = build_uav_push(int(new_level), changed_locations, post)
        draft = build_uav_admin_draft(int(new_level), changed_locations, post)
        message_level = int(new_level)

    key = notification_key(event_type, message_level, combined_cycle, combined_post)
    for territory in changed_locations:
        if territory in territories:
            territories[territory]["last_notification_key"] = key
    state["last_uav_notification_key"] = key
    state["last_uav_event_type"] = event_type
    state["uav_cycle"] = max(int(state.get("uav_cycle", 0)), combined_cycle)
    save_state()
    await send_push(application, message, event_type, message_level, combined_cycle,
                    combined_post, key_override=key)
    await send_admin_post_draft(application, draft, event_type, message_level,
                                combined_cycle, combined_post, key_override=key)
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
    # Прямое упоминание области не обязательно, если сообщение явно называет
    # известный город, район или муниципальный округ Костромской области.
    if not has_kostroma_territory(text):
        return {"locations": [], "uav_event": "none", "uav_level": 0, "rocket_event": "none"}

    locations = find_locations(text)
    if not locations:
        locations = ["Костромская область"]

    uav_event, uav_level = "none", 0
    if contains_uav(text):
        if detect_uav_cancel(text):
            uav_event = "cancel"
            if not locations and inherited_locations:
                locations = list(inherited_locations)
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

        # During the first scan, use recent posts to reconstruct current state; later process only unseen posts.
        first_scan = not bool(state.get("startup_completed"))
        candidates = collected if first_scan else [p for p in collected if p.get("key") not in sent_posts]

        if first_scan:
            # На первом запуске восстанавливаем текущее состояние по недавней истории,
            # не рассылая задним числом каждое промежуточное событие.
            territories = state.get("uav_territories") or {}
            effective = {}
            if territories:
                for territory, item in territories.items():
                    effective[territory] = {
                        "level": int(item.get("level", 0)),
                        "post": {
                            "key": item.get("last_post_key") or "",
                            "source_name": item.get("last_source") or "Публичный источник",
                            "url": item.get("last_post_url") or "",
                            "date": item.get("last_post_date"),
                            "text": item.get("last_post_text") or "",
                        },
                    }
            elif int(state.get("uav_level", 0)) > 0:
                legacy_post = {
                    "key": state.get("last_uav_post_key") or "",
                    "source_name": state.get("last_uav_source") or "Публичный источник",
                    "url": state.get("last_uav_post") or "",
                    "date": state.get("last_uav_post_date"),
                    "text": state.get("last_uav_post_text") or "",
                }
                for territory in state.get("active_locations") or ["Костромская область"]:
                    effective[territory] = {"level": int(state.get("uav_level", 0)), "post": legacy_post}

            before_levels = {territory: int(item.get("level", 0)) for territory, item in effective.items()}
            latest_rocket = None
            for post in candidates:
                event = get_post_event(post)
                if event["uav_event"] != "none":
                    logger.info("Региональное событие БПЛА: %s level=%s source=%s text=%s",
                                event["uav_event"], event["uav_level"], post.get("source_name"), post.get("text", "")[:180])
                    if event["uav_event"] == "active":
                        for territory in event["locations"] or ["Костромская область"]:
                            effective[territory] = {"level": int(event["uav_level"]), "post": post}
                    elif event["uav_event"] == "cancel":
                        if "Костромская область" in event["locations"]:
                            for territory, item in list(effective.items()):
                                if int(item.get("level", 0)) > 0:
                                    effective[territory] = {"level": 0, "post": post}
                        else:
                            for territory in event["locations"]:
                                if territory in effective and int(effective[territory].get("level", 0)) > 0:
                                    effective[territory] = {"level": 0, "post": post}
                if event["rocket_event"] != "none":
                    latest_rocket = (post, event)

            changed_territories = []
            all_territories = set(before_levels) | set(effective)
            for territory in sorted(all_territories):
                old_level = before_levels.get(territory, 0)
                item = effective.get(territory, {"level": 0, "post": {}})
                new_level = int(item.get("level", 0))
                if new_level != old_level:
                    changed_territories.append((territory, new_level, item.get("post") or {}))

            # Группируем изменившиеся территории из одной исходной публикации,
            # чтобы при восстановлении состояния не рассылать отдельный push на каждый район.
            grouped_changes = {}
            for territory, new_level, event_post in changed_territories:
                post_key = str(event_post.get("key") or "")
                group_key = (post_key, int(new_level))
                if group_key not in grouped_changes:
                    grouped_changes[group_key] = {"level": int(new_level), "post": event_post, "locations": []}
                grouped_changes[group_key]["locations"].append(territory)
            for group in grouped_changes.values():
                await apply_uav_event(application, group["level"], group["locations"], group["post"])
            if not changed_territories:
                await retry_pending_notifications(application)
            if latest_rocket:
                post, event = latest_rocket
                await apply_rocket_event(application, event["rocket_event"] == "active", post)
        else:
            # При обычном мониторинге обрабатываем каждое новое событие по порядку.
            # Иначе «Внимание» и последующий «Отбой» между двумя проверками могли
            # взаимно погаситься и подписчики не получили бы ни одного уведомления.
            saw_uav_event = False
            saw_rocket_event = False
            for post in candidates:
                event = get_post_event(post)
                if event["uav_event"] != "none":
                    saw_uav_event = True
                    logger.info("Региональное событие БПЛА: %s level=%s source=%s text=%s",
                                event["uav_event"], event["uav_level"], post.get("source_name"), post.get("text", "")[:180])
                    level = int(event["uav_level"]) if event["uav_event"] == "active" else 0
                    await apply_uav_event(application, level, event["locations"], post)
                if event["rocket_event"] != "none":
                    saw_rocket_event = True
                    await apply_rocket_event(application, event["rocket_event"] == "active", post)
            # Проверяем недоставленные последние события даже если в этой итерации
            # пришли только посты без новых изменений статуса.
            await retry_pending_notifications(application)
            if not saw_rocket_event:
                key = state.get("last_rocket_notification_key")
                if key and not notification_events.get(key, {}).get("sent"):
                    active = bool(state.get("rocket_active"))
                    old_post = {
                        "key": state.get("last_rocket_post_key") or "",
                        "source_name": state.get("last_rocket_source") or "Публичный источник",
                        "url": state.get("last_rocket_post") or "",
                        "date": state.get("last_rocket_post_date"),
                    }
                    await send_push(application, build_rocket_push(active, old_post),
                                    state.get("last_rocket_event_type") or ("rocket_active" if active else "rocket_cancel"),
                                    1 if active else 0, int(state.get("rocket_cycle", 0)), old_post,
                                    key_override=key)

        # Mark every collected post as seen, including irrelevant/out-of-region posts.
        # This prevents repeatedly analysing the same unrelated posts on each scan.
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
    logger.info("Мониторинг запущен. Интервал сканирования %s сек.; окно анализа %s ч.; повтор доставки каждые %s сек.",
                CHECK_INTERVAL, HISTORY_WINDOW_HOURS, RETRY_INTERVAL_SECONDS)
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
    locations = state.get("active_locations") or []
    rocket = "🟥 Активна" if state.get("rocket_active") else "🟢 Не объявлена"
    if locations:
        territory_line = "Активные территории: " + html.escape(", ".join(locations))
    else:
        territory_line = "Активных территорий в сохранённом статусе нет."
    return ("<b>UAV ALERT — текущий статус</b>\n\n" + html.escape(level_title(level)) +
            f"\n{territory_line}\nПоследнее изменение: {format_time(state.get('last_uav_time'))}\n\n" +
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
    lines += ["", "Фильтр событий: Костромская область, её города, районы и муниципальные округа.",
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
                    "channel_id": CHANNEL_ID, "publication_mode": "admin_dm_draft", "last_scan_time": state.get("last_scan_time")}), 200

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
