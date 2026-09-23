import os
import json
import asyncio
import threading
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
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))

CHANNEL = os.getenv("CHANNEL", "@RADAR_Kostroma")

PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = 60

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATE_FILE = os.path.join(BASE_DIR, "state.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")
SENT_POSTS_FILE = os.path.join(BASE_DIR, "sent_posts.json")


# =========================================================
# ИСТОЧНИКИ
# =========================================================

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


# =========================================================
# КОСТРОМСКАЯ ОБЛАСТЬ
# =========================================================

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

TERRITORIES = [
    "антроповский",
    "буйский",
    "вохомский",
    "галичский",
    "кадийский",
    "кологривский",
    "макарьевский",
    "мантуровский",
    "межевской",
    "нейский",
    "октябрьский",
    "островский",
    "павинский",
    "парфеньевский",
    "поназыревский",
    "пыщугский",
    "солигаличский",
    "сусанинский",
    "чухломский",
    "шарьинский",
]

REGION_WORDS = [
    "костромская область",
    "костромской области",
    "костромской обл",
    "костромская обл",
    "костромская",
]


# =========================================================
# РАКЕТНАЯ ОПАСНОСТЬ
# =========================================================

RED_WORDS = [
    "ракетная опасность",
    "ракетной опасности",
    "ракетная тревога",
    "опасность по ракетам",
    "угроза ракетного нападения",
    "ракетная угроза",
]

RED_CANCEL_WORDS = [
    "отбой ракетной опасности",
    "отбой ракетной тревоги",
    "ракетная опасность снята",
    "ракетная опасность отменена",
    "ракетная угроза снята",
    "ракетная угроза отменена",
    "отбой опасности по ракетам",
]


# =========================================================
# БПЛА — ОТБОЙ
# =========================================================

UAV_CANCEL_WORDS = [
    "отбой бпла",
    "отбой по бпла",
    "отбой беспилотной опасности",
    "отбой беспилотной угрозы",
    "отбой опасности бпла",
    "отбой угрозы бпла",
    "опасность бпла снята",
    "опасность по бпла снята",
    "угроза бпла снята",
    "угроза по бпла снята",
    "опасность беспилотников снята",
    "угроза беспилотников снята",
    "опасность беспилотника снята",
    "угроза беспилотника снята",
    "беспилотная опасность снята",
]


# =========================================================
# БПЛА — 3 УРОВНЯ
# =========================================================

# 🟡 ВНИМАНИЕ
UAV_ATTENTION_WORDS = [
    "внимание по бпла",
    "внимание бпла",
    "внимание беспилотник",
    "внимание беспилотники",
    "внимание по беспилотникам",
]


# 🟠 УГРОЗА
UAV_THREAT_WORDS = [
    "угроза по бпла",
    "угроза бпла",
    "угроза беспилотников",
    "угроза беспилотника",
    "угроза беспилотных",
    "угроза атаки бпла",
]


# 🔴 ОПАСНОСТЬ
UAV_DANGER_WORDS = [
    "опасность по бпла",
    "опасность бпла",
    "опасность беспилотников",
    "опасность беспилотника",
    "беспилотная опасность",
    "опасность беспилотной атаки",
]


# Дополнительные сообщения о фиксации БПЛА.
# Они определяются как "Внимание по БПЛА".

UAV_DETECTION_WORDS = [
    "фиксация бпла",
    "зафиксирован бпла",
    "зафиксированы бпла",
    "обнаружен бпла",
    "обнаружены бпла",
    "бпла замечен",
    "бпла замечены",
]


DRONE_WORDS = [
    "бпла",
    "беспилотник",
    "беспилотников",
    "беспилотника",
    "беспилотные",
    "дрон",
    "дроны",
]


GENERAL_CANCEL_WORDS = [
    "отбой опасности",
    "опасность снята",
    "опасность отменена",
    "угроза снята",
    "угроза отменена",
]


# =========================================================
# СОСТОЯНИЕ
# =========================================================

state = {
    "status": "green",
    "title": "Опасность не объявлена",

    # 0 = нет
    # 1 = внимание
    # 2 = угроза
    # 3 = опасность
    "uav_level": 0,

    "rocket_active": False,

    "location": "Костромская область",
    "city": "",
    "territory": "",

    "source": "",
    "source_url": "",

    "updated_at": "",

    "event_post_id": "",
    "event_source_id": "",
}


history = []
subscribers = []
sent_posts = []


# =========================================================
# ЗАГРУЗКА JSON
# =========================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"Ошибка загрузки {filename}: {e}"
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            f"Ошибка сохранения {filename}: {e}"
        )


def load_all_data():

    global state
    global history
    global subscribers
    global sent_posts

    loaded_state = load_json(
        STATE_FILE,
        {}
    )

    if isinstance(
        loaded_state,
        dict
    ):

        state.update(
            loaded_state
        )

    history = load_json(
        HISTORY_FILE,
        []
    )

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    sent_posts = load_json(
        SENT_POSTS_FILE,
        []
    )

    # Совместимость со старой версией.
    if "uav_level" not in state:

        old_status = state.get(
            "status",
            "green"
        )

        if old_status == "yellow":
            state["uav_level"] = 1

        else:
            state["uav_level"] = 0

    if "rocket_active" not in state:

        state["rocket_active"] = (
            state.get("status") == "red"
        )

    rebuild_state_status()


# =========================================================
# СОХРАНЕНИЕ
# =========================================================

def save_all():

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    save_json(
        SUBSCRIBERS_FILE,
        subscribers
    )

    save_json(
        SENT_POSTS_FILE,
        sent_posts
    )


# =========================================================
# НАЗВАНИЕ УРОВНЯ БПЛА
# =========================================================

def uav_level_name(level):

    if level == 1:
        return "🟡 Внимание по БПЛА"

    if level == 2:
        return "🟠 Угроза по БПЛА"

    if level == 3:
        return "🔴 Опасность по БПЛА"

    return ""


# =========================================================
# ПЕРЕСБОРКА СОСТОЯНИЯ
# =========================================================

def rebuild_state_status():

    global state

    uav_level = int(
        state.get(
            "uav_level",
            0
        )
    )

    rocket = bool(
        state.get(
            "rocket_active",
            False
        )
    )

    # Ракеты + БПЛА
    if rocket and uav_level > 0:

        state["status"] = "both"

        state["title"] = (
            "Ракетная опасность + "
            + uav_level_name(uav_level)
        )

        return

    # Только ракеты
    if rocket:

        state["status"] = "red"

        state["title"] = (
            "Ракетная опасность"
        )

        return

    # Только БПЛА
    if uav_level > 0:

        if uav_level == 1:

            state["status"] = "uav_attention"

        elif uav_level == 2:

            state["status"] = "uav_threat"

        else:

            state["status"] = "uav_danger"

        state["title"] = uav_level_name(
            uav_level
        )

        return

    # Ничего
    state["status"] = "green"

    state["title"] = (
        "Опасность не объявлена"
    )


# =========================================================
# ИСТОРИЯ
# =========================================================

def add_history(
    event_type,
    post
):

    item = {

        "time": datetime.now(
            timezone.utc
        ).isoformat(),

        "event_type": event_type,

        "status": state.get(
            "status",
            "green"
        ),

        "title": state.get(
            "title",
            ""
        ),

        "uav_level": state.get(
            "uav_level",
            0
        ),

        "rocket_active": state.get(
            "rocket_active",
            False
        ),

        "location": state.get(
            "location",
            ""
        ),

        "city": state.get(
            "city",
            ""
        ),

        "territory": state.get(
            "territory",
            ""
        ),

        "source": state.get(
            "source",
            ""
        ),

        "source_url": state.get(
            "source_url",
            ""
        ),

        "post_id": post.get(
            "post_id",
            ""
        ),
    }

    history.insert(
        0,
        item
    )

    del history[100:]

    save_json(
        HISTORY_FILE,
        history
    )


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def add_subscriber(user_id):

    user_id = int(user_id)

    if user_id not in subscribers:

        subscribers.append(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )


def remove_subscriber(user_id):

    user_id = int(user_id)

    if user_id in subscribers:

        subscribers.remove(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )


# =========================================================
# ОТПРАВЛЕННЫЕ ПОСТЫ
# =========================================================

def post_key(post):

    return (
        f"{post.get('source_id', '')}:"
        f"{post.get('post_id', '')}"
    )


def was_post_sent(post):

    return post_key(
        post
    ) in sent_posts


def mark_post_sent(post):

    key = post_key(
        post
    )

    if key not in sent_posts:

        sent_posts.append(
            key
        )

    sent_posts[:] = sent_posts[-1000:]

    save_json(
        SENT_POSTS_FILE,
        sent_posts
    )


# =========================================================
# ОПРЕДЕЛЕНИЕ СОБЫТИЯ
# =========================================================

def detect_status(text):

    if not text:
        return None

    text = (
        text
        .lower()
        .replace("ё", "е")
    )

    # -----------------------------------------------------
    # 1. ОТБОЙ БПЛА
    # -----------------------------------------------------

    for word in UAV_CANCEL_WORDS:

        if word in text:

            return "uav_cancel"

    # -----------------------------------------------------
    # 2. ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # -----------------------------------------------------

    for word in RED_CANCEL_WORDS:

        if word in text:

            return "red_cancel"

    # -----------------------------------------------------
    # 3. ОПАСНОСТЬ ПО БПЛА
    # Самый высокий уровень
    # -----------------------------------------------------

    for word in UAV_DANGER_WORDS:

        if word in text:

            return "uav_danger"

    # -----------------------------------------------------
    # 4. УГРОЗА ПО БПЛА
    # -----------------------------------------------------

    for word in UAV_THREAT_WORDS:

        if word in text:

            return "uav_threat"

    # -----------------------------------------------------
    # 5. ВНИМАНИЕ ПО БПЛА
    # -----------------------------------------------------

    for word in UAV_ATTENTION_WORDS:

        if word in text:

            return "uav_attention"

    # -----------------------------------------------------
    # 6. РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

    for word in RED_WORDS:

        if word in text:

            return "red"

    # -----------------------------------------------------
    # 7. ФИКСАЦИЯ БПЛА
    # -----------------------------------------------------

    for word in UAV_DETECTION_WORDS:

        if word in text:

            return "uav_attention"

    # -----------------------------------------------------
    # 8. БПЛА + ОПАСНОСТЬ/УГРОЗА
    # -----------------------------------------------------

    has_drone = any(
        word in text
        for word in DRONE_WORDS
    )

    danger_words = [
        "опасность",
        "угроза",
        "тревога",
        "внимание",
        "атака",
        "фиксация",
        "обнаружен",
        "обнаружены",
        "замечен",
        "замечены",
    ]

    has_danger = any(
        word in text
        for word in danger_words
    )

    if has_drone and has_danger:

        # Если есть именно слово
        # "опасность" — уровень 3.
        if "опасность" in text:

            return "uav_danger"

        # Если есть "угроза" — уровень 2.
        if "угроза" in text:

            return "uav_threat"

        return "uav_attention"

    # -----------------------------------------------------
    # 9. ОБЩИЙ ОТБОЙ
    # -----------------------------------------------------

    for word in GENERAL_CANCEL_WORDS:

        if word in text:

            return "general_cancel"

    return None


# =========================================================
# КОСТРОМСКАЯ ОБЛАСТЬ
# =========================================================

def is_kostroma(text):

    if not text:
        return False

    text = (
        text
        .lower()
        .replace("ё", "е")
    )

    for word in REGION_WORDS:

        if word in text:

            return True

    for city in CITIES:

        if city in text:

            return True

    for territory in TERRITORIES:

        if territory in text:

            return True

    return False


# =========================================================
# ОПРЕДЕЛЕНИЕ ГОРОДА
# =========================================================

def detect_city(text):

    if not text:
        return ""

    text = text.lower()

    for city in CITIES:

        if city in text:

            return city.capitalize()

    return ""


# =========================================================
# ОПРЕДЕЛЕНИЕ МУНИЦИПАЛЬНОГО ОКРУГА
# =========================================================

def detect_territory(text):

    if not text:
        return ""

    text = text.lower()

    for territory in TERRITORIES:

        if territory in text:

            return (
                territory.capitalize()
                + " муниципальный округ"
            )

    return ""


# =========================================================
# ПОЛУЧЕНИЕ ПОСТОВ TELEGRAM
# =========================================================

def get_telegram_posts(
    source_id,
    source
):

    posts = []

    try:

        response = requests.get(

            source["url"],

            timeout=20,

            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36"
            }
        )

        if response.status_code != 200:

            print(
                f"{source_id}: "
                f"HTTP {response.status_code}"
            )

            return posts

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        messages = soup.select(
            ".tgme_widget_message"
        )

        for message in messages:

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            if not text_element:

                continue

            text = text_element.get_text(
                " ",
                strip=True
            )

            post_data = message.get(
                "data-post",
                ""
            )

            if not post_data:

                continue

            post_id = (
                post_data
                .split("/")[-1]
            )

            time_element = message.select_one(
                "time"
            )

            if time_element:

                date_string = (
                    time_element.get(
                        "datetime",
                        ""
                    )
                )

            else:

                date_string = ""

            link = (
                "https://t.me/"
                + post_data
            )

            posts.append({

                "source_id":
                    source_id,

                "source_name":
                    source["name"],

                "source_url":
                    source["url"],

                "post_id":
                    post_id,

                "text":
                    text,

                "datetime":
                    date_string,

                "link":
                    link,
            })

    except Exception as e:

        print(
            f"Ошибка получения "
            f"{source_id}: {e}"
        )

    return posts


# =========================================================
# ДАТА
# =========================================================

def parse_datetime(value):

    if not value:

        return datetime.min.replace(
            tzinfo=timezone.utc
        )

    try:

        value = value.replace(
            "Z",
            "+00:00"
        )

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:

        return datetime.min.replace(
            tzinfo=timezone.utc
        )


# =========================================================
# СБОР СОБЫТИЙ
# =========================================================

def collect_events():

    events = []

    for source_id, source in SOURCES.items():

        posts = get_telegram_posts(
            source_id,
            source
        )

        for post in posts:

            text = post.get(
                "text",
                ""
            )

            if not is_kostroma(text):

                continue

            detected = detect_status(
                text
            )

            if not detected:

                continue

            post["detected_status"] = (
                detected
            )

            events.append(
                post
            )

    events.sort(
        key=lambda x:
            parse_datetime(
                x.get(
                    "datetime",
                    ""
                )
            )
    )

    return events


# =========================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# =========================================================

def apply_event(
    post,
    detected_status
):

    global state

    old_uav_level = int(
        state.get(
            "uav_level",
            0
        )
    )

    old_rocket = bool(
        state.get(
            "rocket_active",
            False
        )
    )

    # =====================================================
    # 🟡 ВНИМАНИЕ
    # =====================================================

    if detected_status == "uav_attention":

        # Не понижаем более высокий уровень
        # старым сообщением.

        state["uav_level"] = max(
            old_uav_level,
            1
        )

    # =====================================================
    # 🟠 УГРОЗА
    # =====================================================

    elif detected_status == "uav_threat":

        state["uav_level"] = max(
            old_uav_level,
            2
        )

    # =====================================================
    # 🔴 ОПАСНОСТЬ
    # =====================================================

    elif detected_status == "uav_danger":

        state["uav_level"] = max(
            old_uav_level,
            3
        )

    # =====================================================
    # ОТБОЙ БПЛА
    # =====================================================

    elif detected_status == "uav_cancel":

        state["uav_level"] = 0

    # =====================================================
    # РАКЕТНАЯ ОПАСНОСТЬ
    # =====================================================

    elif detected_status == "red":

        state["rocket_active"] = True

    # =====================================================
    # ОТБОЙ РАКЕТ
    # =====================================================

    elif detected_status == "red_cancel":

        state["rocket_active"] = False

    # =====================================================
    # ОБЩИЙ ОТБОЙ
    # =====================================================

    elif detected_status == "general_cancel":

        # Если активны и ракеты, и БПЛА,
        # общий отбой не угадываем.

        if (
            state.get("rocket_active", False)
            and
            state.get("uav_level", 0) > 0
        ):

            print(
                "Общий отбой при нескольких "
                "активных состояниях. "
                "Ждём конкретный отбой."
            )

            return False

        if state.get(
            "uav_level",
            0
        ) > 0:

            state["uav_level"] = 0

        elif state.get(
            "rocket_active",
            False
        ):

            state["rocket_active"] = False

        else:

            return False

    else:

        return False

    # =====================================================
    # ИНФОРМАЦИЯ О СОБЫТИИ
    # =====================================================

    state["location"] = (
        "Костромская область"
    )

    state["city"] = detect_city(
        post.get(
            "text",
            ""
        )
    )

    state["territory"] = detect_territory(
        post.get(
            "text",
            ""
        )
    )

    state["source"] = post.get(
        "source_name",
        ""
    )

    state["source_url"] = post.get(
        "link",
        ""
    )

    state["updated_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    state["event_post_id"] = (
        post.get(
            "post_id",
            ""
        )
    )

    state["event_source_id"] = (
        post.get(
            "source_id",
            ""
        )
    )

    rebuild_state_status()

    new_uav_level = int(
        state.get(
            "uav_level",
            0
        )
    )

    new_rocket = bool(
        state.get(
            "rocket_active",
            False
        )
    )

    # Проверяем, действительно ли изменилось состояние.

    if (
        old_uav_level == new_uav_level
        and
        old_rocket == new_rocket
    ):

        # Новое сообщение того же уровня
        # не должно создавать новый статус.

        return False

    return True


# =========================================================
# ФОРМАТ СТАТУСА
# =========================================================

def format_state():

    rebuild_state_status()

    lines = []

    lines.append(
        "🚨 <b>UAV ALERT</b>"
    )

    lines.append("")

    uav_level = int(
        state.get(
            "uav_level",
            0
        )
    )

    rocket = bool(
        state.get(
            "rocket_active",
            False
        )
    )

    # Ракеты
    if rocket:

        lines.append(
            "🚨 <b>Ракетная опасность</b>"
        )

    # БПЛА
    if uav_level > 0:

        lines.append(
            uav_level_name(
                uav_level
            )
        )

    # Ничего нет
    if (
        not rocket
        and
        uav_level == 0
    ):

        lines.append(
            "🟢 <b>Опасность не объявлена</b>"
        )

    lines.append("")

    lines.append(
        "📍 "
        + state.get(
            "location",
            "Костромская область"
        )
    )

    if state.get("city"):

        lines.append(
            "🏙 "
            + state["city"]
        )

    if state.get("territory"):

        lines.append(
            "🗺 "
            + state["territory"]
        )

    if state.get("source"):

        lines.append(
            "📡 Источник: "
            + state["source"]
        )

    return "\n".join(lines)


# =========================================================
# КЛАВИАТУРА
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🚨 Текущий статус",
                callback_data="status"
            )
        ],

        [
            InlineKeyboardButton(
                "📍 Костромская область",
                callback_data="location"
            )
        ],

        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="stats"
            )
        ],

        [
            InlineKeyboardButton(
                "📜 История",
                callback_data="history"
            )
        ],

        [
            InlineKeyboardButton(
                "📡 Источники",
                callback_data="sources"
            )
        ],

        [
            InlineKeyboardButton(
                "📢 Канал",
                callback_data="channel"
            )
        ],
    ])


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    add_subscriber(
        user_id
    )

    await update.message.reply_text(

        "🚨 UAV ALERT\n\n"

        "Гражданский информационный "
        "сервис по сообщениям об угрозах.\n\n"

        "🟢 Опасность не объявлена\n"
        "🟡 Внимание по БПЛА\n"
        "🟠 Угроза по БПЛА\n"
        "🔴 Опасность по БПЛА\n"
        "🚨 Ракетная опасность\n\n"

        "Статус БПЛА сохраняется "
        "до сообщения «Отбой по БПЛА».",

        reply_markup=main_keyboard()
    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        format_state(),

        parse_mode="HTML",

        reply_markup=main_keyboard()
    )


# =========================================================
# /STOP
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    remove_subscriber(
        user_id
    )

    await update.message.reply_text(
        "🔕 Вы отписались от уведомлений UAV ALERT."
    )


# =========================================================
# /STATS
# =========================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    rebuild_state_status()

    uav_level = int(
        state.get(
            "uav_level",
            0
        )
    )

    if uav_level == 1:

        uav_text = (
            "🟡 Внимание по БПЛА — АКТИВНО"
        )

    elif uav_level == 2:

        uav_text = (
            "🟠 Угроза по БПЛА — АКТИВНА"
        )

    elif uav_level == 3:

        uav_text = (
            "🔴 Опасность по БПЛА — АКТИВНА"
        )

    else:

        uav_text = (
            "🟢 БПЛА — опасность не объявлена"
        )

    if state.get(
        "rocket_active",
        False
    ):

        rocket_text = (
            "🚨 Ракетная опасность — АКТИВНА"
        )

    else:

        rocket_text = (
            "🟢 Ракетная опасность — "
            "не объявлена"
        )

    text = (

        "📊 <b>Статистика UAV ALERT</b>\n\n"

        f"{uav_text}\n"
        f"{rocket_text}\n\n"

        f"👥 Подписчиков: "
        f"{len(subscribers)}\n"

        f"📜 Записей истории: "
        f"{len(history)}\n\n"

        "ℹ️ Состояние БПЛА сохраняется "
        "до получения сообщения "
        "«Отбой по БПЛА»."
    )

    await update.message.reply_text(

        text,

        parse_mode="HTML",

        reply_markup=main_keyboard()
    )


# =========================================================
# /TEST
# =========================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )

        return

    await update.message.reply_text(
        "🧪 Тест UAV ALERT\n\n"
        "Бот работает."
    )


# =========================================================
# CALLBACK
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # =====================================================
    # STATUS
    # =====================================================

    if data == "status":

        await query.edit_message_text(

            format_state(),

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )

    # =====================================================
    # LOCATION
    # =====================================================

    elif data == "location":

        await query.edit_message_text(

            "📍 <b>Регион мониторинга</b>\n\n"

            "Костромская область\n\n"

            "Бот учитывает сообщения, "
            "содержащие название региона, "
            "городов или муниципальных округов.",

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )

    # =====================================================
    # STATS
    # =====================================================

    elif data == "stats":

        uav_level = int(
            state.get(
                "uav_level",
                0
            )
        )

        if uav_level == 1:

            uav = (
                "🟡 Внимание по БПЛА — АКТИВНО"
            )

        elif uav_level == 2:

            uav = (
                "🟠 Угроза по БПЛА — АКТИВНА"
            )

        elif uav_level == 3:

            uav = (
                "🔴 Опасность по БПЛА — АКТИВНА"
            )

        else:

            uav = (
                "🟢 БПЛА — не объявлено"
            )

        rocket = (

            "🚨 Ракетная опасность — АКТИВНА"

            if state.get(
                "rocket_active",
                False
            )

            else

            "🟢 Ракетная опасность — "
            "не объявлена"
        )

        text = (

            "📊 <b>Статистика</b>\n\n"

            f"{uav}\n"
            f"{rocket}\n\n"

            f"👥 Подписчиков: "
            f"{len(subscribers)}\n"

            f"📜 История: "
            f"{len(history)}"
        )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )

    # =====================================================
    # HISTORY
    # =====================================================

    elif data == "history":

        if not history:

            text = (
                "📜 <b>История</b>\n\n"
                "Событий пока нет."
            )

        else:

            items = []

            for item in history[:10]:

                event_type = item.get(
                    "event_type",
                    ""
                )

                if event_type == "uav_attention":

                    icon = "🟡"

                    name = (
                        "Внимание по БПЛА"
                    )

                elif event_type == "uav_threat":

                    icon = "🟠"

                    name = (
                        "Угроза по БПЛА"
                    )

                elif event_type == "uav_danger":

                    icon = "🔴"

                    name = (
                        "Опасность по БПЛА"
                    )

                elif event_type == "uav_cancel":

                    icon = "🟢"

                    name = (
                        "Отбой по БПЛА"
                    )

                elif event_type == "red":

                    icon = "🚨"

                    name = (
                        "Ракетная опасность"
                    )

                elif event_type == "red_cancel":

                    icon = "🟢"

                    name = (
                        "Отбой ракетной опасности"
                    )

                else:

                    icon = "ℹ️"

                    name = event_type

                items.append(

                    f"{icon} {name}\n"
                    f"📡 {item.get('source', '')}"
                )

            text = (

                "📜 <b>Последние события</b>\n\n"

                + "\n\n".join(items)
            )

        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )

    # =====================================================
    # SOURCES
    # =====================================================

    elif data == "sources":

        text = (
            "📡 <b>Источники мониторинга</b>\n\n"
        )

        for source in SOURCES.values():

            text += (

                f"{source['name']}\n"
                f"{source['url']}\n\n"
            )

        text += (

            "⚠️ Сообщения из сторонних "
            "источников требуют проверки. "
            "Официальные сообщения гражданских "
            "органов имеют приоритет."
        )

        await query.edit_message_text(

            text,

            reply_markup=main_keyboard()
        )

    # =====================================================
    # CHANNEL
    # =====================================================

    elif data == "channel":

        if CHANNEL.startswith("@"):

            channel_link = (
                "https://t.me/"
                + CHANNEL[1:]
            )

        else:

            channel_link = CHANNEL

        await query.edit_message_text(

            "📢 <b>Канал UAV ALERT</b>\n\n"
            f"{channel_link}",

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

async def notify_subscribers(
    application,
    text
):

    for user_id in list(
        subscribers
    ):

        try:

            await application.bot.send_message(

                chat_id=user_id,

                text=text,

                parse_mode="HTML"
            )

        except Exception as e:

            print(
                f"Ошибка отправки "
                f"{user_id}: {e}"
            )


# =========================================================
# ПУБЛИКАЦИЯ В КАНАЛ
# =========================================================

async def publish_channel(
    application,
    text
):

    try:

        await application.bot.send_message(

            chat_id=CHANNEL,

            text=text,

            parse_mode="HTML"
        )

    except Exception as e:

        print(
            f"Ошибка публикации в канал: {e}"
        )


# =========================================================
# ПЕРВИЧНОЕ ВОССТАНОВЛЕНИЕ
# =========================================================

async def initialize_from_sources():

    print(
        "🔎 Первичная проверка источников..."
    )

    events = await asyncio.to_thread(
        collect_events
    )

    if not events:

        print(
            "ℹ️ Подходящих событий не найдено."
        )

        return

    print(
        f"Найдено событий: {len(events)}"
    )

    for event in events:

        detected = event.get(
            "detected_status"
        )

        apply_event(
            event,
            detected
        )

        mark_post_sent(
            event
        )

    save_all()

    print(
        "✅ Состояние восстановлено:"
    )

    print(
        format_state()
    )


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitor_loop(
    application
):

    await initialize_from_sources()

    while True:

        try:

            events = await asyncio.to_thread(
                collect_events
            )

            for event in events:

                if was_post_sent(
                    event
                ):

                    continue

                detected = event.get(
                    "detected_status"
                )

                print(
                    "Новое событие: "
                    f"{detected} | "
                    f"{event.get('text', '')}"
                )

                changed = apply_event(

                    event,

                    detected
                )

                mark_post_sent(
                    event
                )

                if not changed:

                    continue

                save_all()

                add_history(

                    detected,

                    event
                )

                message = format_state()

                # Канал
                await publish_channel(

                    application,

                    message
                )

                # Личные уведомления
                await notify_subscribers(

                    application,

                    message
                )

        except Exception as e:

            print(
                f"Ошибка monitor_loop: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application
):

    await application.bot.set_my_commands([

        BotCommand(
            "start",
            "Запустить UAV ALERT"
        ),

        BotCommand(
            "status",
            "Текущий статус"
        ),

        BotCommand(
            "stats",
            "Статистика"
        ),

        BotCommand(
            "stop",
            "Отписаться"
        ),

        BotCommand(
            "test",
            "Тест бота"
        ),
    ])

    application.create_task(
        monitor_loop(
            application
        )
    )


# =========================================================
# FLASK
# =========================================================

app_web = Flask(__name__)


@app_web.route("/")
def index():

    return "UAV ALERT is running"


@app_web.route("/health")
def health():

    return {

        "status": "ok",

        "service": "UAV ALERT",

        "uav_level":
            state.get(
                "uav_level",
                0
            ),

        "rocket_active":
            state.get(
                "rocket_active",
                False
            ),
    }


def run_flask():

    app_web.run(

        host="0.0.0.0",

        port=PORT
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не установлен."
        )

    load_all_data()

    print(
        "================================="
    )

    print(
        "🚨 UAV ALERT запускается"
    )

    print(
        "📍 Костромская область"
    )

    print(
        f"👥 Подписчиков: "
        f"{len(subscribers)}"
    )

    print(
        f"🛩️ Уровень БПЛА: "
        f"{state.get('uav_level', 0)}"
    )

    print(
        f"🚨 Ракетная опасность: "
        f"{state.get('rocket_active', False)}"
    )

    print(
        "================================="
    )

    threading.Thread(

        target=run_flask,

        daemon=True

    ).start()

    application = (

        Application

        .builder()

        .token(BOT_TOKEN)

        .post_init(post_init)

        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats_command
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            stop_command
        )
    )

    application.add_handler(
        CommandHandler(
            "test",
            test_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    main()
