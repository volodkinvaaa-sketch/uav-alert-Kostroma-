import os
import re
import json
import html
import asyncio
import hashlib
import logging
import threading
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest, RetryAfter
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters

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
CHECK_INTERVAL = env_int("CHECK_INTERVAL", 30, 15)
HISTORY_WINDOW_HOURS = env_int("HISTORY_WINDOW_HOURS", 5, 1)
UAV_REMINDER_HOURS = env_int("UAV_REMINDER_HOURS", 5, 1)
OFFICIAL_ALERT_RECHECK_HOURS = env_int("OFFICIAL_ALERT_RECHECK_HOURS", 5, 1)
REQUEST_TIMEOUT = env_int("REQUEST_TIMEOUT", 15, 5)
SOURCE_LIMIT = env_int("SOURCE_LIMIT", 100, 30)
DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
# Целевой канал для готовых постов. Бот присылает черновик администратору в личку,
# а публикация в @RADAR_Kostroma выполняется вручную после проверки.
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip() or "@RADAR_Kostroma"
SUPPORT_URL = "https://pay.cloudtips.ru/p/397e035c"
KOSTROMA_LAT = 58.55
KOSTROMA_LON = 41.26
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
ADMIN_AUDIT_FILE = json_path("admin_audit.json")
admin_audit = load_json(ADMIN_AUDIT_FILE, [])
if not isinstance(admin_audit, list):
    admin_audit = []
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
def save_admin_audit():
    global admin_audit
    admin_audit = admin_audit[-1000:]
    save_json(ADMIN_AUDIT_FILE, admin_audit)

def add_admin_audit(action, details, user_id=None):
    admin_audit.append({"time": now_msk().isoformat(timespec="seconds"), "action": str(action)[:100],
                        "details": str(details)[:1500], "user_id": user_id})
    save_admin_audit()

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
# Автоматически проверяются только официальные сайты на заголовки публикаций
# о предупреждениях/отбое. Неофициальные каналы наблюдения за БПЛА не сканируются.
SOURCES = {}
OFFICIAL_MONITOR_SOURCES = [
    ("Администрация Костромской области", "https://adm44.ru/"),
    ("ГУ МЧС России по Костромской области", "https://44.mchs.gov.ru/"),
    ("Департамент региональной безопасности Костромской области", "https://drb.kostroma.gov.ru/"),
]
OFFICIAL_ALERT_TERMS = re.compile(
    r"(?:бпла|беспилот\w*|беспилотн\w*|ракетн\w*\s+(?:опасност\w*|тревог\w*)|"
    r"угроз\w*\s+ракетн\w*|ракет\w*\s+опасност\w*)", re.I
)
OFFICIAL_STATUS_TERMS = re.compile(
    r"(?:опасност\w*|угроз\w*|отбо\w*|внимани\w*|тревог\w*|отмен\w*|снят\w*)", re.I
)

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


def build_uav_reminder_push(level, locations, post):
    """Информационное напоминание через 10 часов; не объявляет угрозу официально."""
    territory = ", ".join(locations) if locations else "Костромская область"
    return (
        f"<b>⏳ Сохранение статуса по БПЛА: {html.escape(level_title(level))}</b>\n\n"
        f"Территория: {html.escape(territory)}\n"
        f"Напоминание через {UAV_REMINDER_HOURS} ч. после исходного сообщения.\n\n"
        f"{source_line(post)}\n\n"
        "В доступных отслеживаемых сообщениях отбой не обнаружен. Это не подтверждение текущей обстановки: сверяйтесь с официальными оповещениями региона."
    )


def build_uav_reminder_draft(level, locations, post):
    territory = ", ".join(locations) if locations else "Костромская область"
    category = uav_category_label(post)
    lines = [
        "🛰 UAV ALERT",
        f"📍 Регион: {territory}",
        "",
        f"⏳ Сохранение статуса по БПЛА: {level_title(level)}",
        f"Категория: {category}",
        f"Время напоминания: {now_msk().strftime('%d.%m.%Y %H:%M МСК')}",
        f"Исходное сообщение: {format_time(post.get('date'))}",
        "",
        f"Источник: {post.get('source_name') or 'публичный источник'}",
    ]
    if post.get("url"):
        lines.append(f"Ссылка на источник: {post['url']}")
    lines.extend([
        "",
        f"Через {UAV_REMINDER_HOURS} ч. после исходного сообщения в доступных отслеживаемых источниках отбой не обнаружен. Это не подтверждение текущей обстановки; сверяйтесь с официальными оповещениями региона.",
        "Информация собрана из публичных сообщений и не заменяет официальные оповещения.",
    ])
    return "\n".join(lines)


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
        "created_at": record.get("created_at") or now_msk().isoformat(timespec="seconds"),
        "attempted_at": now_msk().isoformat(timespec="seconds"),
        "delivered_users": sorted(delivered_users),
    })
    notification_events[key] = record
    save_notification_events()

    # Администратор получает служебные/официальные push независимо от подписки.
    # Пользователи получают их только если подписались на рассылку.
    recipients = set(subscribers)
    if ADMIN_ID > 0:
        recipients.add(ADMIN_ID)

    if not recipients:
        record.update({"sent": False, "sent_count": len(delivered_users), "failed_count": 0,
                       "error": "Нет получателей; событие сохранено для повторной проверки."})
        notification_events[key] = record
        save_notification_events()
        logger.warning("Push %s пока не отправлен: нет получателей.", event_type)
        return len(delivered_users)

    dead_users = []
    errors = []
    for user_id in sorted(recipients):
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

    failed_count = sum(1 for user_id in recipients if user_id not in delivered_users)
    notification_events[key] = {
        "sent": failed_count == 0 and bool(recipients),
        "sent_count": len(delivered_users.intersection(recipients)),
        "failed_count": failed_count,
        "delivered_users": sorted(delivered_users),
        "created_at": record.get("created_at") or now_msk().isoformat(timespec="seconds"),
        "attempted_at": now_msk().isoformat(timespec="seconds"),
        "event": event_type, "level": int(level), "cycle": int(cycle),
        "post": post.get("key"), "post_data": post, "message": message, "key": key,
        "error": "; ".join(errors[:10]) if errors else None,
    }
    save_notification_events()
    logger.info("Push %s: доставлено %s; осталось %s", event_type,
                len(delivered_users.intersection(recipients)), failed_count)
    return len(delivered_users.intersection(recipients))

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
                   "created_at": record.get("created_at") or now_msk().isoformat(timespec="seconds"),
                   "attempted_at": now_msk().isoformat(timespec="seconds")})
    admin_drafts[key] = record
    save_admin_drafts()
    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=("📝 ГОТОВЫЙ ПОСТ ДЛЯ ТГК @RADAR_Kostroma\n"
                  "Скопируй текст ниже и опубликуй после проверки:\n\n" + message),
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
            "key": key, "created_at": record.get("created_at") or now_msk().isoformat(timespec="seconds"),
            "attempted_at": now_msk().isoformat(timespec="seconds"), "error": None,
        }
        save_channel_posts()
        logger.info("Событие %s опубликовано в канале %s.", event_type, CHANNEL_ID)
        return True
    except TelegramError as exc:
        channel_posts[key] = {
            "sent": False, "channel": CHANNEL_ID, "event": event_type,
            "level": int(level), "cycle": int(cycle), "source_post": post.get("key"),
            "message": message, "post_data": post, "key": key,
            "created_at": record.get("created_at") or now_msk().isoformat(timespec="seconds"),
            "attempted_at": now_msk().isoformat(timespec="seconds"), "error": str(exc)[:500],
        }
        save_channel_posts()
        logger.exception("Не удалось опубликовать %s в канале %s. Проверь права администратора бота.", event_type, CHANNEL_ID)
        return False

async def retry_pending_notifications(application):
    """Повторяет все недоставленные push, черновики администратора и публикации канала."""
    total_delivered = 0
    for key, record in list(notification_events.items()):
        if not isinstance(record, dict) or record.get("sent"):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            # Старые записи, созданные до сохранения текста, безопасно восстановить нельзя.
            continue
        attempted = parse_datetime(record.get("created_at"))
        if attempted is None or now_msk() - attempted > timedelta(minutes=30):
            # Предупреждение может устареть: старые push автоматически не отправляем.
            continue
        post = record.get("post_data") or {"key": record.get("post") or ""}
        total_delivered += await send_push(
            application, message, event_type, int(record.get("level", 0)),
            int(record.get("cycle", 0)), post, key_override=key,
        )

    for key, record in list(admin_drafts.items()):
        if not isinstance(record, dict) or record.get("sent"):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            continue
        attempted = parse_datetime(record.get("created_at"))
        if attempted is None or now_msk() - attempted > timedelta(minutes=30):
            continue
        post = record.get("post_data") or {"key": record.get("source_post") or ""}
        await send_admin_post_draft(
            application, message, event_type, int(record.get("level", 0)),
            int(record.get("cycle", 0)), post, key_override=key,
        )

    for key, record in list(channel_posts.items()):
        if not isinstance(record, dict) or record.get("sent"):
            continue
        message = record.get("message")
        event_type = record.get("event")
        if not message or not event_type:
            continue
        attempted = parse_datetime(record.get("created_at"))
        if attempted is None or now_msk() - attempted > timedelta(minutes=30):
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
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=("🛰 <b>UAV ALERT</b>\n\nПодписка активирована. В боте доступны погодные сведения, памятки безопасности, полезные контакты и проверенные сообщения, которые администратор вручную проверяет перед рассылкой.\n\nАвтоматическая проверка официальных сайтов на заголовки предупреждений/отбоя включена; сбор сведений о местах фиксации и маршрутах БПЛА отключён. Публикация в канал — после проверки администратором."),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except TelegramError:
        logger.exception("Не удалось отправить приветствие пользователю %s", user_id)


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
    """Обновляет каждую названную территорию отдельно; отбой не сбрасывает соседние районы."""
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
    changed = False

    if new_level <= 0:
        # Явный общий отбой по области очищает все активные территории; локальный — только названные.
        if "Костромская область" in locations:
            targets = [name for name, item in territories.items() if int(item.get("level", 0)) > 0]
        else:
            targets = [name for name in locations if name in territories and int(territories[name].get("level", 0)) > 0]
        if not targets:
            logger.info("Отбой найден, но для указанной территории активного статуса нет.")
            return False

        for territory in targets:
            item = territories.setdefault(territory, {})
            cycle = int(item.get("cycle", 0)) + 1
            item.update({"level": 0, "cycle": cycle, "last_time": now,
                         "last_source": post.get("source_name"), "last_post_url": post.get("url"),
                         "last_post_key": post.get("key"), "last_post_date": post.get("date"),
                         "last_post_text": post.get("text", ""), "last_event_type": "uav_cancel",
                         "reminder_due_at": None, "reminder_sent_at": None, "reminder_key": None})
            event_post = dict(post)
            event_post["key"] = f"{post.get('key', '')}|territory:{territory}"
            key = notification_key("uav_cancel", 0, cycle, event_post)
            item["last_notification_key"] = key
            state["last_uav_notification_key"] = key
            state["last_uav_event_type"] = "uav_cancel"
            state["uav_cycle"] = max(int(state.get("uav_cycle", 0)), cycle)
            add_history("uav_cancel", f"Отбой по опасности БПЛА — {territory}", post)
            await send_push(application, build_uav_cancel_push(post, [territory]), "uav_cancel", 0, cycle, event_post, key_override=key)
            await send_admin_post_draft(application, build_uav_admin_draft(0, [territory], post),
                                        "uav_cancel", 0, cycle, event_post, key_override=key)
            changed = True
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
            event_type = "uav_active" if old_level == 0 else "uav_update"
            event_post = dict(post)
            event_post["key"] = f"{post.get('key', '')}|territory:{territory}"
            key = notification_key(event_type, int(new_level), cycle, event_post)
            announced_at = parse_datetime(post.get("date")) or now_msk()
            reminder_due = (announced_at + timedelta(hours=UAV_REMINDER_HOURS)).isoformat(timespec="seconds")
            item.update({"level": int(new_level), "cycle": cycle, "last_time": now,
                         "last_source": post.get("source_name"), "last_post_url": post.get("url"),
                         "last_post_key": post.get("key"), "last_post_date": post.get("date"),
                         "last_post_text": post.get("text", ""), "last_event_type": event_type,
                         "last_notification_key": key, "reminder_due_at": reminder_due,
                         "reminder_sent_at": None, "reminder_key": None})
            state["last_uav_notification_key"] = key
            state["last_uav_event_type"] = event_type
            state["uav_cycle"] = max(int(state.get("uav_cycle", 0)), cycle)
            add_history(event_type, f"{level_title(int(new_level))} — {territory}", post)
            await send_push(application, build_uav_push(int(new_level), [territory], post),
                            event_type, int(new_level), cycle, event_post, key_override=key)
            await send_admin_post_draft(application, build_uav_admin_draft(int(new_level), [territory], post),
                                        event_type, int(new_level), cycle, event_post, key_override=key)
            changed = True

    sync_uav_summary()
    # Legacy summary fields remain available for health endpoints and old clients.
    state["last_uav_time"] = now
    state["last_uav_source"] = post.get("source_name")
    state["last_uav_post"] = post.get("url")
    state["last_uav_post_key"] = post.get("key")
    state["last_uav_post_date"] = post.get("date")
    state["last_uav_post_text"] = post.get("text", "")
    save_state()
    if not changed:
        await retry_pending_notifications(application)
    return changed


async def process_due_uav_reminders(application):
    """Отправляет сохранённое напоминание через N часов, только пока событие активно."""
    territories = state.get("uav_territories") or {}
    now = now_msk()
    changed = False
    for territory, item in list(territories.items()):
        try:
            level = int(item.get("level", 0))
        except (TypeError, ValueError):
            level = 0
        if level not in (1, 2, 3):
            continue
        if item.get("reminder_sent_at"):
            continue

        due = parse_datetime(item.get("reminder_due_at"))
        if due is None:
            # Миграция ранее сохранённых активных событий: считаем 10 часов от их last_time.
            started = parse_datetime(item.get("last_time"))
            due = started + timedelta(hours=UAV_REMINDER_HOURS) if started else now + timedelta(hours=UAV_REMINDER_HOURS)
            item["reminder_due_at"] = due.isoformat(timespec="seconds")
            changed = True
        if now < due:
            continue

        post = {
            "key": item.get("last_post_key") or "",
            "source_name": item.get("last_source") or "Публичный источник",
            "url": item.get("last_post_url") or "",
            "date": item.get("last_post_date") or item.get("last_time"),
            "text": item.get("last_post_text") or "",
        }
        # В ключ включаем территорию, цикл и уровень: повторная отправка после рестарта не дублируется.
        reminder_post = dict(post)
        reminder_post["key"] = f"{post.get('key', '')}|reminder:{territory}:{item.get('cycle', 0)}:{level}"
        key = item.get("reminder_key") or notification_key("uav_reminder", level, int(item.get("cycle", 0)), reminder_post)
        item["reminder_key"] = key
        message = build_uav_reminder_push(level, [territory], post)
        draft = build_uav_reminder_draft(level, [territory], post)
        await send_push(application, message, "uav_reminder", level, int(item.get("cycle", 0)), reminder_post, key_override=key)
        await send_admin_post_draft(application, draft, "uav_reminder", level, int(item.get("cycle", 0)), reminder_post, key_override=key)
        push_done = bool(notification_events.get(key, {}).get("sent"))
        draft_done = bool(admin_drafts.get(key, {}).get("sent"))
        if push_done and draft_done:
            item["reminder_sent_at"] = now_msk().isoformat(timespec="seconds")
            changed = True
        else:
            logger.warning("Напоминание по территории %s пока не завершено; повторим при следующей проверке.", territory)
    if changed:
        save_state()


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
# MONITORING — OFFICIAL WARNING HEADLINES ONLY
# ============================================================
def _fetch_official_alert_headlines(source_name, source_url):
    """Reads headline links from an official regional site; never extracts routes/locations."""
    response = requests.get(
        source_url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "UAV-ALERT-Civil-Info/1.0 (+official-publication-check)"},
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    soup = BeautifulSoup(response.text, "html.parser")
    results = []
    seen = set()
    for anchor in soup.select("a[href]"):
        title = normalize_text(anchor.get_text(" ", strip=True))
        if not title or len(title) < 8:
            continue
        # Only official-warning/cancellation headlines, not sighting or movement reports.
        if not OFFICIAL_ALERT_TERMS.search(title) or not OFFICIAL_STATUS_TERMS.search(title):
            continue
        url = requests.compat.urljoin(response.url, anchor.get("href", ""))
        parsed = urlparse(url)
        root_host = (urlparse(source_url).hostname or "").lower()
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (host == root_host or host.endswith("." + root_host)):
            continue
        key = hashlib.sha256((url + "|" + title).encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        results.append({"key": key, "title": anchor.get_text(" ", strip=True)[:220],
                        "url": url, "source": source_name})
    return results[:20]


def classify_official_alert_title(title):
    """Classifies explicit warning/cancellation wording in an official-site headline."""
    value = normalize_text(title)
    if detect_rocket_cancel(value):
        return "rocket_cancel", 0
    if detect_rocket(value):
        return "rocket_active", 1
    if detect_uav_cancel(value):
        return "uav_cancel", 0
    level = detect_uav_level(value)
    if level in (1, 2, 3):
        return f"uav_level_{level}", level
    return None


async def process_official_alert_title(application, item):
    """Pushes new, explicit official warning/cancellation headlines to subscribers and admin."""
    classified = classify_official_alert_title(item.get("title", ""))
    if not classified:
        return
    event_type, level = classified
    post = {
        "key": item.get("key", ""),
        "source_name": item.get("source", "официальный сайт"),
        "url": item.get("url", ""),
        "date": now_msk().isoformat(timespec="seconds"),
        "text": item.get("title", ""),
    }
    if event_type == "rocket_active":
        message = build_rocket_push(True, post)
    elif event_type == "rocket_cancel":
        message = build_rocket_push(False, post)
    elif event_type == "uav_cancel":
        message = build_uav_cancel_push(post, ["Костромская область"])
    else:
        message = build_uav_push(level, ["Костромская область"], post)

    # Stable key: same official headline cannot create duplicate pushes after restart.
    daily_title_key = normalize_text(item.get("title", ""))
    key_override = hashlib.sha256(
        f"official-alert|{event_type}|{daily_title_key}|{now_msk().date().isoformat()}".encode("utf-8")
    ).hexdigest()
    await send_push(application, message, event_type, level, 0, post, key_override=key_override)

    active = source_state.setdefault("official_active_alerts", {})
    alert_key = f"{event_type}|{hashlib.sha256(normalize_text(item.get('title', '')).encode('utf-8')).hexdigest()}"
    if event_type in ("uav_level_1", "uav_level_2", "uav_level_3", "rocket_active"):
        active[alert_key] = {
            "event_type": event_type, "level": level,
            "source": post["source_name"], "title": post["text"], "url": post["url"],
            "last_admin_recheck": now_msk().isoformat(timespec="seconds"),
        }
    elif event_type == "uav_cancel":
        for key, old in list(active.items()):
            if str(old.get("event_type", "")).startswith("uav_level_"):
                active.pop(key, None)
    elif event_type == "rocket_cancel":
        for key, old in list(active.items()):
            if old.get("event_type") == "rocket_active":
                active.pop(key, None)
    save_source_state()


async def send_due_official_rechecks(application):
    """Every five hours, remind only the admin to verify a previously seen warning."""
    active = source_state.get("official_active_alerts", {})
    if not isinstance(active, dict):
        return
    now = now_msk()
    changed = False
    for key, item in list(active.items()):
        last_check = parse_datetime(item.get("last_admin_recheck"))
        if last_check and now - last_check < timedelta(hours=OFFICIAL_ALERT_RECHECK_HOURS):
            continue
        url = str(item.get("url", ""))
        source = html.escape(str(item.get("source", "официальный сайт")))
        title = html.escape(str(item.get("title", "")))
        url_html = html.escape(url, quote=True)
        message = (
            "⏰ <b>Плановая перепроверка официального предупреждения</b>\n\n"
            f"Заголовок: {title}\n"
            f"Источник: <a href=\"{url_html}\">{source}</a>\n\n"
            "Проверьте полную публикацию: действует ли предупреждение или опубликован отбой. "
            "Старая публикация сама по себе не подтверждает текущую угрозу. Это напоминание только администратору."
        )
        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID, text=message, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            item["last_admin_recheck"] = now.isoformat(timespec="seconds")
            changed = True
        except TelegramError:
            logger.exception("Не удалось отправить напоминание о перепроверке официального предупреждения.")
    if changed:
        save_source_state()


async def _check_official_source(application, source_name, source_url):
    try:
        headlines = await asyncio.to_thread(
            _fetch_official_alert_headlines, source_name, source_url
        )
        record = source_state.setdefault("official_monitor", {})
        source_record = record.setdefault(source_url, {"initialized": False, "seen": []})
        old_seen = set(source_record.get("seen", []))
        new_items = [item for item in headlines if item["key"] not in old_seen]
        was_initialized = bool(source_record.get("initialized"))

        # На первом запуске старые заголовки показываются администратору для проверки,
        # но не рассылаются подписчикам как будто это новое предупреждение.
        if not was_initialized:
            candidates = headlines[:5]
            source_record["initialized"] = True
        else:
            candidates = new_items

        for item in candidates:
            message = (
                "📡 <b>Новая публикация на официальном ресурсе</b>\n\n"
                f"Источник: {html.escape(item['source'])}\n"
                f"Заголовок: {html.escape(item['title'])}\n\n"
                f"<a href=\"{html.escape(item['url'], quote=True)}\">Открыть официальную публикацию</a>\n\n"
                "Проверьте полную публикацию. В канал автоматически ничего не публикуется."
            )
            try:
                await application.bot.send_message(
                    chat_id=ADMIN_ID, text=message, parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            except TelegramError:
                logger.exception("Не удалось отправить администратору ссылку на официальную публикацию.")
            # On the first scan, old headlines establish a baseline and go to admin for review only.
            # Push subscribers only for a newly discovered headline after initialization.
            if was_initialized and item in new_items:
                await process_official_alert_title(application, item)

        source_record["seen"] = list(dict.fromkeys(
            list(source_record.get("seen", [])) + [item["key"] for item in headlines]
        ))[-500:]
        source_record["last_check"] = now_msk().isoformat(timespec="seconds")
        source_record["last_error"] = None
        save_source_state()
        return len(headlines), None
    except Exception as exc:
        record = source_state.setdefault("official_monitor", {}).setdefault(
            source_url, {"initialized": False, "seen": []}
        )
        record["last_check"] = now_msk().isoformat(timespec="seconds")
        record["last_error"] = str(exc)[:300]
        save_source_state()
        logger.warning("Официальный источник недоступен (%s): %s", source_name, exc)
        return 0, str(exc)


async def check_sources(application):
    """Проверяет официальные сайты на заголовки предупреждений и отбоя.

    Не сканирует каналы фиксации БПЛА и не собирает сведения о маршрутах/местах.
    Новые явные официальные предупреждения отправляются push подписчикам и админу;
    в канал автоматической публикации нет.
    """
    results = await asyncio.gather(*(
        _check_official_source(application, name, url)
        for name, url in OFFICIAL_MONITOR_SOURCES
    ), return_exceptions=True)
    state["last_scan_time"] = now_msk().isoformat(timespec="seconds")
    state["last_scan_count"] = sum(
        result[0] for result in results if isinstance(result, tuple)
    )
    state["official_sources_available"] = sum(
        1 for result in results if isinstance(result, tuple) and result[1] is None
    )
    state["official_sources_total"] = len(OFFICIAL_MONITOR_SOURCES)
    save_state()
    await send_due_official_rechecks(application)
    await retry_pending_notifications(application)


async def monitor_loop(application):
    logger.info("Запущена проверка официальных сайтов на заголовки предупреждений/отбоя; автопубликация отключена.")
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
        [InlineKeyboardButton("📣 Официальные сообщения", callback_data="official"), InlineKeyboardButton("🌦 Погода", callback_data="weather")],
        [InlineKeyboardButton("🧭 Памятка безопасности", callback_data="safety"), InlineKeyboardButton("☎️ Полезные контакты", callback_data="contacts")],
        [InlineKeyboardButton("📰 Официальные ресурсы", callback_data="news"), InlineKeyboardButton("💚 Поддержать проект", url=SUPPORT_URL)],
        [InlineKeyboardButton("🔔 Подписаться", callback_data="subscribe"), InlineKeyboardButton("🔕 Отписаться", callback_data="unsubscribe")],
        [InlineKeyboardButton("📚 История", callback_data="history"), InlineKeyboardButton("ℹ️ О проекте", callback_data="help")],
    ])



def format_status():
    lines = [
        "<b>🛰 UAV ALERT — информационный сервис</b>", "",
        "Автоматически проверяются только заголовки публикаций официальных сайтов. Сведения о местах фиксации и маршрутах БПЛА не собираются. Публикация в канал — только после проверки администратором.", "",
        f"Последняя проверка сервиса: {format_time(state.get('last_scan_time'))}",
        f"Официальных источников настроено: {len(OFFICIAL_MONITOR_SOURCES)}/{len(OFFICIAL_MONITOR_SOURCES)}",
        f"Доступность при последней проверке: {state.get('official_sources_available', 0)}/{state.get('official_sources_total', len(OFFICIAL_MONITOR_SOURCES))}", "",
        "<b>Состояние сайтов:</b>"
    ]
    records = source_state.get("official_monitor", {})
    for name, url in OFFICIAL_MONITOR_SOURCES:
        item = records.get(url, {}) if isinstance(records, dict) else {}
        if item.get("last_error"):
            status = "🔴 ошибка проверки"
            detail = str(item.get("last_error"))[:110]
        elif item.get("last_check"):
            status = "🟢 проверка прошла"
            detail = format_time(item.get("last_check"))
        else:
            status = "⚪ ещё не проверялся"
            detail = "нет результата"
        lines.append(f"• <b>{html.escape(name)}</b>: {status} — {html.escape(detail)}")
    lines.extend(["", "<i>Успешная загрузка сайта не гарантирует полноту его публикаций. Бот не является официальной системой оповещения. В экстренной ситуации следуйте указаниям уполномоченных служб.</i>"])
    return "\n".join(lines)



def format_history(limit=10):
    visible_history = [item for item in history
                       if not re.search(r"бпла|беспилот|фиксац", str(item.get("title", item.get("event", ""))), re.I)]
    if not visible_history:
        return "<b>📚 История UAV ALERT</b>\n\nНовых проверенных сообщений пока нет."
    lines = ["<b>📚 Последние сообщения UAV ALERT</b>", ""]
    for item in reversed(visible_history[-limit:]):
        lines.append(f"• <b>{html.escape(str(item.get('title', item.get('event', 'Событие'))))}</b>")
        lines.append(f"  {format_time(item.get('time'))}")
        if item.get("source"):
            lines.append(f"  Источник: {html.escape(str(item['source']))}")
        if item.get("url"):
            lines.append(f'  <a href="{html.escape(str(item["url"]), quote=True)}">Открыть публикацию</a>')
        lines.append("")
    return "\n".join(lines)


def format_sources():
    return ("<b>📰 Официальные ресурсы</b>\n\n"
            '<a href="https://adm44.ru/">Администрация Костромской области</a>\n'
            '<a href="https://44.mchs.gov.ru/">Главное управление МЧС России по Костромской области</a>\n'
            '<a href="https://drb.kostroma.gov.ru/">Департамент региональной безопасности Костромской области</a>\n'
            '<a href="https://meteoinfo.ru/">Гидрометцентр России — погодная информация</a>\n\n'
            "Для сообщений о безопасности ориентируйтесь на официальные публикации уполномоченных органов. Ссылки на сторонние каналы не используются как официальные источники UAV ALERT.")



def format_stats():
    return ("<b>📊 Статистика UAV ALERT</b>\n\n" +
            f"Подписчиков: {len(subscribers)}\nЗаписей истории: {len(history)}\n" +
            f"Проверенных публикаций в базе: {len(sent_posts)}\nСобытий уведомлений: {len(notification_events)}\n" +
            "Официальные сайты проверяются автоматически на заголовки предупреждений/отбоя; сведения о местах фиксации и маршрутах БПЛА не собираются. Публикация — после проверки администратором.")


# ============================================================
# COMMANDS / CALLBACKS
# ============================================================
def is_admin(update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


# Для проверки и мониторинга используются только официальные сайты уполномоченных органов.
async def review_sources_command(update, context):
    """Показывает администратору список официальных сайтов мониторинга."""
    message = update.effective_message
    if not message:
        return

    if not is_admin(update):
        await message.reply_text("⛔ Команда доступна только администратору.")
        return

    lines = [
        "📡 Официальные источники UAV ALERT",
        "",
        "Мониторинг проверяет заголовки официальных сайтов на сообщения о предупреждениях и отбое:",
        "",
    ]
    lines.extend(f"{name}: {url}" for name, url in OFFICIAL_MONITOR_SOURCES)
    lines.extend([
        "",
        "Бот отправляет найденные публикации администратору для проверки.",
        "Автоматической публикации в канал нет. Сведения о местах обнаружения и маршрутах БПЛА не собираются.",
        "Официальные экстренные оповещения имеют приоритет перед этим сервисом.",
    ])
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)

async def official_sms_review_handler(update, context):
    """Сохраняет вручную переданный администратором текст SMS 112 для проверки; ничего не публикует автоматически."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or user.id != ADMIN_ID:
        return
    if not chat or chat.type != "private" or not message.text:
        return
    raw_text = message.text.strip()
    normalized = re.sub(r"\s+", " ", raw_text.casefold()).replace("ё", "е")
    sender_phrase = "служба 112 костромской области"
    threat_phrase = "угроза беспилотной опасности"
    cancel_phrase = "отбой беспилотной опасности"
    has_sender = sender_phrase in normalized
    if threat_phrase in normalized and cancel_phrase not in normalized:
        signal_type = "УГРОЗА"
        signal_label = "распознана формулировка об угрозе беспилотной опасности"
    elif cancel_phrase in normalized and threat_phrase not in normalized:
        signal_type = "ОТБОЙ"
        signal_label = "распознана формулировка об отбое беспилотной опасности"
    else:
        return
    received_at = now_msk().isoformat(timespec="seconds")
    # Хеш без секундной метки предотвращает случайные дубли при повторной пересылке одного SMS.
    digest = hashlib.sha256(f"sms112|{signal_type}|{normalized}".encode("utf-8")).hexdigest()[:24]
    key = "sms112_" + digest
    existing = admin_drafts.get(key)
    if isinstance(existing, dict) and existing.get("reviewed_at"):
        await message.reply_text("ℹ️ Такое SMS уже сохранено для проверки. Статус и публикации не менялись.")
        return
    review_text = (
        "📩 ВНУТРЕННЯЯ ПРОВЕРКА SMS — СЛУЖБА 112 КОСТРОМСКОЙ ОБЛАСТИ\n\n"
        f"Сигнал: {signal_type}\nВремя получения ботом: {format_time(received_at)}\n"
        f"Отправитель указан в тексте: {'да' if has_sender else 'нет; текст введён/переслан администратором'}\n\n"
        f"Результат распознавания: {signal_label}.\n\n"
        "Переданный текст:\n" + raw_text[:1500] + "\n\n"
        "Это только дополнительный сигнал для ручной проверки. Статус UAV ALERT автоматически НЕ изменён; районы по SMS не определяются; в канал сообщение не опубликовано. Проверьте исходное SMS и актуальные официальные сообщения перед любым публичным использованием."
    )
    admin_drafts[key] = {"sent": True, "admin_id": ADMIN_ID, "event": "official_sms_review",
                         "signal_type": signal_type, "message": review_text, "raw_sms_text": raw_text[:1500],
                         "attempted_at": received_at, "reviewed_at": received_at, "key": key,
                         "automatic_status_change": False, "automatic_channel_publication": False}
    save_admin_drafts()
    add_admin_audit("sms112_review", f"Сигнал {signal_type}; sender_in_text={has_sender}; key={key}", user.id)
    await message.reply_text(review_text, disable_web_page_preview=True)
    logger.info("Получен ручной сигнал SMS 112 для проверки: %s; статус не менялся.", signal_type)


async def start_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("🛰 <b>UAV ALERT</b>\nГражданский информационный сервис Костромской области. Здесь доступны погодные сведения, памятки безопасности и проверенные официальные сообщения. Автоматическая проверка официальных сайтов на заголовки предупреждений/отбоя включена; сбор сведений о местах фиксации и маршрутах БПЛА отключён. Публикация в канал — после проверки администратором.\n\nВ экстренной ситуации следуйте указаниям уполномоченных служб.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard(), disable_web_page_preview=True)


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


async def health_command(update, context):
    message = update.effective_message
    if not message:
        return
    if not is_admin(update):
        await message.reply_text("⛔ Команда доступна только администратору.")
        return
    records = source_state.get("official_monitor", {})
    lines = ["🩺 ДИАГНОСТИКА UAV ALERT", "", f"Последний цикл: {format_time(state.get('last_scan_time'))}",
             f"Доступно сайтов: {state.get('official_sources_available', 0)}/{state.get('official_sources_total', len(OFFICIAL_MONITOR_SOURCES))}",
             f"Подписчиков: {len(subscribers)}", f"Недоставленных push-записей: {sum(1 for x in notification_events.values() if isinstance(x, dict) and not x.get('sent'))}",
             f"Неотправленных черновиков: {sum(1 for x in admin_drafts.values() if isinstance(x, dict) and not x.get('sent'))}",
             f"Записей аудита администратора: {len(admin_audit)}", ""]
    for name, url in OFFICIAL_MONITOR_SOURCES:
        item = records.get(url, {}) if isinstance(records, dict) else {}
        lines.append(name)
        lines.append("Последняя проверка: " + format_time(item.get("last_check")))
        lines.append("Ошибка: " + (str(item.get("last_error"))[:250] if item.get("last_error") else "нет"))
        lines.append("")
    lines.append("Повторные попытки доставки ограничены 30 минутами, чтобы не рассылать потенциально устаревшие предупреждения.")
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)


async def admin_command(update, context):
    if not update.effective_message:
        return
    if not is_admin(update):
        await update.effective_message.reply_text("Команда доступна только администратору.")
        return
    await update.effective_message.reply_text("<b>Админ-панель UAV ALERT</b>\n\n" +
        f"Подписчиков: {len(subscribers)}\nАвтоматическая обработка фиксаций БПЛА: отключена\n" +
        f"Последняя проверка: {format_time(state.get('last_scan_time'))}\n" +
        f"Доступность сайтов: {state.get('official_sources_available', 0)}/{state.get('official_sources_total', len(OFFICIAL_MONITOR_SOURCES))}\n" +
        f"Записей аудита: {len(admin_audit)}\n\nКоманда /health показывает подробную диагностику.",
        parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def help_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text("<b>Команды UAV ALERT</b>\n\n/start — главное меню\n/subscribe — подписаться\n/unsubscribe — отписаться\n/status — информация о сервисе\n/history — история\n/sources — официальные ресурсы\n/stats — статистика\n/test — тест (администратор)\n/admin — админ-информация\n/health — диагностика (администратор)\n/broadcast категория | текст | официальная ссылка — ручная проверенная рассылка (администратор)\n/review_sources — дополнительные источники (администратор)\n/help — помощь\n\nАвтоматическая проверка официальных сайтов на заголовки предупреждений/отбоя включена; сбор сведений о местах фиксации и маршрутах БПЛА отключён. Публикация в канал — после проверки администратором.", parse_mode=ParseMode.HTML, reply_markup=main_keyboard())


async def weather_text():
    """Текущая погода и прогноз для Костромы по Open-Meteo; не заменяет штормовые предупреждения."""
    url = ("https://api.open-meteo.com/v1/forecast?latitude=58.55&longitude=41.26"
           "&current=temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&forecast_days=1&timezone=Europe%2FMoscow")
    try:
        response = await asyncio.to_thread(requests.get, url, timeout=10)
        response.raise_for_status()
        data = response.json()
        current = data.get("current", {})
        daily = data.get("daily", {})
        code = current.get("weather_code")
        descriptions = {0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
                        45: "туман", 48: "изморозевый туман", 51: "слабая морось", 53: "морось", 55: "сильная морось",
                        61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
                        80: "ливневый дождь", 81: "ливни", 82: "сильные ливни", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}
        temp = current.get("temperature_2m", "—")
        feels = current.get("apparent_temperature", "—")
        wind = current.get("wind_speed_10m", "—")
        humidity = current.get("relative_humidity_2m", "—")
        high = (daily.get("temperature_2m_max") or ["—"])[0]
        low = (daily.get("temperature_2m_min") or ["—"])[0]
        precip = (daily.get("precipitation_probability_max") or ["—"])[0]
        desc = descriptions.get(code, "условия уточняются")
        return ("<b>🌦 Погода в Костроме</b>\n\n"
                f"Сейчас: {html.escape(str(desc))}\nТемпература: {html.escape(str(temp))} °C\n"
                f"Ощущается как: {html.escape(str(feels))} °C\nВетер: {html.escape(str(wind))} км/ч\n"
                f"Влажность: {html.escape(str(humidity))}%\n\nНа сегодня: {html.escape(str(low))}…{html.escape(str(high))} °C\n"
                f"Вероятность осадков: {html.escape(str(precip))}%\n\n"
                "<i>Прогноз не является официальным экстренным предупреждением. Проверяйте сообщения МЧС и Гидрометцентра.</i>")
    except Exception:
        logger.exception("Не удалось получить погоду")
        return "Не удалось загрузить погоду. Попробуйте позже или проверьте официальные погодные предупреждения."


def safety_text():
    return ("<b>🧭 Памятка безопасности</b>\n\n"
            "• При официальном предупреждении сохраняйте спокойствие и следуйте указаниям властей и экстренных служб.\n"
            "• Не публикуйте фото и видео работы экстренных служб, ПВО, места происшествий или сведения, способные раскрыть их расположение.\n"
            "• Не распространяйте неподтверждённые сообщения и проверяйте дату и первоисточник.\n"
            "• При непосредственной угрозе жизни звоните 112.\n\n"
            "Это общая памятка; конкретные действия зависят от официальных указаний для вашей ситуации.")


def contacts_text():
    return ("<b>☎️ Полезные контакты</b>\n\n"
            "Экстренная помощь: <b>112</b>\n"
            "Пожарные и спасатели: <b>101</b>\n"
            "Полиция: <b>102</b>\n"
            "Скорая помощь: <b>103</b>\n\n"
            '<a href="https://44.mchs.gov.ru/">МЧС по Костромской области</a>\n'
            '<a href="https://adm44.ru/">Администрация Костромской области</a>')


async def broadcast_command(update, context):
    """Ручная рассылка только после проверки категории и официальной ссылки.

    Формат: /broadcast Категория | текст сообщения | https://официальный-домен/...
    Автоматический сбор и публикация сообщений о перемещении БПЛА не выполняются.
    """
    message = update.effective_message
    if not message:
        return
    if not is_admin(update):
        await message.reply_text("Команда доступна только администратору.")
        return

    raw = (message.text or "").partition(" ")[2].strip()
    parts = [part.strip() for part in raw.split("|", 2)]
    if len(parts) != 3 or not all(parts):
        await message.reply_text(
            "Формат команды:\n"
            "/broadcast Категория | текст сообщения | ссылка\n\n"
            "Категории: Внимание, Угроза, Опасность, Отбой, Официальная информация.\n"
            "Ссылка должна вести на официальный ресурс МЧС или властей."
        )
        return

    category, body, source_url = parts
    allowed_categories = {
        "внимание": "🟡 Внимание",
        "угроза": "🟠 Угроза",
        "опасность": "🔴 Опасность",
        "отбой": "🟢 Отбой",
        "официальная информация": "📣 Официальная информация",
    }
    category_label = allowed_categories.get(normalize_text(category))
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    official_domains = ("adm44.ru", "44.mchs.gov.ru", "mchs.gov.ru", "gov.ru")
    is_official_link = parsed.scheme == "https" and any(
        host == domain or host.endswith("." + domain) for domain in official_domains
    )
    if not category_label:
        await message.reply_text("Неизвестная категория. Используй: Внимание, Угроза, Опасность, Отбой или Официальная информация.")
        return
    if not is_official_link:
        await message.reply_text("Не отправлено: укажи HTTPS-ссылку на официальный ресурс МЧС или органов власти.")
        return

    safe_body = html.escape(body)
    safe_url = html.escape(source_url, quote=True)
    announcement = (
        f"<b>🛰 UAV ALERT</b>\n"
        f"{category_label}\n\n"
        f"{safe_body}\n\n"
        f"📡 <a href=\"{safe_url}\">Официальный источник</a>\n\n"
        "<i>Информационное сообщение. Следуйте официальным инструкциям экстренных служб.</i>"
    )
    sent = 0
    failed = 0
    recipients = set(subscribers)
    recipients.add(ADMIN_ID)
    for user_id in sorted(recipients):
        try:
            await context.application.bot.send_message(
                chat_id=user_id, text=announcement, parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            sent += 1
        except TelegramError:
            failed += 1
            logger.exception("Не удалось отправить ручное уведомление пользователю %s", user_id)
    add_admin_audit("manual_broadcast", f"Категория={category_label}; sent={sent}; failed={failed}; ссылка={source_url}", update.effective_user.id if update.effective_user else None)
    await message.reply_text(f"Рассылка завершена. Отправлено: {sent}; ошибок: {failed}.")


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
    elif data == "sources" or data == "news": text = format_sources()
    elif data == "official": text = ("<b>📣 Официальные сообщения</b>\n\n" +
        "Автоматический сбор сведений о местах фиксации и маршрутах БПЛА отключён. Официальные сообщения публикуются только после проверки администратором.\n\n" +
        "При угрозе руководствуйтесь официальными каналами властей и экстренных служб.")
    elif data == "weather": text = await weather_text()
    elif data == "safety": text = safety_text()
    elif data == "contacts": text = contacts_text()
    elif data == "help": text = ("<b>🛰 UAV ALERT</b>\n\nГражданский информационный сервис Костромской области.\n" +
        "Погода, памятки, контакты, ссылки на официальные ресурсы и проверенные сообщения.\n\n" +
        "Автоматическая проверка официальных сайтов на заголовки предупреждений/отбоя включена; сбор сведений о местах фиксации и маршрутах БПЛА отключён. Публикация в канал — после проверки администратором. Бот не является официальной системой оповещения.")
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
    return jsonify({"service": "UAV ALERT", "status": "running", "mode": "civilian_information_service",
                    "uav_spotting_reposts": "disabled", "subscribers": len(subscribers),
                    "channel_id": CHANNEL_ID, "publication_mode": "manual_admin_review", "last_scan_time": state.get("last_scan_time")}), 200

@app.get("/status")
def health_status():
    records = source_state.get("official_monitor", {})
    if not isinstance(records, dict):
        records = {}
    return jsonify({"status": "ok", "uav_spotting_reposts": "disabled",
                    "last_scan_time": state.get("last_scan_time"),
                    "official_sources_available": state.get("official_sources_available", 0),
                    "official_sources_total": state.get("official_sources_total", len(OFFICIAL_MONITOR_SOURCES)),
                    "source_checks": [{"name": name, "last_check": (records.get(url, {}) or {}).get("last_check"),
                                       "ok": not bool((records.get(url, {}) or {}).get("last_error")) and bool((records.get(url, {}) or {}).get("last_check"))}
                                      for name, url in OFFICIAL_MONITOR_SOURCES]}), 200


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
                BotCommand("admin", "Админ-информация"), BotCommand("health", "Диагностика (администратор)"), BotCommand("broadcast", "Рассылка проверенного сообщения"), BotCommand("review_sources", "Дополнительные источники"), BotCommand("help", "Помощь")]
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
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("review_sources", review_sources_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, official_sms_review_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))
    logger.info("Запускаем Telegram polling.")
    application.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
