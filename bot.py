import os
import re
import json
import time
import hashlib
import asyncio
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional
import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(
    os.getenv(
        "ADMIN_ID",
        "1421675956",
    )
)
PORT = int(
    os.getenv(
        "PORT",
        "10000",
    )
)
CHECK_INTERVAL = int(
    os.getenv(
        "CHECK_INTERVAL",
        "30",
    )
)
MSK = ZoneInfo("Europe/Moscow")
DATA_DIR = (
    os.getenv(
        "DATA_DIR",
        ".",
    ).strip()
    or "."
)
STATE_FILE = os.path.join(
    DATA_DIR,
    "state.json",
)
HISTORY_FILE = os.path.join(
    DATA_DIR,
    "history.json",
)
SUBSCRIBERS_FILE = os.path.join(
    DATA_DIR,
    "subscribers.json",
)
SENT_POSTS_FILE = os.path.join(
    DATA_DIR,
    "sent_posts.json",
)
NOTIFICATION_EVENTS_FILE = os.path.join(
    DATA_DIR,
    "notification_events.json",
)
SOURCE_STATE_FILE = os.path.join(
    DATA_DIR,
    "source_state.json",
)
LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).upper()
# ============================================================
# ACCESS MODE
# ============================================================
# True = пользоваться ботом может только ADMIN_ID.
# Старые подписчики push полностью очищаются.
ADMIN_ONLY = True
# ============================================================
# LOGGING
# ============================================================
logging.basicConfig(
    level=getattr(
        logging,
        LOG_LEVEL,
        logging.INFO,
    ),
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(message)s"
    ),
)
logger = logging.getLogger(
    "UAV_ALERT"
)
# ============================================================
# FLASK
# ============================================================
app = Flask(__name__)
@app.route("/")
def index():
    return "UAV ALERT работает!"
@app.route("/health")
def health():
    return {
        "status": "ok",
        "service": "UAV ALERT",
        "time": now_msk_str(),
    }
# ============================================================
# LOCK
# ============================================================
file_lock = threading.RLock()
# ============================================================
# TIME
# ============================================================
def now_msk() -> datetime:
    return datetime.now(MSK)
def now_msk_str() -> str:
    return now_msk().strftime(
        "%d.%m.%Y %H:%M:%S"
    )
def iso_now() -> str:
    return now_msk().isoformat()
# ============================================================
# JSON
# ============================================================
def load_json(
    path: str,
    default: Any,
) -> Any:
    with file_lock:
        try:
            if not os.path.exists(path):
                return default
            with open(
                path,
                "r",
                encoding="utf-8",
            ) as f:
                return json.load(f)
        except Exception as e:
            logger.error(
                "Ошибка чтения %s: %s",
                path,
                e,
            )
            return default
def save_json(
    path: str,
    data: Any,
) -> None:
    with file_lock:
        tmp = path + ".tmp"
        try:
            directory = (
                os.path.dirname(path)
                or "."
            )
            os.makedirs(
                directory,
                exist_ok=True,
            )
            with open(
                tmp,
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(
                tmp,
                path,
            )
        except Exception as e:
            logger.error(
                "Ошибка сохранения %s: %s",
                path,
                e,
            )
# ============================================================
# DEFAULT STATE
# ============================================================
DEFAULT_STATE = {
    "uav_level": 0,
    "uav_cycle": 0,
    "rocket_active": False,
    "rocket_cycle": 0,
    "active_locations": [],
    "last_uav_title": "",
    "last_uav_source": "",
    "last_uav_url": "",
    "last_uav_time": "",
    "last_rocket_title": "",
    "last_rocket_source": "",
    "last_rocket_url": "",
    "last_rocket_time": "",
    "last_update": "",
}
# ============================================================
# STATE
# ============================================================
state = load_json(
    STATE_FILE,
    DEFAULT_STATE.copy(),
)
if not isinstance(
    state,
    dict,
):
    state = DEFAULT_STATE.copy()
def normalize_state():
    changed = False
    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value
            changed = True
    if not isinstance(
        state.get(
            "active_locations"
        ),
        list,
    ):
        state[
            "active_locations"
        ] = []
        changed = True
    if changed:
        save_json(
            STATE_FILE,
            state,
        )
normalize_state()
# ============================================================
# HISTORY
# ============================================================
history = load_json(
    HISTORY_FILE,
    [],
)
if not isinstance(
    history,
    list,
):
    history = []
# ============================================================
# SUBSCRIBERS
# ============================================================
subscribers = load_json(
    SUBSCRIBERS_FILE,
    [],
)
if not isinstance(
    subscribers,
    list,
):
    subscribers = []
# ============================================================
# ADMIN ONLY:
# CLEAR OLD SUBSCRIBERS
# ============================================================
if ADMIN_ONLY:
    if subscribers != [ADMIN_ID]:
        logger.info(
            "ADMIN_ONLY=True: очищаем старых подписчиков."
        )
        subscribers = [ADMIN_ID]
        save_json(
            SUBSCRIBERS_FILE,
            subscribers,
        )
# ============================================================
# SENT POSTS
# ============================================================
sent_posts = load_json(
    SENT_POSTS_FILE,
    {},
)
if not isinstance(
    sent_posts,
    dict,
):
    sent_posts = {}
# ============================================================
# NOTIFICATION EVENTS
# ============================================================
notification_events = load_json(
    NOTIFICATION_EVENTS_FILE,
    [],
)
if not isinstance(
    notification_events,
    list,
):
    notification_events = []
# ============================================================
# SOURCE STATE
# ============================================================
source_state = load_json(
    SOURCE_STATE_FILE,
    {},
)
if not isinstance(
    source_state,
    dict,
):
    source_state = {}
# ============================================================
# SOURCES
# ============================================================
SOURCES = {
    "locator": {
        "name": "LOCATOR",
        "url": (
            "https://t.me/s/locatorru"
        ),
    },
    "monitoring": {
        "name": "RUSSIA MONITORING",
        "url": (
            "https://t.me/s/"
            "russiamonitoring_radar_bpla"
        ),
    },
    "radar": {
        "name": "RADAR",
        "url": (
            "https://t.me/s/"
            "radarrussiia"
        ),
    },
    "bpla": {
        "name": "BPLA",
        "url": (
            "https://t.me/s/"
            "bplarussiaru"
        ),
    },
}
# ============================================================
# KOSTROMA LOCATIONS
# ============================================================
KOSTROMA_LOCATIONS = [
    "Кострома",
    "Костромская область",
    "Буй",
    "Буйский округ",
    "Волгореченск",
    "Галич",
    "Галичский округ",
    "Шарья",
    "Шарьинский округ",
    "Мантурово",
    "Мантуровский округ",
    "Нерехта",
    "Нерехтский округ",
    "Чухлома",
    "Чухломский округ",
    "Макарьев",
    "Макарьевский округ",
    "Солигалич",
    "Солигаличский округ",
    "Кологрив",
    "Кологривский округ",
    "Нея",
    "Нейский округ",
    "Островское",
    "Островский округ",
    "Парфеньево",
    "Парфеньевский округ",
    "Поназырево",
    "Поназыревский округ",
    "Пыщуг",
    "Пыщугский округ",
    "Судиславль",
    "Судиславский округ",
    "Сусанинский округ",
    "Сусанино",
    "Антропово",
    "Антроповский округ",
    "Вохма",
    "Вохомский округ",
    "Кадый",
    "Кадыйский округ",
    "Красное-на-Волге",
    "Красносельский округ",
    "Октябрьский",
    "Октябрьский округ",
]
# ============================================================
# LEVELS
# ============================================================
LEVEL_NONE = 0
LEVEL_ATTENTION = 1
LEVEL_THREAT = 2
LEVEL_DANGER = 3
LEVEL_NAMES = {
    LEVEL_NONE:
        "Нет опасности",
    LEVEL_ATTENTION:
        "Внимание по БПЛА",
    LEVEL_THREAT:
        "Угроза по БПЛА",
    LEVEL_DANGER:
        "Опасность по БПЛА",
}
LEVEL_EMOJI = {
    LEVEL_NONE:
        "🟢",
    LEVEL_ATTENTION:
        "🟡",
    LEVEL_THREAT:
        "🟠",
    LEVEL_DANGER:
        "🔴",
}
# ============================================================
# TEXT
# ============================================================
def normalize_text(
    text: str,
) -> str:
    if not text:
        return ""
    text = text.replace(
        "\xa0",
        " ",
    )
    text = text.replace(
        "\u200b",
        "",
    )
    text = text.replace(
        "\ufeff",
        "",
    )
    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )
    return text.strip()
def text_lower(
    text: str,
) -> str:
    return normalize_text(
        text
    ).lower()
# ============================================================
# HTTP
# ============================================================
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept-Language":
        "ru-RU,ru;q=0.9,en;q=0.8",
}
# ============================================================
# FETCH SOURCE
# ============================================================
def fetch_source(
    source_key: str,
) -> List[Dict[str, Any]]:
    source = SOURCES[
        source_key
    ]
    try:
        response = requests.get(
            source["url"],
            headers=HTTP_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )
        result = []
        messages = soup.select(
            "div.tgme_widget_message"
        )
        for message in messages:
            text_node = (
                message.select_one(
                    ".tgme_widget_message_text"
                )
            )
            if text_node:
                text = text_node.get_text(
                    "\n",
                    strip=True,
                )
            else:
                text = message.get_text(
                    "\n",
                    strip=True,
                )
            text = normalize_text(
                text
            )
            if not text:
                continue
            data_post = message.get(
                "data-post"
            )
            post_link = ""
            if data_post:
                post_link = (
                    "https://t.me/"
                    + str(data_post)
                )
            if not post_link:
                link = (
                    message.select_one(
                        "a.tgme_widget_message_date"
                    )
                )
                if link:
                    post_link = (
                        link.get(
                            "href"
                        )
                        or ""
                    )
            if not post_link:
                post_link = source[
                    "url"
                ]
            post_id = ""
            if data_post:
                post_id = str(
                    data_post
                )
            if not post_id:
                match = re.search(
                    r"/(\d+)$",
                    post_link,
                )
                if match:
                    post_id = (
                        match.group(1)
                    )
            date_node = (
                message.select_one(
                    "time"
                )
            )
            post_date = ""
            if date_node:
                post_date = (
                    date_node.get(
                        "datetime"
                    )
                    or ""
                )
            # ------------------------------------------------
            # STABLE POST KEY
            # ------------------------------------------------
            if post_id:
                stable_key = (
                    source_key
                    + ":"
                    + post_id
                )
            else:
                stable_key = (
                    source_key
                    + "|"
                    + post_link
                    + "|"
                    + text
                )
            post_hash = hashlib.sha256(
                stable_key.encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest()
            result.append(
                {
                    "source_key":
                        source_key,
                    "source_name":
                        source["name"],
                    "source_url":
                        source["url"],
                    "url":
                        post_link,
                    "post_id":
                        post_id,
                    "text":
                        text,
                    "date":
                        post_date,
                    "hash":
                        post_hash,
                }
            )
        # ----------------------------------------------------
        # SORT BY POST ID IF POSSIBLE
        # ----------------------------------------------------
        def sort_key(post):
            try:
                return int(
                    post.get(
                        "post_id",
                        "0",
                    )
                )
            except Exception:
                return 0
        result.sort(
            key=sort_key
        )
        return result
    except Exception as e:
        logger.error(
            "Ошибка получения %s: %s",
            source["name"],
            e,
        )
        return []
# ============================================================
# LOCATION
# ============================================================
def find_locations(
    text: str,
) -> List[str]:
    lower = text_lower(
        text
    )
    found = []
    for location in KOSTROMA_LOCATIONS:
        if location.lower() in lower:
            if location not in found:
                found.append(
                    location
                )
    return found
# ============================================================
# CANCEL
# ============================================================
def detect_cancel(
    text: str,
) -> bool:
    t = text_lower(
        text
    )
    patterns = [
        r"\bотбой\b.{0,80}\bбпла\b",
        r"\bотбой\b.{0,80}\bбеспилот",
        r"\bотбой\b.{0,80}\bдрон",
        r"\bбпла\b.{0,80}\bотбой\b",
        r"\bбеспилот.{0,80}\bотбой\b",
        r"\bдрон.{0,80}\bотбой\b",
        r"\bопасност\w*\b.{0,80}\bотмен",
        r"\bугроз\w*\b.{0,80}\bотмен",
        r"\bопасност\w*\b.{0,80}\bснят",
        r"\bугроз\w*\b.{0,80}\bснят",
    ]
    for pattern in patterns:
        if re.search(
            pattern,
            t,
        ):
            return True
    return False
# ============================================================
# ROCKET
# ============================================================
def detect_rocket(
    text: str,
) -> bool:
    t = text_lower(
        text
    )
    patterns = [
        r"ракетн\w*\s+опасност",
        r"ракетн\w*\s+угроз",
        r"угроз\w*\s+ракет",
    ]
    for pattern in patterns:
        if re.search(
            pattern,
            t,
        ):
            if not detect_cancel(
                text
            ):
                return True
    return False
# ============================================================
# UAV LEVEL
# ============================================================
def detect_uav_level(
    text: str,
) -> int:
    t = text_lower(
        text
    )
    # --------------------------------------------------------
    # DANGER
    # --------------------------------------------------------
    danger_patterns = [
        r"опасност\w*\s+по\s+бпла",
        r"опасност\w*\s+по\s+беспилот",
        r"опасност\w*\s+по\s+дрон",
        r"опасност\w*\s+бпла",
        r"опасност\w*\s+беспилот",
        r"опасност\w*\s+дрон",
        r"бпла.{0,40}опасност",
        r"беспилот.{0,40}опасност",
        r"дрон.{0,40}опасност",
        r"опасность атаки бпла",
        r"опасность атаки беспилотников",
    ]
    for pattern in danger_patterns:
        if re.search(
            pattern,
            t,
        ):
            return LEVEL_DANGER
    # --------------------------------------------------------
    # THREAT
    # --------------------------------------------------------
    threat_patterns = [
        r"угроз\w*\s+по\s+бпла",
        r"угроз\w*\s+по\s+беспилот",
        r"угроз\w*\s+по\s+дрон",
        r"угроз\w*\s+бпла",
        r"угроз\w*\s+беспилот",
        r"угроз\w*\s+дрон",
        r"бпла.{0,40}угроз",
        r"беспилот.{0,40}угроз",
        r"дрон.{0,40}угроз",
        r"угроза атаки бпла",
        r"угроза атаки беспилотников",
    ]
    for pattern in threat_patterns:
        if re.search(
            pattern,
            t,
        ):
            return LEVEL_THREAT
    # --------------------------------------------------------
    # ATTENTION
    # --------------------------------------------------------
    attention_patterns = [
        r"внимани\w*\s+по\s+бпла",
        r"внимани\w*\s+по\s+беспилот",
        r"внимани\w*\s+по\s+дрон",
        r"внимани\w*\s+бпла",
        r"внимани\w*\s+беспилот",
        r"внимани\w*\s+дрон",
        r"бпла.{0,40}внимани",
        r"беспилот.{0,40}внимани",
        r"дрон.{0,40}внимани",
    ]
    for pattern in attention_patterns:
        if re.search(
            pattern,
            t,
        ):
            return LEVEL_ATTENTION
    return LEVEL_NONE
# ============================================================
# PARSE
# ============================================================
def parse_post(
    post: Dict[str, Any],
) -> Dict[str, Any]:
    text = post.get(
        "text",
        "",
    )
    locations = find_locations(
        text
    )
    cancel = detect_cancel(
        text
    )
    rocket = detect_rocket(
        text
    )
    uav_level = LEVEL_NONE
    if not cancel:
        uav_level = detect_uav_level(
            text
        )
    return {
        **post,
        "locations":
            locations,
        "cancel":
            cancel,
        "rocket":
            rocket,
        "uav_level":
            uav_level,
    }
# ============================================================
# RELEVANCE
# ============================================================
def is_relevant_uav_post(
    event: Dict[str, Any],
) -> bool:
    level = int(
        event.get(
            "uav_level",
            0,
        )
        or 0
    )
    cancel = bool(
        event.get(
            "cancel",
            False,
        )
    )
    locations = event.get(
        "locations",
        [],
    )
    text = text_lower(
        event.get(
            "text",
            "",
        )
    )
    # --------------------------------------------------------
    # IMPORTANT:
    # We only accept actual UAV wording.
    # --------------------------------------------------------
    has_uav_word = (
        "бпла" in text
        or "беспилот" in text
        or "дрон" in text
    )
    if level > 0 and has_uav_word:
        return True
    if cancel and has_uav_word:
        return True
    if locations and has_uav_word:
        return True
    return False
# ============================================================
# HISTORY
# ============================================================
def add_history(
    event_type: str,
    title: str,
    source_name: str,
    source_url: str,
    post_url: str,
    locations: Optional[
        List[str]
    ] = None,
    level: int = 0,
) -> None:
    global history
    item = {
        "id":
            hashlib.sha256(
                (
                    event_type
                    + "|"
                    + title
                    + "|"
                    + post_url
                    + "|"
                    + iso_now()
                ).encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest()[:16],
        "type":
            event_type,
        "title":
            title,
        "level":
            level,
        "level_name":
            LEVEL_NAMES.get(
                level,
                "",
            ),
        "source":
            source_name,
        "source_url":
            source_url,
        "post_url":
            post_url,
        "locations":
            locations or [],
        "time":
            iso_now(),
        "time_msk":
            now_msk_str(),
    }
    history.insert(
        0,
        item,
    )
    history = history[:500]
    save_json(
        HISTORY_FILE,
        history,
    )
# ============================================================
# POST DEDUP
# ============================================================
def post_was_processed(
    post: Dict[str, Any],
) -> bool:
    key = post.get(
        "hash",
        "",
    )
    if not key:
        return False
    return key in sent_posts
def mark_post_processed(
    post: Dict[str, Any],
) -> None:
    global sent_posts
    key = post.get(
        "hash",
        "",
    )
    if not key:
        return
    sent_posts[key] = {
        "time":
            time.time(),
        "source":
            post.get(
                "source_key",
                "",
            ),
        "post_id":
            post.get(
                "post_id",
                "",
            ),
    }
    save_json(
        SENT_POSTS_FILE,
        sent_posts,
    )
# ============================================================
# NOTIFICATION DEDUP
# ============================================================
def notification_key(
    event_type: str,
    level: int,
    locations: List[str],
    post_hash: str = "",
) -> str:
    raw = (
        event_type
        + "|"
        + str(level)
        + "|"
        + "|".join(
            sorted(
                locations
            )
        )
        + "|"
        + post_hash
    )
    return hashlib.sha256(
        raw.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()
def notification_already_sent(
    key: str,
) -> bool:
    for item in notification_events:
        if (
            isinstance(
                item,
                dict,
            )
            and item.get(
                "key"
            ) == key
        ):
            return True
    return False
def remember_notification(
    key: str,
) -> None:
    global notification_events
    notification_events.insert(
        0,
        {
            "key":
                key,
            "time":
                iso_now(),
        },
    )
    notification_events = (
        notification_events[:1000]
    )
    save_json(
        NOTIFICATION_EVENTS_FILE,
        notification_events,
    )
# ============================================================
# STATUS
# ============================================================
def current_status_text() -> str:
    uav_level = int(
        state.get(
            "uav_level",
            0,
        )
    )
    rocket_active = bool(
        state.get(
            "rocket_active",
            False,
        )
    )
    lines = []
    if uav_level == LEVEL_NONE:
        lines.append(
            "🟢 <b>Опасность не объявлена</b>"
        )
    else:
        lines.append(
            f"{LEVEL_EMOJI[uav_level]} "
            f"<b>{LEVEL_NAMES[uav_level]}</b>"
        )
        locations = state.get(
            "active_locations",
            [],
        )
        if locations:
            lines.append("")
            lines.append(
                "<b>Районы:</b>"
            )
            for location in locations:
                lines.append(
                    f"• {location}"
                )
    if rocket_active:
        lines.append("")
        lines.append(
            "🟥 <b>Ракетная опасность</b>"
        )
    return "\n".join(
        lines
    )
# ============================================================
# KEYBOARD
# ============================================================
def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📡 Текущий статус",
                    callback_data="status",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 История",
                    callback_data="history",
                ),
                InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    "📡 Источники",
                    callback_data="sources",
                ),
            ],
        ]
    )
# ============================================================
# SOURCES
# ============================================================
def format_sources() -> str:
    lines = [
        "📡 <b>Источники UAV ALERT</b>",
        "",
        (
            "Информация собирается "
            "из открытых Telegram-источников."
        ),
        "",
    ]
    for source in SOURCES.values():
        lines.append(
            f"• <b>{source['name']}</b>"
        )
        lines.append(
            source["url"]
        )
        lines.append("")
    lines.append(
        "⚠️ UAV ALERT — гражданский "
        "информационный сервис. "
        "Проверяйте информацию "
        "по официальным каналам."
    )
    return "\n".join(
        lines
    )
# ============================================================
# HISTORY
# ============================================================
def format_history() -> str:
    if not history:
        return (
            "📜 <b>История</b>\n\n"
            "Пока событий нет."
        )
    lines = [
        "📜 <b>История UAV ALERT</b>",
        "",
    ]
    shown = 0
    for item in history:
        if not isinstance(
            item,
            dict,
        ):
            continue
        title = item.get(
            "title",
            "",
        )
        time_value = item.get(
            "time_msk",
            "",
        )
        level = int(
            item.get(
                "level",
                0,
            )
            or 0
        )
        emoji = LEVEL_EMOJI.get(
            level,
            "📌",
        )
        lines.append(
            f"{emoji} <b>{title}</b>"
        )
        if time_value:
            lines.append(
                f"🕒 {time_value}"
            )
        source = item.get(
            "source",
            "",
        )
        if source:
            lines.append(
                f"📡 {source}"
            )
        locations = item.get(
            "locations",
            [],
        )
        if locations:
            lines.append(
                "📍 "
                + ", ".join(
                    locations
                )
            )
        post_url = item.get(
            "post_url",
            "",
        )
        if post_url:
            lines.append(
                f'🔗 <a href="{post_url}">'
                f"Источник</a>"
            )
        lines.append("")
        shown += 1
        if shown >= 20:
            break
    return "\n".join(
        lines
    )
# ============================================================
# STATS
# ============================================================
def format_stats() -> str:
    attention = 0
    threat = 0
    danger = 0
    cancel = 0
    rocket = 0
    for item in history:
        if not isinstance(
            item,
            dict,
        ):
            continue
        event_type = item.get(
            "type",
            "",
        )
        level = int(
            item.get(
                "level",
                0,
            )
            or 0
        )
        if level == LEVEL_ATTENTION:
            attention += 1
        elif level == LEVEL_THREAT:
            threat += 1
        elif level == LEVEL_DANGER:
            danger += 1
        if event_type == "uav_cancel":
            cancel += 1
        if event_type == "rocket":
            rocket += 1
    current_level = int(
        state.get(
            "uav_level",
            0,
        )
    )
    current_name = LEVEL_NAMES.get(
        current_level,
        "Нет опасности",
    )
    return "\n".join(
        [
            "📊 <b>Статистика UAV ALERT</b>",
            "",
            f"🟡 Внимание: <b>{attention}</b>",
            f"🟠 Угроза: <b>{threat}</b>",
            f"🔴 Опасность: <b>{danger}</b>",
            f"🟢 Отбоев: <b>{cancel}</b>",
            f"🟥 Ракетных событий: <b>{rocket}</b>",
            "",
            f"📡 Текущий статус: <b>{current_name}</b>",
            f"👤 Администратор: <b>{ADMIN_ID}</b>",
        ]
    )
# ============================================================
# ACCESS
# ============================================================
def is_admin(
    user_id: Optional[int],
) -> bool:
    return (
        user_id is not None
        and int(user_id) == ADMIN_ID
    )
def access_allowed(
    user_id: Optional[int],
) -> bool:
    if not ADMIN_ONLY:
        return True
    return is_admin(
        user_id
    )
# ============================================================
# START
# ============================================================
async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    user_id = (
        update.effective_user.id
    )
    if not access_allowed(
        user_id
    ):
        if update.message:
            await update.message.reply_text(
                "⛔ Доступ к UAV ALERT ограничен."
            )
        return
    if not update.message:
        return
    text = (
        "🚨 <b>UAV ALERT</b>\n\n"
        "Гражданский информационный "
        "сервис по уведомлениям о БПЛА.\n\n"
        "Выберите нужный раздел:"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )
# ============================================================
# STATUS
# ============================================================
async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not access_allowed(
        update.effective_user.id
    ):
        return
    if not update.message:
        return
    await update.message.reply_text(
        current_status_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )
# ============================================================
# HISTORY
# ============================================================
async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not access_allowed(
        update.effective_user.id
    ):
        return
    if not update.message:
        return
    await update.message.reply_text(
        format_history(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )
# ============================================================
# STATS
# ============================================================
async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not access_allowed(
        update.effective_user.id
    ):
        return
    if not update.message:
        return
    await update.message.reply_text(
        format_stats(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )
# ============================================================
# SOURCES
# ============================================================
async def sources_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not access_allowed(
        update.effective_user.id
    ):
        return
    if not update.message:
        return
    await update.message.reply_text(
        format_sources(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )
# ============================================================
# CALLBACK
# ============================================================
async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query:
        return
    user_id = (
        query.from_user.id
        if query.from_user
        else 0
    )
    if not access_allowed(
        user_id
    ):
        await query.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return
    await query.answer()
    data = query.data
    if data == "status":
        await query.edit_message_text(
            current_status_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return
    if data == "history":
        await query.edit_message_text(
            format_history(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return
    if data == "stats":
        await query.edit_message_text(
            format_stats(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return
    if data == "sources":
        await query.edit_message_text(
            format_sources(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return
# ============================================================
# PUSH TEXT
# ============================================================
def build_uav_push(
    level: int,
    locations: List[str],
    event: Dict[str, Any],
) -> str:
    title = LEVEL_NAMES.get(
        level,
        "Изменение статуса",
    )
    emoji = LEVEL_EMOJI.get(
        level,
        "📢",
    )
    lines = [
        f"{emoji} <b>{title}</b>",
        "",
    ]
    if locations:
        lines.append(
            "<b>Районы:</b>"
        )
        for location in locations:
            lines.append(
                f"• {location}"
            )
        lines.append("")
    lines.append(
        f"🕒 {now_msk_str()}"
    )
    source_name = event.get(
        "source_name",
        "",
    )
    if source_name:
        lines.append(
            f"📡 Источник: "
            f"<b>{source_name}</b>"
        )
    post_url = event.get(
        "url",
        "",
    )
    if post_url:
        lines.append(
            f'🔗 <a href="{post_url}">'
            f"Источник</a>"
        )
    lines.append("")
    lines.append(
        "⚠️ Информационное сообщение. "
        "Проверяйте официальные источники."
    )
    return "\n".join(
        lines
    )
def build_uav_cancel_push(
    event: Dict[str, Any],
) -> str:
    lines = [
        "🟢 <b>Отбой по опасности БПЛА</b>",
        "",
        f"🕒 {now_msk_str()}",
    ]
    source_name = event.get(
        "source_name",
        "",
    )
    if source_name:
        lines.append(
            f"📡 Источник: "
            f"<b>{source_name}</b>"
        )
    post_url = event.get(
        "url",
        "",
    )
    if post_url:
        lines.append(
            f'🔗 <a href="{post_url}">'
            f"Источник</a>"
        )
    return "\n".join(
        lines
    )
def build_rocket_push(
    event: Dict[str, Any],
) -> str:
    lines = [
        "🟥 <b>Ракетная опасность</b>",
        "",
        f"🕒 {now_msk_str()}",
    ]
    source_name = event.get(
        "source_name",
        "",
    )
    if source_name:
        lines.append(
            f"📡 Источник: "
            f"<b>{source_name}</b>"
        )
    post_url = event.get(
        "url",
        "",
    )
    if post_url:
        lines.append(
            f'🔗 <a href="{post_url}">'
            f"Источник</a>"
        )
    return "\n".join(
        lines
    )
# ============================================================
# SEND PUSH
# ============================================================
async def send_push(
    application: Application,
    text: str,
    notification_key_value: str,
) -> None:
    if notification_already_sent(
        notification_key_value
    ):
        logger.info(
            "Дубликат push пропущен: %s",
            notification_key_value[:12],
        )
        return
    remember_notification(
        notification_key_value
    )
    if not subscribers:
        logger.info(
            "Нет подписчиков."
        )
        return
    for user_id in list(
        subscribers
    ):
        if not access_allowed(
            user_id
        ):
            continue
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            await asyncio.sleep(
                0.05
            )
        except Exception as e:
            logger.warning(
                "Ошибка push %s: %s",
                user_id,
                e,
            )
# ============================================================
# APPLY UAV
# ============================================================
async def apply_uav_event(
    application: Application,
    event: Dict[str, Any],
) -> None:
    global state
    new_level = int(
        event.get(
            "uav_level",
            0,
        )
        or 0
    )
    locations = event.get(
        "locations",
        [],
    )
    if not isinstance(
        locations,
        list,
    ):
        locations = []
    locations = list(
        dict.fromkeys(
            locations
        )
    )
    post_hash = event.get(
        "hash",
        "",
    )
    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------
    if event.get(
        "cancel",
        False,
    ):
        old_level = int(
            state.get(
                "uav_level",
                0,
            )
        )
        if old_level <= 0:
            logger.info(
                "Старый/лишний отбой пропущен: "
                "активной тревоги нет."
            )
            return
        state[
            "uav_level"
        ] = LEVEL_NONE
        state[
            "active_locations"
        ] = []
        state[
            "uav_cycle"
        ] = (
            int(
                state.get(
                    "uav_cycle",
                    0,
                )
            )
            + 1
        )
        state[
            "last_uav_title"
        ] = (
            "Отбой по опасности БПЛА"
        )
        state[
            "last_uav_source"
        ] = event.get(
            "source_name",
            "",
        )
        state[
            "last_uav_url"
        ] = event.get(
            "url",
            "",
        )
        state[
            "last_uav_time"
        ] = now_msk_str()
        state[
            "last_update"
        ] = iso_now()
        save_json(
            STATE_FILE,
            state,
        )
        add_history(
            event_type="uav_cancel",
            title="Отбой по опасности БПЛА",
            source_name=event.get(
                "source_name",
                "",
            ),
            source_url=event.get(
                "source_url",
                "",
            ),
            post_url=event.get(
                "url",
                "",
            ),
            locations=locations,
            level=LEVEL_NONE,
        )
        key = notification_key(
            "uav_cancel",
            LEVEL_NONE,
            locations,
            post_hash,
        )
        await send_push(
            application,
            build_uav_cancel_push(
                event
            ),
            key,
        )
        logger.info(
            "UAV ОТБОЙ: %s",
            event.get(
                "url",
                "",
            ),
        )
        return
    # --------------------------------------------------------
    # NO ALERT
    # --------------------------------------------------------
    if new_level <= 0:
        return
    old_level = int(
        state.get(
            "uav_level",
            0,
        )
    )
    # --------------------------------------------------------
    # LOWER LEVEL WITHOUT CANCEL
    # --------------------------------------------------------
    if new_level < old_level:
        logger.info(
            "Понижение %s -> %s "
            "без отбоя пропущено.",
            old_level,
            new_level,
        )
        current_locations = state.get(
            "active_locations",
            [],
        )
        merged = list(
            dict.fromkeys(
                current_locations
                + locations
            )
        )
        if merged != current_locations:
            state[
                "active_locations"
            ] = merged
            state[
                "last_update"
            ] = iso_now()
            save_json(
                STATE_FILE,
                state,
            )
        return
    # --------------------------------------------------------
    # SAME LEVEL
    # --------------------------------------------------------
    if new_level == old_level:
        current_locations = state.get(
            "active_locations",
            [],
        )
        merged = list(
            dict.fromkeys(
                current_locations
                + locations
            )
        )
        if merged != current_locations:
            state[
                "active_locations"
            ] = merged
            state[
                "last_uav_source"
            ] = event.get(
                "source_name",
                "",
            )
            state[
                "last_uav_url"
            ] = event.get(
                "url",
                "",
            )
            state[
                "last_uav_time"
            ] = now_msk_str()
            state[
                "last_update"
            ] = iso_now()
            save_json(
                STATE_FILE,
                state,
            )
        logger.info(
            "Тот же уровень %s — push не отправляется.",
            new_level,
        )
        return
    # --------------------------------------------------------
    # HIGHER LEVEL
    # --------------------------------------------------------
    state[
        "uav_level"
    ] = new_level
    current_locations = state.get(
        "active_locations",
        [],
    )
    merged_locations = list(
        dict.fromkeys(
            current_locations
            + locations
        )
    )
    state[
        "active_locations"
    ] = merged_locations
    state[
        "last_uav_title"
    ] = LEVEL_NAMES.get(
        new_level,
        "Изменение статуса",
    )
    state[
        "last_uav_source"
    ] = event.get(
        "source_name",
        "",
    )
    state[
        "last_uav_url"
    ] = event.get(
        "url",
        "",
    )
    state[
        "last_uav_time"
    ] = now_msk_str()
    state[
        "last_update"
    ] = iso_now()
    save_json(
        STATE_FILE,
        state,
    )
    add_history(
        event_type="uav_alert",
        title=LEVEL_NAMES.get(
            new_level,
            "Изменение статуса",
        ),
        source_name=event.get(
            "source_name",
            "",
        ),
        source_url=event.get(
            "source_url",
            "",
        ),
        post_url=event.get(
            "url",
            "",
        ),
        locations=merged_locations,
        level=new_level,
    )
    key = notification_key(
        "uav_alert",
        new_level,
        merged_locations,
        post_hash,
    )
    await send_push(
        application,
        build_uav_push(
            new_level,
            merged_locations,
            event,
        ),
        key,
    )
    logger.info(
        "UAV LEVEL %s -> %s",
        old_level,
        new_level,
    )
# ============================================================
# APPLY ROCKET
# ============================================================
async def apply_rocket_event(
    application: Application,
    event: Dict[str, Any],
) -> None:
    if not event.get(
        "rocket",
        False,
    ):
        return
    if event.get(
        "cancel",
        False,
    ):
        if state.get(
            "rocket_active",
            False,
        ):
            state[
                "rocket_active"
            ] = False
            state[
                "rocket_cycle"
            ] = (
                int(
                    state.get(
                        "rocket_cycle",
                        0,
                    )
                )
                + 1
            )
            state[
                "last_update"
            ] = iso_now()
            save_json(
                STATE_FILE,
                state,
            )
        return
    if state.get(
        "rocket_active",
        False,
    ):
        return
    state[
        "rocket_active"
    ] = True
    state[
        "last_rocket_title"
    ] = "Ракетная опасность"
    state[
        "last_rocket_source"
    ] = event.get(
        "source_name",
        "",
    )
    state[
        "last_rocket_url"
    ] = event.get(
        "url",
        "",
    )
    state[
        "last_rocket_time"
    ] = now_msk_str()
    state[
        "last_update"
    ] = iso_now()
    save_json(
        STATE_FILE,
        state,
    )
    locations = event.get(
        "locations",
        [],
    )
    add_history(
        event_type="rocket",
        title="Ракетная опасность",
        source_name=event.get(
            "source_name",
            "",
        ),
        source_url=event.get(
            "source_url",
            "",
        ),
        post_url=event.get(
            "url",
            "",
        ),
        locations=locations,
        level=0,
    )
    key = notification_key(
        "rocket",
        0,
        locations,
        event.get(
            "hash",
            "",
        ),
    )
    await send_push(
        application,
        build_rocket_push(
            event
        ),
        key,
    )
# ============================================================
# STARTUP BASELINE
# ============================================================
startup_baseline_done = False
def make_startup_baseline(
    posts_by_source: Dict[
        str,
        List[Dict[str, Any]]
    ],
) -> None:
    global startup_baseline_done
    global source_state
    logger.info(
        "Создание стартовой базы."
    )
    for source_key, posts in (
        posts_by_source.items()
    ):
        if not posts:
            continue
        newest = posts[-1]
        source_state[
            source_key
        ] = {
            "latest_hash":
                newest.get(
                    "hash",
                    "",
                ),
            "latest_post_id":
                newest.get(
                    "post_id",
                    "",
                ),
            "initialized":
                True,
        }
        # ----------------------------------------------------
        # VERY IMPORTANT:
        # Mark every currently visible post
        # as already known.
        #
        # This prevents old posts from being
        # processed after deployment/restart.
        # ----------------------------------------------------
        for post in posts:
            mark_post_processed(
                post
            )
    save_json(
        SOURCE_STATE_FILE,
        source_state,
    )
    startup_baseline_done = True
    logger.info(
        "Стартовая база создана. "
        "Старые сообщения НЕ будут отправлены."
    )
# ============================================================
# GET NEW POSTS
# ============================================================
def get_new_posts(
    source_key: str,
    posts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not posts:
        return []
    source_info = source_state.get(
        source_key
    )
    if not isinstance(
        source_info,
        dict,
    ):
        return []
    old_hash = source_info.get(
        "latest_hash",
        "",
    )
    old_post_id = source_info.get(
        "latest_post_id",
        "",
    )
    # --------------------------------------------------------
    # Find old post by hash first.
    # --------------------------------------------------------
    start_index = None
    if old_hash:
        for index, post in enumerate(
            posts
        ):
            if post.get(
                "hash",
                "",
            ) == old_hash:
                start_index = (
                    index + 1
                )
                break
    # --------------------------------------------------------
    # If hash isn't found, use post ID.
    # --------------------------------------------------------
    if (
        start_index is None
        and old_post_id
    ):
        try:
            old_id = int(
                old_post_id
            )
            for index, post in enumerate(
                posts
            ):
                try:
                    current_id = int(
                        post.get(
                            "post_id",
                            "0",
                        )
                    )
                except Exception:
                    continue
                if current_id == old_id:
                    start_index = (
                        index + 1
                    )
                    break
        except Exception:
            pass
    # --------------------------------------------------------
    # CRITICAL SAFETY RULE:
    #
    # If the previous message is no longer
    # visible in the public page, DO NOT
    # guess that all visible posts are new.
    #
    # This was the source of the old bug.
    # --------------------------------------------------------
    if start_index is None:
        logger.warning(
            "[%s] Старый пост исчез из ленты. "
            "Новые посты НЕ обрабатываем, "
            "чтобы не отправить старую историю.",
            source_key,
        )
        # We move baseline to the newest post.
        # But we intentionally do NOT process it.
        newest = posts[-1]
        source_state[
            source_key
        ] = {
            "latest_hash":
                newest.get(
                    "hash",
                    "",
                ),
            "latest_post_id":
                newest.get(
                    "post_id",
                    "",
                ),
            "initialized":
                True,
        }
        save_json(
            SOURCE_STATE_FILE,
            source_state,
        )
        # Mark all currently visible posts known.
        for post in posts:
            mark_post_processed(
                post
            )
        return []
    new_posts = posts[
        start_index:
    ]
    return new_posts
# ============================================================
# SOURCE CHECK
# ============================================================
async def check_sources(
    application: Application,
) -> None:
    global startup_baseline_done
    global source_state
    posts_by_source = {}
    for source_key in SOURCES:
        posts = await asyncio.to_thread(
            fetch_source,
            source_key,
        )
        posts_by_source[
            source_key
        ] = posts
    # --------------------------------------------------------
    # FIRST RUN
    # --------------------------------------------------------
    if not startup_baseline_done:
        make_startup_baseline(
            posts_by_source
        )
        return
    # --------------------------------------------------------
    # NORMAL RUN
    # --------------------------------------------------------
    for source_key, posts in (
        posts_by_source.items()
    ):
        if not posts:
            continue
        new_posts = get_new_posts(
            source_key,
            posts,
        )
        if not new_posts:
            continue
        # ----------------------------------------------------
        # Process oldest -> newest.
        # ----------------------------------------------------
        for post in new_posts:
            post_hash = post.get(
                "hash",
                "",
            )
            # Absolute duplicate protection.
            if post_was_processed(
                post
            ):
                logger.info(
                    "[%s] Уже обработан: %s",
                    source_key,
                    post_hash[:12],
                )
                continue
            event = parse_post(
                post
            )
            # ------------------------------------------------
            # Mark immediately.
            #
            # If Render crashes after this point,
            # the same Telegram post won't be processed
            # again.
            # ------------------------------------------------
            mark_post_processed(
                post
            )
            # ------------------------------------------------
            # Rocket
            # ------------------------------------------------
            if event.get(
                "rocket",
                False,
            ):
                await apply_rocket_event(
                    application,
                    event,
                )
            # ------------------------------------------------
            # UAV
            # ------------------------------------------------
            if is_relevant_uav_post(
                event
            ):
                await apply_uav_event(
                    application,
                    event,
                )
        # ----------------------------------------------------
        # Only after successful processing update
        # the source cursor.
        # ----------------------------------------------------
        newest = posts[-1]
        source_state[
            source_key
        ] = {
            "latest_hash":
                newest.get(
                    "hash",
                    "",
                ),
            "latest_post_id":
                newest.get(
                    "post_id",
                    "",
                ),
            "initialized":
                True,
        }
        save_json(
            SOURCE_STATE_FILE,
            source_state,
        )
# ============================================================
# MONITOR
# ============================================================
monitor_running = False
async def monitor_loop(
    application: Application,
) -> None:
    global monitor_running
    if monitor_running:
        return
    monitor_running = True
    logger.info(
        "Мониторинг источников запущен."
    )
    while True:
        try:
            await check_sources(
                application
            )
        except Exception as e:
            logger.exception(
                "Ошибка мониторинга: %s",
                e,
            )
        await asyncio.sleep(
            max(
                CHECK_INTERVAL,
                10,
            )
        )
# ============================================================
# ADMIN
# ============================================================
async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    text = (
        "🛠 <b>UAV ALERT ADMIN</b>\n\n"
        f"UAV level: "
        f"{state.get('uav_level', 0)}\n"
        f"UAV cycle: "
        f"{state.get('uav_cycle', 0)}\n"
        f"Rocket: "
        f"{state.get('rocket_active', False)}\n"
        f"Subscribers: "
        f"{len(subscribers)}\n"
        f"History: "
        f"{len(history)}\n"
        f"Known source states: "
        f"{len(source_state)}"
    )
    if update.message:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
        )
# ============================================================
# TEST PUSH
# ============================================================
async def test_push_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    test_text = (
        "🧪 <b>Тест UAV ALERT</b>\n\n"
        "Push-уведомления работают."
    )
    sent = 0
    for user_id in list(
        subscribers
    ):
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=test_text,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
        except Exception as e:
            logger.warning(
                "Ошибка тестового push %s: %s",
                user_id,
                e,
            )
    if update.message:
        await update.message.reply_text(
            f"🧪 Тест завершён.\n"
            f"Отправлено: {sent}"
        )
# ============================================================
# ADMIN TEST ATTENTION
# ============================================================
async def test_attention_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    event = {
        "source_name":
            "ADMIN TEST",
        "source_url":
            "",
        "url":
            "",
        "hash":
            "admin_attention_"
            + str(
                time.time()
            ),
        "locations":
            [
                "Костромская область"
            ],
        "uav_level":
            LEVEL_ATTENTION,
        "cancel":
            False,
    }
    await apply_uav_event(
        context.application,
        event,
    )
    if update.message:
        await update.message.reply_text(
            "🟡 Тестовое состояние "
            "«Внимание по БПЛА» применено."
        )
# ============================================================
# ADMIN TEST THREAT
# ============================================================
async def test_threat_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    event = {
        "source_name":
            "ADMIN TEST",
        "source_url":
            "",
        "url":
            "",
        "hash":
            "admin_threat_"
            + str(
                time.time()
            ),
        "locations":
            [
                "Костромская область"
            ],
        "uav_level":
            LEVEL_THREAT,
        "cancel":
            False,
    }
    await apply_uav_event(
        context.application,
        event,
    )
    if update.message:
        await update.message.reply_text(
            "🟠 Тестовое состояние "
            "«Угроза по БПЛА» применено."
        )
# ============================================================
# ADMIN TEST DANGER
# ============================================================
async def test_danger_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    event = {
        "source_name":
            "ADMIN TEST",
        "source_url":
            "",
        "url":
            "",
        "hash":
            "admin_danger_"
            + str(
                time.time()
            ),
        "locations":
            [
                "Костромская область"
            ],
        "uav_level":
            LEVEL_DANGER,
        "cancel":
            False,
    }
    await apply_uav_event(
        context.application,
        event,
    )
    if update.message:
        await update.message.reply_text(
            "🔴 Тестовое состояние "
            "«Опасность по БПЛА» применено."
        )
# ============================================================
# ADMIN TEST CANCEL
# ============================================================
async def test_cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_user:
        return
    if not is_admin(
        update.effective_user.id
    ):
        return
    event = {
        "source_name":
            "ADMIN TEST",
        "source_url":
            "",
        "url":
            "",
        "hash":
            "admin_cancel_"
            + str(
                time.time()
            ),
        "locations":
            [],
        "uav_level":
            LEVEL_NONE,
        "cancel":
            True,
    }
    await apply_uav_event(
        context.application,
        event,
    )
    if update.message:
        await update.message.reply_text(
            "🟢 Тестовый "
            "«Отбой по БПЛА» применён."
        )
# ============================================================
# ERROR
# ============================================================
async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.exception(
        "Telegram error: %s",
        context.error,
    )
# ============================================================
# FLASK THREAD
# ============================================================
def run_flask() -> None:
    logger.info(
        "Flask запускается на порту %s",
        PORT,
    )
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True,
        use_reloader=False,
    )
# ============================================================
# POST INIT
# ============================================================
async def post_init(
    application: Application,
) -> None:
    commands = [
        BotCommand(
            "start",
            "Главное меню",
        ),
        BotCommand(
            "status",
            "Текущий статус",
        ),
        BotCommand(
            "history",
            "История",
        ),
        BotCommand(
            "stats",
            "Статистика",
        ),
        BotCommand(
            "sources",
            "Источники",
        ),
        BotCommand(
            "admin",
            "Админ-панель",
        ),
    ]
    try:
        await application.bot.set_my_commands(
            commands
        )
    except Exception as e:
        logger.warning(
            "Не удалось установить команды: %s",
            e,
        )
    application.create_task(
        monitor_loop(
            application
        )
    )
    logger.info(
        "Telegram bot инициализирован."
    )
# ============================================================
# MAIN
# ============================================================
def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN "
            "в Environment Variables."
        )
    # --------------------------------------------------------
    # FLASK
    # --------------------------------------------------------
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
        name="FlaskThread",
    )
    flask_thread.start()
    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------
    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------
    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "history",
            history_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "stats",
            stats_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "sources",
            sources_command,
        )
    )
    # --------------------------------------------------------
    # CALLBACKS
    # --------------------------------------------------------
    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )
    # --------------------------------------------------------
    # ADMIN
    # --------------------------------------------------------
    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "testpush",
            test_push_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "testattention",
            test_attention_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "testthreat",
            test_threat_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "testdanger",
            test_danger_command,
        )
    )
    application.add_handler(
        CommandHandler(
            "testcancel",
            test_cancel_command,
        )
    )
    application.add_error_handler(
        error_handler
    )
    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------
    logger.info(
        "========================================"
    )
    logger.info(
        "UAV ALERT START"
    )
    logger.info(
        "========================================"
    )
    logger.info(
        "UAV level: %s",
        state.get(
            "uav_level",
            0,
        ),
    )
    logger.info(
        "Rocket: %s",
        state.get(
            "rocket_active",
            False,
        ),
    )
    logger.info(
        "Admin: %s",
        ADMIN_ID,
    )
    logger.info(
        "Admin only: %s",
        ADMIN_ONLY,
    )
    logger.info(
        "CHECK_INTERVAL: %s",
        CHECK_INTERVAL,
    )
    logger.info(
        "Sources: %s",
        len(SOURCES),
    )
    # --------------------------------------------------------
    # POLLING
    # --------------------------------------------------------
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    main()
