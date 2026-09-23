import os
import re
import json
import time
import hashlib
import threading
import logging
import asyncio
from datetime import datetime

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
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))

PORT = int(os.getenv("PORT", "10000"))

CHANNEL = os.getenv("CHANNEL", "@RADAR_Kostroma")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

DEDUP_HOURS = int(os.getenv("DEDUP_HOURS", "24"))


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("UAV_ALERT")


# ============================================================
# ФАЙЛЫ
# ============================================================

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "state.json"
HISTORY_FILE = "history.json"
SENT_POSTS_FILE = "sent_posts.json"
NOTIFICATION_EVENTS_FILE = "notification_events.json"


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
# СЛОВА КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

REGION_WORDS = [
    "костромская область",
    "костромской области",
    "костромской обл",
    "костромская обл",
    "костромская",
]


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
# СОСТОЯНИЕ ПО УМОЛЧАНИЮ
# ============================================================

DEFAULT_STATE = {
    "status": "green",
    "title": "🟢 Опасность не объявлена",

    "uav_level": 0,
    "rocket_active": False,

    "location": "Костромская область",

    "districts": [],
    "cities": [],

    "source": "",
    "source_url": "",

    "updated_at": "",

    "event_post_id": "",
    "event_source_id": "",

    # Номер текущего цикла тревоги.
    # Нужен, чтобы после нового объявления
    # новый "Отбой" снова отправлялся.
    "uav_cycle": 0,
    "rocket_cycle": 0,

    # Что было активно непосредственно перед отбоем.
    "last_uav_level": 0,
    "last_uav_districts": [],
    "last_uav_cities": [],

    "last_rocket_active": False,
}


# ============================================================
# РАБОТА С JSON
# ============================================================

file_lock = threading.Lock()


def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        logger.error("Ошибка чтения %s: %s", filename, e)
        return default


def save_json(filename, data):
    try:
        with file_lock:
            temp_file = filename + ".tmp"

            with open(
                temp_file,
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(
                    data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            os.replace(temp_file, filename)

        return True

    except Exception as e:
        logger.error("Ошибка сохранения %s: %s", filename, e)
        return False


# ============================================================
# СОСТОЯНИЕ
# ============================================================

def load_state():
    state = load_json(STATE_FILE, {})

    result = DEFAULT_STATE.copy()
    result.update(state)

    return result


def save_state(state):
    save_json(STATE_FILE, state)


STATE = load_state()


# ============================================================
# ПОДПИСЧИКИ
# ============================================================

def load_subscribers():
    data = load_json(SUBSCRIBERS_FILE, [])

    if not isinstance(data, list):
        return []

    return data


def save_subscribers(subscribers):
    save_json(SUBSCRIBERS_FILE, subscribers)


# ============================================================
# ИСТОРИЯ
# ============================================================

def load_history():
    data = load_json(HISTORY_FILE, [])

    if not isinstance(data, list):
        return []

    return data


def save_history(history):
    save_json(HISTORY_FILE, history)


# ============================================================
# ВРЕМЯ
# ============================================================

def now_string():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def current_timestamp():
    return int(time.time())


# ============================================================
# ОЧИСТКА ТЕКСТА
# ============================================================

def normalize_text(text):
    if not text:
        return ""

    text = text.lower()

    text = text.replace("ё", "е")

    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# ОПРЕДЕЛЕНИЕ РАЙОНОВ
# ============================================================

def detect_districts(text):
    text = normalize_text(text)

    found = []

    for key, full_name in DISTRICTS.items():

        # Для Макарьева, Буйского и Галичского
        # обязательно требуем "район" или "р-н".
        #
        # Это предотвращает ошибку:
        # "город Макарьев" -> "Макарьевский район".

        if key in {
            "макарьевский",
            "буйский",
            "галичский",
        }:

            patterns = [
                rf"\b{re.escape(key)}\s+район\b",
                rf"\b{re.escape(key)}\s+р[\.\-]?\s*н\b",
            ]

        else:

            patterns = [
                rf"\b{re.escape(key)}\s+район\b",
                rf"\b{re.escape(key)}\s+р[\.\-]?\s*н\b",
                rf"\b{re.escape(key)}\b",
            ]

        matched = False

        for pattern in patterns:

            if re.search(pattern, text):
                matched = True
                break

        if matched and full_name not in found:
            found.append(full_name)

    return sorted(found)


# ============================================================
# ОПРЕДЕЛЕНИЕ ГОРОДОВ
# ============================================================

def detect_cities(text):
    text = normalize_text(text)

    found = []

    for city in CITIES:

        pattern = rf"\b{re.escape(city)}\b"

        if re.search(pattern, text):

            if city == "буй":
                name = "Буй"

            elif city == "галич":
                name = "Галич"

            elif city == "шарья":
                name = "Шарья"

            elif city == "макарьев":
                name = "Макарьев"

            elif city == "кологрив":
                name = "Кологрив"

            elif city == "нея":
                name = "Нея"

            elif city == "кострома":
                name = "Кострома"

            elif city == "волгореченск":
                name = "Волгореченск"

            elif city == "мантурово":
                name = "Мантурово"

            elif city == "нерехта":
                name = "Нерехта"

            elif city == "чухлома":
                name = "Чухлома"

            else:
                name = city.capitalize()

            if name not in found:
                found.append(name)

    return sorted(found)


# ============================================================
# ПРОВЕРКА ОТНОШЕНИЯ К КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

def is_kostroma_related(text):
    normalized = normalize_text(text)

    if any(word in normalized for word in REGION_WORDS):
        return True

    districts = detect_districts(normalized)

    if districts:
        return True

    cities = detect_cities(normalized)

    if cities:
        return True

    return False


# ============================================================
# ОПРЕДЕЛЕНИЕ СОБЫТИЯ
# ============================================================

def detect_event(text):
    normalized = normalize_text(text)

    # --------------------------------------------------------
    # ОТБОЙ БПЛА
    # --------------------------------------------------------

    uav_cancel_phrases = [
        "отбой по бпла",
        "отбой бпла",
        "угроза бпла отменена",
        "опасность бпла отменена",
        "угроза атаки бпла снята",
        "опасность атаки бпла снята",
        "угроза беспилотников отменена",
        "опасность беспилотников отменена",
    ]

    if any(
        phrase in normalized
        for phrase in uav_cancel_phrases
    ):
        return "uav_cancel", 0


    # --------------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # --------------------------------------------------------

    rocket_cancel_phrases = [
        "отбой ракетной опасности",
        "ракетная опасность отменена",
        "ракетная опасность снята",
        "отбой ракетной угрозы",
        "ракетная угроза отменена",
    ]

    if any(
        phrase in normalized
        for phrase in rocket_cancel_phrases
    ):
        return "rocket_cancel", 0


    # --------------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # --------------------------------------------------------

    rocket_phrases = [
        "ракетная опасность",
        "опасность ракетного удара",
        "ракетная угроза",
    ]

    if any(
        phrase in normalized
        for phrase in rocket_phrases
    ):
        return "rocket", 0


    # --------------------------------------------------------
    # ОПАСНОСТЬ БПЛА
    # --------------------------------------------------------

    danger_phrases = [
        "опасность по бпла",
        "опасность бпла",
        "высокая опасность бпла",
        "опасность беспилотников",
        "опасность атаки бпла",
    ]

    if any(
        phrase in normalized
        for phrase in danger_phrases
    ):
        return "uav", 3


    # --------------------------------------------------------
    # УГРОЗА БПЛА
    # --------------------------------------------------------

    threat_phrases = [
        "угроза по бпла",
        "угроза бпла",
        "угроза атаки бпла",
        "угроза беспилотников",
    ]

    if any(
        phrase in normalized
        for phrase in threat_phrases
    ):
        return "uav", 2


    # --------------------------------------------------------
    # ВНИМАНИЕ БПЛА
    # --------------------------------------------------------

    attention_phrases = [
        "внимание по бпла",
        "внимание бпла",
        "беспилотная опасность",
    ]

    if any(
        phrase in normalized
        for phrase in attention_phrases
    ):
        return "uav", 1


    return None, 0


# ============================================================
# FINGERPRINT ПОСТА
# ============================================================

def make_post_fingerprint(source_id, post_id, text):
    raw = (
        f"{source_id}|"
        f"{post_id}|"
        f"{text.strip()}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# FINGERPRINT СОБЫТИЯ
# ============================================================

def make_event_fingerprint(
    event_type,
    level=0,
    districts=None,
    cycle=0,
):
    districts = districts or []

    raw = (
        f"{event_type}|"
        f"{level}|"
        f"{cycle}|"
        f"{'|'.join(sorted(districts))}"
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


# ============================================================
# ДЕДУПЛИКАЦИЯ ПОСТОВ
# ============================================================

def cleanup_old_dict(data):
    now = current_timestamp()

    limit = DEDUP_HOURS * 3600

    result = {}

    for key, timestamp in data.items():

        try:
            timestamp = int(timestamp)

            if now - timestamp <= limit:
                result[key] = timestamp

        except Exception:
            pass

    return result


def is_post_processed(fingerprint):
    data = load_json(
        SENT_POSTS_FILE,
        {},
    )

    data = cleanup_old_dict(data)

    save_json(
        SENT_POSTS_FILE,
        data,
    )

    return fingerprint in data


def mark_post_processed(fingerprint):
    data = load_json(
        SENT_POSTS_FILE,
        {},
    )

    data = cleanup_old_dict(data)

    data[fingerprint] = current_timestamp()

    save_json(
        SENT_POSTS_FILE,
        data,
    )


# ============================================================
# ДЕДУПЛИКАЦИЯ УВЕДОМЛЕНИЙ
# ============================================================

def is_notification_sent(fingerprint):
    data = load_json(
        NOTIFICATION_EVENTS_FILE,
        {},
    )

    data = cleanup_old_dict(data)

    save_json(
        NOTIFICATION_EVENTS_FILE,
        data,
    )

    return fingerprint in data


def mark_notification_sent(fingerprint):
    data = load_json(
        NOTIFICATION_EVENTS_FILE,
        {},
    )

    data = cleanup_old_dict(data)

    data[fingerprint] = current_timestamp()

    save_json(
        NOTIFICATION_EVENTS_FILE,
        data,
    )


# ============================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# ============================================================

def apply_event(
    event_type,
    level,
    source_name,
    source_url,
    text,
):
    global STATE

    old_state = STATE.copy()

    districts = detect_districts(text)
    cities = detect_cities(text)

    now = now_string()

    # ========================================================
    # ОТБОЙ БПЛА
    # ========================================================

    if event_type == "uav_cancel":

        was_active = old_state["uav_level"] > 0

        previous_level = old_state["uav_level"]

        previous_districts = list(
            old_state.get("districts", [])
        )

        previous_cities = list(
            old_state.get("cities", [])
        )

        # Запоминаем информацию для push.
        STATE["last_uav_level"] = previous_level

        STATE["last_uav_districts"] = (
            previous_districts
        )

        STATE["last_uav_cities"] = (
            previous_cities
        )

        # Полностью снимаем тревогу.
        STATE["uav_level"] = 0

        STATE["districts"] = []

        STATE["cities"] = []

        STATE["status"] = "green"

        STATE["title"] = (
            "🟢 Опасность не объявлена"
        )

        STATE["source"] = source_name

        STATE["source_url"] = source_url

        STATE["updated_at"] = now

        save_state(STATE)

        # Отбой является отдельным событием
        # только если тревога действительно была.
        if not was_active:
            return (
                False,
                False,
                "",
                previous_level,
                previous_districts,
                previous_cities,
            )

        fingerprint = make_event_fingerprint(
            "uav_cancel",
            previous_level,
            previous_districts,
            old_state.get("uav_cycle", 0),
        )

        return (
            True,
            True,
            fingerprint,
            previous_level,
            previous_districts,
            previous_cities,
        )


    # ========================================================
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # ========================================================

    if event_type == "rocket_cancel":

        was_active = bool(
            old_state.get(
                "rocket_active",
                False,
            )
        )

        STATE["last_rocket_active"] = (
            was_active
        )

        STATE["rocket_active"] = False

        STATE["source"] = source_name

        STATE["source_url"] = source_url

        STATE["updated_at"] = now

        save_state(STATE)

        if not was_active:
            return (
                False,
                False,
                "",
                0,
                [],
                [],
            )

        fingerprint = make_event_fingerprint(
            "rocket_cancel",
            0,
            [],
            old_state.get(
                "rocket_cycle",
                0,
            ),
        )

        return (
            True,
            True,
            fingerprint,
            0,
            [],
            [],
        )


    # ========================================================
    # РАКЕТНАЯ ОПАСНОСТЬ
    # ========================================================

    if event_type == "rocket":

        changed = not old_state.get(
            "rocket_active",
            False,
        )

        if not old_state.get(
            "rocket_active",
            False,
        ):
            STATE["rocket_cycle"] = (
                int(
                    old_state.get(
                        "rocket_cycle",
                        0,
                    )
                )
                + 1
            )

        STATE["rocket_active"] = True

        STATE["source"] = source_name

        STATE["source_url"] = source_url

        STATE["updated_at"] = now

        save_state(STATE)

        fingerprint = make_event_fingerprint(
            "rocket",
            1,
            [],
            STATE["rocket_cycle"],
        )

        return (
            changed,
            True,
            fingerprint,
            0,
            [],
            [],
        )


    # ========================================================
    # БПЛА
    # ========================================================

    if event_type == "uav":

        old_level = int(
            old_state.get(
                "uav_level",
                0,
            )
        )

        old_districts = list(
            old_state.get(
                "districts",
                [],
            )
        )

        # Нельзя понизить уровень.
        new_level = max(
            old_level,
            level,
        )

        # Новый цикл начинается,
        # если до этого опасности не было.
        if old_level == 0:

            STATE["uav_cycle"] = (
                int(
                    old_state.get(
                        "uav_cycle",
                        0,
                    )
                )
                + 1
            )

        merged_districts = sorted(
            set(
                old_districts
                + districts
            )
        )

        merged_cities = sorted(
            set(
                old_state.get(
                    "cities",
                    [],
                )
                + cities
            )
        )

        changed = (
            new_level != old_level
            or merged_districts != old_districts
        )

        STATE["uav_level"] = new_level

        STATE["districts"] = (
            merged_districts
        )

        STATE["cities"] = (
            merged_cities
        )

        STATE["status"] = (
            UAV_LEVELS[new_level]["status"]
        )

        STATE["title"] = (
            UAV_LEVELS[new_level]["title"]
        )

        STATE["source"] = source_name

        STATE["source_url"] = source_url

        STATE["updated_at"] = now

        STATE["event_post_id"] = ""

        STATE["event_source_id"] = ""

        save_state(STATE)

        fingerprint = make_event_fingerprint(
            "uav",
            new_level,
            merged_districts,
            STATE["uav_cycle"],
        )

        return (
            changed,
            True,
            fingerprint,
            new_level,
            merged_districts,
            merged_cities,
        )


    return (
        False,
        False,
        "",
        0,
        [],
        [],
    )


# ============================================================
# ДОБАВЛЕНИЕ В ИСТОРИЮ
# ============================================================

def add_history(
    event_type,
    level,
    source_name,
    source_url,
    text,
    districts,
    cities,
):
    history = load_history()

    item = {
        "time": now_string(),
        "event_type": event_type,
        "level": level,
        "source": source_name,
        "source_url": source_url,
        "text": text[:2000],
        "districts": districts,
        "cities": cities,
    }

    history.insert(0, item)

    # Храним последние 300 событий.
    history = history[:300]

    save_history(history)


# ============================================================
# ФОРМИРОВАНИЕ PUSH
# ============================================================

def build_push_message(
    event_type,
    level,
    source_name,
    source_url,
    text,
    districts=None,
    cities=None,
    previous_level=0,
    previous_districts=None,
    previous_cities=None,
):
    districts = districts or []

    cities = cities or []

    previous_districts = (
        previous_districts or []
    )

    previous_cities = (
        previous_cities or []
    )


    # ========================================================
    # ОТБОЙ БПЛА
    # ========================================================

    if event_type == "uav_cancel":

        lines = [
            "🟢 <b>Отбой по БПЛА</b>",
            "",
        ]

        if previous_level in UAV_LEVELS:
            lines.append(
                "Ранее действовало:"
            )

            lines.append(
                UAV_LEVELS[
                    previous_level
                ]["title"]
            )

            lines.append("")

        if previous_districts:

            lines.append(
                "📍 <b>Ранее затронутые районы:</b>"
            )

            for district in previous_districts:
                lines.append(
                    f"• {district}"
                )

            lines.append("")

        if previous_cities:

            lines.append(
                "🏙 <b>Упомянутые города:</b>"
            )

            for city in previous_cities:
                lines.append(
                    f"• {city}"
                )

            lines.append("")

        lines.append(
            f"📡 <b>Источник:</b> "
            f"{source_name}"
        )

        return "\n".join(lines)


    # ========================================================
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # ========================================================

    if event_type == "rocket_cancel":

        return (
            "🟢 <b>Отбой ракетной опасности</b>\n"
            "\n"
            "Ракетная опасность отменена.\n"
            "\n"
            f"📡 <b>Источник:</b> "
            f"{source_name}"
        )


    # ========================================================
    # РАКЕТНАЯ ОПАСНОСТЬ
    # ========================================================

    if event_type == "rocket":

        return (
            "🚨 <b>Ракетная опасность</b>\n"
            "\n"
            "Объявлена ракетная опасность.\n"
            "\n"
            f"📡 <b>Источник:</b> "
            f"{source_name}"
        )


    # ========================================================
    # БПЛА
    # ========================================================

    if event_type == "uav":

        info = UAV_LEVELS.get(
            level,
            UAV_LEVELS[1],
        )

        lines = [
            info["title"],
            "",
        ]

        if districts:

            lines.append(
                "📍 <b>Районы:</b>"
            )

            for district in districts:
                lines.append(
                    f"• {district}"
                )

            lines.append("")

        if cities:

            lines.append(
                "🏙 <b>Упомянутые города:</b>"
            )

            for city in cities:
                lines.append(
                    f"• {city}"
                )

            lines.append("")

        lines.append(
            f"📡 <b>Источник:</b> "
            f"{source_name}"
        )

        return "\n".join(lines)


    return (
        "ℹ️ <b>UAV ALERT</b>\n\n"
        "Получено новое событие."
    )


# ============================================================
# ОТПРАВКА PUSH
# ============================================================

async def send_pushes(
    application,
    message,
):
    subscribers = load_subscribers()

    if not subscribers:
        logger.info(
            "Нет подписчиков для push."
        )
        return

    alive = []

    for user_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            alive.append(user_id)

            logger.info(
                "Push отправлен: %s",
                user_id,
            )

        except Exception as e:

            error_text = str(e).lower()

            # Пользователь заблокировал бота
            # или чат больше недоступен.
            if (
                "blocked" in error_text
                or "chat not found" in error_text
                or "deactivated" in error_text
            ):
                logger.info(
                    "Удаляем недоступного "
                    "подписчика: %s",
                    user_id,
                )

                continue

            alive.append(user_id)

            logger.error(
                "Ошибка push %s: %s",
                user_id,
                e,
            )

    save_subscribers(alive)


# ============================================================
# ОБРАБОТКА ПОСТА
# ============================================================

async def process_post(
    application,
    source_id,
    source_name,
    source_url,
    post_id,
    text,
):
    if not text:
        return

    event_type, level = detect_event(text)

    if not event_type:
        return


    # ========================================================
    # ПРОВЕРКА КОСТРОМСКОЙ ОБЛАСТИ
    # ========================================================

    if event_type in {
        "uav",
        "uav_cancel",
    }:

        if not is_kostroma_related(text):

            logger.info(
                "Пропуск: событие БПЛА "
                "не относится к Костромской области."
            )

            return


    # Ракетную опасность оставляем отдельно.
    # Она может быть опубликована без
    # конкретного района в тексте.


    # ========================================================
    # ДЕДУПЛИКАЦИЯ САМОГО ПОСТА
    # ========================================================

    post_fingerprint = make_post_fingerprint(
        source_id,
        post_id,
        text,
    )

    if is_post_processed(
        post_fingerprint
    ):
        return

    mark_post_processed(
        post_fingerprint
    )


    # ========================================================
    # СОХРАНЯЕМ ТЕКУЩИЕ ДАННЫЕ
    # ========================================================

    districts_before = detect_districts(
        text
    )

    cities_before = detect_cities(
        text
    )


    # ========================================================
    # ПРИМЕНЯЕМ СОБЫТИЕ
    # ========================================================

    (
        changed,
        should_notify,
        event_fingerprint,
        effective_level,
        effective_districts,
        effective_cities,
    ) = apply_event(
        event_type,
        level,
        source_name,
        source_url,
        text,
    )


    # ========================================================
    # ИСТОРИЯ
    # ========================================================

    add_history(
        event_type,
        effective_level,
        source_name,
        source_url,
        text,
        effective_districts,
        effective_cities,
    )


    # ========================================================
    # НЕ НАДО ОТПРАВЛЯТЬ PUSH
    # ========================================================

    if not should_notify:
        return


    # ========================================================
    # ДЕДУПЛИКАЦИЯ УВЕДОМЛЕНИЯ
    # ========================================================

    if is_notification_sent(
        event_fingerprint
    ):

        logger.info(
            "Push уже отправлялся: %s",
            event_fingerprint,
        )

        return


    mark_notification_sent(
        event_fingerprint
    )


    # ========================================================
    # ФОРМИРУЕМ PUSH
    # ========================================================

    message = build_push_message(
        event_type=event_type,
        level=effective_level,
        source_name=source_name,
        source_url=source_url,
        text=text,
        districts=effective_districts,
        cities=effective_cities,
        previous_level=effective_level,
        previous_districts=effective_districts,
        previous_cities=effective_cities,
    )


    # ========================================================
    # PUSH
    # ========================================================

    await send_pushes(
        application,
        message,
    )

    logger.info(
        "Новое уведомление: %s | %s",
        event_type,
        message.replace("\n", " | "),
    )


# ============================================================
# ПОЛУЧЕНИЕ ПОСТОВ TELEGRAM-КАНАЛА
# ============================================================

def fetch_source_posts(source_url):
    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140.0 Safari/537.36"
            )
        }

        response = requests.get(
            source_url,
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
            "div.tgme_widget_message"
        )

        for message in messages:

            data_post = message.get(
                "data-post",
                "",
            )

            if not data_post:
                continue

            post_id = data_post.split("/")[-1]

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            if text_element:

                text = text_element.get_text(
                    " ",
                    strip=True,
                )

            else:
                text = ""

            if not text:
                continue

            result.append(
                {
                    "id": post_id,
                    "text": text,
                }
            )

        return result[-30:]

    except Exception as e:

        logger.error(
            "Ошибка получения %s: %s",
            source_url,
            e,
        )

        return []


# ============================================================
# МОНИТОРИНГ ИСТОЧНИКОВ
# ============================================================

async def monitor_sources(
    application,
):
    logger.info(
        "Мониторинг источников запущен."
    )

    while True:

        try:

            for source_id, source in SOURCES.items():

                posts = fetch_source_posts(
                    source["url"]
                )

                if not posts:
                    continue

                for post in posts:

                    try:

                        await process_post(
                            application=application,
                            source_id=source_id,
                            source_name=source["name"],
                            source_url=source["url"],
                            post_id=post["id"],
                            text=post["text"],
                        )

                    except Exception as e:

                        logger.exception(
                            "Ошибка обработки поста: %s",
                            e,
                        )

        except Exception as e:

            logger.exception(
                "Ошибка цикла мониторинга: %s",
                e,
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# КНОПКИ
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📍 Костромская область",
                    callback_data="status",
                )
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
                    "📊 Статистика",
                    callback_data="stats",
                )
            ],
        ]
    )


# ============================================================
# ФОРМАТ ТЕКУЩЕГО СТАТУСА
# ============================================================

def get_status_text():
    state = load_state()

    lines = [
        "<b>UAV ALERT</b>",
        "",
        f"{state['title']}",
        "",
    ]


    if state.get("rocket_active"):

        lines.append(
            "🚨 <b>Ракетная опасность активна</b>"
        )

        lines.append("")


    if state.get("uav_level", 0) > 0:

        level = state["uav_level"]

        lines.append(
            UAV_LEVELS[level]["title"]
        )

        lines.append("")


        if state.get("districts"):

            lines.append(
                "📍 <b>Районы:</b>"
            )

            for district in state["districts"]:

                lines.append(
                    f"• {district}"
                )

            lines.append("")


        if state.get("cities"):

            lines.append(
                "🏙 <b>Упомянутые города:</b>"
            )

            for city in state["cities"]:

                lines.append(
                    f"• {city}"
                )

            lines.append("")


    if (
        state.get("uav_level", 0) == 0
        and not state.get("rocket_active")
    ):

        lines.append(
            "Сейчас активных угроз "
            "в сохранённом состоянии нет."
        )

        lines.append("")


    if state.get("source"):

        lines.append(
            f"📡 <b>Источник:</b> "
            f"{state['source']}"
        )

    if state.get("updated_at"):

        lines.append(
            f"🕐 <b>Обновлено:</b> "
            f"{state['updated_at']}"
        )

    return "\n".join(lines)


# ============================================================
# /START
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    subscribed = (
        str(user_id) in
        [str(x) for x in subscribers]
    )

    if subscribed:
        button_text = "🔕 Отписаться"
    else:
        button_text = "🔔 Подписаться"

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📍 Статус Костромской области",
                    callback_data="status",
                )
            ],
            [
                InlineKeyboardButton(
                    button_text,
                    callback_data=(
                        "unsubscribe"
                        if subscribed
                        else "subscribe"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="stats",
                )
            ],
        ]
    )

    await update.message.reply_text(
        "<b>UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "оперативных уведомлений.\n\n"
        "📍 Регион: Костромская область\n\n"
        "Бот отслеживает сообщения источников "
        "и отправляет уведомления об изменении "
        "статуса.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /STATUS
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        get_status_text(),
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /SUBSCRIBE
# ============================================================

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    normalized = [
        str(x)
        for x in subscribers
    ]

    if str(user_id) not in normalized:

        subscribers.append(
            user_id
        )

        save_subscribers(
            subscribers
        )

        text = (
            "🔔 <b>Подписка включена</b>\n\n"
            "Теперь вы будете получать "
            "уведомления UAV ALERT по "
            "Костромской области."
        )

    else:

        text = (
            "🔔 Вы уже подписаны "
            "на уведомления."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /UNSUBSCRIBE
# ============================================================

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    subscribers = [
        x
        for x in subscribers
        if str(x) != str(user_id)
    ]

    save_subscribers(
        subscribers
    )

    await update.message.reply_text(
        "🔕 <b>Подписка отключена.</b>",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# /STATS
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    subscribers = load_subscribers()

    history = load_history()

    state = load_state()

    text = (
        "<b>📊 Статистика UAV ALERT</b>\n\n"
        f"👥 Подписчиков: "
        f"{len(subscribers)}\n"
        f"📰 Событий в истории: "
        f"{len(history)}\n\n"
        f"📍 Регион: "
        f"Костромская область\n\n"
        f"🚨 Текущий статус:\n"
        f"{state['title']}"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# CALLBACK КНОПОК
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if query.data == "status":

        await query.message.reply_text(
            get_status_text(),
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # SUBSCRIBE
    # --------------------------------------------------------

    if query.data == "subscribe":

        subscribers = load_subscribers()

        if str(user_id) not in [
            str(x)
            for x in subscribers
        ]:

            subscribers.append(
                user_id
            )

            save_subscribers(
                subscribers
            )

        await query.message.reply_text(
            "🔔 <b>Вы подписались "
            "на уведомления UAV ALERT.</b>",
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # UNSUBSCRIBE
    # --------------------------------------------------------

    if query.data == "unsubscribe":

        subscribers = load_subscribers()

        subscribers = [
            x
            for x in subscribers
            if str(x) != str(user_id)
        ]

        save_subscribers(
            subscribers
        )

        await query.message.reply_text(
            "🔕 <b>Вы отписались "
            "от уведомлений.</b>",
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    if query.data == "stats":

        subscribers = load_subscribers()

        history = load_history()

        await query.message.reply_text(
            "<b>📊 Статистика</b>\n\n"
            f"👥 Подписчиков: "
            f"{len(subscribers)}\n"
            f"📰 Событий в истории: "
            f"{len(history)}",
            parse_mode=ParseMode.HTML,
        )

        return


# ============================================================
# ADMIN /TEST
# ============================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    message = (
        "🧪 <b>Тестовое уведомление UAV ALERT</b>\n\n"
        "Если вы получили это сообщение, "
        "push-уведомления работают."
    )

    await send_pushes(
        context.application,
        message,
    )


# ============================================================
# ADMIN /TESTUAV
# ============================================================

async def test_uav_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    test_message = (
        "🔴 <b>Опасность по БПЛА</b>\n\n"
        "📍 <b>Тестовые районы:</b>\n"
        "• Нейский район\n"
        "• Антроповский район\n"
        "• Парфеньевский район\n"
        "• Кологривский район\n"
        "• Кадыйский район\n"
        "• Макарьевский район\n"
        "• Шарьинский район\n"
        "• Межевский район\n\n"
        "📡 <b>Источник:</b> ТЕСТ UAV ALERT"
    )

    await send_pushes(
        context.application,
        test_message,
    )


# ============================================================
# ADMIN /TESTCANCEL
# ============================================================

async def test_cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    test_message = (
        "🟢 <b>Отбой по БПЛА</b>\n\n"
        "Ранее действовала:\n"
        "🔴 Опасность по БПЛА\n\n"
        "📍 <b>Ранее затронутые районы:</b>\n"
        "• Нейский район\n"
        "• Антроповский район\n"
        "• Парфеньевский район\n"
        "• Кологривский район\n"
        "• Кадыйский район\n"
        "• Макарьевский район\n"
        "• Шарьинский район\n"
        "• Межевский район\n\n"
        "📡 <b>Источник:</b> ТЕСТ UAV ALERT"
    )

    await send_pushes(
        context.application,
        test_message,
    )


# ============================================================
# ADMIN /TESTROCKET
# ============================================================

async def test_rocket_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    message = (
        "🚨 <b>Ракетная опасность</b>\n\n"
        "Объявлена ракетная опасность.\n\n"
        "📡 <b>Источник:</b> ТЕСТ UAV ALERT"
    )

    await send_pushes(
        context.application,
        message,
    )


# ============================================================
# ADMIN /TESTROCKETCANCEL
# ============================================================

async def test_rocket_cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.effective_user.id != ADMIN_ID:
        return

    message = (
        "🟢 <b>Отбой ракетной опасности</b>\n\n"
        "Ракетная опасность отменена.\n\n"
        "📡 <b>Источник:</b> ТЕСТ UAV ALERT"
    )

    await send_pushes(
        context.application,
        message,
    )


# ============================================================
# FLASK
# ============================================================

app_web = Flask(__name__)


@app_web.route("/")
def index():
    state = load_state()

    return jsonify(
        {
            "service": "UAV ALERT",
            "status": "online",
            "region": "Костромская область",
            "uav_level": state.get(
                "uav_level",
                0,
            ),
            "rocket_active": state.get(
                "rocket_active",
                False,
            ),
            "updated_at": state.get(
                "updated_at",
                "",
            ),
        }
    )


@app_web.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": "UAV ALERT",
        }
    )


# ============================================================
# FLASK SERVER
# ============================================================

def run_web_server():
    app_web.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application: Application,
):
    commands = [
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
            "stats",
            "Статистика",
        ),
    ]

    # Админские команды также доступны,
    # но обработчик проверяет ADMIN_ID.

    commands.extend(
        [
            BotCommand(
                "test",
                "Тест push",
            ),
            BotCommand(
                "testuav",
                "Тест БПЛА",
            ),
            BotCommand(
                "testcancel",
                "Тест отбоя БПЛА",
            ),
            BotCommand(
                "testrocket",
                "Тест ракетной опасности",
            ),
            BotCommand(
                "testrocketcancel",
                "Тест отбоя ракетной опасности",
            ),
        ]
    )

    await application.bot.set_my_commands(
        commands
    )

    # Запускаем мониторинг.
    asyncio.create_task(
        monitor_sources(
            application
        )
    )

    logger.info(
        "Мониторинг UAV ALERT запущен."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "Не найден BOT_TOKEN "
            "в переменных окружения Render."
        )


    # --------------------------------------------------------
    # Flask
    # --------------------------------------------------------

    web_thread = threading.Thread(
        target=run_web_server,
        daemon=True,
    )

    web_thread.start()


    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )


    # --------------------------------------------------------
    # Команды
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
            "stats",
            stats_command,
        )
    )


    # --------------------------------------------------------
    # Админские тесты
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "test",
            test_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testuav",
            test_uav_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testcancel",
            test_cancel_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testrocket",
            test_rocket_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testrocketcancel",
            test_rocket_cancel_command,
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
        "========================================"
    )

    logger.info(
        "UAV ALERT запускается..."
    )

    logger.info(
        "Регион: Костромская область"
    )

    logger.info(
        "Источников: %s",
        len(SOURCES),
    )

    logger.info(
        "Интервал проверки: %s сек.",
        CHECK_INTERVAL,
    )

    logger.info(
        "========================================"
    )


    # run_polling сам корректно
    # управляет event loop.
    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    main()
