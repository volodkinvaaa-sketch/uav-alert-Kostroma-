
import os
import re
import json
import asyncio
import hashlib
import logging
import threading
import html
from datetime import datetime, timezone, timedelta
from collections import Counter

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
    from ai_provider import AIProviderError
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# ============================================================
# UAV ALERT — CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip()

PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL", "5")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))

ALERT_TTL_HOURS = max(1, int(os.getenv("ALERT_TTL_HOURS", "5")))
ALERT_TTL = timedelta(hours=ALERT_TTL_HOURS)

HISTORY_BACKFILL_HOURS = max(
    1, int(os.getenv("HISTORY_BACKFILL_HOURS", "6"))
)
HISTORY_MAX_PAGES = max(
    1, min(10, int(os.getenv("HISTORY_MAX_PAGES", "6")))
)

DATA_DIR = os.getenv("DATA_DIR", ".")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MSK = timezone(timedelta(hours=3))

os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "uav_alert_state.json")
SUBSCRIBERS_FILE = os.path.join(
    DATA_DIR, "uav_alert_subscribers.json"
)

# ============================================================
# IMAGES — 7 EVENT PHOTOS
# ============================================================

UAV_ATTENTION_IMAGE = os.path.join(
    BASE_DIR, "uav_attention.jpg"
)
UAV_DANGER_IMAGE = os.path.join(
    BASE_DIR, "uav_danger.jpg"
)
UAV_CANCEL_IMAGE = os.path.join(
    BASE_DIR, "uav_cancel.jpg"
)

MWSH_DANGER_IMAGE = os.path.join(
    BASE_DIR, "mwsh_danger.jpg"
)
MWSH_CANCEL_IMAGE = os.path.join(
    BASE_DIR, "mwsh_cancel.jpg"
)

ROCKET_DANGER_IMAGE = os.path.join(
    BASE_DIR, "rocket_danger.jpg"
)
ROCKET_CANCEL_IMAGE = os.path.join(
    BASE_DIR, "rocket_cancel.jpg"
)

LEGACY_DANGER_IMAGE = os.path.join(
    BASE_DIR, "danger.jpg"
)
LEGACY_CLEAR_IMAGE = os.path.join(
    BASE_DIR, "all_clear.jpg"
)

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
# SOURCES
# ============================================================

SOURCES = [
    ("locatorru", "https://t.me/s/locatorru"),
    ("radarrussiia", "https://t.me/s/radarrussiia"),
    ("bplarussiaru", "https://t.me/s/bplarussiaru"),
]

NEWS_SOURCES = [
    (
        "В курсе Кострома",
        "https://t.me/s/v_kurse_kostroma",
    ),
]

OFFICIAL_SOURCES = [
    (
        "МЧС по Костромской области",
        "https://44.mchs.gov.ru/",
    ),
    (
        "Администрация Костромской области",
        "https://adm44.ru/",
    ),
]

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
KOSTROMA_LAT = 57.7679
KOSTROMA_LON = 40.9269

TERRITORY_TERMS = [
    "костром", "нерехт", "бую", "буй", "волгоречен",
    "галич", "шарь", "манту", "чухлом", "макарьев",
    "солигалич", "кологрив", "нея", "антропов",
    "боговаров", "кадый", "красное-на-волге",
    "красносель", "межев", "октябрьск", "островск",
    "павин", "парфеньев", "поназырев", "пышуг",
    "сусанин", "судислав", "вохом", "варнав",
    "ветлуг",
]

# Оперативные сведения не пересылаются в новостном режиме.
NEWS_SENSITIVE_RE = re.compile(
    r"бпла|беспилот|дрон|мвш|мрш|пво|зенитн|перехват|"
    r"сбит|облом|координат|маршрут|траектор|"
    r"военн\w*\s+объект|ракетн\w*\s+опасност|"
    r"авиаудар|позици\w*\s+(?:военн|пво)",
    re.I,
)

# ============================================================
# EVENT CLASSIFIERS
# ============================================================

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
# LOGGING AND FLASK
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger("uav-alert")

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return jsonify({
        "service": "UAV ALERT",
        "status": "running",
        "region": "Kostroma Oblast",
    })

@web_app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time_msk": now_msk().isoformat(),
        "monitor_interval": CHECK_INTERVAL,
        "subscribers": len(subscribers),
    })

def run_web():
    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )

# ============================================================
# STATE
# ============================================================

def now_msk():
    return datetime.now(MSK)

def normalize(value):
    return re.sub(
        r"\s+",
        " ",
        (value or "").casefold().replace("ё", "е"),
    ).strip()

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
        json.dump(
            value,
            file,
            ensure_ascii=False,
            indent=2,
        )
    os.replace(temporary, path)

state = load_json(STATE_FILE, {})

if not isinstance(state, dict):
    state = {}

DEFAULT_STATE = {
    "seen": {},
    "subscribers": [],
    "last_scan": None,
    "last_errors": {},
    "initialized": {},
    "pending": {},
    "approved": {},
    "rejected": {},
    "active_alerts": {},
    "news_seen": {},
    "news_initialized": {},
    "news_last_errors": {},
    "event_history": [],
    "news_history": [],
}

for key, default in DEFAULT_STATE.items():
    state.setdefault(key, default)

subscribers = set()

for value in load_json(
    SUBSCRIBERS_FILE,
    state.get("subscribers", []),
):
    try:
        subscribers.add(int(value))
    except (TypeError, ValueError):
        pass

startup_backfill_done = set()
monitor_task = None

def save_state():
    state["subscribers"] = sorted(subscribers)
    save_json(STATE_FILE, state)
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))

# ============================================================
# REGION AND EVENT CLASSIFICATION
# ============================================================

def mentions_kostroma(text):
    value = normalize(text)

    if "костромской области" in value:
        return True

    return any(term in value for term in TERRITORY_TERMS)

def classify_mwsh(text):
    value = normalize(text)

    if MWSH_CANCEL_RE.search(value):
        return "mwsh_cancel", "Отбой опасности по МВШ"

    if MWSH_DANGER_RE.search(value):
        return "mwsh_danger", "Опасность по МВШ"

    return None

def classify_rocket(text):
    value = normalize(text)

    if ROCKET_CANCEL_RE.search(value):
        return "rocket_cancel", "Отбой ракетной опасности"

    if ROCKET_NEGATION_RE.search(value):
        return None

    if ROCKET_RE.search(value):
        return "rocket_danger", "Ракетная опасность"

    return None

def classify_uav(text):
    value = normalize(text)

    if UAV_CANCEL_RE.search(value):
        return "cancel", "Отбой опасности по БПЛА"

    if not UAV_ALERT_RE.search(value):
        return None

    if UAV_DANGER_RE.search(value) or "беспилотной опасности" in value:
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
    return kind in (
        "cancel",
        "rocket_cancel",
        "mwsh_cancel",
    )

def event_is_danger(kind):
    return kind in (
        "danger",
        "threat",
        "attention",
        "mwsh_danger",
        "rocket_danger",
    )

def event_title(kind):
    return {
        "mwsh_cancel": (
            "🟢 Отбой опасности по МВШ",
            "МВШ",
        ),
        "mwsh_danger": (
            "🔴 Опасность по МВШ",
            "МВШ",
        ),
        "rocket_cancel": (
            "🟢 Отбой ракетной опасности",
            "Ракетная опасность",
        ),
        "rocket_danger": (
            "🔴 Ракетная опасность",
            "Ракетная опасность",
        ),
        "cancel": (
            "🟢 Отбой опасности по БПЛА",
            "БПЛА",
        ),
        "threat": (
            "🟠 Угроза по БПЛА",
            "БПЛА",
        ),
        "attention": (
            "🟡 Внимание: БПЛА",
            "БПЛА",
        ),
        "danger": (
            "🔴 Опасность по БПЛА",
            "БПЛА",
        ),
    }.get(kind, ("ℹ️ Сообщение", "Информация"))

def format_event_time(value):
    parsed = parse_iso(value)

    if parsed:
        return parsed.strftime("%d.%m.%Y %H:%M МСК")

    return "не указано"

# ============================================================
# TELEGRAM SOURCE PARSER
# ============================================================

def parse_published_time(node):
    time_node = node.select_one(
        "a.tgme_widget_message_date time"
    )

    if not time_node:
        return None

    raw = time_node.get("datetime")

    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )

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

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        page_posts = []
        numeric_ids = []

        for node in soup.select(".tgme_widget_message"):
            text_node = node.select_one(
                ".tgme_widget_message_text"
            )

            if not text_node:
                continue

            text = text_node.get_text(" ", strip=True)

            if not text:
                continue

            link_node = node.select_one(
                "a.tgme_widget_message_date"
            )

            post_url = (
                link_node.get("href", "")
                if link_node else source_url
            )

            post_id = (
                node.get("data-post", "")
                or post_url
            )

            published_at = parse_published_time(node)

            tail = str(post_id).rsplit("/", 1)[-1]

            if tail.isdigit():
                numeric_ids.append(int(tail))

            digest = hashlib.sha256(
                (
                    str(post_id) + "|" + text
                ).encode("utf-8")
            ).hexdigest()

            post = {
                "key": digest,
                "text": text[:5000],
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

        if page_number + 1 >= max_pages:
            break

        if not numeric_ids:
            break

        before_id = min(numeric_ids)

        if before_id in seen_page_ids:
            break

        seen_page_ids.add(before_id)

        separator = "&" if "?" in source_url else "?"

        page_url = (
            f"{source_url}{separator}before={before_id}"
        )

    posts = list(posts_by_key.values())

    posts.sort(
        key=lambda post: (
            parse_iso(post.get("published_at"))
            or datetime.min.replace(tzinfo=MSK)
        )
    )

    return posts

# ============================================================
# EVENT TEXT AND PHOTOS
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
    kind,
    label,
    source,
    url,
    source_text,
    published_at=None,
):
    title, category = event_title(kind)

    stamp = html.escape(
        format_event_time(published_at)
    )

    safe_url = html.escape(url, quote=True)
    safe_source = html.escape(source)

    return (
        "<b>🛰 UAV ALERT</b>\n"
        "📍 <b>Регион:</b> Костромская область\n\n"
        f"<b>{title}</b>\n"
        f"Категория: {html.escape(category)}\n"
        f"Время исходного сообщения: {stamp}\n\n"
        "📍 <b>Территории:</b> Костромская область\n\n"
        f'📡 <b>Источник:</b> <a href="{safe_url}">{safe_source}</a>\n\n'
        "⚠️ Сообщение получено из стороннего источника "
        "и не является самостоятельным подтверждением угрозы. "
        "Сверяйте информацию с официальными оповещениями "
        "МЧС и органов власти. Следуйте инструкциям "
        "экстренных служб.\n\n"
        "<i>UAV ALERT не является официальным источником.</i>"
    )

async def send_text_or_photo(
    bot,
    chat_id,
    message,
    is_cancel=False,
    kind=None,
):
    image_path = (
        event_image_path(kind)
        if kind
        else (
            LEGACY_CLEAR_IMAGE
            if is_cancel
            else LEGACY_DANGER_IMAGE
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
                )
        else:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
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
        )

# ============================================================
# MODERATION
# ============================================================

def moderation_keyboard(pending_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить и опубликовать",
                callback_data=f"approve:{pending_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{pending_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔎 Центр событий",
                callback_data="admin_center",
            ),
        ],
    ])

def conflict_warning(kind):
    family = event_family(kind)
    opposite = event_is_cancel(kind)
    matches = []

    for item in state.get("pending", {}).values():
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
            "В очереди имеется недавнее сообщение с другим "
            "статусом этой категории. Проверьте источники "
            "и официальные оповещения."
        )

    return ""

def add_history(item, decision, actor_id=None):
    history = state.setdefault("event_history", [])

    history.append({
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

    state["event_history"] = history[-500:]

def record_approved_event(item):
    active = state.setdefault("active_alerts", {})
    family = event_family(item["kind"])

    if event_is_cancel(item["kind"]):
        for key in list(active):
            if event_family(key) == family:
                active.pop(key, None)
        return

    if event_is_danger(item["kind"]):
        event_time = (
            parse_iso(item.get("published_at"))
            or now_msk()
        )

        active[item["kind"]] = {
            "label": item["label"],
            "published_at": event_time.isoformat(),
            "expires_at": (
                event_time + ALERT_TTL
            ).isoformat(),
            "source": item["source"],
            "url": item["url"],
            "stale": False,
        }

async def notify_subscribers(bot, message, kind=None):
    failed = []

    for user_id in list(subscribers):
        try:
            await send_text_or_photo(
                bot,
                user_id,
                message,
                is_cancel=(
                    event_is_cancel(kind)
                    if kind else False
                ),
                kind=kind,
            )
        except TelegramError as exc:
            logger.warning(
                "Ошибка отправки подписчику %s: %s",
                user_id,
                exc,
            )
            failed.append(user_id)

    return failed

async def send_event(
    application,
    kind,
    label,
    source,
    url,
    text,
    published_at=None,
):
    pending_id = hashlib.sha256(
        (
            f"{url}|{text}|{published_at or ''}"
        ).encode("utf-8")
    ).hexdigest()[:16]

    if pending_id in state["approved"]:
        return

    if pending_id in state["pending"]:
        return

    if pending_id in state["rejected"]:
        return

    item = {
        "id": pending_id,
        "kind": kind,
        "label": label,
        "source": source,
        "url": url,
        "text": text[:5000],
        "published_at": published_at,
        "message": event_message(
            kind,
            label,
            source,
            url,
            text,
            published_at,
        ),
        "created_at": now_msk().isoformat(
            timespec="seconds"
        ),
    }

    state["pending"][pending_id] = item
    save_state()

    admin_message = (
        "🛡 <b>Новое событие — требуется проверка</b>\n\n"
        f"{item['message']}"
        f"{conflict_warning(kind)}\n\n"
        "Проверьте исходную публикацию и официальные "
        "оповещения. Публикация произойдёт только "
        "после вашего решения."
    )

    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=moderation_keyboard(pending_id),
        )

        logger.info(
            "Событие добавлено в очередь: %s",
            pending_id,
        )

    except TelegramError:
        logger.exception(
            "Не удалось отправить событие администратору"
        )

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

    try:
        await send_text_or_photo(
            context.bot,
            CHANNEL_ID,
            item["message"],
            is_cancel=event_is_cancel(item["kind"]),
            kind=item["kind"],
        )

    except TelegramError:
        logger.exception("Ошибка публикации в канал")

        await query.answer(
            "Ошибка публикации. Проверь права бота и CHANNEL_ID.",
            show_alert=True,
        )
        return

    state["pending"].pop(pending_id, None)

    state["approved"][pending_id] = (
        now_msk().isoformat(timespec="seconds")
    )

    record_approved_event(item)

    add_history(
        item,
        "published",
        query.from_user.id,
    )

    save_state()

    failed = await notify_subscribers(
        context.bot,
        item["message"],
        item["kind"],
    )

    try:
        await query.edit_message_text(
            "✅ <b>Событие опубликовано в канал</b>\n\n"
            f"{item['message']}\n\n"
            f"Уведомлено подписчиков: "
            f"{len(subscribers) - len(failed)}.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except (BadRequest, TelegramError):
        pass

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

    state["rejected"][pending_id] = (
        now_msk().isoformat(timespec="seconds")
    )

    add_history(
        item,
        "rejected",
        query.from_user.id,
    )

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
# BACKFILL AND MONITORING
# ============================================================

async def send_backfill_notice(
    application,
    name,
    post,
    kind,
    label,
):
    title, category = event_title(kind)

    draft = (
        "🛰 <b>UAV ALERT — архивная информация</b>\n"
        "📍 Регион: Костромская область\n\n"
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

    await application.bot.send_message(
        chat_id=ADMIN_ID,
        text=draft,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def monitor_source(application, name, url):
    first_scan = url not in startup_backfill_done
    startup_backfill_done.add(url)

    cutoff = (
        now_msk()
        - timedelta(hours=HISTORY_BACKFILL_HOURS)
    )

    pages = (
        HISTORY_MAX_PAGES
        if first_scan else 1
    )

    posts = await asyncio.to_thread(
        source_posts,
        url,
        pages,
        cutoff if first_scan else None,
    )

    seen_map = state.setdefault("seen", {})
    source_seen = set(seen_map.get(url, []))

    for post in posts:
        key = post["key"]

        if key in source_seen:
            continue

        source_seen.add(key)

        published = parse_iso(post.get("published_at"))

        if first_scan:
            if not published or published < cutoff:
                continue

        text = post["text"]

        if not mentions_kostroma(text):
            continue

        result = classify(text)

        if not result:
            continue

        kind, label = result

        if first_scan:
            if kind not in (
                "danger",
                "attention",
                "cancel",
            ):
                continue

            try:
                await send_backfill_notice(
                    application,
                    name,
                    post,
                    kind,
                    label,
                )
            except TelegramError:
                logger.exception(
                    "Ошибка отправки архивной записи"
                )

            continue

        await send_event(
            application,
            kind,
            label,
            name,
            post["url"],
            text,
            post.get("published_at"),
        )

    ordered = [
        post["key"]
        for post in posts
        if post["key"] in source_seen
    ]

    seen_map[url] = ordered[-500:]

    state.setdefault("initialized", {})[url] = True
    state.setdefault("last_errors", {}).pop(url, None)
    state["last_scan"] = now_msk().isoformat()

    save_state()

def is_safe_regional_news(text):
    return bool(
        text
        and mentions_kostroma(text)
        and not NEWS_SENSITIVE_RE.search(text)
    )

async def monitor_news_source(application, name, url):
    posts = await asyncio.to_thread(
        source_posts,
        url,
        1,
        None,
    )

    seen_map = state.setdefault("news_seen", {})
    source_seen = set(seen_map.get(url, []))

    initialized = state.setdefault(
        "news_initialized", {}
    ).get(url, False)

    if not initialized:
        seen_map[url] = [
            post["key"] for post in posts
        ][-300:]

        state["news_initialized"][url] = True
        save_state()
        return

    for post in posts:
        if post["key"] in source_seen:
            continue

        source_seen.add(post["key"])

        if not is_safe_regional_news(post["text"]):
            continue

        published = format_event_time(
            post.get("published_at")
        )

        message = (
            "📰 <b>Региональная новость — на проверку</b>\n"
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

            history = state.setdefault(
                "news_history", []
            )

            history.append({
                "source": name,
                "url": post["url"],
                "text": post["text"][:1500],
                "published_at": post.get("published_at"),
                "received_at": now_msk().isoformat(),
            })

            state["news_history"] = history[-300:]

        except TelegramError:
            logger.exception(
                "Ошибка отправки региональной новости"
            )

    seen_map[url] = [
        post["key"]
        for post in posts
        if post["key"] in source_seen
    ][-300:]

    save_state()

def expire_active_alerts():
    active = state.setdefault("active_alerts", {})
    now = now_msk()
    changed = False

    for key, item in list(active.items()):
        expires = parse_iso(item.get("expires_at"))

        if not expires:
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
        "Мониторинг запущен: интервал=%s сек.",
        CHECK_INTERVAL,
    )

    while True:
        for name, url in SOURCES:
            try:
                await monitor_source(
                    application,
                    name,
                    url,
                )

            except Exception as exc:
                state.setdefault(
                    "last_errors", {}
                )[url] = str(exc)[:500]

                save_state()

                logger.exception(
                    "Ошибка мониторинга источника %s",
                    name,
                )

        for name, url in NEWS_SOURCES:
            try:
                await monitor_news_source(
                    application,
                    name,
                    url,
                )

            except Exception as exc:
                state.setdefault(
                    "news_last_errors", {}
                )[url] = str(exc)[:500]

                save_state()

                logger.exception(
                    "Ошибка новостного источника %s",
                    name,
                )

        expire_active_alerts()

        await asyncio.sleep(CHECK_INTERVAL)

# ============================================================
# SUBSCRIPTIONS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Подписаться на Костромскую область",
                callback_data="subscribe",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="status",
            ),
        ],
        [
            InlineKeyboardButton(
                "ℹ️ О проекте",
                callback_data="about",
            ),
        ],
    ])

    await update.message.reply_text(
        "🛰 <b>UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "Костромской области.\n\n"
        "Здесь можно подписаться на уведомления "
        "и посмотреть информацию о работе сервиса.\n\n"
        "⚠️ Мы не являемся МЧС или официальным "
        "источником оповещений. Не используйте "
        "наши сообщения как единственное подтверждение "
        "опасности. Следите за сообщениями органов власти "
        "и экстренных служб.",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    subscribers.add(user.id)
    save_state()

    await update.message.reply_text(
        "🔔 Вы подписались на уведомления "
        "UAV ALERT по Костромской области."
    )

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    subscribers.discard(user.id)
    save_state()

    await update.message.reply_text(
        "🔕 Вы отписались от уведомлений."
    )

# ============================================================
# STATUS, HISTORY, STATISTICS
# ============================================================

def build_status_text():
    active = state.get("active_alerts", {})

    lines = [
        "🛰 <b>UAV ALERT</b>",
        "📍 Костромская область",
        "",
    ]

    if not active:
        lines.append(
            "🟢 Опасность не объявлена "
            "по подтверждённым записям бота."
        )
    else:
        for item in active.values():
            if item.get("stale"):
                lines.append(
                    "⚪ Актуальность записи не подтверждена."
                )
            else:
                lines.append(
                    f"ℹ️ {html.escape(item.get('label', 'Событие'))}"
                )

    lines.extend([
        "",
        "Данные бота не заменяют официальные оповещения.",
    ])

    return "\n".join(lines)

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        build_status_text(),
        parse_mode=ParseMode.HTML,
    )

async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    history = state.get("event_history", [])[-10:]

    if not history:
        await update.message.reply_text(
            "История подтверждённых решений пока пуста."
        )
        return

    lines = ["📚 <b>Последние решения администратора</b>\n"]

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

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "Команда доступна только администратору."
        )
        return

    cutoff = now_msk() - timedelta(hours=24)

    recent = []

    for item in state.get("event_history", []):
        stamp = parse_iso(item.get("decision_at"))

        if stamp and stamp >= cutoff:
            recent.append(item)

    published = sum(
        1 for item in recent
        if item.get("decision") == "published"
    )

    rejected = sum(
        1 for item in recent
        if item.get("decision") == "rejected"
    )

    pending = len(state.get("pending", {}))

    text = (
        "📊 <b>Статистика за 24 часа</b>\n\n"
        f"Всего решений: {len(recent)}\n"
        f"Опубликовано: {published}\n"
        f"Отклонено: {rejected}\n"
        f"В очереди: {pending}\n"
        f"Подписчиков: {len(subscribers)}\n"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )

# ============================================================
# ADMIN CENTER
# ============================================================

def admin_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📥 Очередь событий",
                callback_data="admin_center",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin_stats",
            ),
        ],
        [
            InlineKeyboardButton(
                "📚 История",
                callback_data="admin_history",
            ),
        ],
        [
            InlineKeyboardButton(
                "📸 Проверить 7 фотографий",
                callback_data="admin_testphotos",
            ),
        ],
    ])

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if (
        not update.effective_user
        or update.effective_user.id != ADMIN_ID
    ):
        await update.message.reply_text(
            "Доступ запрещён."
        )
        return

    await update.message.reply_text(
        "🛡 <b>Панель администратора UAV ALERT</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(),
    )

async def show_admin_center(
    query,
    context,
):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "Доступ запрещён.",
            show_alert=True,
        )
        return

    pending = state.get("pending", {})

    if not pending:
        await query.message.reply_text(
            "📥 Очередь событий пуста."
        )
        await query.answer()
        return

    await query.message.reply_text(
        f"📥 В очереди событий: {len(pending)}"
    )

    for pending_id, item in list(pending.items())[-15:]:
        message = (
            "🛡 <b>Событие на проверку</b>\n\n"
            f"{item['message']}\n\n"
            f"ID: <code>{pending_id}</code>"
        )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=moderation_keyboard(pending_id),
        )

    await query.answer()

# ============================================================
# TEST PHOTOS — ALL 7 PHOTOS
# ============================================================

async def test_photos_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if (
        not update.effective_user
        or update.effective_user.id != ADMIN_ID
    ):
        await update.message.reply_text(
            "⛔ Эта команда доступна только администратору."
        )
        return

    await update.message.reply_text(
        "📸 <b>Проверка фотографий UAV ALERT</b>\n"
        "Начинаю отправку семи изображений.",
        parse_mode=ParseMode.HTML,
    )

    sent = 0
    missing = []

    for title, image_path in PHOTO_LIST:
        if not os.path.isfile(image_path):
            missing.append(title)

            await update.message.reply_text(
                f"⚠️ Не найден файл: "
                f"<code>{html.escape(os.path.basename(image_path))}</code>",
                parse_mode=ParseMode.HTML,
            )
            continue

        try:
            with open(image_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=ADMIN_ID,
                    photo=photo,
                    caption=f"📷 {title}",
                )

            sent += 1

        except TelegramError as exc:
            logger.exception(
                "Ошибка тестовой фотографии: %s",
                title,
            )

            await update.message.reply_text(
                f"❌ Ошибка отправки «{title}»: {exc}"
            )

    result = (
        "✅ <b>Проверка завершена</b>\n\n"
        f"Отправлено: {sent}/7\n"
        f"Не найдено: {len(missing)}"
    )

    await update.message.reply_text(
        result,
        parse_mode=ParseMode.HTML,
    )

# ============================================================
# WEATHER
# ============================================================

def get_weather():
    response = requests.get(
        WEATHER_URL,
        params={
            "latitude": KOSTROMA_LAT,
            "longitude": KOSTROMA_LON,
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

async def weather_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        data = await asyncio.to_thread(get_weather)
        current = data.get("current", {})

        temperature = current.get("temperature_2m", "—")
        humidity = current.get("relative_humidity_2m", "—")
        wind = current.get("wind_speed_10m", "—")

        await update.message.reply_text(
            "🌤 <b>Погода в Костроме</b>\n\n"
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
# AI COMMANDS
# ============================================================

async def ai_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not AI_AVAILABLE:
        await update.message.reply_text(
            "ИИ-модуль сейчас недоступен."
        )
        return

    text = " ".join(context.args).strip()

    if not text:
        await update.message.reply_text(
            "Использование: /ai текст для анализа"
        )
        return

    try:
        result = await asyncio.to_thread(
            analyze_text,
            text,
        )

        await update.message.reply_text(
            str(result)[:4000],
        )

    except Exception as exc:
        logger.exception("Ошибка ИИ-анализа")

        await update.message.reply_text(
            f"Ошибка ИИ-модуля: {html.escape(str(exc)[:300])}"
        )

async def ai_status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not AI_AVAILABLE:
        await update.message.reply_text(
            "ИИ-модуль недоступен."
        )
        return

    try:
        result = await asyncio.to_thread(ai_status_text)

        await update.message.reply_text(
            str(result)[:4000],
        )

    except Exception:
        logger.exception("Ошибка AI status")

        await update.message.reply_text(
            "Не удалось получить статус ИИ."
        )

async def ai_history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not AI_AVAILABLE:
        await update.message.reply_text(
            "ИИ-модуль недоступен."
        )
        return

    try:
        result = await asyncio.to_thread(ai_history_text)

        await update.message.reply_text(
            str(result)[:4000],
        )

    except Exception:
        logger.exception("Ошибка AI history")

        await update.message.reply_text(
            "Не удалось получить историю ИИ."
        )

# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    if data == "subscribe":
        subscribers.add(query.from_user.id)
        save_state()

        await query.answer(
            "Вы подписались на уведомления!"
        )

        await query.message.reply_text(
            "🔔 Подписка оформлена."
        )
        return

    if data == "status":
        await query.answer()

        await query.message.reply_text(
            build_status_text(),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "about":
        await query.answer()

        await query.message.reply_text(
            "🛰 UAV ALERT — гражданский информационный сервис.\n\n"
            "Мы не являемся МЧС или официальным источником. "
            "Информация требует самостоятельной проверки.",
        )
        return

    if data.startswith("approve:"):
        pending_id = data.split(":", 1)[1]

        await approve_event(
            query,
            context,
            pending_id,
        )
        return

    if data.startswith("reject:"):
        pending_id = data.split(":", 1)[1]

        await reject_event(
            query,
            pending_id,
        )
        return

    if data == "admin_center":
        await show_admin_center(
            query,
            context,
        )
        return

    if data == "admin_stats":
        if query.from_user.id != ADMIN_ID:
            await query.answer(
                "Доступ запрещён.",
                show_alert=True,
            )
            return

        await query.answer()

        cutoff = now_msk() - timedelta(hours=24)

        recent = [
            item
            for item in state.get("event_history", [])
            if (
                parse_iso(item.get("decision_at"))
                and parse_iso(item.get("decision_at")) >= cutoff
            )
        ]

        published = sum(
            item.get("decision") == "published"
            for item in recent
        )

        rejected = sum(
            item.get("decision") == "rejected"
            for item in recent
        )

        await query.message.reply_text(
            "📊 <b>Статистика за 24 часа</b>\n\n"
            f"Опубликовано: {published}\n"
            f"Отклонено: {rejected}\n"
            f"В очереди: {len(state.get('pending', {}))}\n"
            f"Подписчиков: {len(subscribers)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "admin_history":
        if query.from_user.id != ADMIN_ID:
            await query.answer(
                "Доступ запрещён.",
                show_alert=True,
            )
            return

        await query.answer()

        history = state.get("event_history", [])[-10:]

        if not history:
            await query.message.reply_text(
                "История пока пуста."
            )
            return

        lines = ["📚 <b>История событий</b>\n"]

        for item in reversed(history):
            lines.append(
                f"• {html.escape(item.get('label') or 'Событие')}\n"
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
            await query.answer(
                "Доступ запрещён.",
                show_alert=True,
            )
            return

        await query.answer(
            "Отправляю фотографии..."
        )

        sent = 0

        for title, path in PHOTO_LIST:
            if not os.path.isfile(path):
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=(
                        f"⚠️ Не найден файл: "
                        f"{os.path.basename(path)}"
                    ),
                )
                continue

            try:
                with open(path, "rb") as photo:
                    await context.bot.send_photo(
                        chat_id=ADMIN_ID,
                        photo=photo,
                        caption=title,
                    )

                sent += 1

            except TelegramError:
                logger.exception(
                    "Ошибка отправки тестовой фотографии"
                )

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"Проверка завершена: {sent}/7 фотографий.",
        )
        return

    await query.answer()

# ============================================================
# COMMANDS AND STARTUP
# ============================================================

async def post_init(application):
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Текущий статус"),
        BotCommand("weather", "Погода в Костроме"),
        BotCommand("history", "История событий"),
        BotCommand("admin", "Панель администратора"),
        BotCommand("stats", "Статистика"),
        BotCommand("testphotos", "Проверить 7 фотографий"),
        BotCommand("ai", "ИИ-анализ текста"),
        BotCommand("ai_status", "Статус ИИ"),
        BotCommand("ai_history", "История ИИ"),
    ]

    await application.bot.set_my_commands(commands)

    global monitor_task

    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(
            monitor_loop(application)
        )

    logger.info("UAV ALERT успешно запущен")

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

    application.add_handler(
        CommandHandler("start", start_command)
    )

    application.add_handler(
        CommandHandler("subscribe", subscribe_command)
    )

    application.add_handler(
        CommandHandler("unsubscribe", unsubscribe_command)
    )

    application.add_handler(
        CommandHandler("status", status_command)
    )

    application.add_handler(
        CommandHandler("history", history_command)
    )

    application.add_handler(
        CommandHandler("stats", stats_command)
    )

    application.add_handler(
        CommandHandler("admin", admin_command)
    )

    application.add_handler(
        CommandHandler("testphotos", test_photos_command)
    )

    application.add_handler(
        CommandHandler("weather", weather_command)
    )

    application.add_handler(
        CommandHandler("ai", ai_command)
    )

    application.add_handler(
        CommandHandler("ai_status", ai_status_command)
    )

    application.add_handler(
        CommandHandler("ai_history", ai_history_command)
    )

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    logger.info("Запуск polling...")

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
