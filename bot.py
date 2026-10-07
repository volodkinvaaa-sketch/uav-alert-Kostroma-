# ============================================================
# UAV ALERT — Telegram Bot
# Костромская область
#
# Полная версия:
# - мониторинг Telegram-источников
# - определение Внимание / Угроза / Опасность
# - определение Отбоя
# - ракетная опасность
# - отдельные территории
# - история
# - статистика
# - источники
# - подписчики
# - стабильные push-уведомления
# - защита от повторной обработки старых постов
# - сохранение состояния после перезапуска Render
# - Flask для Render
# ============================================================

import os
import re
import json
import time
import hashlib
import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from html import escape

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

ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))

PORT = int(os.getenv("PORT", "10000"))

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

DATA_DIR = os.getenv("DATA_DIR", ".").strip() or "."

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

SOURCE_LIMIT = int(os.getenv("SOURCE_LIMIT", "30"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("UAV_ALERT")


# ============================================================
# TIMEZONE
# ============================================================

MSK = timezone(timedelta(hours=3))


def now_msk():
    return datetime.now(MSK)


def now_iso():
    return now_msk().isoformat()


def format_time(value=None):
    if value is None:
        value = now_msk()

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except Exception:
            return value

    return value.astimezone(MSK).strftime("%d.%m.%Y %H:%M")


# ============================================================
# FILES
# ============================================================

os.makedirs(DATA_DIR, exist_ok=True)


STATE_FILE = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "subscribers.json")
SENT_POSTS_FILE = os.path.join(DATA_DIR, "sent_posts.json")
NOTIFICATION_EVENTS_FILE = os.path.join(
    DATA_DIR,
    "notification_events.json",
)
SOURCE_STATE_FILE = os.path.join(
    DATA_DIR,
    "source_state.json",
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

    "last_uav_update": None,
    "last_uav_source": None,
    "last_uav_post": None,

    "last_rocket_update": None,
    "last_rocket_source": None,
    "last_rocket_post": None,

    "last_update": None,

    "startup_completed": False,
}


# ============================================================
# FILE HELPERS
# ============================================================

def atomic_write_json(path, data):
    temp_path = path + ".tmp"

    try:
        with open(
            temp_path,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_path, path)

    except Exception:
        logger.exception(
            "Не удалось сохранить JSON: %s",
            path,
        )


def load_json(path, default):
    if not os.path.exists(path):
        return default

    try:
        with open(
            path,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except Exception:
        logger.exception(
            "Не удалось прочитать JSON: %s",
            path,
        )

        return default


def ensure_list(value):
    return value if isinstance(value, list) else []


def ensure_dict(value):
    return value if isinstance(value, dict) else {}


# ============================================================
# LOAD DATA
# ============================================================

state = load_json(
    STATE_FILE,
    DEFAULT_STATE.copy(),
)

if not isinstance(state, dict):
    state = DEFAULT_STATE.copy()


for key, value in DEFAULT_STATE.items():
    if key not in state:
        state[key] = value


history = ensure_list(
    load_json(
        HISTORY_FILE,
        [],
    )
)


subscribers = set(
    str(x)
    for x in ensure_list(
        load_json(
            SUBSCRIBERS_FILE,
            [],
        )
    )
)


sent_posts = ensure_dict(
    load_json(
        SENT_POSTS_FILE,
        {},
    )
)


notification_events = ensure_dict(
    load_json(
        NOTIFICATION_EVENTS_FILE,
        {},
    )
)


source_state = ensure_dict(
    load_json(
        SOURCE_STATE_FILE,
        {},
    )
)


# ============================================================
# SAVE FUNCTIONS
# ============================================================

def save_state():
    atomic_write_json(
        STATE_FILE,
        state,
    )


def save_history():
    atomic_write_json(
        HISTORY_FILE,
        history,
    )


def save_subscribers():
    atomic_write_json(
        SUBSCRIBERS_FILE,
        sorted(subscribers),
    )


def save_sent_posts():
    atomic_write_json(
        SENT_POSTS_FILE,
        sent_posts,
    )


def save_notification_events():
    atomic_write_json(
        NOTIFICATION_EVENTS_FILE,
        notification_events,
    )


def save_source_state():
    atomic_write_json(
        SOURCE_STATE_FILE,
        source_state,
    )


# ============================================================
# SOURCES
# ============================================================

SOURCES = {
    "locator": {
        "name": "Locator",
        "url": "https://t.me/s/locatorru",
    },

    "monitoring": {
        "name": "Russia Monitoring Radar BPLA",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
    },

    "radar": {
        "name": "Radar Russia",
        "url": "https://t.me/s/radarrussiia",
    },

    "bpla": {
        "name": "BPLA Russia",
        "url": "https://t.me/s/bplarussiaru",
    },
}


# ============================================================
# KOSTROMA LOCATIONS
# ============================================================

KOSTROMA_LOCATIONS = [
    "Костромская область",

    "Кострома",
    "Буй",
    "Волгореченск",
    "Галич",
    "Шарья",
    "Мантурово",
    "Нерехта",
    "Чухлома",
    "Макарьев",
    "Солигалич",
    "Кологрив",
    "Нея",

    "Антропово",
    "Вохма",
    "Георгиевское",
    "Кадый",
    "Красное-на-Волге",
    "Межа",
    "Островское",
    "Павино",
    "Парфеньево",
    "Поназырево",
    "Пыщуг",
    "Судиславль",
    "Сусанинский",
    "Сусанино",
    "Чухломский",
    "Островский",
    "Нейский",
    "Мантуровский",
    "Нерехтский",
    "Буйский",
    "Галичский",
    "Костромской",
    "Макарьевский",
    "Шарьинский",
    "Солигаличский",
    "Кологривский",
]


# Длинные названия проверяем раньше коротких.
KOSTROMA_LOCATIONS_SORTED = sorted(
    KOSTROMA_LOCATIONS,
    key=len,
    reverse=True,
)


# ============================================================
# STATUS TEXT
# ============================================================

UAV_LEVEL_NAMES = {
    0: "🟢 Нет опасности",
    1: "🟡 Внимание по БПЛА",
    2: "🟠 Угроза по БПЛА",
    3: "🔴 Опасность по БПЛА",
}


UAV_LEVEL_SHORT = {
    0: "Нет опасности",
    1: "Внимание",
    2: "Угроза",
    3: "Опасность",
}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
)


# ============================================================
# TELEGRAM SOURCE FETCH
# ============================================================

def fetch_source(source_key, source_info):
    """
    Получает последние посты публичного Telegram-канала.

    Важно:
    эта функция только читает источник.
    Никаких уведомлений здесь не отправляется.
    """

    url = source_info["url"]

    try:
        response = session.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

    except Exception as exc:
        logger.warning(
            "Ошибка загрузки источника %s: %s",
            source_key,
            exc,
        )
        return []

    try:
        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

    except Exception as exc:
        logger.warning(
            "Ошибка BeautifulSoup %s: %s",
            source_key,
            exc,
        )
        return []

    posts = []

    message_nodes = soup.select(
        ".tgme_widget_message"
    )

    for node in message_nodes:
        try:
            data_post = node.get(
                "data-post",
                "",
            ).strip()

            post_id = None

            if data_post:
                match = re.search(
                    r"/(\d+)$",
                    data_post,
                )

                if match:
                    post_id = int(
                        match.group(1)
                    )

            if post_id is None:
                link = node.select_one(
                    ".tgme_widget_message_date"
                )

                if link:
                    href = link.get(
                        "href",
                        "",
                    )

                    match = re.search(
                        r"/(\d+)$",
                        href,
                    )

                    if match:
                        post_id = int(
                            match.group(1)
                        )

            if post_id is None:
                continue

            text_node = node.select_one(
                ".tgme_widget_message_text"
            )

            text_value = (
                text_node.get_text(
                    "\n",
                    strip=True,
                )
                if text_node
                else ""
            )

            date_value = None

            time_node = node.select_one(
                "time"
            )

            if time_node:
                date_value = (
                    time_node.get(
                        "datetime"
                    )
                )

            post_url = (
                f"https://t.me/"
                f"{data_post.replace('/', '/s/')}"
                if data_post
                else ""
            )

            if not post_url:
                username = (
                    source_info["url"]
                    .replace(
                        "https://t.me/s/",
                        "",
                    )
                    .strip("/")
                )

                post_url = (
                    f"https://t.me/"
                    f"{username}/{post_id}"
                )

            stable_key = (
                f"{source_key}:{post_id}"
            )

            content_hash = hashlib.sha256(
                (
                    source_key
                    + "|"
                    + str(post_id)
                    + "|"
                    + text_value
                ).encode(
                    "utf-8",
                    errors="ignore",
                )
            ).hexdigest()

            posts.append(
                {
                    "source": source_key,
                    "source_name": source_info["name"],
                    "post_id": post_id,
                    "text": text_value,
                    "date": date_value,
                    "url": post_url,
                    "key": stable_key,
                    "hash": content_hash,
                }
            )

        except Exception:
            logger.exception(
                "Ошибка обработки поста %s",
                source_key,
            )

    posts.sort(
        key=lambda item: item["post_id"]
    )

    if len(posts) > SOURCE_LIMIT:
        posts = posts[-SOURCE_LIMIT:]

    return posts


# ============================================================
# LOCATION DETECTION
# ============================================================

def find_locations(text):
    if not text:
        return []

    found = []

    text_lower = text.lower()

    for location in KOSTROMA_LOCATIONS_SORTED:
        pattern = (
            r"(?<![а-яёa-z])"
            + re.escape(location.lower())
            + r"(?![а-яёa-z])"
        )

        if re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        ):
            found.append(location)

    # Убираем дубликаты
    unique = []

    for location in found:
        if location not in unique:
            unique.append(location)

    return unique


# ============================================================
# UAV KEYWORDS
# ============================================================

UAV_WORDS = [
    "бпла",
    "беспилот",
    "дрон",
    "дроны",
    "дронов",
    "беспилотник",
    "беспилотники",
    "беспилотного",
    "беспилотным",
    "беспилотников",
    "летательный аппарат",
]


def contains_uav(text):
    if not text:
        return False

    text_lower = text.lower()

    return any(
        word in text_lower
        for word in UAV_WORDS
    )


# ============================================================
# CANCEL DETECTION
# ============================================================

def detect_cancel(text):
    if not text:
        return False

    text_lower = text.lower()

    cancel_patterns = [
        r"\bотбой\b",
        r"\bопасность\s+отмен",
        r"\bугроза\s+отмен",
        r"\bопасность\s+снята",
        r"\bугроза\s+снята",
        r"\bопасность\s+ликвидирован",
        r"\bугроза\s+ликвидирован",
        r"\bотменяется\s+опасность",
        r"\bотменена\s+опасность",
        r"\bотменена\s+угроза",
    ]

    return any(
        re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        )
        for pattern in cancel_patterns
    )


# ============================================================
# ROCKET DETECTION
# ============================================================

def detect_rocket(text):
    if not text:
        return False

    text_lower = text.lower()

    rocket_words = [
        "ракетная опасность",
        "ракетной опасности",
        "ракетная угроза",
        "ракетная тревога",
    ]

    return any(
        word in text_lower
        for word in rocket_words
    )


def detect_rocket_cancel(text):
    if not text:
        return False

    text_lower = text.lower()

    rocket_cancel_patterns = [
        r"отбой.*ракет",
        r"ракет.*отбой",
        r"ракетн.*опасност.*отмен",
        r"ракетн.*опасност.*снят",
        r"ракетн.*угроз.*отмен",
        r"ракетн.*угроз.*снят",
    ]

    return any(
        re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        )
        for pattern in rocket_cancel_patterns
    )


# ============================================================
# UAV LEVEL DETECTION
# ============================================================

def detect_uav_level(text):
    if not text:
        return 0

    text_lower = text.lower()

    # Сначала самая высокая степень.
    danger_patterns = [
        r"\bопасность\s+по\s+бпла\b",
        r"\bопасность\s+бпла\b",
        r"\bопасность\s+беспилот",
        r"\bопасность.*дрон",
    ]

    for pattern in danger_patterns:
        if re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        ):
            return 3

    threat_patterns = [
        r"\bугроза\s+по\s+бпла\b",
        r"\bугроза\s+бпла\b",
        r"\bугроза.*беспилот",
        r"\bугроза.*дрон",
    ]

    for pattern in threat_patterns:
        if re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        ):
            return 2

    attention_patterns = [
        r"\bвнимание\s+по\s+бпла\b",
        r"\bвнимание\s+бпла\b",
        r"\bвнимание.*беспилот",
        r"\bвнимание.*дрон",
    ]

    for pattern in attention_patterns:
        if re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        ):
            return 1

    # Дополнительные формулировки.
    if (
        "опасность бпла" in text_lower
        or "опасность беспилотников" in text_lower
    ):
        return 3

    if (
        "угроза бпла" in text_lower
        or "угроза беспилотников" in text_lower
    ):
        return 2

    if (
        "внимание бпла" in text_lower
        or "внимание беспилотников" in text_lower
    ):
        return 1

    return 0


# ============================================================
# RELEVANCE
# ============================================================

def is_relevant_uav_post(text, locations):
    """
    Для Костромского бота пост должен:
    - содержать БПЛА/беспилот/дрон
    - и относиться к Костромской области/территории.

    Отбой допускается, если активное состояние уже есть.
    """

    if not contains_uav(text):
        return False

    if locations:
        return True

    return False


# ============================================================
# PARSE POST
# ============================================================

def parse_post(post):
    text = post.get(
        "text",
        "",
    )

    locations = find_locations(text)

    cancel = detect_cancel(text)

    rocket = detect_rocket(text)

    rocket_cancel = detect_rocket_cancel(text)

    uav_level = detect_uav_level(text)

    relevant_uav = is_relevant_uav_post(
        text,
        locations,
    )

    return {
        "locations": locations,
        "cancel": cancel,
        "rocket": rocket,
        "rocket_cancel": rocket_cancel,
        "uav_level": uav_level,
        "relevant_uav": relevant_uav,
    }


# ============================================================
# HISTORY
# ============================================================

def add_history(
    event_type,
    level=0,
    locations=None,
    source=None,
    post=None,
    details=None,
):
    locations = locations or []

    item = {
        "time": now_iso(),
        "type": event_type,
        "level": level,
        "level_name": UAV_LEVEL_NAMES.get(
            level,
            "Неизвестно",
        ),
        "locations": list(locations),
        "source": source,
        "post_id": (
            post.get("post_id")
            if post
            else None
        ),
        "url": (
            post.get("url")
            if post
            else None
        ),
        "details": details,
    }

    history.append(item)

    # Храним последние 500 событий.
    if len(history) > 500:
        del history[:-500]

    save_history()


# ============================================================
# SENT POST CLEANUP
# ============================================================

def cleanup_sent_posts():
    """
    sent_posts больше не является главным механизмом защиты
    от старых Telegram-постов.

    Основная защита находится в source_state.

    Здесь оставляем ограниченный журнал обработанных постов.
    """

    if len(sent_posts) <= 2000:
        return

    items = list(
        sent_posts.items()
    )

    items.sort(
        key=lambda item: item[1]
        if isinstance(item[1], str)
        else ""
    )

    items = items[-1500:]

    sent_posts.clear()

    for key, value in items:
        sent_posts[key] = value

    save_sent_posts()


def post_was_processed(post):
    key = post.get("key")

    if not key:
        return False

    return key in sent_posts


def mark_post_processed(post):
    key = post.get("key")

    if not key:
        return

    sent_posts[key] = now_iso()

    cleanup_sent_posts()

    save_sent_posts()


# ============================================================
# SOURCE CURSOR
# ============================================================

def get_source_cursor(source_key):
    data = source_state.get(
        source_key,
        {},
    )

    if not isinstance(data, dict):
        return 0

    try:
        return int(
            data.get(
                "last_post_id",
                0,
            )
        )
    except Exception:
        return 0


def set_source_cursor(
    source_key,
    post_id,
):
    if source_key not in source_state:
        source_state[source_key] = {}

    source_state[source_key][
        "last_post_id"
    ] = int(post_id)

    source_state[source_key][
        "updated_at"
    ] = now_iso()

    save_source_state()


def source_has_baseline(source_key):
    data = source_state.get(
        source_key,
        {},
    )

    if not isinstance(data, dict):
        return False

    return bool(
        data.get(
            "baseline_done",
            False,
        )
    )


def set_source_baseline(
    source_key,
    post_id,
):
    if source_key not in source_state:
        source_state[source_key] = {}

    source_state[source_key][
        "baseline_done"
    ] = True

    source_state[source_key][
        "last_post_id"
    ] = int(post_id)

    source_state[source_key][
        "updated_at"
    ] = now_iso()

    save_source_state()


# ============================================================
# NOTIFICATION DEDUP
# ============================================================

def notification_key(
    event_type,
    level=0,
    locations=None,
    cycle=None,
    post=None,
):
    locations = sorted(
        locations or []
    )

    base = {
        "event": event_type,
        "level": level,
        "locations": locations,
        "cycle": (
            cycle
            if cycle is not None
            else state.get(
                "uav_cycle",
                0,
            )
        ),
    }

    if post:
        base["post"] = post.get(
            "key"
        )

    raw = json.dumps(
        base,
        ensure_ascii=False,
        sort_keys=True,
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def notification_already_sent(key):
    return key in notification_events


def mark_notification_sent(
    key,
    event_type,
):
    notification_events[key] = {
        "time": now_iso(),
        "event": event_type,
    }

    # Не даём файлу бесконечно расти.
    if len(notification_events) > 3000:
        items = list(
            notification_events.items()
        )

        items = items[-2000:]

        notification_events.clear()

        for item_key, item_value in items:
            notification_events[
                item_key
            ] = item_value

    save_notification_events()


# ============================================================
# PUSH MESSAGE BUILDERS
# ============================================================

def format_locations(locations):
    if not locations:
        return "Костромская область"

    return "\n".join(
        f"• {escape(location)}"
        for location in locations
    )


def build_uav_push(
    event_type,
    level,
    locations,
    post=None,
):
    locations = locations or []

    source_name = (
        post.get(
            "source_name"
        )
        if post
        else None
    )

    source_url = (
        post.get(
            "url"
        )
        if post
        else None
    )

    if event_type == "uav_cancel":
        title = "🟢 ОТБОЙ ПО БПЛА"
        status = (
            "Опасность по БПЛА отменена."
        )

    else:
        title = UAV_LEVEL_NAMES.get(
            level,
            "⚠️ БПЛА",
        )

        status = (
            "Зафиксирован новый статус "
            "по БПЛА."
        )

    parts = [
        f"<b>{title}</b>",
        "",
        escape(status),
        "",
        "<b>Территории:</b>",
        format_locations(
            locations
        ),
    ]

    if source_name:
        parts.extend(
            [
                "",
                "<b>Источник:</b> "
                + escape(source_name),
            ]
        )

    if source_url:
        parts.extend(
            [
                "",
                f'<a href="{escape(source_url)}">'
                "Открыть источник"
                "</a>",
            ]
        )

    return "\n".join(parts)


def build_rocket_push(
    active,
    post=None,
):
    if active:
        title = (
            "🟥 РАКЕТНАЯ ОПАСНОСТЬ"
        )

        status = (
            "Зафиксирована ракетная "
            "опасность."
        )

    else:
        title = (
            "🟢 ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ"
        )

        status = (
            "Ракетная опасность отменена."
        )

    parts = [
        f"<b>{title}</b>",
        "",
        escape(status),
    ]

    if post:
        source_name = post.get(
            "source_name"
        )

        source_url = post.get(
            "url"
        )

        if source_name:
            parts.extend(
                [
                    "",
                    "<b>Источник:</b> "
                    + escape(
                        source_name
                    ),
                ]
            )

        if source_url:
            parts.extend(
                [
                    "",
                    f'<a href="{escape(source_url)}">'
                    "Открыть источник"
                    "</a>",
                ]
            )

    return "\n".join(parts)


# ============================================================
# PUSH SENDER
# ============================================================

async def send_push(
    application,
    message,
    event_type,
    level=0,
    locations=None,
    cycle=None,
    post=None,
):
    """
    Центральная функция push-уведомлений.

    Очень важно:
    здесь проверяется notification_key.

    Поэтому одно и то же событие не отправляется
    повторно после повторной проверки источника.
    """

    key = notification_key(
        event_type=event_type,
        level=level,
        locations=locations,
        cycle=cycle,
        post=post,
    )

    if notification_already_sent(key):
        logger.info(
            "Push уже отправлялся: %s",
            event_type,
        )
        return 0

    successful = 0

    dead_users = []

    for chat_id in list(subscribers):
        try:
            await application.bot.send_message(
                chat_id=int(chat_id),
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            successful += 1

        except Exception as exc:
            logger.warning(
                "Не удалось отправить push %s: %s",
                chat_id,
                exc,
            )

            error_text = str(exc).lower()

            if (
                "chat not found" in error_text
                or "bot was blocked" in error_text
                or "user is deactivated" in error_text
                or "forbidden" in error_text
            ):
                dead_users.append(
                    str(chat_id)
                )

    for chat_id in dead_users:
        subscribers.discard(
            chat_id
        )

    if dead_users:
        save_subscribers()

    # Запоминаем событие только после попытки отправки.
    mark_notification_sent(
        key,
        event_type,
    )

    logger.info(
        "Push %s: успешно=%s подписчиков=%s",
        event_type,
        successful,
        len(subscribers),
    )

    return successful


# ============================================================
# APPLY UAV EVENT
# ============================================================

async def apply_uav_event(
    application,
    new_level,
    locations,
    post,
):
    """
    Применяет новое состояние БПЛА.

    Правила:

    0 -> 1 = push Внимание
    1 -> 2 = push Угроза
    2 -> 3 = push Опасность

    1 -> 1 = без повторного push
    2 -> 2 = без повторного push
    3 -> 3 = без повторного push

    3 -> 2 = НЕ понижаем автоматически.
    Для этого нужен явный Отбой.

    active_locations при этом обновляются.
    """

    locations = list(
        dict.fromkeys(
            locations or []
        )
    )

    old_level = int(
        state.get(
            "uav_level",
            0,
        )
    )

    old_locations = list(
        state.get(
            "active_locations",
            [],
        )
    )

    # --------------------------------------------------------
    # ОТБОЙ
    # --------------------------------------------------------

    if new_level == 0:
        if old_level <= 0:
            return False

        state["uav_level"] = 0
        state["active_locations"] = []

        state["uav_cycle"] = int(
            state.get(
                "uav_cycle",
                0,
            )
        ) + 1

        state["last_uav_update"] = now_iso()

        state["last_uav_source"] = post.get(
            "source_name"
        )

        state["last_uav_post"] = post.get(
            "url"
        )

        state["last_update"] = now_iso()

        save_state()

        add_history(
            event_type="uav_cancel",
            level=0,
            locations=old_locations,
            source=post.get(
                "source_name"
            ),
            post=post,
        )

        message = build_uav_push(
            event_type="uav_cancel",
            level=0,
            locations=old_locations,
            post=post,
        )

        await send_push(
            application,
            message,
            event_type="uav_cancel",
            level=0,
            locations=old_locations,
            cycle=state[
                "uav_cycle"
            ],
            post=None,
        )

        return True

    # --------------------------------------------------------
    # НЕТ НОВОГО СТАТУСА
    # --------------------------------------------------------

    if new_level <= 0:
        return False

    # --------------------------------------------------------
    # ПЕРВОЕ АКТИВНОЕ СОБЫТИЕ
    # --------------------------------------------------------

    if old_level == 0:
        state["uav_level"] = new_level

        state["active_locations"] = (
            locations
        )

        state["uav_cycle"] = int(
            state.get(
                "uav_cycle",
                0,
            )
        ) + 1

        state["last_uav_update"] = now_iso()

        state["last_uav_source"] = post.get(
            "source_name"
        )

        state["last_uav_post"] = post.get(
            "url"
        )

        state["last_update"] = now_iso()

        save_state()

        add_history(
            event_type="uav",
            level=new_level,
            locations=locations,
            source=post.get(
                "source_name"
            ),
            post=post,
        )

        message = build_uav_push(
            event_type="uav",
            level=new_level,
            locations=locations,
            post=post,
        )

        await send_push(
            application,
            message,
            event_type="uav",
            level=new_level,
            locations=locations,
            cycle=state[
                "uav_cycle"
            ],
            post=None,
        )

        return True

    # --------------------------------------------------------
    # ТОТ ЖЕ УРОВЕНЬ
    # --------------------------------------------------------

    if new_level == old_level:
        merged = list(
            dict.fromkeys(
                old_locations
                + locations
            )
        )

        if merged != old_locations:
            state[
                "active_locations"
            ] = merged

            state[
                "last_uav_update"
            ] = now_iso()

            state[
                "last_uav_source"
            ] = post.get(
                "source_name"
            )

            state[
                "last_uav_post"
            ] = post.get(
                "url"
            )

            state[
                "last_update"
            ] = now_iso()

            save_state()

            add_history(
                event_type="uav_location",
                level=old_level,
                locations=merged,
                source=post.get(
                    "source_name"
                ),
                post=post,
                details=(
                    "Обновление территорий "
                    "без изменения уровня"
                ),
            )

        return False

    # --------------------------------------------------------
    # УРОВЕНЬ НИЖЕ ТЕКУЩЕГО
    # --------------------------------------------------------

    if new_level < old_level:
        logger.info(
            "Игнорируем автоматическое "
            "понижение уровня %s -> %s. "
            "Нужен Отбой.",
            old_level,
            new_level,
        )

        # Но территории можем обновить.
        merged = list(
            dict.fromkeys(
                old_locations
                + locations
            )
        )

        if merged != old_locations:
            state[
                "active_locations"
            ] = merged

            state[
                "last_uav_update"
            ] = now_iso()

            save_state()

        return False

    # --------------------------------------------------------
    # ПОВЫШЕНИЕ
    # --------------------------------------------------------

    state["uav_level"] = new_level

    state["active_locations"] = list(
        dict.fromkeys(
            old_locations
            + locations
        )
    )

    state["last_uav_update"] = now_iso()

    state["last_uav_source"] = post.get(
        "source_name"
    )

    state["last_uav_post"] = post.get(
        "url"
    )

    state["last_update"] = now_iso()

    save_state()

    add_history(
        event_type="uav",
        level=new_level,
        locations=state[
            "active_locations"
        ],
        source=post.get(
            "source_name"
        ),
        post=post,
        details=(
            f"Повышение уровня "
            f"{old_level} -> {new_level}"
        ),
    )

    message = build_uav_push(
        event_type="uav",
        level=new_level,
        locations=state[
            "active_locations"
        ],
        post=post,
    )

    await send_push(
        application,
        message,
        event_type="uav",
        level=new_level,
        locations=state[
            "active_locations"
        ],
        cycle=state[
            "uav_cycle"
        ],
        post=None,
    )

    return True


# ============================================================
# APPLY ROCKET EVENT
# ============================================================

async def apply_rocket_event(
    application,
    active,
    post,
):
    old_active = bool(
        state.get(
            "rocket_active",
            False,
        )
    )

    # Нет изменения.
    if active == old_active:
        return False

    state["rocket_active"] = active

    state["rocket_cycle"] = int(
        state.get(
            "rocket_cycle",
            0,
        )
    ) + 1

    state[
        "last_rocket_update"
    ] = now_iso()

    state[
        "last_rocket_source"
    ] = post.get(
        "source_name"
    )

    state[
        "last_rocket_post"
    ] = post.get(
        "url"
    )

    state["last_update"] = now_iso()

    save_state()

    event_type = (
        "rocket_on"
        if active
        else "rocket_off"
    )

    add_history(
        event_type=event_type,
        level=0,
        locations=[],
        source=post.get(
            "source_name"
        ),
        post=post,
    )

    message = build_rocket_push(
        active,
        post,
    )

    await send_push(
        application,
        message,
        event_type=event_type,
        level=0,
        locations=[],
        cycle=state[
            "rocket_cycle"
        ],
        post=None,
    )

    return True


# ============================================================
# SOURCE PROCESSING
# ============================================================

async def process_post(
    application,
    post,
):
    """
    Обрабатывает один НОВЫЙ пост.

    Важно:
    сюда попадают только посты, которые прошли
    source cursor.

    Старые посты сюда повторно не попадут.
    """

    if post_was_processed(post):
        logger.info(
            "Пост уже обработан: %s",
            post.get("key"),
        )
        return

    parsed = parse_post(
        post
    )

    text = post.get(
        "text",
        "",
    )

    locations = parsed[
        "locations"
    ]

    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # --------------------------------------------------------

    if (
        parsed["rocket_cancel"]
        and state.get(
            "rocket_active",
            False,
        )
    ):
        await apply_rocket_event(
            application,
            False,
            post,
        )

    elif parsed["rocket"]:
        await apply_rocket_event(
            application,
            True,
            post,
        )

    # --------------------------------------------------------
    # БПЛА
    # --------------------------------------------------------

    if (
        parsed["cancel"]
        and contains_uav(text)
        and (
            state.get(
                "uav_level",
                0,
            )
            > 0
        )
    ):
        await apply_uav_event(
            application,
            0,
            locations,
            post,
        )

    elif (
        parsed["relevant_uav"]
        and parsed["uav_level"] > 0
    ):
        await apply_uav_event(
            application,
            parsed["uav_level"],
            locations,
            post,
        )

    mark_post_processed(
        post
    )


# ============================================================
# STARTUP BASELINE
# ============================================================

async def make_startup_baseline():
    """
    Первый запуск:

    Мы читаем существующие посты источников,
    запоминаем последний ID,
    но НИЧЕГО не отправляем пользователям.

    Это критично для защиты от старых пушей.
    """

    logger.info(
        "Создание первоначальной базы источников..."
    )

    changed = False

    for source_key, source_info in SOURCES.items():
        posts = await asyncio.to_thread(
            fetch_source,
            source_key,
            source_info,
        )

        if not posts:
            logger.warning(
                "Нет постов для baseline: %s",
                source_key,
            )
            continue

        latest = posts[-1]

        set_source_baseline(
            source_key,
            latest["post_id"],
        )

        changed = True

        logger.info(
            "Baseline %s: post_id=%s",
            source_key,
            latest["post_id"],
        )

    if changed:
        state[
            "startup_completed"
        ] = True

        state[
            "last_update"
        ] = now_iso()

        save_state()

    logger.info(
        "Первоначальная база источников создана."
    )


# ============================================================
# GET NEW POSTS
# ============================================================

def get_new_posts_for_source(
    source_key,
    posts,
):
    """
    Возвращает только действительно новые посты.

    Главная защита от старых сообщений.

    Если cursor найден:
        post_id > cursor

    Если cursor НЕ найден среди старой страницы:
        НЕ берём posts[-10:].
        Вместо этого создаём новый baseline.
    """

    if not posts:
        return []

    latest_id = posts[-1][
        "post_id"
    ]

    # Первый запуск конкретного источника.
    if not source_has_baseline(
        source_key
    ):
        set_source_baseline(
            source_key,
            latest_id,
        )

        logger.info(
            "Первый baseline источника %s",
            source_key,
        )

        return []

    cursor = get_source_cursor(
        source_key
    )

    if cursor <= 0:
        set_source_baseline(
            source_key,
            latest_id,
        )

        return []

    # Нормальный случай.
    newer = [
        post
        for post in posts
        if post["post_id"] > cursor
    ]

    if newer:
        return newer

    # Ничего нового.
    return []


# ============================================================
# CHECK SOURCES
# ============================================================

monitor_lock = asyncio.Lock()


async def check_sources(
    application,
):
    """
    Один цикл мониторинга всех источников.
    """

    if monitor_lock.locked():
        logger.warning(
            "Предыдущая проверка источников "
            "ещё выполняется."
        )
        return

    async with monitor_lock:
        for source_key, source_info in SOURCES.items():
            try:
                posts = await asyncio.to_thread(
                    fetch_source,
                    source_key,
                    source_info,
                )

                if not posts:
                    continue

                # ------------------------------------------------
                # Получаем новые посты.
                # ------------------------------------------------

                new_posts = get_new_posts_for_source(
                    source_key,
                    posts,
                )

                if not new_posts:
                    continue

                logger.info(
                    "Источник %s: новых постов %s",
                    source_key,
                    len(new_posts),
                )

                # ------------------------------------------------
                # Обрабатываем от старого к новому.
                # ------------------------------------------------

                for post in new_posts:
                    await process_post(
                        application,
                        post,
                    )

                    # Cursor двигаем ПОСЛЕ обработки.
                    set_source_cursor(
                        source_key,
                        post["post_id"],
                    )

            except Exception:
                logger.exception(
                    "Ошибка проверки источника %s",
                    source_key,
                )


# ============================================================
# MONITOR LOOP
# ============================================================

monitor_task = None


async def monitor_loop(
    application,
):
    logger.info(
        "Мониторинг источников запущен. "
        "Интервал: %s сек.",
        CHECK_INTERVAL,
    )

    # Первый запуск:
    # только baseline.
    if not state.get(
        "startup_completed",
        False,
    ):
        try:
            await make_startup_baseline()
        except Exception:
            logger.exception(
                "Ошибка создания startup baseline"
            )

    else:
        # На уже работающем боте сразу проверяем.
        await check_sources(
            application
        )

    while True:
        try:
            await asyncio.sleep(
                CHECK_INTERVAL
            )

            await check_sources(
                application
            )

        except asyncio.CancelledError:
            logger.info(
                "Мониторинг остановлен."
            )
            raise

        except Exception:
            logger.exception(
                "Ошибка monitor loop"
            )

            await asyncio.sleep(
                CHECK_INTERVAL
            )


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    level = int(
        state.get(
            "uav_level",
            0,
        )
    )

    status_text = UAV_LEVEL_NAMES.get(
        level,
        "🟢 Нет опасности",
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status",
            ),
            InlineKeyboardButton(
                "📜 История",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "📡 Источники",
                callback_data="sources",
            ),
            InlineKeyboardButton(
                "📈 Статистика",
                callback_data="stats",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔔 Подписка",
                callback_data="subscribe",
            ),
        ],
    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# STATUS TEXT
# ============================================================

def format_status():
    level = int(
        state.get(
            "uav_level",
            0,
        )
    )

    locations = state.get(
        "active_locations",
        [],
    )

    rocket_active = bool(
        state.get(
            "rocket_active",
            False,
        )
    )

    lines = [
        "<b>UAV ALERT</b>",
        "",
        f"<b>Статус:</b> "
        f"{escape(UAV_LEVEL_NAMES.get(level, 'Неизвестно'))}",
    ]

    if locations:
        lines.extend(
            [
                "",
                "<b>Активные территории:</b>",
                format_locations(
                    locations
                ),
            ]
        )

    if rocket_active:
        lines.extend(
            [
                "",
                "<b>🟥 Ракетная опасность:</b> активна",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "<b>🟥 Ракетная опасность:</b> нет",
            ]
        )

    last_update = state.get(
        "last_update"
    )

    if last_update:
        lines.extend(
            [
                "",
                "<b>Последнее обновление:</b> "
                + escape(
                    format_time(
                        last_update
                    )
                ),
            ]
        )

    return "\n".join(lines)


# ============================================================
# HISTORY TEXT
# ============================================================

def format_history(limit=15):
    if not history:
        return (
            "<b>История</b>\n\n"
            "История пока пустая."
        )

    items = history[-limit:]

    lines = [
        "<b>📜 Последние события</b>",
        "",
    ]

    for item in reversed(items):
        event_type = item.get(
            "type",
            "",
        )

        level = int(
            item.get(
                "level",
                0,
            )
        )

        timestamp = item.get(
            "time"
        )

        locations = item.get(
            "locations",
            [],
        )

        if event_type == "uav_cancel":
            title = "🟢 Отбой по БПЛА"

        elif event_type == "uav_location":
            title = (
                "📍 Обновление территорий"
            )

        elif event_type == "rocket_on":
            title = (
                "🟥 Ракетная опасность"
            )

        elif event_type == "rocket_off":
            title = (
                "🟢 Отбой ракетной опасности"
            )

        else:
            title = UAV_LEVEL_NAMES.get(
                level,
                "Событие",
            )

        line = (
            f"<b>{escape(title)}</b> — "
            f"{escape(format_time(timestamp))}"
        )

        lines.append(line)

        if locations:
            lines.append(
                "Территории: "
                + ", ".join(
                    escape(x)
                    for x in locations
                )
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# SOURCES TEXT
# ============================================================

def format_sources():
    lines = [
        "<b>📡 Источники мониторинга</b>",
        "",
    ]

    for source_key, source_info in SOURCES.items():
        cursor = get_source_cursor(
            source_key
        )

        lines.append(
            "• <b>"
            + escape(
                source_info["name"]
            )
            + "</b>"
        )

        lines.append(
            f'<a href="{escape(source_info["url"])}">'
            "Открыть источник"
            "</a>"
        )

        if cursor:
            lines.append(
                f"Последний ID: {cursor}"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# STATISTICS
# ============================================================

def format_stats():
    uav_events = 0
    cancel_events = 0
    rocket_events = 0

    for item in history:
        event_type = item.get(
            "type",
            "",
        )

        if event_type == "uav":
            uav_events += 1

        elif event_type == "uav_cancel":
            cancel_events += 1

        elif event_type in (
            "rocket_on",
            "rocket_off",
        ):
            rocket_events += 1

    lines = [
        "<b>📈 Статистика UAV ALERT</b>",
        "",
        f"Подписчиков: <b>{len(subscribers)}</b>",
        f"Событий БПЛА: <b>{uav_events}</b>",
        f"Отбоев БПЛА: <b>{cancel_events}</b>",
        f"Ракетных событий: <b>{rocket_events}</b>",
        f"Записей истории: <b>{len(history)}</b>",
        "",
        "<b>Текущий уровень:</b>",
        escape(
            UAV_LEVEL_NAMES.get(
                int(
                    state.get(
                        "uav_level",
                        0,
                    )
                ),
                "Неизвестно",
            )
        ),
    ]

    return "\n".join(lines)


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_chat:
        return

    chat_id = str(
        update.effective_chat.id
    )

    subscribers.add(chat_id)

    save_subscribers()

    text = (
        "<b>UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "по ситуации с БПЛА "
        "в Костромской области.\n\n"
        "🔔 Вы подписаны на push-уведомления.\n\n"
        "Уведомления будут приходить при "
        "изменении статуса."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /STOP
# ============================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_chat:
        return

    chat_id = str(
        update.effective_chat.id
    )

    subscribers.discard(
        chat_id
    )

    save_subscribers()

    await update.effective_message.reply_text(
        "🔕 Вы отписались от push-уведомлений.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# /STATUS
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        format_status(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /HISTORY
# ============================================================

async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        format_history(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /SOURCES
# ============================================================

async def sources_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        format_sources(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.effective_message.reply_text(
        format_stats(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /SUBSCRIBE
# ============================================================

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.effective_chat:
        return

    chat_id = str(
        update.effective_chat.id
    )

    subscribers.add(chat_id)

    save_subscribers()

    await update.effective_message.reply_text(
        "🔔 Push-уведомления включены.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# /UNSUBSCRIBE
# ============================================================

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await stop_command(
        update,
        context,
    )


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(update):
    if not update.effective_user:
        return False

    return (
        update.effective_user.id
        == ADMIN_ID
    )


# ============================================================
# /TEST
# ============================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    args = context.args

    if not args:
        await update.effective_message.reply_text(
            "Использование:\n"
            "/test attention\n"
            "/test threat\n"
            "/test danger\n"
            "/test cancel\n"
            "/test rocket\n"
            "/test rocket_off"
        )
        return

    test_type = args[0].lower()

    fake_post = {
        "source_name": "UAV ALERT TEST",
        "url": "",
        "post_id": 0,
        "key": (
            "test:"
            + test_type
            + ":"
            + str(
                int(
                    time.time()
                )
            )
        ),
    }

    if test_type == "attention":
        await send_push(
            context.application,
            build_uav_push(
                "uav",
                1,
                ["Костромская область"],
                fake_post,
            ),
            "test_attention",
            1,
            ["Костромская область"],
            int(
                time.time()
            ),
            post=None,
        )

    elif test_type == "threat":
        await send_push(
            context.application,
            build_uav_push(
                "uav",
                2,
                ["Костромская область"],
                fake_post,
            ),
            "test_threat",
            2,
            ["Костромская область"],
            int(
                time.time()
            ),
            post=None,
        )

    elif test_type == "danger":
        await send_push(
            context.application,
            build_uav_push(
                "uav",
                3,
                ["Костромская область"],
                fake_post,
            ),
            "test_danger",
            3,
            ["Костромская область"],
            int(
                time.time()
            ),
            post=None,
        )

    elif test_type == "cancel":
        await send_push(
            context.application,
            build_uav_push(
                "uav_cancel",
                0,
                ["Костромская область"],
                fake_post,
            ),
            "test_cancel",
            0,
            ["Костромская область"],
            int(
                time.time()
            ),
            post=None,
        )

    elif test_type == "rocket":
        await send_push(
            context.application,
            build_rocket_push(
                True,
                fake_post,
            ),
            "test_rocket",
            0,
            [],
            int(
                time.time()
            ),
            post=None,
        )

    elif test_type == "rocket_off":
        await send_push(
            context.application,
            build_rocket_push(
                False,
                fake_post,
            ),
            "test_rocket_off",
            0,
            [],
            int(
                time.time()
            ),
            post=None,
        )

    else:
        await update.effective_message.reply_text(
            "Неизвестный тест."
        )
        return

    await update.effective_message.reply_text(
        "✅ Тестовое push-уведомление отправлено."
    )


# ============================================================
# /ADMIN
# ============================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ Нет доступа."
        )
        return

    text = (
        "<b>Админ-панель UAV ALERT</b>\n\n"
        f"Подписчиков: {len(subscribers)}\n"
        f"История: {len(history)}\n"
        f"Источников: {len(SOURCES)}\n"
        f"Интервал: {CHECK_INTERVAL} сек."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
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

    await query.answer()

    data = query.data

    if data == "status":
        await query.message.reply_text(
            format_status(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

    elif data == "history":
        await query.message.reply_text(
            format_history(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

    elif data == "sources":
        await query.message.reply_text(
            format_sources(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

    elif data == "stats":
        await query.message.reply_text(
            format_stats(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

    elif data == "subscribe":
        chat_id = str(
            query.message.chat.id
        )

        subscribers.add(chat_id)

        save_subscribers()

        await query.message.reply_text(
            "🔔 Push-уведомления включены.",
            reply_markup=main_keyboard(),
        )


# ============================================================
# FLASK
# ============================================================

flask_app = Flask(
    __name__
)


@flask_app.route("/")
def index():
    return (
        "UAV ALERT работает!",
        200,
    )


@flask_app.route("/status")
def web_status():
    level = int(
        state.get(
            "uav_level",
            0,
        )
    )

    return {
        "service": "UAV ALERT",
        "status": "ok",
        "uav_level": level,
        "uav_status": UAV_LEVEL_NAMES.get(
            level,
            "Неизвестно",
        ),
        "rocket_active": bool(
            state.get(
                "rocket_active",
                False,
            )
        ),
        "subscribers": len(
            subscribers
        ),
        "history": len(
            history
        ),
        "updated": state.get(
            "last_update"
        ),
    }, 200


def run_flask():
    logger.info(
        "Flask запускается на порту %s",
        PORT,
    )

    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):
    global monitor_task

    logger.info(
        "Инициализация UAV ALERT..."
    )

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Подписаться на уведомления",
            ),
            BotCommand(
                "stop",
                "Отключить уведомления",
            ),
            BotCommand(
                "status",
                "Текущий статус",
            ),
            BotCommand(
                "history",
                "История событий",
            ),
            BotCommand(
                "sources",
                "Источники",
            ),
            BotCommand(
                "stats",
                "Статистика",
            ),
            BotCommand(
                "subscribe",
                "Включить push",
            ),
            BotCommand(
                "unsubscribe",
                "Отключить push",
            ),
        ]
    )

    monitor_task = asyncio.create_task(
        monitor_loop(
            application
        )
    )

    logger.info(
        "UAV ALERT готов."
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application,
):
    global monitor_task

    if monitor_task:
        monitor_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    logger.info(
        "UAV ALERT остановлен."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Переменная окружения BOT_TOKEN не задана."
        )

    # Flask запускаем отдельно.
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
        name="FlaskThread",
    )

    flask_thread.start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
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
            "stop",
            stop_command,
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
            "sources",
            sources_command,
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
            "subscribe",
            subscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "test",
            test_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "admin",
            admin_command,
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
    # RUN
    # --------------------------------------------------------

    logger.info(
        "Запуск Telegram polling..."
    )

    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
