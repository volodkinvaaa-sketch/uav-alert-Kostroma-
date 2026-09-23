import os
import re
import json
import time
import hashlib
import threading
import logging
from datetime import datetime, timezone

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
# UAV ALERT — Telegram Bot
# Костромская область
#
# Основные возможности:
# - Внимание по БПЛА
# - Угроза по БПЛА
# - Опасность по БПЛА
# - Ракетная опасность
# - Отбой по БПЛА
# - Несколько районов одновременно
# - Районы и города разделены
# - История событий
# - Статистика
# - Источники
# - Подписчики
# - Автоматический мониторинг Telegram-источников
# - Жёсткая защита от повторных пушей
# ============================================================


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))

PORT = int(os.getenv("PORT", "10000"))

CHANNEL = os.getenv("CHANNEL", "@RADAR_Kostroma")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

# Сколько часов хранить отпечатки уведомлений.
# Это защита от повторного спама.
DEDUP_HOURS = int(os.getenv("DEDUP_HOURS", "24"))


# ============================================================
# ФАЙЛЫ
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
SENT_POSTS_FILE = os.path.join(BASE_DIR, "sent_posts.json")
NOTIFICATION_EVENTS_FILE = os.path.join(
    BASE_DIR,
    "notification_events.json"
)


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("UAV_ALERT")


# ============================================================
# ИСТОЧНИКИ
# ============================================================

SOURCES = {
    "locator": {
        "name": "📡 Локатор России",
        "url": "https://t.me/s/locatorru",
    },

    "monitoring": {
        "name": "🛰️ Мониторинг БПЛА",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
    },

    "radar": {
        "name": "📡 Радар Россия",
        "url": "https://t.me/s/radarrussiia",
    },

    "bpla": {
        "name": "📢 БПЛА Россия",
        "url": "https://t.me/s/bplarussiaru",
    },
}


# ============================================================
# РАЙОНЫ КОСТРОМСКОЙ ОБЛАСТИ
#
# ВАЖНО:
# Названия районов используются отдельно от городов.
# Поэтому слово "Макарьев" не означает автоматически
# "Макарьевский район".
# ============================================================

DISTRICTS = {
    "антроповский": "Антроповский район",
    "буйский": "Буйский район",
    "вохомский": "Вохомский район",
    "галичский": "Галичский район",
    "кадийский": "Кадыйский район",
    "кологривский": "Кологривский район",
    "макарьевский": "Макарьевский район",
    "мантуровский": "Мантуровский район",
    "межевский": "Межевский район",
    "нейский": "Нейский район",
    "октябрьский": "Октябрьский район",
    "островский": "Островский район",
    "павинский": "Павинский район",
    "парфеньевский": "Парфеньевский район",
    "поназыревский": "Поназыревский район",
    "пыщугский": "Пыщугский район",
    "солигаличский": "Солигаличский район",
    "сусанинский": "Сусанинский район",
    "чухломский": "Чухломский район",
    "шарьинский": "Шарьинский район",
}


# ============================================================
# ГОРОДА
#
# Они используются только для определения места в тексте.
# Они НЕ добавляются в список районов под опасностью.
# ============================================================

CITIES = [
    "кострома",
    "буй",
    "волгореченск",
    "галич",
    "шарья",
    "мантурово",
    "нерехта",
    "чухлома",
    "макарьев",
    "солигалич",
    "кологрив",
    "нея",
]


# ============================================================
# КЛЮЧЕВЫЕ СЛОВА РЕГИОНА
# ============================================================

REGION_WORDS = [
    "костромская область",
    "костромской области",
    "костромской обл",
    "костромская обл",
    "костромская",
]


# ============================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ============================================================

DEFAULT_STATE = {
    "status": "green",

    "title": "🟢 Опасность не объявлена",

    # 0 = нет
    # 1 = внимание
    # 2 = угроза
    # 3 = опасность
    "uav_level": 0,

    "rocket_active": False,

    "location": "Костромская область",

    # Активные районы
    "districts": [],

    # Найденные города для информационного отображения
    "cities": [],

    "source": "",
    "source_url": "",

    "updated_at": "",

    "event_post_id": "",
    "event_source_id": "",
}


state_lock = threading.Lock()


# ============================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ JSON
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception as e:
        logger.error(
            "Ошибка чтения %s: %s",
            filename,
            e,
        )

        return default


def save_json(filename, data):
    try:
        tmp = filename + ".tmp"

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

        os.replace(tmp, filename)

    except Exception as e:
        logger.error(
            "Ошибка сохранения %s: %s",
            filename,
            e,
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
    {},
)

notification_events = load_json(
    NOTIFICATION_EVENTS_FILE,
    {},
)


# ============================================================
# НОРМАЛИЗАЦИЯ СОСТОЯНИЯ
# ============================================================

def normalize_state():
    global state

    if not isinstance(state, dict):
        state = DEFAULT_STATE.copy()

    for key, value in DEFAULT_STATE.items():
        if key not in state:
            state[key] = value

    if not isinstance(state.get("districts"), list):
        state["districts"] = []

    if not isinstance(state.get("cities"), list):
        state["cities"] = []


normalize_state()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def clean_text(text):
    if not text:
        return ""

    text = text.replace("\xa0", " ")

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_text(text):
    text = clean_text(text).lower()

    # Убираем URL
    text = re.sub(
        r"https?://\S+",
        "",
        text,
    )

    # Убираем лишние символы
    text = re.sub(
        r"[^\w\sа-яё-]",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def make_hash(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def unique_list(items):
    result = []

    for item in items:
        if item not in result:
            result.append(item)

    return result


# ============================================================
# ОПРЕДЕЛЕНИЕ РАЙОНОВ
# ============================================================

def detect_districts(text):
    """
    Определяет именно районы.

    Например:
    "Нейский район" -> Нейский район

    "Макарьев" -> НЕ Макарьевский район

    "Макарьевский район" -> Макарьевский район
    """

    text_lower = text.lower()

    found = []

    for key, district_name in DISTRICTS.items():

        variants = [
            f"{key} район",
            f"{key} р-н",
            key,
        ]

        # Для неоднозначных названий требуем
        # именно форму с "район".
        if key == "макарьевский":
            variants = [
                "макарьевский район",
                "макарьевский р-н",
            ]

        if key == "буйский":
            variants = [
                "буйский район",
                "буйский р-н",
            ]

        if key == "галичский":
            variants = [
                "галичский район",
                "галичский р-н",
            ]

        for variant in variants:
            if variant in text_lower:
                found.append(district_name)
                break

    return unique_list(found)


# ============================================================
# ОПРЕДЕЛЕНИЕ ГОРОДОВ
# ============================================================

def detect_cities(text):
    text_lower = text.lower()

    found = []

    for city in CITIES:

        pattern = (
            r"(?<![а-яё])"
            + re.escape(city)
            + r"(?![а-яё])"
        )

        if re.search(
            pattern,
            text_lower,
            flags=re.IGNORECASE,
        ):
            found.append(city.capitalize())

    return unique_list(found)


# ============================================================
# УРОВНИ БПЛА
# ============================================================

UAV_LEVELS = {
    0: {
        "name": "Нет опасности",
        "title": "🟢 Опасность не объявлена",
        "status": "green",
    },

    1: {
        "name": "Внимание по БПЛА",
        "title": "🟡 Внимание по БПЛА",
        "status": "yellow",
    },

    2: {
        "name": "Угроза по БПЛА",
        "title": "🟠 Угроза по БПЛА",
        "status": "orange",
    },

    3: {
        "name": "Опасность по БПЛА",
        "title": "🔴 Опасность по БПЛА",
        "status": "red",
    },
}


# ============================================================
# ОПРЕДЕЛЕНИЕ ТИПА СОБЫТИЯ
# ============================================================

def detect_event(text):
    """
    Возвращает:
        None
        ("uav", level)
        ("uav_cancel", 0)
        ("rocket", 1)
        ("rocket_cancel", 0)
    """

    normalized = normalize_text(text)

    # --------------------------------------------------------
    # ОТБОЙ
    # --------------------------------------------------------

    if (
        "отбой по бпла" in normalized
        or "отбой бпла" in normalized
        or "угроза бпла отменена" in normalized
        or "опасность бпла отменена" in normalized
        or "угроза атаки бпла снята" in normalized
        or "опасность атаки бпла снята" in normalized
    ):
        return ("uav_cancel", 0)


    # --------------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------

    if (
        "отбой ракетной опасности" in normalized
        or "ракетная опасность отменена" in normalized
        or "ракетная опасность снята" in normalized
    ):
        return ("rocket_cancel", 0)


    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # --------------------------------------------------------

    if (
        "ракетная опасность" in normalized
        or "опасность ракетного удара" in normalized
    ):
        return ("rocket", 1)


    # --------------------------------------------------------
    # БПЛА — ОПАСНОСТЬ
    # --------------------------------------------------------

    if (
        "опасность по бпла" in normalized
        or "опасность бпла" in normalized
        or "высокая опасность бпла" in normalized
    ):
        return ("uav", 3)


    # --------------------------------------------------------
    # БПЛА — УГРОЗА
    # --------------------------------------------------------

    if (
        "угроза по бпла" in normalized
        or "угроза бпла" in normalized
        or "угроза атаки бпла" in normalized
    ):
        return ("uav", 2)


    # --------------------------------------------------------
    # БПЛА — ВНИМАНИЕ
    # --------------------------------------------------------

    if (
        "внимание по бпла" in normalized
        or "внимание бпла" in normalized
        or "беспилотная опасность" in normalized
        or "опасность беспилотников" in normalized
    ):
        return ("uav", 1)


    return None


# ============================================================
# ПРОВЕРКА ОТНОШЕНИЯ К КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

def is_kostroma_related(text):
    normalized = normalize_text(text)

    for word in REGION_WORDS:
        if word in normalized:
            return True

    districts = detect_districts(text)

    if districts:
        return True

    cities = detect_cities(text)

    if cities:
        return True

    return False


# ============================================================
# ФОРМИРОВАНИЕ ОПИСАНИЯ СОБЫТИЯ
# ============================================================

def build_event_description(text):
    districts = detect_districts(text)
    cities = detect_cities(text)

    return {
        "districts": districts,
        "cities": cities,
    }


# ============================================================
# СОЗДАНИЕ ОТПЕЧАТКА СОБЫТИЯ
#
# ЭТО ГЛАВНАЯ ЗАЩИТА ОТ ПЯТИ ОДИНАКОВЫХ ПУШЕЙ.
#
# Если пять источников напишут одно и то же событие,
# бот сможет распознать его как одно событие.
# ============================================================

def make_event_fingerprint(
    event_type,
    level,
    districts,
    rocket_active,
    text="",
):
    districts_sorted = sorted(
        set(districts or [])
    )

    # Для UAV главное:
    # уровень + районы.
    #
    # Текст намеренно не является обязательной частью
    # отпечатка, потому что источники могут написать
    # одно и то же разными словами.

    if event_type == "uav":
        raw = (
            "uav|"
            + str(level)
            + "|"
            + "|".join(districts_sorted)
        )

    elif event_type == "rocket":
        raw = "rocket|1"

    elif event_type == "uav_cancel":
        raw = "uav_cancel"

    elif event_type == "rocket_cancel":
        raw = "rocket_cancel"

    else:
        raw = (
            str(event_type)
            + "|"
            + str(level)
            + "|"
            + "|".join(districts_sorted)
            + "|"
            + normalize_text(text)
        )

    return make_hash(raw)


# ============================================================
# ОТПЕЧАТОК КОНКРЕТНОГО ПОСТА
# ============================================================

def make_post_fingerprint(
    source_key,
    post_id,
    text,
):
    if post_id:
        raw = (
            source_key
            + ":"
            + str(post_id)
        )

    else:
        raw = (
            source_key
            + "|"
            + normalize_text(text)
        )

    return make_hash(raw)


# ============================================================
# ОЧИСТКА СТАРЫХ ДЕДУП-ЗАПИСЕЙ
# ============================================================

def cleanup_dedup():
    global sent_posts
    global notification_events

    current = time.time()

    max_age = DEDUP_HOURS * 60 * 60

    # --------------------------------------------------------
    # Посты
    # --------------------------------------------------------

    new_sent_posts = {}

    for key, value in sent_posts.items():

        try:
            timestamp = float(
                value.get("timestamp", 0)
            )

            if current - timestamp <= max_age:
                new_sent_posts[key] = value

        except Exception:
            pass

    sent_posts = new_sent_posts


    # --------------------------------------------------------
    # События
    # --------------------------------------------------------

    new_events = {}

    for key, value in notification_events.items():

        try:
            timestamp = float(
                value.get("timestamp", 0)
            )

            if current - timestamp <= max_age:
                new_events[key] = value

        except Exception:
            pass

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
# ПРОВЕРКА: ОТПРАВЛЯЛОСЬ ЛИ ЭТОТ ПОСТ
# ============================================================

def post_was_processed(post_fingerprint):
    return post_fingerprint in sent_posts


def mark_post_processed(
    post_fingerprint,
    source_key,
    post_id,
):
    sent_posts[post_fingerprint] = {
        "timestamp": time.time(),
        "source": source_key,
        "post_id": post_id,
    }

    save_json(
        SENT_POSTS_FILE,
        sent_posts,
    )


# ============================================================
# ПРОВЕРКА: БЫЛО ЛИ УЖЕ УВЕДОМЛЕНИЕ ОБ ЭТОМ СОБЫТИИ
# ============================================================

def event_was_notified(event_fingerprint):
    data = notification_events.get(
        event_fingerprint
    )

    if not data:
        return False

    try:
        timestamp = float(
            data.get("timestamp", 0)
        )

        if (
            time.time() - timestamp
            <= DEDUP_HOURS * 60 * 60
        ):
            return True

    except Exception:
        pass

    return False


# ============================================================
# СОХРАНЕНИЕ ФАКТА УВЕДОМЛЕНИЯ
# ============================================================

def mark_event_notified(
    event_fingerprint,
    event_type,
    level,
    districts,
):
    notification_events[
        event_fingerprint
    ] = {
        "timestamp": time.time(),
        "event_type": event_type,
        "level": level,
        "districts": districts,
    }

    save_json(
        NOTIFICATION_EVENTS_FILE,
        notification_events,
    )


# ============================================================
# ПОЛУЧЕНИЕ ТЕКУЩЕГО СТАТУСА
# ============================================================

def get_status_title():
    if state.get("rocket_active"):
        if state.get("uav_level", 0) > 0:
            return (
                "🚨 Ракетная опасность\n"
                + UAV_LEVELS[
                    state["uav_level"]
                ]["title"]
            )

        return "🚨 Ракетная опасность"

    return UAV_LEVELS[
        state.get("uav_level", 0)
    ]["title"]


# ============================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# ============================================================

def apply_event(
    event_type,
    level=0,
    text="",
    source_name="",
    source_url="",
    post_id="",
):
    """
    Возвращает:
        changed
        should_notify
        event_fingerprint
    """

    global state

    description = build_event_description(
        text
    )

    found_districts = description[
        "districts"
    ]

    found_cities = description[
        "cities"
    ]

    with state_lock:

        old_level = state.get(
            "uav_level",
            0,
        )

        old_rocket = state.get(
            "rocket_active",
            False,
        )

        old_districts = sorted(
            set(
                state.get(
                    "districts",
                    [],
                )
            )
        )

        # ----------------------------------------------------
        # ОТБОЙ БПЛА
        # ----------------------------------------------------

        if event_type == "uav_cancel":

            changed = (
                old_level != 0
                or len(old_districts) > 0
            )

            state["uav_level"] = 0
            state["districts"] = []
            state["cities"] = []

            state["status"] = "green"

            state["title"] = (
                "🟢 Опасность не объявлена"
            )

            state["source"] = source_name
            state["source_url"] = source_url
            state["updated_at"] = now_iso()
            state["event_post_id"] = str(
                post_id
            )

            save_json(
                STATE_FILE,
                state,
            )

            fingerprint = make_event_fingerprint(
                "uav_cancel",
                0,
                [],
                False,
                text,
            )

            return (
                changed,
                True,
                fingerprint,
            )


        # ----------------------------------------------------
        # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
        # ----------------------------------------------------

        if event_type == "rocket_cancel":

            changed = old_rocket

            state["rocket_active"] = False

            state["status"] = UAV_LEVELS[
                old_level
            ]["status"]

            state["title"] = get_status_title()

            state["source"] = source_name
            state["source_url"] = source_url
            state["updated_at"] = now_iso()
            state["event_post_id"] = str(
                post_id
            )

            save_json(
                STATE_FILE,
                state,
            )

            fingerprint = make_event_fingerprint(
                "rocket_cancel",
                0,
                [],
                False,
                text,
            )

            return (
                changed,
                True,
                fingerprint,
            )


        # ----------------------------------------------------
        # РАКЕТНАЯ ОПАСНОСТЬ
        # ----------------------------------------------------

        if event_type == "rocket":

            state["rocket_active"] = True

            state["source"] = source_name
            state["source_url"] = source_url
            state["updated_at"] = now_iso()
            state["event_post_id"] = str(
                post_id
            )

            state["status"] = "red"
            state["title"] = (
                "🚨 Ракетная опасность"
            )

            save_json(
                STATE_FILE,
                state,
            )

            fingerprint = make_event_fingerprint(
                "rocket",
                1,
                [],
                True,
                text,
            )

            changed = (
                not old_rocket
            )

            return (
                changed,
                True,
                fingerprint,
            )


        # ----------------------------------------------------
        # БПЛА
        # ----------------------------------------------------

        if event_type == "uav":

            # Не понижаем уровень из-за старого сообщения.
            new_level = max(
                old_level,
                level,
            )

            # Добавляем новые районы,
            # а не заменяем старые.
            merged_districts = unique_list(
                old_districts
                + found_districts
            )

            merged_districts = sorted(
                merged_districts
            )

            # Города только для информации.
            merged_cities = unique_list(
                state.get(
                    "cities",
                    [],
                )
                + found_cities
            )

            level_changed = (
                new_level != old_level
            )

            districts_changed = (
                merged_districts
                != old_districts
            )

            changed = (
                level_changed
                or districts_changed
            )

            state["uav_level"] = new_level

            state["districts"] = (
                merged_districts
            )

            state["cities"] = (
                merged_cities
            )

            state["status"] = UAV_LEVELS[
                new_level
            ]["status"]

            state["source"] = source_name
            state["source_url"] = source_url
            state["updated_at"] = now_iso()
            state["event_post_id"] = str(
                post_id
            )

            state["title"] = (
                get_status_title()
            )

            save_json(
                STATE_FILE,
                state,
            )

            fingerprint = make_event_fingerprint(
                "uav",
                new_level,
                merged_districts,
                state.get(
                    "rocket_active",
                    False,
                ),
                text,
            )

            # ВАЖНО:
            #
            # Если уровень или районы изменились —
            # новое уведомление разрешается.
            #
            # Если пришёл тот же самый статус повторно —
            # fingerprint будет тем же самым.
            #
            # Это защищает от 5 одинаковых пушей.

            return (
                changed,
                True,
                fingerprint,
            )


    return (
        False,
        False,
        "",
    )


# ============================================================
# ДОБАВЛЕНИЕ В ИСТОРИЮ
# ============================================================

def add_history(
    event_type,
    level,
    text,
    source_name,
    source_url,
    post_id,
):
    item = {
        "timestamp": now_iso(),
        "event_type": event_type,
        "level": level,
        "text": clean_text(text),
        "source": source_name,
        "source_url": source_url,
        "post_id": str(post_id),
        "districts": detect_districts(text),
        "cities": detect_cities(text),
    }

    history.insert(
        0,
        item,
    )

    # Храним последние 200 событий.
    del history[200:]

    save_json(
        HISTORY_FILE,
        history,
    )


# ============================================================
# ФОРМАТИРОВАНИЕ ТЕКУЩЕГО СТАТУСА
# ============================================================

def format_status():
    with state_lock:

        title = get_status_title()

        lines = [
            "🛰️ <b>UAV ALERT</b>",
            "",
            title,
            "",
            "📍 <b>Костромская область</b>",
        ]

        districts = state.get(
            "districts",
            [],
        )

        if districts:
            lines.append("")
            lines.append(
                "📌 <b>Районы:</b>"
            )

            for district in districts:
                lines.append(
                    f"• {district}"
                )

        cities = state.get(
            "cities",
            [],
        )

        if cities:
            lines.append("")
            lines.append(
                "🏙 <b>Упомянутые города:</b>"
            )

            for city in cities[:10]:
                lines.append(
                    f"• {city}"
                )

        if state.get("source"):
            lines.append("")
            lines.append(
                "📡 <b>Источник:</b> "
                + state["source"]
            )

        if state.get("updated_at"):
            lines.append(
                "🕐 <b>Обновлено:</b> "
                + state["updated_at"]
            )

        return "\n".join(lines)


# ============================================================
# ФОРМАТИРОВАНИЕ ИСТОРИИ
# ============================================================

def format_history(limit=10):
    if not history:
        return (
            "📜 <b>История</b>\n\n"
            "Пока событий нет."
        )

    lines = [
        "📜 <b>Последние события</b>",
        "",
    ]

    for item in history[:limit]:

        event_type = item.get(
            "event_type",
            "",
        )

        level = item.get(
            "level",
            0,
        )

        if event_type == "rocket":
            title = "🚨 Ракетная опасность"

        elif event_type == "rocket_cancel":
            title = (
                "🟢 Отбой ракетной опасности"
            )

        elif event_type == "uav_cancel":
            title = (
                "🟢 Отбой по БПЛА"
            )

        else:
            title = UAV_LEVELS.get(
                level,
                UAV_LEVELS[0],
            )["title"]

        districts = item.get(
            "districts",
            [],
        )

        lines.append(
            f"{title}"
        )

        if districts:
            lines.append(
                "📌 "
                + ", ".join(districts)
            )

        source = item.get(
            "source",
            "",
        )

        if source:
            lines.append(
                f"📡 {source}"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# ФОРМАТИРОВАНИЕ СТАТИСТИКИ
# ============================================================

def format_stats():
    total = len(history)

    uav_attention = 0
    uav_threat = 0
    uav_danger = 0
    uav_cancel = 0
    rocket = 0
    rocket_cancel = 0

    for item in history:

        event_type = item.get(
            "event_type",
            "",
        )

        level = item.get(
            "level",
            0,
        )

        if event_type == "uav":

            if level == 1:
                uav_attention += 1

            elif level == 2:
                uav_threat += 1

            elif level == 3:
                uav_danger += 1

        elif event_type == "uav_cancel":
            uav_cancel += 1

        elif event_type == "rocket":
            rocket += 1

        elif event_type == "rocket_cancel":
            rocket_cancel += 1

    return (
        "📊 <b>Статистика UAV ALERT</b>\n\n"
        f"📚 Всего событий: <b>{total}</b>\n\n"
        f"🟡 Внимание по БПЛА: <b>{uav_attention}</b>\n"
        f"🟠 Угроза по БПЛА: <b>{uav_threat}</b>\n"
        f"🔴 Опасность по БПЛА: <b>{uav_danger}</b>\n"
        f"🟢 Отбоев БПЛА: <b>{uav_cancel}</b>\n\n"
        f"🚨 Ракетная опасность: <b>{rocket}</b>\n"
        f"🟢 Отбоев ракетной опасности: <b>{rocket_cancel}</b>\n\n"
        f"👥 Подписчиков: <b>{len(subscribers)}</b>"
    )


# ============================================================
# ФОРМАТИРОВАНИЕ ИСТОЧНИКОВ
# ============================================================

def format_sources():
    lines = [
        "📡 <b>Источники UAV ALERT</b>",
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
        "⚠️ Информация носит "
        "информационный характер. "
        "При официальном объявлении тревоги "
        "ориентируйтесь на сообщения властей "
        "и экстренных служб."
    )

    return "\n".join(lines)


# ============================================================
# КЛАВИАТУРА
# ============================================================

def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "🛰️ Статус",
                callback_data="status",
            ),
        ],

        [
            InlineKeyboardButton(
                "📍 Районы",
                callback_data="locations",
            ),
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="stats",
            ),
        ],

        [
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
    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# ============================================================
# СООБЩЕНИЕ START
# ============================================================

def start_text():
    return (
        "🛰️ <b>UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "мониторинга сообщений об угрозах.\n\n"
        "📍 Регион: <b>Костромская область</b>\n\n"
        "🟢 Опасность не объявлена\n\n"
        "Используйте кнопки ниже для просмотра "
        "текущего статуса, районов, истории "
        "и источников."
    )


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if user:
        user_id = user.id

        if user_id not in subscribers:
            subscribers.append(
                user_id
            )

            save_json(
                SUBSCRIBERS_FILE,
                subscribers,
            )

    await update.message.reply_text(
        start_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


# ============================================================
# /STATUS
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
# /STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        format_stats(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# /HISTORY
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
# /SOURCES
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
# /SUBSCRIBE
# ============================================================

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id not in subscribers:

        subscribers.append(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers,
        )

        text = (
            "🔔 <b>Вы подписались на UAV ALERT</b>\n\n"
            "Регион: Костромская область.\n\n"
            "Вы будете получать уведомления "
            "о новых событиях."
        )

    else:

        text = (
            "🔔 Вы уже подписаны "
            "на уведомления UAV ALERT."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# /UNSUBSCRIBE
# ============================================================

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    if user_id in subscribers:

        subscribers.remove(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers,
        )

    await update.message.reply_text(
        "🔕 Вы отписались от уведомлений UAV ALERT.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# CALLBACK-КНОПКИ
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
    # LOCATIONS
    # --------------------------------------------------------

    if data == "locations":

        districts = state.get(
            "districts",
            [],
        )

        cities = state.get(
            "cities",
            [],
        )

        if districts:

            text = (
                "📍 <b>Районы под текущим "
                "статусом БПЛА:</b>\n\n"
                + "\n".join(
                    f"• {x}"
                    for x in districts
                )
            )

        else:

            text = (
                "📍 <b>Активные районы</b>\n\n"
                "Сейчас районы под угрозой "
                "не зафиксированы."
            )

        if cities:

            text += (
                "\n\n🏙 <b>Города, упомянутые "
                "в источниках:</b>\n"
                + "\n".join(
                    f"• {x}"
                    for x in cities[:10]
                )
            )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
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
            disable_web_page_preview=True,
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

        if user_id not in subscribers:

            subscribers.append(
                user_id
            )

            save_json(
                SUBSCRIBERS_FILE,
                subscribers,
            )

            text = (
                "🔔 <b>Подписка включена.</b>\n\n"
                "Теперь вы будете получать "
                "уведомления UAV ALERT."
            )

        else:

            text = (
                "🔔 Вы уже подписаны "
                "на уведомления."
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

        if user_id in subscribers:

            subscribers.remove(
                user_id
            )

            save_json(
                SUBSCRIBERS_FILE,
                subscribers,
            )

        await query.edit_message_text(
            "🔕 Подписка отключена.",
            reply_markup=main_keyboard(),
        )

        return


# ============================================================
# ПОЛУЧЕНИЕ ПОСТОВ ИЗ TELEGRAM WEB
# ============================================================

def fetch_source(source_key, source_data):
    try:

        response = requests.get(
            source_data["url"],
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/140 Safari/537.36"
                )
            },
        )

        if response.status_code != 200:

            logger.warning(
                "Источник %s вернул HTTP %s",
                source_key,
                response.status_code,
            )

            return []


        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        posts = []

        for message in soup.select(
            ".tgme_widget_message"
        ):

            # ------------------------------------------------
            # POST ID
            # ------------------------------------------------

            data_post = message.get(
                "data-post",
                "",
            )

            post_id = ""

            if data_post:

                post_id = (
                    data_post
                    .split("/")[-1]
                )


            # ------------------------------------------------
            # TEXT
            # ------------------------------------------------

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            if text_element:

                text = text_element.get_text(
                    "\n",
                    strip=True,
                )

            else:

                text = message.get_text(
                    "\n",
                    strip=True,
                )


            text = clean_text(text)


            if not text:
                continue


            # ------------------------------------------------
            # LINK
            # ------------------------------------------------

            link = ""

            if data_post:

                parts = data_post.split("/")

                if len(parts) >= 2:

                    channel_name = parts[0]

                    message_number = parts[-1]

                    link = (
                        "https://t.me/"
                        + channel_name
                        + "/"
                        + message_number
                    )


            posts.append(
                {
                    "source_key": source_key,
                    "source_name": source_data["name"],
                    "source_url": source_data["url"],
                    "post_id": post_id,
                    "link": link,
                    "text": text,
                }
            )


        return posts

    except Exception as e:

        logger.error(
            "Ошибка загрузки %s: %s",
            source_key,
            e,
        )

        return []


# ============================================================
# ОБРАБОТКА ОДНОГО ПОСТА
# ============================================================

async def process_post(
    bot,
    post,
):
    text = post["text"]

    source_key = post[
        "source_key"
    ]

    source_name = post[
        "source_name"
    ]

    source_url = post[
        "source_url"
    ]

    post_id = post[
        "post_id"
    ]

    # --------------------------------------------------------
    # Определяем событие
    # --------------------------------------------------------

    detected = detect_event(
        text
    )

    if not detected:
        return


    event_type, level = detected


    # --------------------------------------------------------
    # Для тревожных событий проверяем регион.
    #
    # Отбои тоже разрешаем обрабатывать, если
    # соответствующее событие уже активно.
    # --------------------------------------------------------

    if event_type in (
        "uav",
        "rocket",
    ):

        if (
            not is_kostroma_related(text)
            and event_type == "uav"
        ):
            return


    # --------------------------------------------------------
    # ОТПЕЧАТОК КОНКРЕТНОГО ПОСТА
    # --------------------------------------------------------

    post_fingerprint = (
        make_post_fingerprint(
            source_key,
            post_id,
            text,
        )
    )


    # --------------------------------------------------------
    # ЕСЛИ ЭТОТ ПОСТ УЖЕ ОБРАБАТЫВАЛИ —
    # ВООБЩЕ НИЧЕГО НЕ ДЕЛАЕМ.
    #
    # Это защита от повторного чтения одной публикации.
    # --------------------------------------------------------

    if post_was_processed(
        post_fingerprint
    ):

        return


    # --------------------------------------------------------
    # Сначала помечаем пост обработанным.
    #
    # Это важно: если несколько циклов мониторинга
    # одновременно увидят один пост, он не будет
    # обработан повторно.
    # --------------------------------------------------------

    mark_post_processed(
        post_fingerprint,
        source_key,
        post_id,
    )


    # --------------------------------------------------------
    # ПРИМЕНЯЕМ СОБЫТИЕ
    # --------------------------------------------------------

    changed, _, event_fingerprint = (
        apply_event(
            event_type=event_type,
            level=level,
            text=text,
            source_name=source_name,
            source_url=source_url,
            post_id=post_id,
        )
    )


    # --------------------------------------------------------
    # СОХРАНЯЕМ ИСТОРИЮ
    #
    # Даже если пуш не отправится из-за дедупликации,
    # само событие может остаться в истории.
    # --------------------------------------------------------

    add_history(
        event_type=event_type,
        level=level,
        text=text,
        source_name=source_name,
        source_url=source_url,
        post_id=post_id,
    )


    # --------------------------------------------------------
    # ГЛАВНАЯ ЗАЩИТА ОТ ПЯТИ ПУШЕЙ
    #
    # Если fingerprint уже существует:
    #
    # НЕ отправляем повтор.
    #
    # Даже если:
    # - другой источник
    # - другой post_id
    # - тот же текст
    # - бот увидел событие снова
    # --------------------------------------------------------

    if event_was_notified(
        event_fingerprint
    ):

        logger.info(
            "Дубликат события. Пуш пропущен: %s",
            event_fingerprint,
        )

        return


    # --------------------------------------------------------
    # НОВОЕ СОБЫТИЕ
    # --------------------------------------------------------

    mark_event_notified(
        event_fingerprint=event_fingerprint,
        event_type=event_type,
        level=level,
        districts=state.get(
            "districts",
            [],
        ),
    )


    # --------------------------------------------------------
    # ФОРМИРУЕМ PUSH
    # --------------------------------------------------------

    message = build_push_message(
        event_type=event_type,
        level=level,
        text=text,
        source_name=source_name,
        source_link=post.get(
            "link",
            "",
        ),
    )


    # --------------------------------------------------------
    # ОТПРАВЛЯЕМ ПОДПИСЧИКАМ
    # --------------------------------------------------------

    await send_pushes(
        bot,
        message,
    )


# ============================================================
# ФОРМИРОВАНИЕ PUSH-СООБЩЕНИЯ
# ============================================================

def build_push_message(
    event_type,
    level,
    text,
    source_name,
    source_link,
):
    # --------------------------------------------------------
    # ЗАГОЛОВОК
    # --------------------------------------------------------

    if event_type == "uav":

        title = UAV_LEVELS[
            level
        ]["title"]

    elif event_type == "uav_cancel":

        title = (
            "🟢 Отбой по БПЛА"
        )

    elif event_type == "rocket":

        title = (
            "🚨 Ракетная опасность"
        )

    elif event_type == "rocket_cancel":

        title = (
            "🟢 Отбой ракетной опасности"
        )

    else:

        title = (
            "🛰️ Новое событие"
        )


    # --------------------------------------------------------
    # РАЙОНЫ
    # --------------------------------------------------------

    districts = detect_districts(
        text
    )


    # --------------------------------------------------------
    # СООБЩЕНИЕ
    # --------------------------------------------------------

    lines = [
        "<b>UAV ALERT</b>",
        "",
        title,
        "",
        "📍 <b>Костромская область</b>",
    ]


    if districts:

        lines.append("")
        lines.append(
            "📌 <b>Районы:</b>"
        )

        for district in districts:
            lines.append(
                f"• {district}"
            )


    # --------------------------------------------------------
    # Для города показываем информацию,
    # но НЕ объявляем город опасным автоматически.
    # --------------------------------------------------------

    cities = detect_cities(
        text
    )

    if cities:

        lines.append("")
        lines.append(
            "🏙 <b>Упомянуто:</b> "
            + ", ".join(cities[:10])
        )


    # --------------------------------------------------------
    # ИСТОЧНИК
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "📡 <b>Источник:</b> "
        + source_name
    )


    if source_link:

        lines.append(
            f'🔗 <a href="{source_link}">'
            "Открыть публикацию"
            "</a>"
        )


    # --------------------------------------------------------
    # Время
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "🕐 "
        + datetime.now().strftime(
            "%d.%m.%Y %H:%M"
        )
    )


    return "\n".join(lines)


# ============================================================
# ОТПРАВКА PUSH
# ============================================================

async def send_pushes(
    bot,
    message,
):
    if not subscribers:

        logger.info(
            "Нет подписчиков для push."
        )

        return


    failed = []

    for user_id in list(
        subscribers
    ):

        try:

            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            logger.info(
                "Push отправлен: %s",
                user_id,
            )

        except Exception as e:

            logger.warning(
                "Не удалось отправить push %s: %s",
                user_id,
                e,
            )

            # Если пользователь заблокировал бота,
            # Telegram может вернуть ошибку.
            # Удаляем его из подписчиков.

            error_text = str(e).lower()

            if (
                "blocked" in error_text
                or "chat not found" in error_text
                or "user is deactivated" in error_text
            ):
                failed.append(
                    user_id
                )


    for user_id in failed:

        if user_id in subscribers:

            subscribers.remove(
                user_id
            )


    if failed:

        save_json(
            SUBSCRIBERS_FILE,
            subscribers,
        )


# ============================================================
# МОНИТОРИНГ
# ============================================================

monitor_running = False


async def monitor_loop(
    application,
):
    global monitor_running

    if monitor_running:

        logger.info(
            "Мониторинг уже запущен."
        )

        return

    monitor_running = True

    logger.info(
        "UAV ALERT мониторинг запущен."
    )


    while True:

        try:

            cleanup_dedup()


            for source_key, source_data in SOURCES.items():

                posts = fetch_source(
                    source_key,
                    source_data,
                )


                # Берём последние публикации.
                # Обычно достаточно 20.
                for post in posts[-20:]:

                    try:

                        await process_post(
                            application.bot,
                            post,
                        )

                    except Exception as e:

                        logger.error(
                            "Ошибка обработки поста: %s",
                            e,
                        )


        except Exception as e:

            logger.error(
                "Ошибка главного мониторинга: %s",
                e,
            )


        await asyncio_sleep(
            CHECK_INTERVAL
        )


# ============================================================
# ASYNC SLEEP
# ============================================================

async def asyncio_sleep(seconds):
    import asyncio

    await asyncio.sleep(
        seconds
    )


# ============================================================
# КОМАНДЫ АДМИНИСТРАТОРА
# ============================================================

def is_admin(update):
    user = update.effective_user

    if not user:
        return False

    return user.id == ADMIN_ID


# ============================================================
# /TEST
#
# Админская тестовая команда.
# Она НЕ влияет на реальное состояние.
# ============================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )

        return


    message = (
        "🧪 <b>Тест UAV ALERT</b>\n\n"
        "Это тестовое сообщение.\n"
        "Реальное состояние тревоги не изменено."
    )

    await update.message.reply_text(
        message,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /ADMIN
# ============================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return


    await update.message.reply_text(
        "🛠 <b>Панель администратора</b>\n\n"
        f"👥 Подписчиков: {len(subscribers)}\n"
        f"📚 История: {len(history)}\n"
        f"💾 Дедуп-постов: {len(sent_posts)}\n"
        f"🔐 Дедуп-событий: {len(notification_events)}\n\n"
        "Мониторинг работает автоматически.",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

flask_app = Flask(
    __name__
)


@flask_app.route("/")
def home():
    return (
        "UAV ALERT is running",
        200,
    )


@flask_app.route("/health")
def health():
    return {
        "status": "ok",
        "service": "UAV ALERT",
        "region": "Kostroma",
    }, 200


def run_flask():
    try:

        flask_app.run(
            host="0.0.0.0",
            port=PORT,
            use_reloader=False,
        )

    except Exception as e:

        logger.error(
            "Flask error: %s",
            e,
        )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):
    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Запустить UAV ALERT",
            ),

            BotCommand(
                "status",
                "Текущий статус",
            ),

            BotCommand(
                "subscribe",
                "Подписаться",
            ),

            BotCommand(
                "unsubscribe",
                "Отписаться",
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
        ]
    )


    # --------------------------------------------------------
    # Запускаем мониторинг в фоне
    # --------------------------------------------------------

    application.create_task(
        monitor_loop(
            application
        )
    )

    logger.info(
        "UAV ALERT bot initialized."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "Переменная BOT_TOKEN не установлена."
        )


    logger.info(
        "Запуск UAV ALERT..."
    )

    logger.info(
        "Регион: Костромская область"
    )

    logger.info(
        "Подписчиков: %s",
        len(subscribers),
    )

    logger.info(
        "История: %s",
        len(history),
    )


    # --------------------------------------------------------
    # Flask запускаем отдельным потоком
    # --------------------------------------------------------

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True,
    )

    flask_thread.start()


    # --------------------------------------------------------
    # Telegram Application
    # --------------------------------------------------------

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )


    # --------------------------------------------------------
    # Основные команды
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


    # --------------------------------------------------------
    # Админские команды
    # --------------------------------------------------------

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
    # Кнопки
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )


    # --------------------------------------------------------
    # Запуск
    # --------------------------------------------------------

    logger.info(
        "Telegram polling started."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
