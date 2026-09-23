import os
import re
import json
import time
import hashlib
import threading
import logging
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
# UAV ALERT — Telegram Bot
# Костромская область
#
# Основные возможности:
# - Внимание по БПЛА
# - Угроза по БПЛА
# - Опасность по БПЛА
# - Ракетная опасность
# - Отбой по БПЛА
# - Отбой ракетной опасности
# - Push на все уровни тревоги
# - Push на отбои
# - Несколько районов одновременно
# - Муниципальные округа
# - Города
# - Каждый населённый пункт/район показывается отдельно
# - История событий
# - Статистика
# - Источники
# - Подписчики
# - Автоматический мониторинг Telegram-источников
# - Жёсткая защита от повторных пушей
# - Московское время
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
# МОСКОВСКОЕ ВРЕМЯ
# ============================================================

MSK = timezone(
    timedelta(hours=3)
)


# ============================================================
# ФАЙЛЫ
# ============================================================

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
# ЛОГИ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(
    "UAV_ALERT"
)


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
#
# Например:
#
# "Макарьев" -> город Макарьев
#
# "Макарьевский район" -> Макарьевский район
#
# "Макарьевский муниципальный округ" ->
# муниципальный округ
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
# МУНИЦИПАЛЬНЫЕ ОКРУГА
#
# Бот ищет их отдельно от районов.
# ============================================================

MUNICIPAL_DISTRICTS = {
    "антроповский": "Антроповский муниципальный округ",
    "буйский": "Буйский муниципальный округ",
    "вохомский": "Вохомский муниципальный округ",
    "галичский": "Галичский муниципальный округ",
    "кадийский": "Кадыйский муниципальный округ",
    "кологривский": "Кологривский муниципальный округ",
    "макарьевский": "Макарьевский муниципальный округ",
    "мантуровский": "Мантуровский муниципальный округ",
    "межевский": "Межевский муниципальный округ",
    "нейский": "Нейский муниципальный округ",
    "октябрьский": "Октябрьский муниципальный округ",
    "островский": "Островский муниципальный округ",
    "павинский": "Павинский муниципальный округ",
    "парфеньевский": "Парфеньевский муниципальный округ",
    "поназыревский": "Поназыревский муниципальный округ",
    "пыщугский": "Пыщугский муниципальный округ",
    "солигаличский": "Солигаличский муниципальный округ",
    "сусанинский": "Сусанинский муниципальный округ",
    "чухломский": "Чухломский муниципальный округ",
    "шарьинский": "Шарьинский муниципальный округ",
}


# ============================================================
# ГОРОДА
#
# Они используются только для определения места в тексте.
#
# ВАЖНО:
# Город не превращается автоматически в район.
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

    # Активные муниципальные округа
    "municipal_districts": [],

    # Активные места в едином списке.
    #
    # Например:
    # [
    #   "Нейский район",
    #   "Кострома",
    #   "Шарьинский муниципальный округ"
    # ]
    #
    # Здесь находятся ТОЛЬКО места,
    # где есть активная информация о БПЛА.
    "active_locations": [],

    # Найденные города для информационного отображения
    "cities": [],

    "source": "",
    "source_url": "",

    "updated_at": "",

    "event_post_id": "",
    "event_source_id": "",

    # --------------------------------------------------------
    # НОМЕР ЦИКЛА БПЛА
    # --------------------------------------------------------
    "uav_cycle": 0,

    # --------------------------------------------------------
    # НОМЕР ЦИКЛА РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------
    "rocket_cycle": 0,

    # --------------------------------------------------------
    # Последний уровень БПЛА.
    # --------------------------------------------------------
    "last_uav_level": 0,

    # Районы, которые были активны перед последним отбоем.
    "last_uav_districts": [],

    # Муниципальные округа,
    # которые были активны перед последним отбоем.
    "last_uav_municipal_districts": [],

    # Все активные места перед последним отбоем.
    "last_uav_locations": [],
}


state_lock = threading.Lock()


# ============================================================
# ЗАГРУЗКА / СОХРАНЕНИЕ JSON
# ============================================================

def load_json(
    filename,
    default,
):

    try:

        if not os.path.exists(
            filename
        ):

            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(
                f
            )

    except Exception as e:

        logger.error(
            "Ошибка чтения %s: %s",
            filename,
            e,
        )

        return default


def save_json(
    filename,
    data,
):

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

        os.replace(
            tmp,
            filename,
        )

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

    if not isinstance(
        state,
        dict,
    ):

        state = DEFAULT_STATE.copy()


    for key, value in DEFAULT_STATE.items():

        if key not in state:

            if isinstance(
                value,
                list,
            ):

                state[key] = []

            else:

                state[key] = value


    if not isinstance(
        state.get("districts"),
        list,
    ):

        state["districts"] = []


    if not isinstance(
        state.get("municipal_districts"),
        list,
    ):

        state["municipal_districts"] = []


    if not isinstance(
        state.get("active_locations"),
        list,
    ):

        state["active_locations"] = []


    if not isinstance(
        state.get("cities"),
        list,
    ):

        state["cities"] = []


    if not isinstance(
        state.get("last_uav_districts"),
        list,
    ):

        state["last_uav_districts"] = []


    if not isinstance(
        state.get("last_uav_municipal_districts"),
        list,
    ):

        state["last_uav_municipal_districts"] = []


    if not isinstance(
        state.get("last_uav_locations"),
        list,
    ):

        state["last_uav_locations"] = []


    try:

        state["uav_cycle"] = int(
            state.get(
                "uav_cycle",
                0,
            )
        )

    except Exception:

        state["uav_cycle"] = 0


    try:

        state["rocket_cycle"] = int(
            state.get(
                "rocket_cycle",
                0,
            )
        )

    except Exception:

        state["rocket_cycle"] = 0


    try:

        state["last_uav_level"] = int(
            state.get(
                "last_uav_level",
                0,
            )
        )

    except Exception:

        state["last_uav_level"] = 0


    # --------------------------------------------------------
    # Если active_locations ещё нет,
    # собираем его из старых данных.
    # --------------------------------------------------------

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
        ] = list(
            dict.fromkeys(
                old_locations
            )
        )


normalize_state()


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def now_iso():

    return datetime.now(
        MSK
    ).isoformat()


def clean_text(
    text,
):

    if not text:

        return ""

    text = text.replace(
        "\xa0",
        " ",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def normalize_text(
    text,
):

    text = clean_text(
        text
    ).lower()

    text = re.sub(
        r"https?://\S+",
        "",
        text,
    )

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


def make_hash(
    text,
):

    return hashlib.sha256(
        text.encode(
            "utf-8"
        )
    ).hexdigest()


def unique_list(
    items,
):

    result = []

    for item in items:

        if item not in result:

            result.append(
                item
            )

    return result


# ============================================================
# ОПРЕДЕЛЕНИЕ РАЙОНОВ
# ============================================================

def detect_districts(
    text,
):

    text_lower = text.lower()

    found = []


    for key, district_name in DISTRICTS.items():

        variants = [
            f"{key} район",
            f"{key} р-н",
            key,
        ]


        # ----------------------------------------------------
        # Для неоднозначных названий
        # обязательно требуем "район".
        # ----------------------------------------------------

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

                found.append(
                    district_name
                )

                break


    return unique_list(
        found
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ МУНИЦИПАЛЬНЫХ ОКРУГОВ
# ============================================================

def detect_municipal_districts(
    text,
):

    text_lower = text.lower()

    found = []


    for key, district_name in MUNICIPAL_DISTRICTS.items():

        variants = [
            f"{key} муниципальный округ",
            f"{key} мунициальный округ",
            f"{key} мо",
        ]


        for variant in variants:

            if variant in text_lower:

                found.append(
                    district_name
                )

                break


    # --------------------------------------------------------
    # Дополнительно ловим формы:
    #
    # "Нейский муниципальный округ"
    # "Костромской муниципальный округ"
    #
    # даже если конкретного названия нет в словаре.
    # --------------------------------------------------------

    generic_matches = re.findall(
        r"\b([а-яё-]+)\s+муниципальн(?:ый|ого|ом|ым)\s+округ\b",
        text_lower,
        flags=re.IGNORECASE,
    )


    for match in generic_matches:

        words = match.strip(
            " -"
        ).split()


        if not words:

            continue


        name = words[0].capitalize()

        generic_name = (
            name
            + " муниципальный округ"
        )


        if generic_name not in found:

            found.append(
                generic_name
            )


    # --------------------------------------------------------
    # Городские округа.
    # --------------------------------------------------------

    city_matches = re.findall(
        r"\b([а-яё-]+)\s+городск(?:ой|ого|ом|им)\s+округ\b",
        text_lower,
        flags=re.IGNORECASE,
    )


    for match in city_matches:

        name = match.capitalize()

        generic_name = (
            name
            + " городской округ"
        )


        if generic_name not in found:

            found.append(
                generic_name
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

            found.append(
                city.capitalize()
            )


    return unique_list(
        found
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ ВСЕХ МЕСТ
#
# Здесь собираются только конкретно найденные места.
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


    locations = unique_list(
        districts
        + municipal_districts
        + cities
    )


    return {
        "districts": districts,
        "municipal_districts": municipal_districts,
        "cities": cities,
        "locations": locations,
    }


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

def detect_event(
    text,
):

    normalized = normalize_text(
        text
    )


    # --------------------------------------------------------
    # ОТБОЙ БПЛА
    # --------------------------------------------------------

    if (
        "отбой по бпла" in normalized
        or "отбой бпла" in normalized
        or "угроза бпла отменена" in normalized
        or "опасность бпла отменена" in normalized
        or "угроза атаки бпла снята" in normalized
        or "опасность атаки бпла снята" in normalized
        or "угроза по бпла снята" in normalized
        or "опасность по бпла снята" in normalized
        or "внимание по бпла снято" in normalized
        or "внимание бпла снято" in normalized
    ):

        return (
            "uav_cancel",
            0,
        )


    # --------------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------

    if (
        "отбой ракетной опасности" in normalized
        or "ракетная опасность отменена" in normalized
        or "ракетная опасность снята" in normalized
    ):

        return (
            "rocket_cancel",
            0,
        )


    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # --------------------------------------------------------

    if (
        "ракетная опасность" in normalized
        or "опасность ракетного удара" in normalized
    ):

        return (
            "rocket",
            1,
        )


    # --------------------------------------------------------
    # БПЛА — ОПАСНОСТЬ
    # --------------------------------------------------------

    if (
        "опасность по бпла" in normalized
        or "опасность бпла" in normalized
        or "высокая опасность бпла" in normalized
    ):

        return (
            "uav",
            3,
        )


    # --------------------------------------------------------
    # БПЛА — УГРОЗА
    # --------------------------------------------------------

    if (
        "угроза по бпла" in normalized
        or "угроза бпла" in normalized
        or "угроза атаки бпла" in normalized
    ):

        return (
            "uav",
            2,
        )


    # --------------------------------------------------------
    # БПЛА — ВНИМАНИЕ
    # --------------------------------------------------------

    if (
        "внимание по бпла" in normalized
        or "внимание бпла" in normalized
        or "беспилотная опасность" in normalized
        or "опасность беспилотников" in normalized
    ):

        return (
            "uav",
            1,
        )


    return None


# ============================================================
# ПРОВЕРКА ОТНОШЕНИЯ К КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

def is_kostroma_related(
    text,
):

    normalized = normalize_text(
        text
    )


    # --------------------------------------------------------
    # Прямое упоминание региона.
    # --------------------------------------------------------

    for word in REGION_WORDS:

        if word in normalized:

            return True


    # --------------------------------------------------------
    # Районы.
    # --------------------------------------------------------

    districts = detect_districts(
        text
    )


    if districts:

        return True


    # --------------------------------------------------------
    # Муниципальные округа.
    # --------------------------------------------------------

    municipal_districts = (
        detect_municipal_districts(
            text
        )
    )


    if municipal_districts:

        return True


    # --------------------------------------------------------
    # Города.
    # --------------------------------------------------------

    cities = detect_cities(
        text
    )


    if cities:

        return True


    return False


# ============================================================
# ФОРМИРОВАНИЕ ОПИСАНИЯ СОБЫТИЯ
# ============================================================

def build_event_description(
    text,
):

    detected = detect_locations(
        text
    )


    return detected


# ============================================================
# СОЗДАНИЕ ОТПЕЧАТКА СОБЫТИЯ
# ============================================================

def make_event_fingerprint(
    event_type,
    level,
    districts,
    rocket_active,
    text="",
    uav_cycle=None,
    rocket_cycle=None,
):

    districts_sorted = sorted(
        set(
            districts or []
        )
    )


    # --------------------------------------------------------
    # БПЛА
    # --------------------------------------------------------

    if event_type == "uav":

        raw = (
            "uav|"
            + str(level)
            + "|"
            + "|".join(
                districts_sorted
            )
        )


    # --------------------------------------------------------
    # ОТБОЙ БПЛА
    # --------------------------------------------------------

    elif event_type == "uav_cancel":

        if uav_cycle is None:

            uav_cycle = state.get(
                "uav_cycle",
                0,
            )


        raw = (
            "uav_cancel|"
            + str(uav_cycle)
        )


    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    #
    # Теперь учитываем цикл.
    # --------------------------------------------------------

    elif event_type == "rocket":

        if rocket_cycle is None:

            rocket_cycle = state.get(
                "rocket_cycle",
                0,
            )


        raw = (
            "rocket|1|"
            + str(rocket_cycle)
        )


    # --------------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------

    elif event_type == "rocket_cancel":

        if rocket_cycle is None:

            rocket_cycle = state.get(
                "rocket_cycle",
                0,
            )


        raw = (
            "rocket_cancel|"
            + str(rocket_cycle)
        )


    else:

        raw = (
            str(event_type)
            + "|"
            + str(level)
            + "|"
            + "|".join(
                districts_sorted
            )
            + "|"
            + normalize_text(
                text
            )
        )


    return make_hash(
        raw
    )


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
            + normalize_text(
                text
            )
        )


    return make_hash(
        raw
    )


# ============================================================
# ОЧИСТКА СТАРЫХ ДЕДУП-ЗАПИСЕЙ
# ============================================================

def cleanup_dedup():

    global sent_posts
    global notification_events

    current = time.time()

    max_age = (
        DEDUP_HOURS
        * 60
        * 60
    )


    # --------------------------------------------------------
    # Посты
    # --------------------------------------------------------

    new_sent_posts = {}


    for key, value in sent_posts.items():

        try:

            timestamp = float(
                value.get(
                    "timestamp",
                    0,
                )
            )


            if (
                current - timestamp
                <= max_age
            ):

                new_sent_posts[
                    key
                ] = value


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
                value.get(
                    "timestamp",
                    0,
                )
            )


            if (
                current - timestamp
                <= max_age
            ):

                new_events[
                    key
                ] = value


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

def post_was_processed(
    post_fingerprint,
):

    return (
        post_fingerprint
        in sent_posts
    )


def mark_post_processed(
    post_fingerprint,
    source_key,
    post_id,
):

    sent_posts[
        post_fingerprint
    ] = {

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

def event_was_notified(
    event_fingerprint,
):

    data = notification_events.get(
        event_fingerprint
    )


    if not data:

        return False


    try:

        timestamp = float(
            data.get(
                "timestamp",
                0,
            )
        )


        if (
            time.time()
            - timestamp
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

    if state.get(
        "rocket_active"
    ):

        if state.get(
            "uav_level",
            0,
        ) > 0:

            return (
                "🚨 Ракетная опасность\n"
                + UAV_LEVELS[
                    state["uav_level"]
                ]["title"]
            )


        return (
            "🚨 Ракетная опасность"
        )


    return UAV_LEVELS[
        state.get(
            "uav_level",
            0,
        )
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

    global state


    description = build_event_description(
        text
    )


    found_districts = description[
        "districts"
    ]


    found_municipal_districts = (
        description[
            "municipal_districts"
        ]
    )


    found_cities = description[
        "cities"
    ]


    found_locations = description[
        "locations"
    ]


    with state_lock:

        old_level = int(
            state.get(
                "uav_level",
                0,
            )
        )


        old_rocket = bool(
            state.get(
                "rocket_active",
                False,
            )
        )


        old_districts = sorted(
            set(
                state.get(
                    "districts",
                    [],
                )
            )
        )


        old_municipal_districts = sorted(
            set(
                state.get(
                    "municipal_districts",
                    [],
                )
            )
        )


        old_active_locations = sorted(
            set(
                state.get(
                    "active_locations",
                    [],
                )
            )
        )


        current_uav_cycle = int(
            state.get(
                "uav_cycle",
                0,
            )
        )


        current_rocket_cycle = int(
            state.get(
                "rocket_cycle",
                0,
            )
        )


        # ====================================================
        # ОТБОЙ БПЛА
        # ====================================================

        if event_type == "uav_cancel":

            previous_level = old_level

            previous_districts = list(
                old_districts
            )


            previous_municipal_districts = list(
                old_municipal_districts
            )


            previous_locations = list(
                old_active_locations
            )


            changed = (
                old_level != 0
                or len(
                    old_districts
                ) > 0
                or len(
                    old_municipal_districts
                ) > 0
                or len(
                    old_active_locations
                ) > 0
            )


            should_notify = changed


            state[
                "last_uav_level"
            ] = previous_level


            state[
                "last_uav_districts"
            ] = previous_districts


            state[
                "last_uav_municipal_districts"
            ] = previous_municipal_districts


            state[
                "last_uav_locations"
            ] = previous_locations


            # ------------------------------------------------
            # Очищаем активную БПЛА-опасность.
            # ------------------------------------------------

            state[
                "uav_level"
            ] = 0


            state[
                "districts"
            ] = []


            state[
                "municipal_districts"
            ] = []


            state[
                "active_locations"
            ] = []


            state[
                "cities"
            ] = []


            # ------------------------------------------------
            # Если ракетной опасности нет,
            # статус становится зелёным.
            #
            # Если ракеты всё ещё активны,
            # get_status_title() оставит ракетную опасность.
            # ------------------------------------------------

            if not old_rocket:

                state[
                    "status"
                ] = "green"

            else:

                state[
                    "status"
                ] = "red"


            state[
                "title"
            ] = get_status_title()


            state[
                "source"
            ] = source_name


            state[
                "source_url"
            ] = source_url


            state[
                "updated_at"
            ] = now_iso()


            state[
                "event_post_id"
            ] = str(
                post_id
            )


            fingerprint = make_event_fingerprint(
                "uav_cancel",
                0,
                previous_locations,
                old_rocket,
                text,
                uav_cycle=current_uav_cycle,
            )


            save_json(
                STATE_FILE,
                state,
            )


            return {
                "changed": changed,
                "should_notify": should_notify,
                "event_fingerprint": fingerprint,
                "previous_level": previous_level,
                "previous_districts": previous_districts,
                "previous_municipal_districts": previous_municipal_districts,
                "previous_locations": previous_locations,
                "previous_rocket": old_rocket,
                "cycle": current_uav_cycle,
            }


        # ====================================================
        # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
        # ====================================================

        if event_type == "rocket_cancel":

            changed = old_rocket

            should_notify = changed


            state[
                "rocket_active"
            ] = False


            state[
                "status"
            ] = UAV_LEVELS[
                old_level
            ]["status"]


            state[
                "title"
            ] = get_status_title()


            state[
                "source"
            ] = source_name


            state[
                "source_url"
            ] = source_url


            state[
                "updated_at"
            ] = now_iso()


            state[
                "event_post_id"
            ] = str(
                post_id
            )


            fingerprint = make_event_fingerprint(
                "rocket_cancel",
                0,
                [],
                False,
                text,
                rocket_cycle=current_rocket_cycle,
            )


            save_json(
                STATE_FILE,
                state,
            )


            return {
                "changed": changed,
                "should_notify": should_notify,
                "event_fingerprint": fingerprint,
                "previous_level": old_level,
                "previous_districts": old_districts,
                "previous_municipal_districts": old_municipal_districts,
                "previous_locations": old_active_locations,
                "previous_rocket": old_rocket,
                "cycle": current_rocket_cycle,
            }


        # ====================================================
        # РАКЕТНАЯ ОПАСНОСТЬ
        # ====================================================

        if event_type == "rocket":

            if not old_rocket:

                current_rocket_cycle += 1

                state[
                    "rocket_cycle"
                ] = current_rocket_cycle


            state[
                "rocket_active"
            ] = True


            state[
                "source"
            ] = source_name


            state[
                "source_url"
            ] = source_url


            state[
                "updated_at"
            ] = now_iso()


            state[
                "event_post_id"
            ] = str(
                post_id
            )


            state[
                "status"
            ] = "red"


            state[
                "title"
            ] = get_status_title()


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
                rocket_cycle=current_rocket_cycle,
            )


            changed = (
                not old_rocket
            )


            return {
                "changed": changed,
                "should_notify": True,
                "event_fingerprint": fingerprint,
                "previous_level": old_level,
                "previous_districts": old_districts,
                "previous_municipal_districts": old_municipal_districts,
                "previous_locations": old_active_locations,
                "previous_rocket": old_rocket,
                "cycle": current_rocket_cycle,
            }


        # ====================================================
        # БПЛА
        # ====================================================

        if event_type == "uav":

            if old_level == 0:

                current_uav_cycle += 1

                state[
                    "uav_cycle"
                ] = current_uav_cycle


            # ------------------------------------------------
            # Не понижаем уровень из-за старого сообщения.
            # ------------------------------------------------

            new_level = max(
                old_level,
                level,
            )


            # ------------------------------------------------
            # Добавляем новые районы.
            # ------------------------------------------------

            merged_districts = unique_list(
                old_districts
                + found_districts
            )


            merged_districts = sorted(
                merged_districts
            )


            # ------------------------------------------------
            # Добавляем муниципальные округа.
            # ------------------------------------------------

            merged_municipal_districts = unique_list(
                old_municipal_districts
                + found_municipal_districts
            )


            merged_municipal_districts = sorted(
                merged_municipal_districts
            )


            # ------------------------------------------------
            # Города.
            # ------------------------------------------------

            merged_cities = unique_list(
                state.get(
                    "cities",
                    [],
                )
                + found_cities
            )


            # ------------------------------------------------
            # Единый список активных мест.
            #
            # Только места, которые реально были
            # обнаружены в сообщениях.
            # ------------------------------------------------

            merged_locations = unique_list(
                old_active_locations
                + found_locations
            )


            merged_locations = sorted(
                merged_locations
            )


            level_changed = (
                new_level
                != old_level
            )


            districts_changed = (
                merged_districts
                != old_districts
            )


            municipal_changed = (
                merged_municipal_districts
                != old_municipal_districts
            )


            locations_changed = (
                merged_locations
                != old_active_locations
            )


            changed = (
                level_changed
                or districts_changed
                or municipal_changed
                or locations_changed
            )


            state[
                "uav_level"
            ] = new_level


            state[
                "last_uav_level"
            ] = new_level


            state[
                "districts"
            ] = merged_districts


            state[
                "municipal_districts"
            ] = merged_municipal_districts


            state[
                "active_locations"
            ] = merged_locations


            state[
                "cities"
            ] = merged_cities


            state[
                "status"
            ] = UAV_LEVELS[
                new_level
            ]["status"]


            state[
                "source"
            ] = source_name


            state[
                "source_url"
            ] = source_url


            state[
                "updated_at"
            ] = now_iso()


            state[
                "event_post_id"
            ] = str(
                post_id
            )


            state[
                "title"
            ] = get_status_title()


            save_json(
                STATE_FILE,
                state,
            )


            fingerprint = make_event_fingerprint(
                "uav",
                new_level,
                merged_locations,
                state.get(
                    "rocket_active",
                    False,
                ),
                text,
                uav_cycle=current_uav_cycle,
            )


            return {
                "changed": changed,
                "should_notify": True,
                "event_fingerprint": fingerprint,
                "previous_level": old_level,
                "previous_districts": old_districts,
                "previous_municipal_districts": old_municipal_districts,
                "previous_locations": old_active_locations,
                "previous_rocket": old_rocket,
                "cycle": current_uav_cycle,
            }


    return {
        "changed": False,
        "should_notify": False,
        "event_fingerprint": "",
        "previous_level": 0,
        "previous_districts": [],
        "previous_municipal_districts": [],
        "previous_locations": [],
        "previous_rocket": False,
        "cycle": 0,
    }


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
    districts_override=None,
    municipal_districts_override=None,
    locations_override=None,
):

    # --------------------------------------------------------
    # Районы.
    # --------------------------------------------------------

    if districts_override is None:

        districts = detect_districts(
            text
        )

    else:

        districts = list(
            districts_override
        )


    # --------------------------------------------------------
    # Муниципальные округа.
    # --------------------------------------------------------

    if municipal_districts_override is None:

        municipal_districts = (
            detect_municipal_districts(
                text
            )
        )

    else:

        municipal_districts = list(
            municipal_districts_override
        )


    # --------------------------------------------------------
    # Активные места.
    # --------------------------------------------------------

    if locations_override is None:

        locations = unique_list(
            districts
            + municipal_districts
            + detect_cities(
                text
            )
        )

    else:

        locations = list(
            locations_override
        )


    item = {
        "timestamp": now_iso(),

        "event_type": event_type,

        "level": level,

        "text": clean_text(
            text
        ),

        "source": source_name,

        "source_url": source_url,

        "post_id": str(
            post_id
        ),

        "districts": districts,

        "municipal_districts": municipal_districts,

        "locations": locations,

        "cities": detect_cities(
            text
        ),
    }


    history.insert(
        0,
        item,
    )


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


        # ----------------------------------------------------
        # Только активные места.
        # ----------------------------------------------------

        active_locations = state.get(
            "active_locations",
            [],
        )


        if (
            active_locations
            and state.get(
                "uav_level",
                0,
            ) > 0
        ):

            lines.append("")

            lines.append(
                "📌 <b>Места с активной "
                "опасностью по БПЛА:</b>"
            )


            for location in active_locations:

                lines.append(
                    f"• {location}"
                )


        # ----------------------------------------------------
        # Если уровень БПЛА есть,
        # но конкретное место не указано.
        # ----------------------------------------------------

        elif (
            state.get(
                "uav_level",
                0,
            ) > 0
            and not active_locations
        ):

            lines.append("")

            lines.append(
                "📌 <b>Место в источнике "
                "не указано.</b>"
            )


        # ----------------------------------------------------
        # Источник.
        # ----------------------------------------------------

        if state.get(
            "source"
        ):

            lines.append("")

            lines.append(
                "📡 <b>Источник:</b> "
                + state["source"]
            )


        # ----------------------------------------------------
        # Время МСК.
        # ----------------------------------------------------

        if state.get(
            "updated_at"
        ):

            try:

                updated = datetime.fromisoformat(
                    state["updated_at"]
                )


                lines.append(
                    "🕐 <b>Обновлено:</b> "
                    + updated.astimezone(
                        MSK
                    ).strftime(
                        "%d.%m.%Y %H:%M"
                    )
                    + " МСК"
                )

            except Exception:

                lines.append(
                    "🕐 <b>Обновлено:</b> "
                    + state["updated_at"]
                )


        return "\n".join(
            lines
        )


# ============================================================
# ФОРМАТИРОВАНИЕ ИСТОРИИ
# ============================================================

def format_history(
    limit=10,
):

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

            title = (
                "🚨 Ракетная опасность"
            )


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


        lines.append(
            title
        )


        # ----------------------------------------------------
        # Места события.
        # ----------------------------------------------------

        locations = item.get(
            "locations",
            [],
        )


        if locations:

            lines.append(
                "📍 "
                + ", ".join(
                    locations[:10]
                )
            )


        else:

            districts = item.get(
                "districts",
                [],
            )


            municipal_districts = item.get(
                "municipal_districts",
                [],
            )


            old_locations = unique_list(
                districts
                + municipal_districts
            )


            if old_locations:

                lines.append(
                    "📍 "
                    + ", ".join(
                        old_locations[:10]
                    )
                )


        source = item.get(
            "source",
            "",
        )


        if source:

            lines.append(
                f"📡 {source}"
            )


        timestamp = item.get(
            "timestamp",
            "",
        )


        if timestamp:

            try:

                dt = datetime.fromisoformat(
                    timestamp
                )


                lines.append(
                    "🕐 "
                    + dt.astimezone(
                        MSK
                    ).strftime(
                        "%d.%m.%Y %H:%M"
                    )
                    + " МСК"
                )


            except Exception:

                pass


        lines.append("")


    return "\n".join(
        lines
    )


# ============================================================
# ФОРМАТИРОВАНИЕ СТАТИСТИКИ
# ============================================================

def format_stats():

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


    return "\n".join(
        lines
    )


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

        active_locations = state.get(
            "active_locations",
            [],
        )


        uav_level = state.get(
            "uav_level",
            0,
        )


        if (
            active_locations
            and uav_level > 0
        ):

            level_title = UAV_LEVELS[
                uav_level
            ]["title"]


            text = (
                "📍 <b>Места с активной "
                "опасностью по БПЛА:</b>\n\n"
                + level_title
                + "\n\n"
                + "\n".join(
                    f"• {x}"
                    for x in active_locations
                )
            )


        elif uav_level > 0:

            text = (
                "📍 <b>Активные места</b>\n\n"
                + UAV_LEVELS[
                    uav_level
                ]["title"]
                + "\n\n"
                "Конкретное место "
                "в источнике не указано."
            )


        else:

            text = (
                "📍 <b>Активные места</b>\n\n"
                "🟢 Сейчас конкретных районов, "
                "округов или городов "
                "под опасностью БПЛА не зафиксировано."
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

def fetch_source(
    source_key,
    source_data,
):

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


            text = clean_text(
                text
            )


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

    text = post[
        "text"
    ]


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
    # Определяем событие.
    # --------------------------------------------------------

    detected = detect_event(
        text
    )


    if not detected:

        return


    event_type, level = detected


    # --------------------------------------------------------
    # БПЛА.
    #
    # Для тревожных сообщений обязательно проверяем,
    # что они относятся к Костромской области.
    # --------------------------------------------------------

    if event_type == "uav":

        if not is_kostroma_related(
            text
        ):

            logger.info(
                "БПЛА-сообщение не относится "
                "к Костромской области. Пропуск."
            )

            return


    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ.
    #
    # ВАЖНО:
    # Теперь НЕ берём любое сообщение,
    # где встречается "ракетная опасность".
    #
    # Обрабатываем только если сообщение связано
    # с Костромской областью.
    # --------------------------------------------------------

    if event_type == "rocket":

        if not is_kostroma_related(
            text
        ):

            logger.info(
                "Ракетная опасность в сообщении "
                "не относится к Костромской области. Пропуск."
            )

            return


    # --------------------------------------------------------
    # Отбой ракетной опасности тоже проверяем
    # по Костромской области.
    # --------------------------------------------------------

    if event_type == "rocket_cancel":

        if not is_kostroma_related(
            text
        ):

            logger.info(
                "Отбой ракетной опасности "
                "не относится к Костромской области. Пропуск."
            )

            return


    # --------------------------------------------------------
    # Отбой БПЛА не требует повторного указания региона.
    # --------------------------------------------------------

    post_fingerprint = (
        make_post_fingerprint(
            source_key,
            post_id,
            text,
        )
    )


    # --------------------------------------------------------
    # Если этот пост уже обрабатывали —
    # ничего не делаем.
    # --------------------------------------------------------

    if post_was_processed(
        post_fingerprint
    ):

        return


    # --------------------------------------------------------
    # Помечаем пост обработанным.
    # --------------------------------------------------------

    mark_post_processed(
        post_fingerprint,
        source_key,
        post_id,
    )


    # --------------------------------------------------------
    # ПРИМЕНЯЕМ СОБЫТИЕ
    # --------------------------------------------------------

    result = apply_event(
        event_type=event_type,
        level=level,
        text=text,
        source_name=source_name,
        source_url=source_url,
        post_id=post_id,
    )


    changed = result[
        "changed"
    ]


    should_notify = result[
        "should_notify"
    ]


    event_fingerprint = result[
        "event_fingerprint"
    ]


    previous_level = result[
        "previous_level"
    ]


    previous_districts = result[
        "previous_districts"
    ]


    previous_municipal_districts = result[
        "previous_municipal_districts"
    ]


    previous_locations = result[
        "previous_locations"
    ]


    # --------------------------------------------------------
    # СОХРАНЯЕМ ИСТОРИЮ.
    # --------------------------------------------------------

    if event_type == "uav_cancel":

        add_history(
            event_type=event_type,
            level=previous_level,
            text=text,
            source_name=source_name,
            source_url=source_url,
            post_id=post_id,
            districts_override=previous_districts,
            municipal_districts_override=previous_municipal_districts,
            locations_override=previous_locations,
        )


    else:

        add_history(
            event_type=event_type,
            level=level,
            text=text,
            source_name=source_name,
            source_url=source_url,
            post_id=post_id,
        )


    # --------------------------------------------------------
    # Если отбой пришёл, когда тревоги не было,
    # push не отправляем.
    # --------------------------------------------------------

    if not should_notify:

        logger.info(
            "Событие не изменило активное состояние. "
            "Push пропущен: %s",
            event_type,
        )

        return


    # --------------------------------------------------------
    # ГЛАВНАЯ ЗАЩИТА ОТ ПОВТОРНЫХ PUSH.
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
    # НОВОЕ СОБЫТИЕ.
    # --------------------------------------------------------

    if event_type == "uav_cancel":

        locations_for_notification = (
            previous_locations
        )

        level_for_notification = (
            previous_level
        )

    else:

        locations_for_notification = state.get(
            "active_locations",
            [],
        )

        level_for_notification = level


    mark_event_notified(
        event_fingerprint=event_fingerprint,
        event_type=event_type,
        level=level_for_notification,
        districts=locations_for_notification,
    )


    # --------------------------------------------------------
    # ФОРМИРУЕМ PUSH.
    # --------------------------------------------------------

    message = build_push_message(
        event_type=event_type,
        level=level_for_notification,
        text=text,
        source_name=source_name,
        source_link=post.get(
            "link",
            "",
        ),
        districts_override=locations_for_notification,
        previous_level=previous_level,
        previous_districts=previous_districts,
        previous_municipal_districts=previous_municipal_districts,
        previous_locations=previous_locations,
    )


    # --------------------------------------------------------
    # ОТПРАВЛЯЕМ ПОДПИСЧИКАМ.
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
    districts_override=None,
    previous_level=0,
    previous_districts=None,
    previous_municipal_districts=None,
    previous_locations=None,
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
    # МЕСТА.
    # --------------------------------------------------------

    if districts_override is not None:

        locations = list(
            districts_override
        )

    else:

        detected = detect_locations(
            text
        )

        locations = detected[
            "locations"
        ]


    # --------------------------------------------------------
    # СООБЩЕНИЕ.
    # --------------------------------------------------------

    lines = [
        "<b>UAV ALERT</b>",
        "",
        title,
        "",
        "📍 <b>Костромская область</b>",
    ]


    # ========================================================
    # ОБЫЧНАЯ ТРЕВОГА
    # ========================================================

    if event_type == "uav":

        if locations:

            lines.append("")

            lines.append(
                "📌 <b>Места с активной "
                "опасностью:</b>"
            )


            for location in locations:

                lines.append(
                    f"• {location}"
                )

        else:

            lines.append("")

            lines.append(
                "📌 <b>Конкретное место "
                "в источнике не указано.</b>"
            )


    # ========================================================
    # ОТБОЙ БПЛА
    # ========================================================

    elif event_type == "uav_cancel":

        if previous_level in UAV_LEVELS:

            previous_title = UAV_LEVELS[
                previous_level
            ]["title"]


            lines.append("")

            lines.append(
                "ℹ️ <b>Предыдущий статус:</b>"
            )


            lines.append(
                previous_title
            )


        lines.append("")

        lines.append(
            "Опасность по БПЛА отменена."
        )


        if locations:

            lines.append("")

            lines.append(
                "📍 <b>Ранее активные места:</b>"
            )


            for location in locations:

                lines.append(
                    f"• {location}"
                )


    # ========================================================
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # ========================================================

    elif event_type == "rocket_cancel":

        lines.append("")

        lines.append(
            "Ракетная опасность отменена."
        )


    # ========================================================
    # РАКЕТНАЯ ОПАСНОСТЬ
    # ========================================================

    elif event_type == "rocket":

        lines.append("")

        lines.append(
            "🚨 Требуется ориентироваться "
            "на официальные сообщения властей "
            "и экстренных служб."
        )


    # --------------------------------------------------------
    # Города.
    #
    # Для БПЛА они уже входят в locations,
    # но отдельно показывать их второй раз не нужно.
    # --------------------------------------------------------

    detected_cities = detect_cities(
        text
    )


    if (
        detected_cities
        and event_type not in (
            "uav",
            "uav_cancel",
        )
    ):

        lines.append("")

        lines.append(
            "🏙 <b>Упомянуто:</b> "
            + ", ".join(
                detected_cities[:10]
            )
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
    # Время МСК
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "🕐 "
        + datetime.now(
            MSK
        ).strftime(
            "%d.%m.%Y %H:%M"
        )
        + " МСК"
    )


    return "\n".join(
        lines
    )


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


            error_text = str(
                e
            ).lower()


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

async def asyncio_sleep(
    seconds,
):

    import asyncio

    await asyncio.sleep(
        seconds
    )


# ============================================================
# КОМАНДЫ АДМИНИСТРАТОРА
# ============================================================

def is_admin(
    update,
):

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

    if not is_admin(
        update
    ):

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

    if not is_admin(
        update
    ):

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
    # Запускаем мониторинг в фоне.
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


    logger.info(
        "Часовой пояс: МСК (UTC+3)"
    )


    # --------------------------------------------------------
    # Flask запускаем отдельным потоком.
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
    # Основные команды.
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
    # Админские команды.
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
    # Кнопки.
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )


    # --------------------------------------------------------
    # Запуск.
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
