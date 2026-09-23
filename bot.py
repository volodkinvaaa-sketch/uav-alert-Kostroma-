import os
import re
import json
import time
import hashlib
import threading
import logging
import asyncio
from datetime import datetime, timezone, timedelta

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
# UAV ALERT
# Гражданский информационный сервис
# Костромская область
#
# Версия: полная
# ============================================================


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("UAV_ALERT")


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(
        os.getenv(
            "ADMIN_ID",
            "1421675956",
        )
    )
except Exception:
    ADMIN_ID = 1421675956


try:
    PORT = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )
except Exception:
    PORT = 10000


CHANNEL = os.getenv(
    "CHANNEL",
    "@RADAR_Kostroma",
).strip()


try:
    CHECK_INTERVAL = int(
        os.getenv(
            "CHECK_INTERVAL",
            "30",
        )
    )
except Exception:
    CHECK_INTERVAL = 30


try:
    DEDUP_HOURS = int(
        os.getenv(
            "DEDUP_HOURS",
            "24",
        )
    )
except Exception:
    DEDUP_HOURS = 24


MSK = timezone(
    timedelta(
        hours=3
    )
)


BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)


SUBSCRIBERS_FILE = os.path.join(
    BASE_DIR,
    "subscribers.json",
)

STATE_FILE = os.path.join(
    BASE_DIR,
    "state.json",
)

HISTORY_FILE = os.path.join(
    BASE_DIR,
    "history.json",
)

SENT_POSTS_FILE = os.path.join(
    BASE_DIR,
    "sent_posts.json",
)

NOTIFICATION_EVENTS_FILE = os.path.join(
    BASE_DIR,
    "notification_events.json",
)


# ============================================================
# ИСТОЧНИКИ
# ============================================================

SOURCES = [
    {
        "name": "LOCATOR.RU",
        "url": "https://t.me/s/locatorru",
    },
    {
        "name": "RUSSIA MONITORING RADAR BPLA",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
    },
    {
        "name": "RADAR RUSSIA",
        "url": "https://t.me/s/radarrussiia",
    },
    {
        "name": "BPLA RUSSIA",
        "url": "https://t.me/s/bplarussiaru",
    },
]


# ============================================================
# КОСТРОМСКАЯ ОБЛАСТЬ
# ============================================================

DISTRICTS = {
    "костромской район": "Костромской район",
    "буйский район": "Буйский район",
    "волгореченский район": "Волгореченский район",
    "галичский район": "Галичский район",
    "кадыйский район": "Кадыйский район",
    "кологривский район": "Кологривский район",
    "костромской муниципальный район": "Костромской муниципальный район",
    "красносельский район": "Красносельский район",
    "макарьевский район": "Макарьевский район",
    "манту́ровский район": "Мантуровский район",
    "мантуровский район": "Мантуровский район",
    "межевской район": "Межевской район",
    "неейский район": "Неейский район",
    "не́йский район": "Нейский район",
    "не́рехтский район": "Нерехтский район",
    "нерехтский район": "Нерехтский район",
    "октябрьский район": "Октябрьский район",
    "островский район": "Островский район",
    "павинский район": "Павинский район",
    "парфеньевский район": "Парфеньевский район",
    "поназыревский район": "Поназыревский район",
    "пошехонский район": "Пошехонский район",
    "солигаличский район": "Солигаличский район",
    "сусанинский район": "Сусанинский район",
    "чухломский район": "Чухломский район",
    "шарьинский район": "Шарьинский район",
}


MUNICIPAL_DISTRICTS = {
    "костромской муниципальный округ": "Костромской муниципальный округ",
    "буйский муниципальный округ": "Буйский муниципальный округ",
    "волгореченский муниципальный округ": "Волгореченский муниципальный округ",
    "галичский муниципальный округ": "Галичский муниципальный округ",
    "кадыйский муниципальный округ": "Кадыйский муниципальный округ",
    "кологривский муниципальный округ": "Кологривский муниципальный округ",
    "красносельский муниципальный округ": "Красносельский муниципальный округ",
    "макарьевский муниципальный округ": "Макарьевский муниципальный округ",
    "мантуровский муниципальный округ": "Мантуровский муниципальный округ",
    "межевской муниципальный округ": "Межевской муниципальный округ",
    "нейский муниципальный округ": "Нейский муниципальный округ",
    "нерехтский муниципальный округ": "Нерехтский муниципальный округ",
    "октябрьский муниципальный округ": "Октябрьский муниципальный округ",
    "островский муниципальный округ": "Островский муниципальный округ",
    "павинский муниципальный округ": "Павинский муниципальный округ",
    "парфеньевский муниципальный округ": "Парфеньевский муниципальный округ",
    "поназыревский муниципальный округ": "Поназыревский муниципальный округ",
    "солигаличский муниципальный округ": "Солигаличский муниципальный округ",
    "сусанинский муниципальный округ": "Сусанинский муниципальный округ",
    "чухломский муниципальный округ": "Чухломский муниципальный округ",
    "шарьинский муниципальный округ": "Шарьинский муниципальный округ",
}


CITIES = {
    "кострома": "Кострома",
    "буй": "Буй",
    "волгореченск": "Волгореченск",
    "галич": "Галич",
    "шарья": "Шарья",
    "мантурово": "Мантурово",
    "нерехта": "Нерехта",
    "чухлома": "Чухлома",
    "макарьев": "Макарьев",
    "солигалич": "Солигалич",
    "кологрив": "Кологрив",
    "нея": "Нея",
}


REGION_VARIANTS = [
    "костромская область",
    "костромской области",
    "костромская обл",
    "костромской обл",
    "костромская",
    "костромском регионе",
    "костромского региона",
]


# ============================================================
# УРОВНИ ТРЕВОГИ БПЛА
# ============================================================

UAV_LEVELS = {
    1: {
        "name": "Внимание по БПЛА",
        "emoji": "🟡",
    },
    2: {
        "name": "Угроза по БПЛА",
        "emoji": "🟠",
    },
    3: {
        "name": "Опасность по БПЛА",
        "emoji": "🔴",
    },
}


# ============================================================
# СОСТОЯНИЕ ПО УМОЛЧАНИЮ
# ============================================================

DEFAULT_STATE = {
    "status": "green",
    "title": "🟢 Опасность не объявлена",

    "uav_level": 0,
    "rocket_active": False,

    "location": "Костромская область",

    "districts": [],
    "municipal_districts": [],
    "active_locations": [],
    "cities": [],

    "source": "",
    "source_url": "",

    "updated_at": "",

    "event_post_id": "",
    "event_source_id": "",

    "uav_cycle": 0,
    "rocket_cycle": 0,

    "last_uav_level": 0,
    "last_uav_districts": [],
    "last_uav_municipal_districts": [],
    "last_uav_locations": [],
}


# ============================================================
# ГЛОБАЛЬНЫЕ ДАННЫЕ
# ============================================================

state = {}
subscribers = []
history = []
sent_posts = []
notification_events = []


# ============================================================
# LOCK
# ============================================================

data_lock = threading.RLock()


# ============================================================
# JSON
# ============================================================

def load_json(
    filename,
    default,
):
    try:
        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        return data

    except Exception as error:
        logger.error(
            "Ошибка чтения %s: %s",
            filename,
            error,
        )

        return default


def save_json(
    filename,
    data,
):
    temporary_file = filename + ".tmp"

    try:
        with open(
            temporary_file,
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temporary_file,
            filename,
        )

    except Exception as error:
        logger.error(
            "Ошибка сохранения %s: %s",
            filename,
            error,
        )


# ============================================================
# УНИКАЛЬНЫЙ СПИСОК
# ВАЖНО: эта функция определена ДО normalize_state()
# ============================================================

def unique_list(items):
    result = []

    if not items:
        return result

    for item in items:

        if item is None:
            continue

        value = str(item).strip()

        if not value:
            continue

        if value not in result:
            result.append(value)

    return result


# ============================================================
# НОРМАЛИЗАЦИЯ STATE
# ============================================================

def normalize_state():
    global state

    with data_lock:

        if not isinstance(state, dict):
            state = {}

        for key, value in DEFAULT_STATE.items():

            if key not in state:
                state[key] = value

        # ----------------------------------------------------
        # Нормализуем списки
        # ----------------------------------------------------

        state["districts"] = unique_list(
            state.get(
                "districts",
                [],
            )
        )

        state["municipal_districts"] = unique_list(
            state.get(
                "municipal_districts",
                [],
            )
        )

        state["active_locations"] = unique_list(
            state.get(
                "active_locations",
                [],
            )
        )

        state["cities"] = unique_list(
            state.get(
                "cities",
                [],
            )
        )

        state["last_uav_districts"] = unique_list(
            state.get(
                "last_uav_districts",
                [],
            )
        )

        state["last_uav_municipal_districts"] = unique_list(
            state.get(
                "last_uav_municipal_districts",
                [],
            )
        )

        state["last_uav_locations"] = unique_list(
            state.get(
                "last_uav_locations",
                [],
            )
        )

        # ----------------------------------------------------
        # Если active_locations отсутствует,
        # собираем его из старых данных.
        # ----------------------------------------------------

        if not state.get(
            "active_locations"
        ):

            old_locations = (
                state.get(
                    "districts",
                    [],
                )
                + state.get(
                    "municipal_districts",
                    [],
                )
                + state.get(
                    "cities",
                    [],
                )
            )

            state[
                "active_locations"
            ] = unique_list(
                old_locations
            )

        # ----------------------------------------------------
        # Проверяем UAV level
        # ----------------------------------------------------

        try:
            state["uav_level"] = int(
                state.get(
                    "uav_level",
                    0,
                )
            )
        except Exception:
            state["uav_level"] = 0

        if state["uav_level"] < 0:
            state["uav_level"] = 0

        if state["uav_level"] > 3:
            state["uav_level"] = 3

        # ----------------------------------------------------
        # Проверяем rocket_active
        # ----------------------------------------------------

        state["rocket_active"] = bool(
            state.get(
                "rocket_active",
                False,
            )
        )

        # ----------------------------------------------------
        # Пересобираем общий статус
        # ----------------------------------------------------

        if state["rocket_active"]:
            state["status"] = "rocket"
            state["title"] = "🔴 Ракетная опасность"

        elif state["uav_level"] > 0:
            level_data = UAV_LEVELS[
                state["uav_level"]
            ]

            state["status"] = "uav"
            state["title"] = (
                f"{level_data['emoji']} "
                f"{level_data['name']}"
            )

        else:
            state["status"] = "green"
            state["title"] = (
                "🟢 Опасность не объявлена"
            )


# ============================================================
# ИНИЦИАЛИЗАЦИЯ ДАННЫХ
# ============================================================

state = load_json(
    STATE_FILE,
    DEFAULT_STATE.copy(),
)

subscribers = load_json(
    SUBSCRIBERS_FILE,
    [],
)

history = load_json(
    HISTORY_FILE,
    [],
)

sent_posts = load_json(
    SENT_POSTS_FILE,
    [],
)

notification_events = load_json(
    NOTIFICATION_EVENTS_FILE,
    [],
)


if not isinstance(
    subscribers,
    list,
):
    subscribers = []


if not isinstance(
    history,
    list,
):
    history = []


if not isinstance(
    sent_posts,
    list,
):
    sent_posts = []


if not isinstance(
    notification_events,
    list,
):
    notification_events = []


normalize_state()


# ============================================================
# ВРЕМЯ
# ============================================================

def now_msk():
    return datetime.now(
        MSK
    )


def now_iso():
    return now_msk().isoformat()


def format_time(
    value,
):
    if not value:
        return "неизвестно"

    try:
        dt = datetime.fromisoformat(
            value
        )

        return dt.astimezone(
            MSK
        ).strftime(
            "%d.%m.%Y %H:%M"
        )

    except Exception:
        return str(value)


# ============================================================
# ТЕКСТ
# ============================================================

def clean_text(
    text,
):
    if not text:
        return ""

    text = str(text)

    text = text.replace(
        "\xa0",
        " ",
    )

    text = re.sub(
        r"\r\n?",
        "\n",
        text,
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    return text.strip()


def normalize_text(
    text,
):
    text = clean_text(
        text
    )

    text = text.lower()

    text = text.replace(
        "ё",
        "е",
    )

    return text


# ============================================================
# HASH
# ============================================================

def make_hash(
    text,
):
    return hashlib.sha256(
        text.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()


# ============================================================
# ОПРЕДЕЛЕНИЕ РАЙОНОВ
# ============================================================

def detect_districts(
    text,
):
    normalized = normalize_text(
        text
    )

    found = []

    for key, name in DISTRICTS.items():

        if key in normalized:
            found.append(
                name
            )

    return unique_list(
        found
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ МУНИЦИПАЛЬНЫХ ОКРУГОВ
# ============================================================

def detect_municipal_districts(
    text,
):
    normalized = normalize_text(
        text
    )

    found = []

    for key, name in MUNICIPAL_DISTRICTS.items():

        if key in normalized:
            found.append(
                name
            )

    # Исправление возможной опечатки
    if (
        "мунициальный округ"
        in normalized
    ):
        for key, name in MUNICIPAL_DISTRICTS.items():

            short_key = key.replace(
                "муниципальный",
                "мунициальный",
            )

            if short_key in normalized:
                found.append(
                    name
                )

    return unique_list(
        found
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ ГОРОДОВ
# ============================================================

def detect_cities(
    text,
):
    normalized = normalize_text(
        text
    )

    found = []

    for key, name in CITIES.items():

        pattern = (
            r"(?<![а-яё])"
            + re.escape(key)
            + r"(?![а-яё])"
        )

        if re.search(
            pattern,
            normalized,
        ):
            found.append(
                name
            )

    return unique_list(
        found
    )


# ============================================================
# ВСЕ ЛОКАЦИИ
# ============================================================

def detect_locations(
    text,
):
    districts = detect_districts(
        text
    )

    municipal_districts = (
        detect_municipal_districts(
            text
        )
    )

    cities = detect_cities(
        text
    )

    locations = (
        districts
        + municipal_districts
        + cities
    )

    return (
        unique_list(districts),
        unique_list(municipal_districts),
        unique_list(cities),
        unique_list(locations),
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ СОБЫТИЯ
# ============================================================

def detect_event(
    text,
):
    normalized = normalize_text(
        text
    )

    # --------------------------------------------------------
    # ОТБОЙ БПЛА
    # --------------------------------------------------------

    uav_cancel_patterns = [
        r"отбой.*бпла",
        r"отбой.*беспилот",
        r"угроза.*бпла.*отмен",
        r"опасност.*бпла.*отмен",
        r"внимани.*бпла.*отмен",
        r"бпла.*отмен",
        r"беспилот.*отмен",
    ]

    for pattern in uav_cancel_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "uav_cancel",
                "level": 0,
            }

    # --------------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------

    rocket_cancel_patterns = [
        r"отбой.*ракет",
        r"ракетн.*опасност.*отмен",
        r"ракетн.*угроз.*отмен",
    ]

    for pattern in rocket_cancel_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "rocket_cancel",
                "level": 0,
            }

    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # --------------------------------------------------------

    rocket_patterns = [
        r"ракетная опасность",
        r"ракетн[а-я]* опасност",
        r"ракетн[а-я]* угроза",
        r"угроза ракет",
    ]

    for pattern in rocket_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "rocket",
                "level": 0,
            }

    # --------------------------------------------------------
    # ОПАСНОСТЬ ПО БПЛА
    # --------------------------------------------------------

    danger_patterns = [
        r"опасность по бпла",
        r"опасность.*бпла",
        r"опасность.*беспилот",
        r"бпла.*опасност",
        r"беспилот.*опасност",
    ]

    for pattern in danger_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "uav",
                "level": 3,
            }

    # --------------------------------------------------------
    # УГРОЗА ПО БПЛА
    # --------------------------------------------------------

    threat_patterns = [
        r"угроза по бпла",
        r"угроза.*бпла",
        r"угроза.*беспилот",
        r"бпла.*угроз",
        r"беспилот.*угроз",
    ]

    for pattern in threat_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "uav",
                "level": 2,
            }

    # --------------------------------------------------------
    # ВНИМАНИЕ ПО БПЛА
    # --------------------------------------------------------

    attention_patterns = [
        r"внимание по бпла",
        r"внимание.*бпла",
        r"внимание.*беспилот",
        r"бпла.*внимани",
        r"беспилот.*внимани",
    ]

    for pattern in attention_patterns:

        if re.search(
            pattern,
            normalized,
        ):
            return {
                "type": "uav",
                "level": 1,
            }

    return None


# ============================================================
# ОТНОСИТСЯ ЛИ К КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

def is_kostroma_related(
    text,
):
    normalized = normalize_text(
        text
    )

    # Регион
    for variant in REGION_VARIANTS:

        if variant in normalized:
            return True

    # Районы
    districts = detect_districts(
        normalized
    )

    if districts:
        return True

    # Муниципальные округа
    municipal = detect_municipal_districts(
        normalized
    )

    if municipal:
        return True

    # Города
    cities = detect_cities(
        normalized
    )

    if cities:
        return True

    return False


# ============================================================
# ОПИСАНИЕ СОБЫТИЯ
# ============================================================

def build_event_description(
    event,
    districts,
    municipal_districts,
    cities,
):
    if not event:
        return ""

    event_type = event.get(
        "type"
    )

    if event_type == "uav_cancel":
        return (
            "Отбой по БПЛА"
        )

    if event_type == "rocket_cancel":
        return (
            "Отбой ракетной опасности"
        )

    if event_type == "rocket":
        return (
            "Объявлена ракетная опасность"
        )

    if event_type == "uav":

        level = int(
            event.get(
                "level",
                0,
            )
        )

        level_data = UAV_LEVELS.get(
            level
        )

        if not level_data:
            return "Событие по БПЛА"

        locations = (
            districts
            + municipal_districts
            + cities
        )

        locations = unique_list(
            locations
        )

        if locations:

            return (
                f"{level_data['name']}: "
                + ", ".join(
                    locations
                )
            )

        return (
            f"{level_data['name']}"
        )

    return "Новое событие"


# ============================================================
# FINGERPRINT СОБЫТИЯ
# ============================================================

def make_event_fingerprint(
    event,
    districts,
    municipal_districts,
    cities,
):
    event_type = event.get(
        "type",
        "",
    )

    level = event.get(
        "level",
        0,
    )

    locations = unique_list(
        districts
        + municipal_districts
        + cities
    )

    locations_string = "|".join(
        sorted(
            locations
        )
    )

    cycle = state.get(
        "uav_cycle"
        if event_type == "uav"
        or event_type == "uav_cancel"
        else "rocket_cycle",
        0,
    )

    raw = (
        f"{event_type}|"
        f"{level}|"
        f"{locations_string}|"
        f"{cycle}"
    )

    return make_hash(
        raw
    )


# ============================================================
# FINGERPRINT ПОСТА
# ============================================================

def make_post_fingerprint(
    source_name,
    post_id,
    text,
):
    raw = (
        str(source_name)
        + "|"
        + str(post_id)
        + "|"
        + clean_text(text)
    )

    return make_hash(
        raw
    )


# ============================================================
# ДЕДУПЛИКАЦИЯ
# ============================================================

def cleanup_dedup():
    global sent_posts
    global notification_events

    current_time = time.time()

    post_limit = (
        DEDUP_HOURS
        * 60
        * 60
    )

    event_limit = (
        DEDUP_HOURS
        * 60
        * 60
        * 7
    )

    with data_lock:

        new_posts = []

        for item in sent_posts:

            try:
                timestamp = float(
                    item.get(
                        "timestamp",
                        0,
                    )
                )

                if (
                    current_time
                    - timestamp
                    <= post_limit
                ):
                    new_posts.append(
                        item
                    )

            except Exception:
                continue

        sent_posts = new_posts

        new_events = []

        for item in notification_events:

            try:
                timestamp = float(
                    item.get(
                        "timestamp",
                        0,
                    )
                )

                if (
                    current_time
                    - timestamp
                    <= event_limit
                ):
                    new_events.append(
                        item
                    )

            except Exception:
                continue

        notification_events = new_events

        save_json(
            SENT_POSTS_FILE,
            sent_posts,
        )

        save_json(
            NOTIFICATION_EVENTS_FILE,
            notification_events,
        )


# ============================================================
# ПРОВЕРКА ПОСТА
# ============================================================

def post_was_processed(
    fingerprint,
):
    with data_lock:

        for item in sent_posts:

            if item.get(
                "fingerprint"
            ) == fingerprint:
                return True

    return False


# ============================================================
# СОХРАНИТЬ ПОСТ
# ============================================================

def mark_post_processed(
    fingerprint,
):
    global sent_posts

    with data_lock:

        sent_posts.append(
            {
                "fingerprint": fingerprint,
                "timestamp": time.time(),
                "created_at": now_iso(),
            }
        )

        if len(
            sent_posts
        ) > 2000:
            sent_posts = sent_posts[
                -2000:
            ]

        save_json(
            SENT_POSTS_FILE,
            sent_posts,
        )


# ============================================================
# ПРОВЕРКА СОБЫТИЯ
# ============================================================

def event_was_notified(
    fingerprint,
):
    with data_lock:

        for item in notification_events:

            if item.get(
                "fingerprint"
            ) == fingerprint:

                return True

    return False


# ============================================================
# СОХРАНИТЬ СОБЫТИЕ
# ============================================================

def mark_event_notified(
    fingerprint,
):
    global notification_events

    with data_lock:

        notification_events.append(
            {
                "fingerprint": fingerprint,
                "timestamp": time.time(),
                "created_at": now_iso(),
            }
        )

        if len(
            notification_events
        ) > 5000:
            notification_events = (
                notification_events[
                    -5000:
                ]
            )

        save_json(
            NOTIFICATION_EVENTS_FILE,
            notification_events,
        )


# ============================================================
# ЗАГОЛОВОК СОСТОЯНИЯ
# ============================================================

def get_status_title():
    with data_lock:

        if state.get(
            "rocket_active",
            False,
        ):
            return (
                "🔴 Ракетная опасность"
            )

        level = int(
            state.get(
                "uav_level",
                0,
            )
        )

        if level in UAV_LEVELS:

            data = UAV_LEVELS[
                level
            ]

            return (
                f"{data['emoji']} "
                f"{data['name']}"
            )

        return (
            "🟢 Опасность не объявлена"
        )


# ============================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# ============================================================

def apply_event(
    event,
    districts,
    municipal_districts,
    cities,
    source_name,
    source_url,
    post_id,
):
    global state

    if not event:
        return {
            "changed": False,
            "notify": False,
            "message": "",
        }

    event_type = event.get(
        "type"
    )

    level = int(
        event.get(
            "level",
            0,
        )
    )

    with data_lock:

        # ====================================================
        # БПЛА
        # ====================================================

        if event_type == "uav":

            old_level = int(
                state.get(
                    "uav_level",
                    0,
                )
            )

            old_locations = unique_list(
                state.get(
                    "active_locations",
                    [],
                )
            )

            new_locations = unique_list(
                districts
                + municipal_districts
                + cities
            )

            combined_locations = unique_list(
                old_locations
                + new_locations
            )

            # Уровень не понижаем.
            #
            # Например:
            # было "Опасность" = 3
            # пришло "Внимание" = 1
            #
            # состояние останется 3,
            # пока не придет "Отбой по БПЛА".

            new_level = max(
                old_level,
                level,
            )

            changed = (
                new_level != old_level
                or combined_locations
                != old_locations
            )

            state["uav_level"] = (
                new_level
            )

            state["districts"] = unique_list(
                state.get(
                    "districts",
                    [],
                )
                + districts
            )

            state["municipal_districts"] = (
                unique_list(
                    state.get(
                        "municipal_districts",
                        [],
                    )
                    + municipal_districts
                )
            )

            state["cities"] = unique_list(
                state.get(
                    "cities",
                    [],
                )
                + cities
            )

            state["active_locations"] = (
                combined_locations
            )

            state["last_uav_level"] = (
                new_level
            )

            state["last_uav_districts"] = (
                unique_list(
                    state["districts"]
                )
            )

            state[
                "last_uav_municipal_districts"
            ] = unique_list(
                state[
                    "municipal_districts"
                ]
            )

            state["last_uav_locations"] = (
                unique_list(
                    state[
                        "active_locations"
                    ]
                )
            )

            state["source"] = (
                source_name
            )

            state["source_url"] = (
                source_url
            )

            state["updated_at"] = (
                now_iso()
            )

            state["event_post_id"] = (
                str(post_id)
            )

            state["event_source_id"] = (
                source_name
            )

            if old_level == 0:

                state["uav_cycle"] = (
                    int(
                        state.get(
                            "uav_cycle",
                            0,
                        )
                    )
                    + 1
                )

            state["status"] = "uav"

            state["title"] = (
                get_status_title()
            )

            save_json(
                STATE_FILE,
                state,
            )

            return {
                "changed": changed,
                "notify": changed,
                "message": build_event_description(
                    event,
                    districts,
                    municipal_districts,
                    cities,
                ),
            }

        # ====================================================
        # ОТБОЙ БПЛА
        # ====================================================

        if event_type == "uav_cancel":

            had_alert = (
                int(
                    state.get(
                        "uav_level",
                        0,
                    )
                )
                > 0
            )

            old_locations = unique_list(
                state.get(
                    "active_locations",
                    [],
                )
            )

            old_level = int(
                state.get(
                    "uav_level",
                    0,
                )
            )

            state["last_uav_level"] = (
                old_level
            )

            state["last_uav_districts"] = (
                unique_list(
                    state.get(
                        "districts",
                        [],
                    )
                )
            )

            state[
                "last_uav_municipal_districts"
            ] = unique_list(
                state.get(
                    "municipal_districts",
                    [],
                )
            )

            state["last_uav_locations"] = (
                unique_list(
                    old_locations
                )
            )

            state["uav_level"] = 0

            state["districts"] = []
            state["municipal_districts"] = []
            state["active_locations"] = []
            state["cities"] = []

            state["source"] = (
                source_name
            )

            state["source_url"] = (
                source_url
            )

            state["updated_at"] = (
                now_iso()
            )

            state["event_post_id"] = (
                str(post_id)
            )

            state["event_source_id"] = (
                source_name
            )

            if state.get(
                "rocket_active",
                False,
            ):
                state["status"] = (
                    "rocket"
                )
                state["title"] = (
                    "🔴 Ракетная опасность"
                )

            else:
                state["status"] = (
                    "green"
                )
                state["title"] = (
                    "🟢 Опасность не объявлена"
                )

            save_json(
                STATE_FILE,
                state,
            )

            return {
                "changed": had_alert,
                "notify": had_alert,
                "message": (
                    "🟢 Отбой по БПЛА.\n\n"
                    "Опасность по БПЛА "
                    "отменена."
                ),
            }

        # ====================================================
        # РАКЕТНАЯ ОПАСНОСТЬ
        # ====================================================

        if event_type == "rocket":

            was_active = bool(
                state.get(
                    "rocket_active",
                    False,
                )
            )

            state["rocket_active"] = True

            state["status"] = (
                "rocket"
            )

            state["title"] = (
                "🔴 Ракетная опасность"
            )

            state["source"] = (
                source_name
            )

            state["source_url"] = (
                source_url
            )

            state["updated_at"] = (
                now_iso()
            )

            state["event_post_id"] = (
                str(post_id)
            )

            state["event_source_id"] = (
                source_name
            )

            if not was_active:

                state["rocket_cycle"] = (
                    int(
                        state.get(
                            "rocket_cycle",
                            0,
                        )
                    )
                    + 1
                )

            save_json(
                STATE_FILE,
                state,
            )

            return {
                "changed": not was_active,
                "notify": not was_active,
                "message": (
                    "🔴 РАКЕТНАЯ ОПАСНОСТЬ"
                ),
            }

        # ====================================================
        # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
        # ====================================================

        if event_type == "rocket_cancel":

            was_active = bool(
                state.get(
                    "rocket_active",
                    False,
                )
            )

            state["rocket_active"] = False

            state["source"] = (
                source_name
            )

            state["source_url"] = (
                source_url
            )

            state["updated_at"] = (
                now_iso()
            )

            state["event_post_id"] = (
                str(post_id)
            )

            state["event_source_id"] = (
                source_name
            )

            if state.get(
                "uav_level",
                0,
            ) > 0:

                state["status"] = (
                    "uav"
                )

                state["title"] = (
                    get_status_title()
                )

            else:

                state["status"] = (
                    "green"
                )

                state["title"] = (
                    "🟢 Опасность не объявлена"
                )

            save_json(
                STATE_FILE,
                state,
            )

            return {
                "changed": was_active,
                "notify": was_active,
                "message": (
                    "🟢 Отбой ракетной опасности."
                ),
            }

    return {
        "changed": False,
        "notify": False,
        "message": "",
    }


# ============================================================
# ИСТОРИЯ
# ============================================================

def add_history(
    event,
    districts,
    municipal_districts,
    cities,
    source_name,
    source_url,
    post_id,
    raw_text,
):
    global history

    description = build_event_description(
        event,
        districts,
        municipal_districts,
        cities,
    )

    item = {
        "timestamp": now_iso(),
        "type": event.get(
            "type",
            "",
        ),
        "level": event.get(
            "level",
            0,
        ),
        "description": description,
        "districts": unique_list(
            districts
        ),
        "municipal_districts": unique_list(
            municipal_districts
        ),
        "cities": unique_list(
            cities
        ),
        "source": source_name,
        "source_url": source_url,
        "post_id": str(
            post_id
        ),
        "text": clean_text(
            raw_text
        )[:2000],
    }

    with data_lock:

        history.append(
            item
        )

        if len(
            history
        ) > 300:
            history = history[
                -300:
            ]

        save_json(
            HISTORY_FILE,
            history,
        )


# ============================================================
# ФОРМАТ СТАТУСА
# ============================================================

def format_status():
    with data_lock:

        rocket_active = bool(
            state.get(
                "rocket_active",
                False,
            )
        )

        uav_level = int(
            state.get(
                "uav_level",
                0,
            )
        )

        updated_at = state.get(
            "updated_at",
            "",
        )

        source = state.get(
            "source",
            "",
        )

        locations = unique_list(
            state.get(
                "active_locations",
                [],
            )
        )

        # ----------------------------------------------------
        # Ракета
        # ----------------------------------------------------

        if rocket_active:

            text = (
                "🔴 <b>РАКЕТНАЯ ОПАСНОСТЬ</b>\n\n"
                "Костромская область\n\n"
            )

            if uav_level > 0:

                text += (
                    "Дополнительно действует:\n"
                    f"• {get_status_title()}\n\n"
                )

            text += (
                "⚠️ Следуйте официальным "
                "сообщениям органов власти "
                "и экстренных служб.\n\n"
            )

            if updated_at:
                text += (
                    f"🕒 Обновлено: "
                    f"{format_time(updated_at)}\n"
                )

            if source:
                text += (
                    f"📡 Источник: "
                    f"{source}\n"
                )

            return text

        # ----------------------------------------------------
        # БПЛА
        # ----------------------------------------------------

        if uav_level > 0:

            level_data = UAV_LEVELS[
                uav_level
            ]

            text = (
                f"{level_data['emoji']} "
                f"<b>{level_data['name'].upper()}</b>\n\n"
                "Костромская область\n\n"
            )

            if locations:

                text += (
                    "📍 Районы/города:\n"
                )

                for location in locations[
                    :30
                ]:
                    text += (
                        f"• {location}\n"
                    )

                text += "\n"

            text += (
                "⚠️ Информация является "
                "оперативной справкой. "
                "Ориентируйтесь на официальные "
                "сообщения.\n\n"
            )

            if updated_at:
                text += (
                    f"🕒 Обновлено: "
                    f"{format_time(updated_at)}\n"
                )

            if source:
                text += (
                    f"📡 Источник: "
                    f"{source}\n"
                )

            return text

        # ----------------------------------------------------
        # НЕТ ОПАСНОСТИ
        # ----------------------------------------------------

        text = (
            "🟢 <b>ОПАСНОСТЬ НЕ ОБЪЯВЛЕНА</b>\n\n"
            "Костромская область\n\n"
            "На данный момент активных "
            "состояний тревоги в системе "
            "UAV ALERT нет.\n\n"
        )

        if updated_at:
            text += (
                f"🕒 Последнее обновление: "
                f"{format_time(updated_at)}\n"
            )

        if source:
            text += (
                f"📡 Последний источник: "
                f"{source}\n"
            )

        return text


# ============================================================
# ФОРМАТ ИСТОРИИ
# ============================================================

def format_history(
    limit=10,
):
    with data_lock:

        items = history[
            -limit:
        ]

        items = list(
            reversed(
                items
            )
        )

    if not items:
        return (
            "📜 <b>История</b>\n\n"
            "Событий пока нет."
        )

    text = (
        "📜 <b>Последние события</b>\n\n"
    )

    for index, item in enumerate(
        items,
        1,
    ):

        event_type = item.get(
            "type",
            "",
        )

        if event_type == "uav":
            level = int(
                item.get(
                    "level",
                    0,
                )
            )

            data = UAV_LEVELS.get(
                level,
                {
                    "emoji": "⚠️",
                    "name": "БПЛА",
                },
            )

            emoji = data[
                "emoji"
            ]

        elif event_type == "rocket":
            emoji = "🔴"

        elif (
            event_type
            == "uav_cancel"
        ):
            emoji = "🟢"

        elif (
            event_type
            == "rocket_cancel"
        ):
            emoji = "🟢"

        else:
            emoji = "ℹ️"

        description = item.get(
            "description",
            "Событие",
        )

        timestamp = item.get(
            "timestamp",
            "",
        )

        source = item.get(
            "source",
            "",
        )

        text += (
            f"{emoji} <b>{description}</b>\n"
            f"🕒 {format_time(timestamp)}\n"
        )

        if source:
            text += (
                f"📡 {source}\n"
            )

        text += "\n"

    return text


# ============================================================
# СТАТИСТИКА
# ============================================================

def format_stats():
    with data_lock:

        total = len(
            history
        )

        uav_attention = 0
        uav_threat = 0
        uav_danger = 0
        uav_cancel = 0

        rocket = 0
        rocket_cancel = 0

        for item in history:

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

            if event_type == "uav":

                if level == 1:
                    uav_attention += 1

                elif level == 2:
                    uav_threat += 1

                elif level == 3:
                    uav_danger += 1

            elif (
                event_type
                == "uav_cancel"
            ):
                uav_cancel += 1

            elif event_type == "rocket":
                rocket += 1

            elif (
                event_type
                == "rocket_cancel"
            ):
                rocket_cancel += 1

        subscribers_count = len(
            subscribers
        )

        current_uav = int(
            state.get(
                "uav_level",
                0,
            )
        )

        current_rocket = bool(
            state.get(
                "rocket_active",
                False,
            )
        )

    current_status = (
        get_status_title()
    )

    text = (
        "📊 <b>Статистика UAV ALERT</b>\n\n"
        f"📚 Всего событий: <b>{total}</b>\n\n"
        "🛩 <b>БПЛА:</b>\n"
        f"🟡 Внимание: {uav_attention}\n"
        f"🟠 Угроза: {uav_threat}\n"
        f"🔴 Опасность: {uav_danger}\n"
        f"🟢 Отбой: {uav_cancel}\n\n"
        "🚀 <b>Ракетная опасность:</b>\n"
        f"🔴 Объявлений: {rocket}\n"
        f"🟢 Отбоев: {rocket_cancel}\n\n"
        "👥 <b>Подписчиков:</b> "
        f"{subscribers_count}\n\n"
        "📡 <b>Текущее состояние:</b>\n"
        f"{current_status}\n"
    )

    if current_rocket:
        text += (
            "\n🚨 Ракетная опасность "
            "активна."
        )

    elif current_uav > 0:
        text += (
            "\n🚨 Состояние по БПЛА "
            "активно до официального "
            "отбоя."
        )

    return text


# ============================================================
# ИСТОЧНИКИ
# ============================================================

def format_sources():
    text = (
        "📡 <b>Источники UAV ALERT</b>\n\n"
        "Бот отслеживает открытые "
        "публикации источников.\n\n"
    )

    for source in SOURCES:

        name = source[
            "name"
        ]

        url = source[
            "url"
        ]

        text += (
            f"• <a href=\"{url}\">"
            f"{name}"
            f"</a>\n"
        )

    text += (
        "\n⚠️ UAV ALERT не заменяет "
        "официальные сообщения "
        "экстренных служб и органов власти."
    )

    return text


# ============================================================
# КЛАВИАТУРА
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
                    "📊 Статистика",
                    callback_data="stats",
                ),
                InlineKeyboardButton(
                    "📜 История",
                    callback_data="history",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔔 Подписаться",
                    callback_data="subscribe",
                ),
                InlineKeyboardButton(
                    "🔕 Отписаться",
                    callback_data="unsubscribe",
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
# START
# ============================================================

def start_text():
    return (
        "🛰 <b>UAV ALERT</b>\n"
        "Гражданский информационный сервис\n\n"
        "📍 Регион: <b>Костромская область</b>\n\n"
        "Здесь можно посмотреть текущее "
        "состояние, историю событий и "
        "подписаться на уведомления.\n\n"
        "⚠️ Информация носит справочный "
        "характер. В экстренной ситуации "
        "ориентируйтесь на официальные "
        "сообщения.\n\n"
        f"{get_status_title()}"
    )


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        start_text(),
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
    await update.message.reply_text(
        format_status(),
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
    await update.message.reply_text(
        format_stats(),
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
    await update.message.reply_text(
        format_history(),
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
    await update.message.reply_text(
        format_sources(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# ПОДПИСКА
# ============================================================

def subscribe_user(
    user_id,
):
    global subscribers

    user_id = int(
        user_id
    )

    with data_lock:

        if user_id not in subscribers:

            subscribers.append(
                user_id
            )

            save_json(
                SUBSCRIBERS_FILE,
                subscribers,
            )

            return True

    return False


# ============================================================
# ОТПИСКА
# ============================================================

def unsubscribe_user(
    user_id,
):
    global subscribers

    user_id = int(
        user_id
    )

    with data_lock:

        if user_id in subscribers:

            subscribers.remove(
                user_id
            )

            save_json(
                SUBSCRIBERS_FILE,
                subscribers,
            )

            return True

    return False


# ============================================================
# COMMAND SUBSCRIBE
# ============================================================

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    added = subscribe_user(
        user_id
    )

    if added:

        text = (
            "🔔 <b>Подписка включена</b>\n\n"
            "Вы будете получать уведомления "
            "UAV ALERT по Костромской области."
        )

    else:

        text = (
            "🔔 <b>Подписка уже включена</b>\n\n"
            "Вы уже получаете уведомления."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# COMMAND UNSUBSCRIBE
# ============================================================

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    removed = unsubscribe_user(
        user_id
    )

    if removed:

        text = (
            "🔕 <b>Подписка отключена</b>\n\n"
            "Уведомления UAV ALERT "
            "больше отправляться не будут."
        )

    else:

        text = (
            "🔕 <b>Подписка не найдена</b>\n\n"
            "Вы не были подписаны."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        await query.edit_message_text(
            format_status(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

        return

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if data == "stats":

        await query.edit_message_text(
            format_stats(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    if data == "history":

        await query.edit_message_text(
            format_history(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # SOURCES
    # --------------------------------------------------------

    if data == "sources":

        await query.edit_message_text(
            format_sources(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

        return

    # --------------------------------------------------------
    # SUBSCRIBE
    # --------------------------------------------------------

    if data == "subscribe":

        user_id = query.from_user.id

        added = subscribe_user(
            user_id
        )

        if added:

            text = (
                "🔔 <b>Подписка включена</b>\n\n"
                "Теперь вы будете получать "
                "уведомления UAV ALERT."
            )

        else:

            text = (
                "🔔 <b>Подписка уже включена</b>\n\n"
                "Вы уже получаете уведомления."
            )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # UNSUBSCRIBE
    # --------------------------------------------------------

    if data == "unsubscribe":

        user_id = query.from_user.id

        removed = unsubscribe_user(
            user_id
        )

        if removed:

            text = (
                "🔕 <b>Подписка отключена</b>\n\n"
                "Уведомления отключены."
            )

        else:

            text = (
                "🔕 <b>Подписка не найдена</b>"
            )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

        return


# ============================================================
# ПОЛУЧЕНИЕ TELEGRAM-ПОСТОВ
# ============================================================

def fetch_source(
    source,
):
    url = source[
        "url"
    ]

    name = source[
        "name"
    ]

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/130.0 Safari/537.36"
            )
        }

        response = requests.get(
            url,
            headers=headers,
            timeout=20,
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        result = []

        messages = soup.select(
            ".tgme_widget_message"
        )

        for message in messages:

            post_id = message.get(
                "data-post",
                "",
            )

            if not post_id:
                continue

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            if text_element:

                text = text_element.get_text(
                    "\n",
                    strip=True,
                )

            else:
                text = ""

            if not text:
                continue

            post_link = ""

            link_element = message.select_one(
                ".tgme_widget_message_date"
            )

            if link_element:
                post_link = (
                    link_element.get(
                        "href",
                        "",
                    )
                )

            if not post_link:

                post_link = (
                    "https://t.me/"
                    + post_id
                )

            result.append(
                {
                    "source": name,
                    "source_url": url,
                    "post_id": post_id,
                    "text": clean_text(
                        text
                    ),
                    "link": post_link,
                }
            )

        return result

    except Exception as error:

        logger.error(
            "Ошибка получения %s: %s",
            name,
            error,
        )

        return []


# ============================================================
# PUSH MESSAGE
# ============================================================

def build_push_message(
    event,
    districts,
    municipal_districts,
    cities,
    source_name,
    source_url,
    post_link,
):
    event_type = event.get(
        "type",
        "",
    )

    level = int(
        event.get(
            "level",
            0,
        )
    )

    # --------------------------------------------------------
    # БПЛА
    # --------------------------------------------------------

    if event_type == "uav":

        level_data = UAV_LEVELS.get(
            level,
            {
                "emoji": "⚠️",
                "name": "Событие по БПЛА",
            },
        )

        text = (
            f"{level_data['emoji']} "
            f"<b>{level_data['name'].upper()}!</b>\n\n"
            "📍 <b>Костромская область</b>\n\n"
        )

        locations = unique_list(
            districts
            + municipal_districts
            + cities
        )

        if locations:

            text += (
                "📍 Места:\n"
            )

            for location in locations[
                :20
            ]:

                text += (
                    f"• {location}\n"
                )

            text += "\n"

        text += (
            "ℹ️ Состояние будет сохраняться "
            "до получения сообщения "
            "«Отбой по БПЛА».\n\n"
        )

    # --------------------------------------------------------
    # ОТБОЙ БПЛА
    # --------------------------------------------------------

    elif event_type == "uav_cancel":

        text = (
            "🟢 <b>ОТБОЙ ПО БПЛА</b>\n\n"
            "Костромская область\n\n"
            "Состояние по БПЛА отменено.\n\n"
        )

    # --------------------------------------------------------
    # РАКЕТА
    # --------------------------------------------------------

    elif event_type == "rocket":

        text = (
            "🔴 <b>РАКЕТНАЯ ОПАСНОСТЬ!</b>\n\n"
            "📍 Костромская область\n\n"
            "⚠️ Следуйте официальным "
            "сообщениям органов власти "
            "и экстренных служб.\n\n"
        )

    # --------------------------------------------------------
    # ОТБОЙ РАКЕТЫ
    # --------------------------------------------------------

    elif event_type == "rocket_cancel":

        text = (
            "🟢 <b>ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ</b>\n\n"
            "Костромская область\n\n"
        )

    else:

        text = (
            "ℹ️ <b>UAV ALERT</b>\n\n"
        )

    text += (
        f"📡 Источник: "
        f"<b>{source_name}</b>\n"
    )

    if post_link:

        text += (
            f"🔗 <a href=\"{post_link}\">"
            "Открыть источник"
            "</a>\n"
        )

    text += (
        f"🕒 {now_msk().strftime('%d.%m.%Y %H:%M')} МСК"
    )

    return text


# ============================================================
# PUSH
# ============================================================

async def send_pushes(
    application,
    message,
):
    with data_lock:

        targets = list(
            subscribers
        )

    if not targets:
        logger.info(
            "Нет подписчиков для push."
        )
        return

    sent_count = 0
    failed_count = 0

    for user_id in targets:

        try:

            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            sent_count += 1

            await asyncio.sleep(
                0.05
            )

        except Exception as error:

            failed_count += 1

            logger.warning(
                "Не удалось отправить %s: %s",
                user_id,
                error,
            )

    logger.info(
        "Push завершён: отправлено=%s, ошибок=%s",
        sent_count,
        failed_count,
    )


# ============================================================
# ОБРАБОТКА ПОСТА
# ============================================================

async def process_post(
    application,
    post,
):
    source_name = post.get(
        "source",
        "",
    )

    source_url = post.get(
        "source_url",
        "",
    )

    post_id = post.get(
        "post_id",
        "",
    )

    text = clean_text(
        post.get(
            "text",
            "",
        )
    )

    post_link = post.get(
        "link",
        "",
    )

    if not text:
        return False

    # --------------------------------------------------------
    # Сначала определяем событие
    # --------------------------------------------------------

    event = detect_event(
        text
    )

    if not event:
        return False

    # --------------------------------------------------------
    # Для событий по БПЛА/ракетам
    # проверяем отношение к Костромской области.
    #
    # Отбои БПЛА/ракет рассматриваем отдельно:
    # если соответствующее состояние уже активно,
    # принимаем сообщение даже при отсутствии
    # названия региона в тексте.
    # --------------------------------------------------------

    event_type = event.get(
        "type"
    )

    related = is_kostroma_related(
        text
    )

    if event_type == "uav":

        if not related:
            return False

    elif event_type == "rocket":

        if not related:
            return False

    elif event_type == "uav_cancel":

        with data_lock:
            active = (
                int(
                    state.get(
                        "uav_level",
                        0,
                    )
                )
                > 0
            )

        if not related and not active:
            return False

    elif event_type == "rocket_cancel":

        with data_lock:
            active = bool(
                state.get(
                    "rocket_active",
                    False,
                )
            )

        if not related and not active:
            return False

    # --------------------------------------------------------
    # Дедупликация поста
    # --------------------------------------------------------

    post_fingerprint = make_post_fingerprint(
        source_name,
        post_id,
        text,
    )

    if post_was_processed(
        post_fingerprint
    ):
        return False

    mark_post_processed(
        post_fingerprint
    )

    # --------------------------------------------------------
    # Определяем места
    # --------------------------------------------------------

    (
        districts,
        municipal_districts,
        cities,
        locations,
    ) = detect_locations(
        text
    )

    # --------------------------------------------------------
    # Fingerprint события
    # --------------------------------------------------------

    event_fingerprint = make_event_fingerprint(
        event,
        districts,
        municipal_districts,
        cities,
    )

    if event_was_notified(
        event_fingerprint
    ):
        return False

    # --------------------------------------------------------
    # Применяем
    # --------------------------------------------------------

    result = apply_event(
        event,
        districts,
        municipal_districts,
        cities,
        source_name,
        source_url,
        post_id,
    )

    if not result.get(
        "changed",
        False,
    ):
        return False

    # --------------------------------------------------------
    # История
    # --------------------------------------------------------

    add_history(
        event,
        districts,
        municipal_districts,
        cities,
        source_name,
        source_url,
        post_id,
        text,
    )

    # --------------------------------------------------------
    # Запоминаем уведомление
    # --------------------------------------------------------

    mark_event_notified(
        event_fingerprint
    )

    # --------------------------------------------------------
    # Push
    # --------------------------------------------------------

    if result.get(
        "notify",
        False,
    ):

        push_message = build_push_message(
            event,
            districts,
            municipal_districts,
            cities,
            source_name,
            source_url,
            post_link,
        )

        await send_pushes(
            application,
            push_message,
        )

    logger.info(
        "Обработано событие: %s | %s",
        source_name,
        result.get(
            "message",
            "",
        ),
    )

    return True


# ============================================================
# МОНИТОРИНГ
# ============================================================

async def monitor_loop(
    application,
):
    logger.info(
        "Мониторинг источников запущен."
    )

    await asyncio.sleep(
        5
    )

    while True:

        try:

            cleanup_dedup()

            for source in SOURCES:

                try:

                    posts = fetch_source(
                        source
                    )

                    if not posts:
                        continue

                    # ------------------------------------------------
                    # Обрабатываем от старых к новым
                    # ------------------------------------------------

                    for post in posts:

                        try:

                            await process_post(
                                application,
                                post,
                            )

                        except Exception as error:

                            logger.exception(
                                "Ошибка обработки поста: %s",
                                error,
                            )

                except Exception as error:

                    logger.exception(
                        "Ошибка источника %s: %s",
                        source.get(
                            "name",
                            "",
                        ),
                        error,
                    )

            await asyncio.sleep(
                CHECK_INTERVAL
            )

        except asyncio.CancelledError:

            logger.info(
                "Мониторинг остановлен."
            )

            break

        except Exception as error:

            logger.exception(
                "Ошибка monitoring loop: %s",
                error,
            )

            await asyncio.sleep(
                CHECK_INTERVAL
            )


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(
    update,
):
    if not update.effective_user:
        return False

    return (
        update.effective_user.id
        == ADMIN_ID
    )


# ============================================================
# ADMIN TEST
# ============================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(
        update
    ):

        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )

        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟡 Внимание",
                    callback_data="admin_test_1",
                ),
                InlineKeyboardButton(
                    "🟠 Угроза",
                    callback_data="admin_test_2",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔴 Опасность",
                    callback_data="admin_test_3",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🟢 Отбой БПЛА",
                    callback_data="admin_cancel_uav",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🚀 Ракетная",
                    callback_data="admin_rocket",
                ),
                InlineKeyboardButton(
                    "🟢 Отбой ракеты",
                    callback_data="admin_cancel_rocket",
                ),
            ],
        ]
    )

    await update.message.reply_text(
        "🛠 <b>Админ-тест UAV ALERT</b>\n\n"
        "Выбери тестовое событие:",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ============================================================
# ADMIN COMMAND
# ============================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(
        update
    ):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return

    with data_lock:

        subscriber_count = len(
            subscribers
        )

        history_count = len(
            history
        )

        source_count = len(
            SOURCES
        )

    text = (
        "🛠 <b>UAV ALERT ADMIN</b>\n\n"
        f"👥 Подписчиков: {subscriber_count}\n"
        f"📜 Событий: {history_count}\n"
        f"📡 Источников: {source_count}\n"
        f"⏱ Интервал: {CHECK_INTERVAL} сек.\n"
        f"🧹 Дедупликация: {DEDUP_HOURS} ч.\n\n"
        f"🆔 ADMIN_ID: <code>{ADMIN_ID}</code>\n"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# ADMIN TEST CALLBACK
# ============================================================

async def admin_test_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    if query.from_user.id != ADMIN_ID:

        await query.answer(
            "Нет доступа.",
            show_alert=True,
        )

        return

    data = query.data

    # --------------------------------------------------------
    # Тестовые события не записываем в реальную историю.
    # Они используются только для проверки push.
    # --------------------------------------------------------

    if data == "admin_test_1":

        message = (
            "🟡 <b>ВНИМАНИЕ ПО БПЛА!</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    elif data == "admin_test_2":

        message = (
            "🟠 <b>УГРОЗА ПО БПЛА!</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    elif data == "admin_test_3":

        message = (
            "🔴 <b>ОПАСНОСТЬ ПО БПЛА!</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    elif data == "admin_cancel_uav":

        message = (
            "🟢 <b>ОТБОЙ ПО БПЛА</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    elif data == "admin_rocket":

        message = (
            "🔴 <b>РАКЕТНАЯ ОПАСНОСТЬ!</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    elif data == "admin_cancel_rocket":

        message = (
            "🟢 <b>ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ</b>\n\n"
            "📍 Костромская область\n\n"
            "🧪 Тестовое уведомление UAV ALERT."
        )

    else:

        return

    await send_pushes(
        context.application,
        message,
    )

    await query.edit_message_text(
        "✅ <b>Тест отправлен.</b>\n\n"
        "Проверь сообщения у подписчиков.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# FLASK
# Render должен видеть открытый порт.
# ============================================================

flask_app = Flask(
    __name__
)


@flask_app.route(
    "/",
    methods=[
        "GET",
    ],
)
def health():
    return (
        "UAV ALERT is running",
        200,
    )


@flask_app.route(
    "/health",
    methods=[
        "GET",
    ],
)
def health_check():
    return {
        "status": "ok",
        "service": "UAV ALERT",
        "region": "Костромская область",
        "time": now_iso(),
    }, 200


def run_flask():
    logger.info(
        "Flask запускается на порту %s",
        PORT,
    )

    flask_app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
        threaded=True,
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):
    logger.info(
        "Настройка команд Telegram..."
    )

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Главное меню",
            ),
            BotCommand(
                "status",
                "Текущий статус",
            ),
            BotCommand(
                "stats",
                "Статистика",
            ),
            BotCommand(
                "history",
                "История",
            ),
            BotCommand(
                "sources",
                "Источники",
            ),
            BotCommand(
                "subscribe",
                "Подписаться",
            ),
            BotCommand(
                "unsubscribe",
                "Отписаться",
            ),
        ]
    )

    logger.info(
        "Команды Telegram установлены."
    )

    # --------------------------------------------------------
    # Запускаем мониторинг
    # --------------------------------------------------------

    application.create_task(
        monitor_loop(
            application
        )
    )

    logger.info(
        "Monitoring task создан."
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context,
):
    logger.exception(
        "Ошибка Telegram:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        logger.error(
            "BOT_TOKEN не установлен!"
        )

        raise RuntimeError(
            "BOT_TOKEN environment variable is required."
        )

    logger.info(
        "=========================================="
    )

    logger.info(
        "UAV ALERT запускается..."
    )

    logger.info(
        "Регион: Костромская область"
    )

    logger.info(
        "Порт: %s",
        PORT,
    )

    logger.info(
        "Интервал мониторинга: %s сек.",
        CHECK_INTERVAL,
    )

    logger.info(
        "Источников: %s",
        len(SOURCES),
    )

    logger.info(
        "Подписчиков: %s",
        len(subscribers),
    )

    logger.info(
        "Текущее состояние: %s",
        state.get(
            "title",
            "",
        ),
    )

    logger.info(
        "=========================================="
    )

    # --------------------------------------------------------
    # Flask
    # --------------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
        name="FlaskThread",
    )

    flask_thread.start()

    # --------------------------------------------------------
    # Telegram application
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # Commands
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
            "stats",
            stats_command,
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
    # Callback buttons
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            admin_test_callback,
            pattern=r"^admin_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
            pattern=(
                r"^(status|stats|history|"
                r"sources|subscribe|unsubscribe)$"
            ),
        )
    )

    # --------------------------------------------------------
    # Errors
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # --------------------------------------------------------
    # Polling
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
