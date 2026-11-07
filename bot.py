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

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

try:
    from ai_core import analyze_text, ai_status_text, ai_history_text
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


# ============================================================
# UAV ALERT — CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))

KOSTROMA_CHANNEL_ID = os.getenv(
    "CHANNEL_ID", "@RADAR_Kostroma"
).strip()
IVANOVO_CHANNEL_ID = os.getenv(
    "IVANOVO_CHANNEL_ID", "@RADAR_Ivanova"
).strip()

PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(30, int(os.getenv("CHECK_INTERVAL", "60")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))

ALERT_TTL_HOURS = max(1, int(os.getenv("ALERT_TTL_HOURS", "5")))
HISTORY_BACKFILL_HOURS = max(
    1, int(os.getenv("HISTORY_BACKFILL_HOURS", "6"))
)
HISTORY_MAX_PAGES = max(
    1, min(10, int(os.getenv("HISTORY_MAX_PAGES", "6")))
)

DATA_DIR = os.getenv("DATA_DIR", ".")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MSK = timezone(timedelta(hours=3))
ALERT_TTL = timedelta(hours=ALERT_TTL_HOURS)

os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "uav_alert_state.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "uav_alert_subscribers.json")


# ============================================================
# IMAGES — 7 EVENT PHOTOS
# Place all seven files beside bot.py.
# ============================================================

UAV_ATTENTION_IMAGE = os.path.join(BASE_DIR, "uav_attention.jpg")
UAV_DANGER_IMAGE = os.path.join(BASE_DIR, "uav_danger.jpg")
UAV_CANCEL_IMAGE = os.path.join(BASE_DIR, "uav_cancel.jpg")

MWSH_DANGER_IMAGE = os.path.join(BASE_DIR, "mwsh_danger.jpg")
MWSH_CANCEL_IMAGE = os.path.join(BASE_DIR, "mwsh_cancel.jpg")

ROCKET_DANGER_IMAGE = os.path.join(BASE_DIR, "rocket_danger.jpg")
ROCKET_CANCEL_IMAGE = os.path.join(BASE_DIR, "rocket_cancel.jpg")

LEGACY_DANGER_IMAGE = os.path.join(BASE_DIR, "danger.jpg")
LEGACY_CLEAR_IMAGE = os.path.join(BASE_DIR, "all_clear.jpg")

PHOTO_LIST = [
    ("БПЛА: внимание", UAV_ATTENTION_IMAGE),
    ("БПЛА: опасность", UAV_DANGER_IMAGE),
    ("БПЛА: отбой", UAV_CANCEL_IMAGE),
    ("МВШ: опасность", MWSH_DANGER_IMAGE),
    ("МВШ: отбой", MWSH_CANCEL_IMAGE),
    ("Ракетная опасность", ROCKET_DANGER_IMAGE),
    ("Ракетная опасность: отбой", ROCKET_CANCEL_IMAGE),
]


# ============================================================
# REGIONS AND SOURCES
# Public information only. No coordinates, routes, sightings,
# or operational details are extracted or forwarded.
# ============================================================

COMMON_ALERT_SOURCES = [
    ("locatorru", "https://t.me/s/locatorru"),
    ("radarrussiia", "https://t.me/s/radarrussiia"),
    ("bplarussiaru", "https://t.me/s/bplarussiaru"),
]

REGIONS = {
    "kostroma": {
        "name": "Костромская область",
        "short": "Кострома",
        "channel": KOSTROMA_CHANNEL_ID,
        "latitude": 57.7679,
        "longitude": 40.9269,
        "terms": [
            "костром", "нерехт", "буй", "волгоречен",
            "галич", "шарь", "мантур", "чухлом",
            "макарьев", "солигалич", "кологрив", "нея",
            "антропов", "боговаров", "кадый",
            "красное-на-волге", "красносель",
            "межев", "октябрьск", "островск",
            "павин", "парфеньев", "поназырев",
            "пышуг", "сусанин", "судислав",
            "вохом", "варнав", "ветлуг",
        ],
        "alert_sources": COMMON_ALERT_SOURCES,
        "news_sources": [
            ("В курсе Кострома", "https://t.me/s/v_kurse_kostroma"),
        ],
        "official_sources": [
            ("МЧС России по Костромской области", "https://44.mchs.gov.ru/"),
            ("Администрация Костромской области", "https://adm44.ru/"),
            ("МЧС России", "https://mchs.gov.ru/"),
        ],
    },
    "ivanovo": {
        "name": "Ивановская область",
        "short": "Иваново",
        "channel": IVANOVO_CHANNEL_ID,
        "latitude": 56.9972,
        "longitude": 40.9714,
        "terms": [
            "иванов", "кинеш", "шуя", "тейков",
            "фурманов", "родник", "комсомольск",
            "пучеж", "заволж", "южа", "палех",
            "юрьевец", "приволж", "волгоречен",  # kept only as a text term if explicitly mentioned
            "гавай", "лух", "верхний ландех",
            "ивановский район", "шуйский район",
            "кинешma",
        ],
        "alert_sources": COMMON_ALERT_SOURCES + [
            ("Правительство Ивановской области", "https://t.me/s/ivanovoobl"),
        ],
        "news_sources": [
            ("Ивтелерадио", "https://t.me/s/ivteleradio"),
        ],
        "official_sources": [
            ("МЧС России по Ивановской области", "https://37.mchs.gov.ru/"),
            ("Правительство Ивановской области", "https://ivanovoobl.ru/"),
            ("МЧС России", "https://mchs.gov.ru/"),
        ],
    },
}

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


# ============================================================
# TEXT FILTERS AND EVENT CLASSIFIERS
# ============================================================

NEWS_SENSITIVE_RE = re.compile(
    r"бпла|беспилот|дрон|мвш|мрш|пво|зенитн|перехват|"
    r"сбит|облом|координат|маршрут|траектор|"
    r"военн\w*\s+объект|ракетн\w*\s+опасност|"
    r"авиаудар|позици\w*\s+(?:военн|пво)",
    re.I,
)

UAV_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}(?:беспилотн|бпла|опасност|угроз)|"
    r"(?:беспилотн|бпла|опасност|угроз).{0,70}"
    r"(?:отбой|отмен[аы]|снят[аоы]?)",
    re.I | re.S,
)

UAV_DANGER_RE = re.compile(
    r"беспилотн\w*\s+опасност|"
    r"опасност\w*\s+по\s+бпла|"
    r"красн\w*\s+уров",
    re.I,
)

UAV_THREAT_RE = re.compile(
    r"угроз\w*\s+(?:беспилот|бпла)|"
    r"бпла.{0,30}угроз",
    re.I | re.S,
)

UAV_ATTENTION_RE = re.compile(
    r"внимани\w*.{0,30}бпла|"
    r"бпла.{0,30}внимани",
    re.I | re.S,
)

UAV_ALERT_RE = re.compile(
    r"беспилотн\w*\s+опасност|"
    r"опасност\w*\s+по\s+бпла|"
    r"угроз\w*\s+беспилот|"
    r"угроз\w*\s+бпла|"
    r"внимани\w*.{0,30}бпла|"
    r"тревог\w*.{0,30}(?:бпла|беспилот)|"
    r"бпла.{0,40}(?:внимани|угроз|опасност|тревог)|"
    r"опасност\w*.{0,30}(?:беспилот|бпла)",
    re.I | re.S,
)

MWSH_DANGER_RE = re.compile(
    r"(?:опасност\w*.{0,80}\bмвш\b|"
    r"\bмвш\b.{0,80}(?:опасност|угроз)|"
    r"опасност\w*.{0,100}малоразмерн\w*.{0,50}шар|"
    r"малоразмерн\w*.{0,50}шар\w*.{0,80}(?:опасност|угроз))",
    re.I | re.S,
)

MWSH_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,100}(?:\bмвш\b|малоразмерн\w*.{0,50}шар)|"
    r"(?:\bмвш\b|малоразмерн\w*.{0,50}шар)"
    r".{0,100}(?:отбой|отмен[аы]|снят[аоы]?)",
    re.I | re.S,
)

ROCKET_RE = re.compile(
    r"ракетн\w*.{0,50}(?:опасност|угроз|тревог|объявлен)|"
    r"(?:опасност|угроз|тревог).{0,50}ракетн\w*",
    re.I | re.S,
)

ROCKET_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}ракетн\w*|ракетн\w*.{0,70}"
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)",
    re.I | re.S,
)

ROCKET_NEGATION_RE = re.compile(
    r"ракетн\w*.{0,50}(?:не\s+(?:объявлен\w*|введен\w*|"
    r"подтвержден\w*)|отсутствует|нет)|"
    r"(?:нет|не\s+(?:объявлен\w*|введен\w*|"
    r"подтвержден\w*)).{0,50}ракетн\w*",
    re.I | re.S,
)


# ============================================================
# LOGGING AND FLASK HEALTH CHECKS
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("uav-alert")

web_app = Flask(__name__)


def now_msk():
    return datetime.now(MSK)


def normalize(value):
    return re.sub(
        r"\s+",
        " ",
        (value or "").casefold().replace("ё", "е"),
    ).strip()


@web_app.route("/")
def home():
    return jsonify({
        "service": "UAV ALERT",
        "status": "running",
        "regions": {
            key: {
                "name": value["name"],
                "channel": value["channel"],
            }
            for key, value in REGIONS.items()
        },
        "note": "Unofficial civil information service",
    })


@web_app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time_msk": now_msk().isoformat(),
        "monitor_interval_seconds": CHECK_INTERVAL,
        "regions": list(REGIONS.keys()),
        "pending_events": len(state.get("pending", {})),
        "subscribers": {
            region_id: len(subscribers.get(region_id, set()))
            for region_id in REGIONS
        },
    })


def run_web():
    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# ============================================================
# JSON STATE AND MIGRATION
# ============================================================

def parse_iso(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=MSK)
        return result.astimezone(MSK)
    except (TypeError, ValueError):
        return None


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


state = load_json(STATE_FILE, {})
if not isinstance(state, dict):
    state = {}

DEFAULT_STATE = {
    "seen": {},
    "pending": {},
    "approved": {},
    "rejected": {},
    "active_alerts": {},
    "news_seen": {},
    "initialized": {},
    "news_initialized": {},
    "last_errors": {},
    "news_last_errors": {},
    "event_history": [],
    "news_history": [],
    "manual_drafts": {},
    "archive_drafts": {},
    "last_scan": None,
}

for key, default in DEFAULT_STATE.items():
    state.setdefault(key, default)

# Keep old one-region event records under Kostroma when possible.
old_active = state.get("active_alerts", {})
if isinstance(old_active, dict) and old_active:
    if not set(old_active).issubset(set(REGIONS)):
        state["active_alerts"] = {"kostroma": old_active, "ivanovo": {}}
for region_id in REGIONS:
    state.setdefault("active_alerts", {}).setdefault(region_id, {})

# Subscriber migration: old flat list becomes Kostroma subscribers.
raw_subscribers = load_json(SUBSCRIBERS_FILE, [])
if not raw_subscribers:
    raw_subscribers = state.get("subscribers", [])

subscribers = {region_id: set() for region_id in REGIONS}
if isinstance(raw_subscribers, list):
    for value in raw_subscribers:
        try:
            subscribers["kostroma"].add(int(value))
        except (TypeError, ValueError):
            pass
elif isinstance(raw_subscribers, dict):
    for region_id in REGIONS:
        for value in raw_subscribers.get(region_id, []):
            try:
                subscribers[region_id].add(int(value))
            except (TypeError, ValueError):
                pass

# If a prior state already has regional subscriber records, prefer them.
stored_region_subscribers = state.get("regional_subscribers")
if isinstance(stored_region_subscribers, dict):
    for region_id in REGIONS:
        values = stored_region_subscribers.get(region_id, [])
        if values:
            for value in values:
                try:
                    subscribers[region_id].add(int(value))
                except (TypeError, ValueError):
                    pass

startup_backfill_done = set()
monitor_task = None


def save_state():
    state["regional_subscribers"] = {
        region_id: sorted(users)
        for region_id, users in subscribers.items()
    }
    save_json(STATE_FILE, state)
    save_json(
        SUBSCRIBERS_FILE,
        {
            region_id: sorted(users)
            for region_id, users in subscribers.items()
        },
    )


# ============================================================
# REGION MATCHING AND CLASSIFICATION
# ============================================================

def mentions_region(region_id, text):
    value = normalize(text)
    if REGIONS[region_id]["name"].casefold() in value:
        return True
    return any(term in value for term in REGIONS[region_id]["terms"])


def detect_regions(text):
    return [
        region_id
        for region_id in REGIONS
        if mentions_region(region_id, text)
    ]


def classify_mwsh(text):
    value = normalize(text)
    if MWSH_CANCEL_RE.search(value):
        return "mwsh_cancel", "Отбой опасности по МВШ"
    if MWSH_DANGER_RE.search(value):
        return "mwsh_danger", "Опасность по МВШ"
    return None


def classify_rocket(text):
    value = normalize(text)
    if ROCKET_NEGATION_RE.search(value):
        return None
    if ROCKET_CANCEL_RE.search(value):
        return "rocket_cancel", "Отбой ракетной опасности"
    if ROCKET_RE.search(value):
        return "rocket_danger", "Ракетная опасность"
    return None


def classify_uav(text):
    value = normalize(text)
    if UAV_CANCEL_RE.search(value):
        return "cancel", "Отбой опасности по БПЛА"
    if not UAV_ALERT_RE.search(value):
        return None
    if UAV_DANGER_RE.search(value):
        return "danger", "Опасность по БПЛА"
    if UAV_THREAT_RE.search(value):
        return "threat", "Угроза по БПЛА"
    if UAV_ATTENTION_RE.search(value):
        return "attention", "Внимание: БПЛА"
    return None


def classify(text):
    return (
        classify_mwsh(text)
        or classify_rocket(text)
        or classify_uav(text)
    )


def event_family(kind):
    if kind.startswith("mwsh_"):
        return "mwsh"
    if kind.startswith("rocket_"):
        return "rocket"
    return "uav"


def event_is_cancel(kind):
    return kind in ("cancel", "rocket_cancel", "mwsh_cancel")


def event_is_danger(kind):
    return kind in (
        "danger", "threat", "attention",
        "mwsh_danger", "rocket_danger",
    )


def event_title(kind):
    return {
        "mwsh_cancel": ("🟢 Отбой опасности по МВШ", "МВШ"),
        "mwsh_danger": ("🔴 Опасность по МВШ", "МВШ"),
        "rocket_cancel": ("🟢 Отбой ракетной опасности", "Ракетная опасность"),
        "rocket_danger": ("🔴 Ракетная опасность", "Ракетная опасность"),
        "cancel": ("🟢 Отбой опасности по БПЛА", "БПЛА"),
        "threat": ("🟠 Угроза по БПЛА", "БПЛА"),
        "attention": ("🟡 Внимание: БПЛА", "БПЛА"),
        "danger": ("🔴 Опасность по БПЛА", "БПЛА"),
    }.get(kind, ("ℹ️ Сообщение", "Информация"))


def format_event_time(value):
    parsed = parse_iso(value)
    return parsed.strftime("%d.%m.%Y %H:%M МСК") if parsed else "не указано"


# ============================================================
# TELEGRAM PUBLIC-PAGE PARSER
# ============================================================

def parse_published_time(node):
    time_node = node.select_one("a.tgme_widget_message_date time")
    if not time_node:
        return None
    raw = time_node.get("datetime")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(MSK)
    except ValueError:
        return None


def source_posts(source_url, max_pages=1, cutoff=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; UAV-ALERT/1.0)"
    }
    posts_by_key = {}
    page_url = source_url
    seen_page_ids = set()

    for page_number in range(max(1, int(max_pages))):
        response = requests.get(
            page_url,
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        page_posts = []
        numeric_ids = []

        for node in soup.select(".tgme_widget_message"):
            text_node = node.select_one(".tgme_widget_message_text")
            if not text_node:
                continue

            text_value = text_node.get_text(" ", strip=True)
            if not text_value:
                continue

            link_node = node.select_one("a.tgme_widget_message_date")
            post_url = (
                link_node.get("href", "")
                if link_node else source_url
            )
            post_id = node.get("data-post", "") or post_url
            published_at = parse_published_time(node)

            tail = str(post_id).rsplit("/", 1)[-1]
            if tail.isdigit():
                numeric_ids.append(int(tail))

            digest = hashlib.sha256(
                (str(post_id) + "|" + text_value).encode("utf-8")
            ).hexdigest()

            post = {
                "key": digest,
                "text": text_value[:5000],
                "url": post_url,
                "published_at": (
                    published_at.isoformat(timespec="minutes")
                    if published_at else None
                ),
            }
            page_posts.append(post)
            posts_by_key[digest] = post

        if not page_posts:
            break

        if cutoff:
            dated = [
                parse_iso(post.get("published_at"))
                for post in page_posts
            ]
            dated = [item for item in dated if item]
            if dated and max(dated) < cutoff:
                break

        if page_number + 1 >= max_pages or not numeric_ids:
            break

        before_id = min(numeric_ids)
        if before_id in seen_page_ids:
            break
        seen_page_ids.add(before_id)

        separator = "&" if "?" in source_url else "?"
        page_url = f"{source_url}{separator}before={before_id}"

    posts = list(posts_by_key.values())
    posts.sort(
        key=lambda post: (
            parse_iso(post.get("published_at"))
            or datetime.min.replace(tzinfo=MSK)
        )
    )
    return posts


# ============================================================
# EVENT PHOTOS AND MESSAGE FORMATTING
# ============================================================

def event_image_path(kind):
    paths = {
        "attention": UAV_ATTENTION_IMAGE,
        "threat": UAV_DANGER_IMAGE,
        "danger": UAV_DANGER_IMAGE,
        "cancel": UAV_CANCEL_IMAGE,
        "mwsh_danger": MWSH_DANGER_IMAGE,
        "mwsh_cancel": MWSH_CANCEL_IMAGE,
        "rocket_danger": ROCKET_DANGER_IMAGE,
        "rocket_cancel": ROCKET_CANCEL_IMAGE,
    }
    path = paths.get(kind)
    if path and os.path.isfile(path):
        return path

    fallback = (
        LEGACY_CLEAR_IMAGE
        if event_is_cancel(kind)
        else LEGACY_DANGER_IMAGE
    )
    return fallback if os.path.isfile(fallback) else None


def event_message(
    region_id,
    kind,
    label,
    source,
    url,
    source_text,
    published_at=None,
):
    region = REGIONS[region_id]
    title, category = event_title(kind)
    stamp = html.escape(format_event_time(published_at))
    safe_url = html.escape(url, quote=True)
    safe_source = html.escape(source)

    return (
        "<b>🛰 UAV ALERT</b>\n"
        f"📍 <b>Регион:</b> {html.escape(region['name'])}\n\n"
        f"<b>{title}</b>\n"
        f"Категория: {html.escape(category)}\n"
        f"Время исходного сообщения: {stamp}\n\n"
        f'📡 <b>Источник:</b> <a href="{safe_url}">{safe_source}</a>\n\n'
        "⚠️ Сообщение получено из стороннего источника и "
        "не является самостоятельным подтверждением угрозы. "
        "Сверяйте информацию с официальными оповещениями "
        "МЧС, РСЧС и органов власти. Следуйте инструкциям "
        "экстренных служб.\n\n"
        "<i>UAV ALERT не является официальным источником.</i>"
    )


async def send_text_or_photo(
    bot,
    chat_id,
    message,
    is_cancel=False,
    kind=None,
    reply_markup=None,
):
    image_path = (
        event_image_path(kind)
        if kind
        else (
            LEGACY_CLEAR_IMAGE if is_cancel else LEGACY_DANGER_IMAGE
        )
    )

    if image_path and os.path.isfile(image_path):
        if len(message) <= 1024:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=message,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
        else:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    reply_markup=reply_markup,
                )
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )


# ============================================================
# MODERATION, EVENT HISTORY AND SUBSCRIBER NOTIFICATIONS
# ============================================================

def moderation_keyboard(pending_id, region_id):
    region = REGIONS.get(region_id, REGIONS["kostroma"])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📢 Опубликовать в канал {region['name']}",
            callback_data=f"approve:{pending_id}",
        )],
        [InlineKeyboardButton(
            "❌ Отклонить",
            callback_data=f"reject:{pending_id}",
        )],
        [InlineKeyboardButton(
            "🔎 Центр событий",
            callback_data="admin_center",
        )],
    ])


def conflict_warning(region_id, kind):
    family = event_family(kind)
    opposite = event_is_cancel(kind)
    matches = []

    for item in state.get("pending", {}).values():
        if item.get("region_id") != region_id:
            continue
        if event_family(item.get("kind", "")) != family:
            continue
        if event_is_cancel(item.get("kind", "")) == opposite:
            continue
        created = parse_iso(item.get("created_at"))
        if created and now_msk() - created <= timedelta(hours=3):
            matches.append(item)

    if matches:
        return (
            "\n\n⚠️ <b>ВНИМАНИЕ: возможно противоречие.</b>\n"
            "В очереди есть недавнее сообщение с другим статусом "
            "этой категории. Проверьте исходную публикацию и "
            "официальные оповещения."
        )
    return ""


def add_history(item, decision, actor_id=None):
    history = state.setdefault("event_history", [])
    history.append({
        "region_id": item.get("region_id", "kostroma"),
        "kind": item.get("kind"),
        "label": item.get("label"),
        "source": item.get("source"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "created_at": item.get("created_at"),
        "decision_at": now_msk().isoformat(timespec="seconds"),
        "decision": decision,
        "actor_id": actor_id,
    })
    state["event_history"] = history[-700:]


def record_approved_event(item):
    region_id = item.get("region_id", "kostroma")
    active = state.setdefault("active_alerts", {}).setdefault(
        region_id, {}
    )
    family = event_family(item["kind"])

    if event_is_cancel(item["kind"]):
        for key in list(active):
            if event_family(key) == family:
                active.pop(key, None)
        return

    if event_is_danger(item["kind"]):
        event_time = parse_iso(item.get("published_at")) or now_msk()
        active[item["kind"]] = {
            "label": item["label"],
            "published_at": event_time.isoformat(),
            "expires_at": (event_time + ALERT_TTL).isoformat(),
            "source": item["source"],
            "url": item["url"],
            "stale": False,
        }


async def notify_subscribers(bot, region_id, message, kind=None):
    failed = []
    recipients = list(subscribers.get(region_id, set()))

    for user_id in recipients:
        try:
            await send_text_or_photo(
                bot,
                user_id,
                message,
                is_cancel=event_is_cancel(kind) if kind else False,
                kind=kind,
            )
        except TelegramError as exc:
            logger.warning(
                "Не удалось отправить уведомление %s: %s",
                user_id,
                exc,
            )
            failed.append(user_id)

    return failed


async def send_event(
    application,
    region_id,
    kind,
    label,
    source,
    url,
    text_value,
    published_at=None,
):
    pending_id = hashlib.sha256(
        (
            f"{region_id}|{url}|{text_value}|"
            f"{published_at or ''}"
        ).encode("utf-8")
    ).hexdigest()[:16]

    if (
        pending_id in state["approved"]
        or pending_id in state["pending"]
        or pending_id in state["rejected"]
    ):
        return

    item = {
        "id": pending_id,
        "region_id": region_id,
        "kind": kind,
        "label": label,
        "source": source,
        "url": url,
        "text": text_value[:5000],
        "published_at": published_at,
        "message": event_message(
            region_id, kind, label, source, url,
            text_value, published_at,
        ),
        "created_at": now_msk().isoformat(timespec="seconds"),
    }

    state["pending"][pending_id] = item
    save_state()

    admin_message = (
        "🛡 <b>Новое событие — требуется проверка</b>\n\n"
        f"{item['message']}"
        f"{conflict_warning(region_id, kind)}\n\n"
        "Проверьте исходную публикацию и официальные оповещения. "
        "Публикация произойдёт только после вашего решения."
    )

    try:
        await send_text_or_photo(
            application.bot,
            ADMIN_ID,
            admin_message,
            is_cancel=event_is_cancel(kind),
            kind=kind,
            reply_markup=moderation_keyboard(pending_id, region_id),
        )
        logger.info(
            "Событие добавлено в очередь: region=%s id=%s",
            region_id, pending_id,
        )
    except TelegramError:
        logger.exception("Не удалось отправить событие администратору")


async def approve_event(query, context, pending_id):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "Только администратор может публиковать.",
            show_alert=True,
        )
        return

    item = state["pending"].get(pending_id)
    if not item:
        await query.answer(
            "Событие уже обработано или не найдено.",
            show_alert=True,
        )
        return

    region_id = item.get("region_id", "kostroma")
    channel_id = REGIONS[region_id]["channel"]

    if not channel_id:
        await query.answer(
            "Не задан канал для этого региона.",
            show_alert=True,
        )
        return

    try:
        await send_text_or_photo(
            context.bot,
            channel_id,
            item["message"],
            is_cancel=event_is_cancel(item["kind"]),
            kind=item["kind"],
        )
    except TelegramError:
        logger.exception("Ошибка публикации в канал %s", channel_id)
        await query.answer(
            "Ошибка публикации. Проверь права бота и ID канала.",
            show_alert=True,
        )
        return

    state["pending"].pop(pending_id, None)
    state["approved"][pending_id] = now_msk().isoformat(
        timespec="seconds"
    )
    record_approved_event(item)
    add_history(item, "published", query.from_user.id)
    save_state()

    failed = await notify_subscribers(
        context.bot,
        region_id,
        item["message"],
        item["kind"],
    )

    # Remove the publish button from the original moderation message.
    # The original message may contain a photo, so do not use edit_message_text.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except (BadRequest, TelegramError):
        pass

    await query.message.reply_text(
        "✅ <b>Событие опубликовано</b>\n"
        f"Канал: {html.escape(REGIONS[region_id]['name'])}\n"
        f"Уведомлено подписчиков: "
        f"{len(subscribers.get(region_id, set())) - len(failed)}.",
        parse_mode=ParseMode.HTML,
    )

    await query.answer("Опубликовано!")


async def reject_event(query, pending_id):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "Только администратор может отклонить событие.",
            show_alert=True,
        )
        return

    item = state["pending"].pop(pending_id, None)
    if not item:
        await query.answer(
            "Событие уже обработано или не найдено.",
            show_alert=True,
        )
        return

    state["rejected"][pending_id] = now_msk().isoformat(
        timespec="seconds"
    )
    add_history(item, "rejected", query.from_user.id)
    save_state()

    try:
        await query.edit_message_text(
            "❌ <b>Событие отклонено</b>\n\n"
            "Оно не отправлено в канал и подписчикам.",
            parse_mode=ParseMode.HTML,
        )
    except (BadRequest, TelegramError):
        pass

    await query.answer("Отклонено.")


# ============================================================
# BACKFILL, NEWS REVIEW AND MONITORING
# ============================================================

async def send_backfill_notice(
    application, region_id, name, post, kind, label
):
    title, category = event_title(kind)
    region_name = REGIONS[region_id]["name"]
    display_region = region_name.replace("область", "Область")

    draft = (
        f"🛰 <b>UAV ALERT | {html.escape(display_region)}</b>\n"
        f"📍 Регион: {html.escape(region_name)}\n\n"
        f"{html.escape(title)}\n"
        f"Категория: {html.escape(category)}\n"
        "Время исходного сообщения: "
        f"{html.escape(format_event_time(post.get('published_at')))}\n\n"
        f"Источник: {html.escape(name)}\n\n"
        "⚠️ Информация получена из стороннего источника. "
        "Сверяйте её с официальными оповещениями.\n\n"
        "<i>Историческая информация. Не является "
        "подтверждением текущего статуса.</i>"
    )

    # Save the historical item as a separate, manually publishable draft.
    # Keep the historical disclaimer in the text when it is published.
    archive_id = hashlib.sha256(
        f"archive|{region_id}|{post.get('key', '')}|{post.get('published_at', '')}|{kind}".encode("utf-8")
    ).hexdigest()[:16]
    state.setdefault("archive_drafts", {})[archive_id] = {
        "region_id": region_id,
        "kind": kind,
        "message": draft,
        "created_at": now_msk().isoformat(timespec="seconds"),
    }
    archive_queue = state["archive_drafts"]
    while len(archive_queue) > 200:
        archive_queue.pop(next(iter(archive_queue)))
    save_state()

    archive_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📢 Опубликовать в канал {region_name}",
            callback_data=f"archive_publish:{archive_id}",
        )],
        [InlineKeyboardButton(
            "🗑 Удалить из очереди архива",
            callback_data=f"archive_discard:{archive_id}",
        )],
    ])

    await send_text_or_photo(
        application.bot,
        ADMIN_ID,
        draft,
        is_cancel=event_is_cancel(kind),
        kind=kind,
        reply_markup=archive_keyboard,
    )


async def monitor_source(application, region_id, name, url):
    scan_key = f"{region_id}|{url}"
    first_scan = scan_key not in startup_backfill_done
    startup_backfill_done.add(scan_key)

    cutoff = now_msk() - timedelta(hours=HISTORY_BACKFILL_HOURS)
    pages = HISTORY_MAX_PAGES if first_scan else 1

    posts = await asyncio.to_thread(
        source_posts, url, pages, cutoff if first_scan else None
    )

    seen_map = state.setdefault("seen", {})
    source_seen = set(seen_map.get(scan_key, []))

    for post in posts:
        key = post["key"]
        if key in source_seen:
            continue

        source_seen.add(key)
        published = parse_iso(post.get("published_at"))

        if first_scan and (
            not published or published < cutoff
        ):
            continue

        text_value = post["text"]
        if not mentions_region(region_id, text_value):
            continue

        result = classify(text_value)
        if not result:
            continue

        kind, label = result

        if first_scan:
            # Archive messages are sent only to the administrator.
            try:
                await send_backfill_notice(
                    application, region_id, name, post, kind, label
                )
            except TelegramError:
                logger.exception("Ошибка отправки архивной записи")
            continue

        await send_event(
            application,
            region_id,
            kind,
            label,
            name,
            post["url"],
            text_value,
            post.get("published_at"),
        )

    ordered = [
        post["key"] for post in posts
        if post["key"] in source_seen
    ]
    seen_map[scan_key] = ordered[-500:]
    state.setdefault("initialized", {})[scan_key] = True
    state.setdefault("last_errors", {}).pop(scan_key, None)
    state["last_scan"] = now_msk().isoformat()
    save_state()


def is_safe_regional_news(region_id, text_value):
    return bool(
        text_value
        and mentions_region(region_id, text_value)
        and not NEWS_SENSITIVE_RE.search(text_value)
    )


async def monitor_news_source(application, region_id, name, url):
    posts = await asyncio.to_thread(source_posts, url, 1, None)
    scan_key = f"{region_id}|{url}"

    seen_map = state.setdefault("news_seen", {})
    source_seen = set(seen_map.get(scan_key, []))
    initialized = state.setdefault(
        "news_initialized", {}
    ).get(scan_key, False)

    if not initialized:
        seen_map[scan_key] = [post["key"] for post in posts][-300:]
        state["news_initialized"][scan_key] = True
        save_state()
        return

    for post in posts:
        if post["key"] in source_seen:
            continue

        source_seen.add(post["key"])
        if not is_safe_regional_news(region_id, post["text"]):
            continue

        published = format_event_time(post.get("published_at"))
        message = (
            "📰 <b>Региональная новость — на проверку</b>\n"
            f"Регион: {html.escape(REGIONS[region_id]['name'])}\n"
            f"Источник: {html.escape(name)}\n"
            f"Время публикации: {html.escape(published)}\n\n"
            f"{html.escape(post['text'][:2500])}\n\n"
            "Информация взята из стороннего источника. "
            "Это не официальное оповещение. "
            "Автоматической публикации нет.\n"
            f'<a href="{html.escape(post["url"], quote=True)}">'
            "Открыть исходную публикацию</a>"
        )

        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            history = state.setdefault("news_history", [])
            history.append({
                "region_id": region_id,
                "source": name,
                "url": post["url"],
                "text": post["text"][:1500],
                "published_at": post.get("published_at"),
                "received_at": now_msk().isoformat(),
            })
            state["news_history"] = history[-400:]
        except TelegramError:
            logger.exception("Ошибка отправки региональной новости")

    seen_map[scan_key] = [
        post["key"] for post in posts
        if post["key"] in source_seen
    ][-300:]
    save_state()


def expire_active_alerts():
    changed = False
    now = now_msk()

    for region_id in REGIONS:
        active = state.setdefault("active_alerts", {}).setdefault(
            region_id, {}
        )
        for key, item in list(active.items()):
            expires = parse_iso(item.get("expires_at"))
            if not expires:
                if not item.get("stale"):
                    item["stale"] = True
                    changed = True
                continue
            if now >= expires and not item.get("stale"):
                item["stale"] = True
                item["stale_marked_at"] = now.isoformat()
                changed = True

    if changed:
        save_state()


async def monitor_loop(application):
    logger.info(
        "Мониторинг запущен; интервал=%s сек.",
        CHECK_INTERVAL,
    )

    while True:
        for region_id, region in REGIONS.items():
            for name, url in region["alert_sources"]:
                try:
                    await monitor_source(
                        application, region_id, name, url
                    )
                except Exception as exc:
                    key = f"{region_id}|{url}"
                    state.setdefault("last_errors", {})[key] = str(exc)[:500]
                    save_state()
                    logger.exception(
                        "Ошибка мониторинга источника %s (%s)",
                        name, region_id,
                    )

            for name, url in region["news_sources"]:
                try:
                    await monitor_news_source(
                        application, region_id, name, url
                    )
                except Exception as exc:
                    key = f"{region_id}|{url}"
                    state.setdefault("news_last_errors", {})[key] = str(exc)[:500]
                    save_state()
                    logger.exception(
                        "Ошибка новостного источника %s (%s)",
                        name, region_id,
                    )

        expire_active_alerts()
        await asyncio.sleep(CHECK_INTERVAL)


# ============================================================
# STATUS, HISTORY, STATISTICS AND SOURCES
# ============================================================

def build_status_text(region_id):
    region = REGIONS[region_id]
    active = state.get("active_alerts", {}).get(region_id, {})

    lines = [
        "🛰 <b>UAV ALERT</b>",
        f"📍 {html.escape(region['name'])}",
        "",
    ]

    if not active:
        lines.append(
            "ℹ️ В базе подтверждённых публикаций бота "
            "нет активных записей."
        )
    else:
        for item in active.values():
            if item.get("stale"):
                lines.append(
                    "⚪ Актуальность предыдущей записи не подтверждена."
                )
            else:
                lines.append(
                    f"ℹ️ {html.escape(item.get('label', 'Событие'))}"
                )

    lines.extend([
        "",
        "Это не официальный статус региона. "
        "Проверяйте сообщения МЧС, РСЧС и органов власти.",
    ])
    return "\n".join(lines)


def build_region_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Костромская область",
                callback_data="subscribe:kostroma",
            ),
            InlineKeyboardButton(
                "🔔 Ивановская область",
                callback_data="subscribe:ivanovo",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Статус Костромской области",
                callback_data="status:kostroma",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Статус Ивановской области",
                callback_data="status:ivanovo",
            ),
        ],
        [
            InlineKeyboardButton(
                "📚 Официальные источники",
                callback_data="sources",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ О проекте",
                callback_data="about",
            ),
        ],
    ])


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    await update.message.reply_text(
        "🛰 <b>UAV ALERT</b>\n\n"
        "Независимый гражданский информационный сервис.\n\n"
        "Выберите регион для подписки или просмотра записей сервиса.\n\n"
        "⚠️ UAV ALERT не является МЧС, РСЧС или официальным "
        "источником оповещений. Наши сообщения не подтверждают "
        "угрозу самостоятельно и не заменяют указания экстренных "
        "служб. Приоритет всегда у официальной информации.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_region_keyboard(),
    )


async def subscribe_region(update_or_query, region_id):
    user = update_or_query.effective_user if hasattr(
        update_or_query, "effective_user"
    ) else update_or_query.from_user
    if not user:
        return False
    subscribers.setdefault(region_id, set()).add(user.id)
    save_state()
    return True


async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region_id = "kostroma"
    if context.args:
        arg = context.args[0].strip().lower()
        if arg in ("ivanovo", "иваново", "ивановская"):
            region_id = "ivanovo"
    if not await subscribe_region(update, region_id):
        return
    await update.message.reply_text(
        f"🔔 Вы подписались на уведомления UAV ALERT: "
        f"{REGIONS[region_id]['name']}."
    )


async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    if context.args:
        arg = context.args[0].strip().lower()
        region_id = (
            "ivanovo"
            if arg in ("ivanovo", "иваново", "ивановская")
            else "kostroma"
        )
        subscribers.setdefault(region_id, set()).discard(user.id)
        save_state()
        await update.message.reply_text(
            f"🔕 Вы отписались от уведомлений: {REGIONS[region_id]['name']}."
        )
        return

    removed = False
    for region_id in REGIONS:
        if user.id in subscribers[region_id]:
            subscribers[region_id].discard(user.id)
            removed = True
    save_state()
    await update.message.reply_text(
        "🔕 Вы отписались от уведомлений во всех регионах."
        if removed else
        "У вас нет активных подписок."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region_id = "kostroma"
    if context.args and context.args[0].lower() in (
        "ivanovo", "иваново", "ивановская"
    ):
        region_id = "ivanovo"
    await update.message.reply_text(
        build_status_text(region_id),
        parse_mode=ParseMode.HTML,
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region_id = "kostroma"
    if context.args and context.args[0].lower() in (
        "ivanovo", "иваново", "ивановская"
    ):
        region_id = "ivanovo"

    history = [
        item for item in state.get("event_history", [])
        if item.get("region_id", "kostroma") == region_id
    ][-10:]

    if not history:
        await update.message.reply_text(
            f"История решений по региону «{REGIONS[region_id]['name']}» пока пуста."
        )
        return

    lines = [
        f"📚 <b>Последние решения: {html.escape(REGIONS[region_id]['name'])}</b>\n"
    ]
    for item in reversed(history):
        decision = (
            "Опубликовано"
            if item.get("decision") == "published"
            else "Отклонено"
        )
        lines.append(
            f"• {html.escape(item.get('label') or 'Событие')}\n"
            f"  {decision}\n"
            f"  {html.escape(format_event_time(item.get('decision_at')))}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


def get_recent_stats():
    cutoff = now_msk() - timedelta(hours=24)
    recent = []
    for item in state.get("event_history", []):
        stamp = parse_iso(item.get("decision_at"))
        if stamp and stamp >= cutoff:
            recent.append(item)
    return recent


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "Команда доступна только администратору."
        )
        return

    recent = get_recent_stats()
    published = sum(item.get("decision") == "published" for item in recent)
    rejected = sum(item.get("decision") == "rejected" for item in recent)

    lines = [
        "📊 <b>Статистика за 24 часа</b>",
        "",
        f"Всего решений: {len(recent)}",
        f"Опубликовано: {published}",
        f"Отклонено: {rejected}",
        f"В очереди: {len(state.get('pending', {}))}",
        "",
    ]
    for region_id, region in REGIONS.items():
        lines.append(
            f"{html.escape(region['name'])}: "
            f"{len(subscribers.get(region_id, set()))} подписчиков"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "📚 <b>Официальные ресурсы для самостоятельной проверки</b>",
        "",
        "Костромская область:",
    ]
    for name, url in REGIONS["kostroma"]["official_sources"]:
        lines.append(
            f'• <a href="{html.escape(url, quote=True)}">{html.escape(name)}</a>'
        )
    lines.extend(["", "Ивановская область:"])
    for name, url in REGIONS["ivanovo"]["official_sources"]:
        lines.append(
            f'• <a href="{html.escape(url, quote=True)}">{html.escape(name)}</a>'
        )
    lines.extend([
        "",
        "Ссылки приведены для ручной сверки. Бот не получает "
        "официальный статус РСЧС автоматически с этих страниц.",
        "Приоритет имеют официальные оповещения и инструкции экстренных служб.",
    ])
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ============================================================
# ADMIN CENTER AND MANUAL CHANNEL PUBLICATION
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Очередь событий", callback_data="admin_center")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📚 История", callback_data="admin_history")],
        [InlineKeyboardButton("📸 Проверить 7 фотографий", callback_data="admin_testphotos")],
        [InlineKeyboardButton("📢 Подготовить публикацию", callback_data="admin_publish_help")],
        [InlineKeyboardButton("📚 Официальные ресурсы", callback_data="sources")],
    ])


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Доступ запрещён.")
        return
    await update.message.reply_text(
        "🛡 <b>Панель администратора UAV ALERT</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )


async def show_admin_center(query, context):
    if query.from_user.id != ADMIN_ID:
        await query.answer("Доступ запрещён.", show_alert=True)
        return

    pending = state.get("pending", {})
    if not pending:
        await query.message.reply_text("📥 Очередь событий пуста.")
        await query.answer()
        return

    await query.message.reply_text(f"📥 В очереди событий: {len(pending)}")
    for pending_id, item in list(pending.items())[-15:]:
        region_name = REGIONS.get(
            item.get("region_id", "kostroma"), REGIONS["kostroma"]
        )["name"]
        message = (
            "🛡 <b>Событие на проверку</b>\n"
            f"Регион: {html.escape(region_name)}\n\n"
            f"{item['message']}\n\n"
            f"ID: <code>{html.escape(pending_id)}</code>"
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=moderation_keyboard(pending_id, item.get("region_id", "kostroma")),
        )
    await query.answer()


async def publish_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Доступ запрещён.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/publish kostroma текст сообщения\n"
            "/publish ivanovo текст сообщения\n\n"
            "Бот сначала покажет предпросмотр. В канал сообщение "
            "уйдёт только после нажатия кнопки подтверждения."
        )
        return

    region_id = args[0].lower()
    if region_id not in REGIONS:
        await update.message.reply_text(
            "Укажи регион: kostroma или ivanovo."
        )
        return

    body = " ".join(args[1:]).strip()
    if not body:
        await update.message.reply_text("Текст публикации пуст.")
        return

    draft_id = hashlib.sha256(
        f"{region_id}|{body}|{now_msk().isoformat()}".encode("utf-8")
    ).hexdigest()[:16]

    state.setdefault("manual_drafts", {})[draft_id] = {
        "region_id": region_id,
        "text": body[:3500],
        "created_at": now_msk().isoformat(),
    }
    save_state()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 Опубликовать в канал",
            callback_data=f"manual_publish:{draft_id}",
        )],
        [InlineKeyboardButton(
            "❌ Отменить",
            callback_data=f"manual_cancel:{draft_id}",
        )],
    ])

    await update.message.reply_text(
        "📝 <b>Предпросмотр публикации</b>\n"
        f"Канал: {html.escape(REGIONS[region_id]['name'])}\n\n"
        f"{html.escape(body[:3500])}\n\n"
        "Проверь текст перед отправкой.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ============================================================
# TEST PHOTOS — ALL 7 PHOTOS
# ============================================================

async def send_test_photos(bot, reply_target=None):
    sent = 0
    missing = []

    for title, image_path in PHOTO_LIST:
        if not os.path.isfile(image_path):
            missing.append(os.path.basename(image_path))
            continue
        try:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo,
                    caption=f"📷 {title}",
                )
            sent += 1
        except TelegramError:
            logger.exception("Ошибка тестовой фотографии: %s", title)

    summary = (
        "📸 <b>Проверка завершена</b>\n\n"
        f"Отправлено: {sent}/7\n"
        f"Не найдено: {len(missing)}"
    )
    if missing:
        summary += "\n\nОтсутствуют:\n" + "\n".join(
            f"• <code>{html.escape(name)}</code>" for name in missing
        )

    if reply_target:
        await reply_target.reply_text(
            summary,
            parse_mode=ParseMode.HTML,
        )
    else:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=summary,
            parse_mode=ParseMode.HTML,
        )


async def test_photos_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Эта команда доступна только администратору."
        )
        return

    await update.message.reply_text(
        "📸 Начинаю проверку семи фотографий."
    )
    await send_test_photos(context.bot, update.message)


# ============================================================
# WEATHER — PUBLIC WEATHER DATA ONLY
# ============================================================

def get_weather(region_id):
    region = REGIONS[region_id]
    response = requests.get(
        WEATHER_URL,
        params={
            "latitude": region["latitude"],
            "longitude": region["longitude"],
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "wind_speed_10m"
            ),
            "timezone": "Europe/Moscow",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    region_id = "kostroma"
    if context.args and context.args[0].lower() in (
        "ivanovo", "иваново", "ивановская"
    ):
        region_id = "ivanovo"

    try:
        data = await asyncio.to_thread(get_weather, region_id)
        current = data.get("current", {})
        temperature = current.get("temperature_2m", "—")
        humidity = current.get("relative_humidity_2m", "—")
        wind = current.get("wind_speed_10m", "—")

        await update.message.reply_text(
            f"🌤 <b>Погода: {html.escape(REGIONS[region_id]['name'])}</b>\n\n"
            f"🌡 Температура: {temperature} °C\n"
            f"💧 Влажность: {humidity}%\n"
            f"💨 Ветер: {wind} км/ч",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Ошибка получения погоды")
        await update.message.reply_text(
            "Не удалось получить данные о погоде."
        )


# ============================================================
# OPTIONAL AI COMMANDS
# ============================================================

async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_AVAILABLE:
        await update.message.reply_text("ИИ-модуль сейчас недоступен.")
        return

    text_value = " ".join(context.args).strip()
    if not text_value:
        await update.message.reply_text(
            "Использование: /ai текст для анализа"
        )
        return

    try:
        result = await asyncio.to_thread(analyze_text, text_value)
        await update.message.reply_text(str(result)[:4000])
    except Exception as exc:
        logger.exception("Ошибка ИИ-анализа")
        await update.message.reply_text(
            f"Ошибка ИИ-модуля: {html.escape(str(exc)[:300])}"
        )


async def ai_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_AVAILABLE:
        await update.message.reply_text("ИИ-модуль недоступен.")
        return
    try:
        result = await asyncio.to_thread(ai_status_text)
        await update.message.reply_text(str(result)[:4000])
    except Exception:
        logger.exception("Ошибка AI status")
        await update.message.reply_text("Не удалось получить статус ИИ.")


async def ai_history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_AVAILABLE:
        await update.message.reply_text("ИИ-модуль недоступен.")
        return
    try:
        result = await asyncio.to_thread(ai_history_text)
        await update.message.reply_text(str(result)[:4000])
    except Exception:
        logger.exception("Ошибка AI history")
        await update.message.reply_text("Не удалось получить историю ИИ.")


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = query.data or ""

    if data.startswith("subscribe:"):
        region_id = data.split(":", 1)[1]
        if region_id not in REGIONS:
            await query.answer("Неизвестный регион.", show_alert=True)
            return
        subscribers.setdefault(region_id, set()).add(query.from_user.id)
        save_state()
        await query.answer("Подписка оформлена!")
        await query.message.reply_text(
            f"🔔 Вы подписались на уведомления: {REGIONS[region_id]['name']}."
        )
        return

    if data.startswith("status:"):
        region_id = data.split(":", 1)[1]
        if region_id not in REGIONS:
            await query.answer("Неизвестный регион.", show_alert=True)
            return
        await query.answer()
        await query.message.reply_text(
            build_status_text(region_id),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "sources":
        await query.answer()
        lines = [
            "📚 <b>Официальные ресурсы для самостоятельной проверки</b>",
            "",
        ]
        for region_id, region in REGIONS.items():
            lines.append(f"<b>{html.escape(region['name'])}</b>")
            for name, url in region["official_sources"]:
                lines.append(
                    f'• <a href="{html.escape(url, quote=True)}">'
                    f'{html.escape(name)}</a>'
                )
            lines.append("")
        lines.append(
            "Ссылки приведены для ручной сверки. Автоматического "
            "получения официального статуса РСЧС с этих страниц нет."
        )
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return

    if data == "about":
        await query.answer()
        await query.message.reply_text(
            "🛰 UAV ALERT — независимый гражданский информационный сервис.\n\n"
            "Мы не являемся МЧС, РСЧС или официальным источником. "
            "Информация может запаздывать или требовать проверки. "
            "Не используйте её как единственное подтверждение угрозы. "
            "Следите за официальными оповещениями и инструкциями "
            "экстренных служб."
        )
        return

    if data.startswith("archive_publish:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        archive_id = data.split(":", 1)[1]
        archive_item = state.get("archive_drafts", {}).get(archive_id)
        if not archive_item:
            await query.answer("Архивная запись не найдена или уже обработана.", show_alert=True)
            return

        region_id = archive_item.get("region_id", "kostroma")
        if region_id not in REGIONS:
            await query.answer("Неизвестный регион.", show_alert=True)
            return
        channel_id = REGIONS[region_id]["channel"]
        if not channel_id:
            await query.answer("Не задан канал для этого региона.", show_alert=True)
            return

        try:
            await send_text_or_photo(
                context.bot,
                channel_id,
                archive_item["message"],
                is_cancel=event_is_cancel(archive_item.get("kind")),
                kind=archive_item.get("kind"),
            )
        except TelegramError:
            logger.exception("Ошибка публикации архивной записи в канал %s", channel_id)
            await query.answer("Не удалось опубликовать архив. Проверь права бота и наличие фотографии.", show_alert=True)
            return

        state["archive_drafts"].pop(archive_id, None)
        save_state()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except (BadRequest, TelegramError):
            pass
        await query.message.reply_text(
            "✅ Архивная запись опубликована в канал "
            f"{html.escape(REGIONS[region_id]['name'])}.\n"
            "В публикации сохранена пометка, что это историческая информация."
        )
        await query.answer("Архив опубликован!")
        return

    if data.startswith("archive_discard:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        archive_id = data.split(":", 1)[1]
        if state.get("archive_drafts", {}).pop(archive_id, None) is None:
            await query.answer("Архивная запись уже обработана.", show_alert=True)
            return
        save_state()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except (BadRequest, TelegramError):
            pass
        await query.answer("Удалено из очереди архива.")
        return

    if data.startswith("approve:"):
        await approve_event(query, context, data.split(":", 1)[1])
        return

    if data.startswith("reject:"):
        await reject_event(query, data.split(":", 1)[1])
        return

    if data == "admin_center":
        await show_admin_center(query, context)
        return

    if data == "admin_stats":
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        await query.answer()
        recent = get_recent_stats()
        published = sum(item.get("decision") == "published" for item in recent)
        rejected = sum(item.get("decision") == "rejected" for item in recent)
        lines = [
            "📊 <b>Статистика за 24 часа</b>",
            "",
            f"Опубликовано: {published}",
            f"Отклонено: {rejected}",
            f"В очереди: {len(state.get('pending', {}))}",
        ]
        for region_id, region in REGIONS.items():
            lines.append(
                f"{html.escape(region['name'])}: "
                f"{len(subscribers.get(region_id, set()))} подписчиков"
            )
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "admin_history":
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        await query.answer()
        history = state.get("event_history", [])[-15:]
        if not history:
            await query.message.reply_text("История пока пуста.")
            return
        lines = ["📚 <b>История решений</b>\n"]
        for item in reversed(history):
            region_name = REGIONS.get(
                item.get("region_id", "kostroma"), REGIONS["kostroma"]
            )["name"]
            lines.append(
                f"• {html.escape(item.get('label') or 'Событие')}\n"
                f"  Регион: {html.escape(region_name)}\n"
                f"  Решение: {html.escape(item.get('decision', '—'))}\n"
                f"  {html.escape(format_event_time(item.get('decision_at')))}"
            )
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "admin_testphotos":
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        await query.answer("Отправляю фотографии...")
        await send_test_photos(context.bot)
        return

    if data == "admin_publish_help":
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        await query.answer()
        await query.message.reply_text(
            "Для ручной публикации отправь команду:\n\n"
            "/publish kostroma текст сообщения\n"
            "/publish ivanovo текст сообщения\n\n"
            "Я покажу предпросмотр и кнопку «Опубликовать в канал»."
        )
        return

    if data.startswith("manual_publish:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        draft_id = data.split(":", 1)[1]
        draft = state.get("manual_drafts", {}).get(draft_id)
        if not draft:
            await query.answer("Черновик не найден.", show_alert=True)
            return

        region_id = draft["region_id"]
        channel_id = REGIONS[region_id]["channel"]
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    "🛰 <b>UAV ALERT</b>\n"
                    f"📍 {html.escape(REGIONS[region_id]['name'])}\n\n"
                    f"{html.escape(draft['text'])}\n\n"
                    "<i>UAV ALERT не является официальным источником. "
                    "Сверяйте важную информацию с МЧС, РСЧС и органами власти.</i>"
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except TelegramError:
            logger.exception("Ошибка ручной публикации")
            await query.answer(
                "Не удалось опубликовать. Проверь права бота и ID канала.",
                show_alert=True,
            )
            return

        state["manual_drafts"].pop(draft_id, None)
        save_state()
        try:
            await query.edit_message_text(
                "✅ Сообщение опубликовано в канал "
                f"«{html.escape(REGIONS[region_id]['name'])}».",
                parse_mode=ParseMode.HTML,
            )
        except (BadRequest, TelegramError):
            pass
        await query.answer("Опубликовано!")
        return

    if data.startswith("manual_cancel:"):
        if query.from_user.id != ADMIN_ID:
            await query.answer("Доступ запрещён.", show_alert=True)
            return
        draft_id = data.split(":", 1)[1]
        state.get("manual_drafts", {}).pop(draft_id, None)
        save_state()
        try:
            await query.edit_message_text("❌ Черновик отменён.")
        except (BadRequest, TelegramError):
            pass
        await query.answer("Отменено.")
        return

    await query.answer()


# ============================================================
# COMMANDS AND STARTUP
# ============================================================

async def post_init(application):
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться: /subscribe kostroma или ivanovo"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Статус: /status kostroma или ivanovo"),
        BotCommand("weather", "Погода: /weather kostroma или ivanovo"),
        BotCommand("history", "История: /history kostroma или ivanovo"),
        BotCommand("sources", "Официальные ресурсы"),
        BotCommand("admin", "Панель администратора"),
        BotCommand("stats", "Статистика"),
        BotCommand("publish", "Подготовить публикацию в канал"),
        BotCommand("testphotos", "Проверить 7 фотографий"),
        BotCommand("ai", "ИИ-анализ текста"),
        BotCommand("ai_status", "Статус ИИ"),
        BotCommand("ai_history", "История ИИ"),
    ]
    await application.bot.set_my_commands(commands)

    global monitor_task
    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(monitor_loop(application))

    logger.info("UAV ALERT запущен")


async def post_shutdown(application):
    global monitor_task
    if monitor_task and not monitor_task.done():
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    logger.info("UAV ALERT остановлен")


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN в переменных окружения."
        )

    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("publish", publish_command))
    application.add_handler(CommandHandler("testphotos", test_photos_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("ai", ai_command))
    application.add_handler(CommandHandler("ai_status", ai_status_command))
    application.add_handler(CommandHandler("ai_history", ai_history_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Запуск polling...")
    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
