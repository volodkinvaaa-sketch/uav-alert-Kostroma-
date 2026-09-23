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
# НАСЕЛЁННЫЕ ПУНКТЫ КОСТРОМСКОЙ ОБЛАСТИ
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
# КЛЮЧЕВЫЕ СЛОВА
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


UAV_ALERT_WORDS = [
    "внимание по бпла",
    "внимание бпла",
    "внимание беспилотник",
    "внимание беспилотники",

    "угроза по бпла",
    "угроза бпла",
    "угроза беспилотников",
    "угроза беспилотника",

    "опасность по бпла",
    "опасность бпла",
    "опасность беспилотников",
    "опасность беспилотника",

    "беспилотная опасность",
    "опасность беспилотной атаки",

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

    # Главное:
    "uav_active": False,
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
# JSON
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"Ошибка загрузки {filename}: {e}")
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception as e:
        print(f"Ошибка сохранения {filename}: {e}")


def load_all_data():

    global state
    global history
    global subscribers
    global sent_posts

    loaded_state = load_json(STATE_FILE, {})

    if isinstance(loaded_state, dict):
        state.update(loaded_state)

    history = load_json(HISTORY_FILE, [])
    subscribers = load_json(SUBSCRIBERS_FILE, [])
    sent_posts = load_json(SENT_POSTS_FILE, [])

    # Совместимость со старым state.json
    if "uav_active" not in state:
        state["uav_active"] = state.get("status") == "yellow"

    if "rocket_active" not in state:
        state["rocket_active"] = state.get("status") == "red"

    rebuild_state_status()


# =========================================================
# СОХРАНЕНИЕ
# =========================================================

def save_all():

    save_json(STATE_FILE, state)
    save_json(HISTORY_FILE, history)
    save_json(SUBSCRIBERS_FILE, subscribers)
    save_json(SENT_POSTS_FILE, sent_posts)


# =========================================================
# СОСТОЯНИЕ
# =========================================================

def rebuild_state_status():

    global state

    uav = bool(state.get("uav_active", False))
    rocket = bool(state.get("rocket_active", False))

    if rocket and uav:
        state["status"] = "both"
        state["title"] = "Ракетная опасность + Внимание по БПЛА"

    elif rocket:
        state["status"] = "red"
        state["title"] = "Ракетная опасность"

    elif uav:
        state["status"] = "yellow"
        state["title"] = "Внимание по БПЛА"

    else:
        state["status"] = "green"
        state["title"] = "Опасность не объявлена"


# =========================================================
# ИСТОРИЯ
# =========================================================

def add_history(event_type, post):

    item = {
        "time": datetime.now(timezone.utc).isoformat(),

        "event_type": event_type,

        "status": state.get("status", "green"),

        "title": state.get("title", ""),

        "uav_active": state.get("uav_active", False),

        "rocket_active": state.get("rocket_active", False),

        "location": state.get("location", ""),

        "city": state.get("city", ""),

        "territory": state.get("territory", ""),

        "source": state.get("source", ""),

        "source_url": state.get("source_url", ""),

        "post_id": post.get("post_id", ""),
    }

    history.insert(0, item)

    del history[100:]

    save_json(HISTORY_FILE, history)


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def add_subscriber(user_id):

    user_id = int(user_id)

    if user_id not in subscribers:
        subscribers.append(user_id)

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )


def remove_subscriber(user_id):

    user_id = int(user_id)

    if user_id in subscribers:
        subscribers.remove(user_id)

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )


# =========================================================
# ОТПРАВЛЕННЫЕ ПОСТЫ
# =========================================================

def post_key(post):

    return f"{post.get('source_id', '')}:{post.get('post_id', '')}"


def was_post_sent(post):

    return post_key(post) in sent_posts


def mark_post_sent(post):

    key = post_key(post)

    if key not in sent_posts:
        sent_posts.append(key)

    del sent_posts[:-1000]

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

    text = text.lower().replace("ё", "е")

    # -----------------------------------------------------
    # СНАЧАЛА ОТБОЙ БПЛА
    # -----------------------------------------------------

    for word in UAV_CANCEL_WORDS:
        if word in text:
            return "uav_cancel"

    # -----------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # -----------------------------------------------------

    for word in RED_CANCEL_WORDS:
        if word in text:
            return "red_cancel"

    # -----------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

    for word in RED_WORDS:
        if word in text:
            return "red"

    # -----------------------------------------------------
    # ВНИМАНИЕ ПО БПЛА
    # -----------------------------------------------------

    for word in UAV_ALERT_WORDS:
        if word in text:
            return "yellow"

    # -----------------------------------------------------
    # БПЛА + СЛОВО ОПАСНОСТИ
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
        return "yellow"

    # -----------------------------------------------------
    # ОБЩИЙ ОТБОЙ
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

    text = text.lower().replace("ё", "е")

    # Регион напрямую
    for word in REGION_WORDS:
        if word in text:
            return True

    # Города
    for city in CITIES:
        if city in text:
            return True

    # Муниципальные округа
    for territory in TERRITORIES:
        if territory in text:
            return True

    return False


# =========================================================
# ГОРОД
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
# ТЕРРИТОРИЯ
# =========================================================

def detect_territory(text):

    if not text:
        return ""

    text = text.lower()

    for territory in TERRITORIES:

        if territory in text:
            return territory.capitalize() + " муниципальный округ"

    return ""


# =========================================================
# TELEGRAM SCRAPER
# =========================================================

def get_telegram_posts(source_id, source):

    posts = []

    try:

        response = requests.get(
            source["url"],
            timeout=20,

            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36"
                )
            }
        )

        if response.status_code != 200:

            print(
                f"{source_id}: HTTP {response.status_code}"
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

            post_id = post_data.split("/")[-1]

            time_element = message.select_one(
                "time"
            )

            if time_element:

                date_string = time_element.get(
                    "datetime",
                    ""
                )

            else:
                date_string = ""

            link = (
                "https://t.me/"
                + post_data
            )

            posts.append({
                "source_id": source_id,
                "source_name": source["name"],
                "source_url": source["url"],

                "post_id": post_id,

                "text": text,

                "datetime": date_string,

                "link": link,
            })

    except Exception as e:

        print(
            f"Ошибка получения {source_id}: {e}"
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
# ПОЛУЧЕНИЕ СОБЫТИЙ
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

            detected = detect_status(text)

            if not detected:
                continue

            post["detected_status"] = detected

            events.append(post)

    events.sort(
        key=lambda x: parse_datetime(
            x.get("datetime", "")
        )
    )

    return events


# =========================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# =========================================================

def apply_event(post, detected_status):

    global state

    # -----------------------------------------------------
    # ВНИМАНИЕ ПО БПЛА
    # -----------------------------------------------------

    if detected_status == "yellow":

        state["uav_active"] = True

    # -----------------------------------------------------
    # ОТБОЙ ПО БПЛА
    # -----------------------------------------------------

    elif detected_status == "uav_cancel":

        state["uav_active"] = False

    # -----------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

    elif detected_status == "red":

        state["rocket_active"] = True

    # -----------------------------------------------------
    # ОТБОЙ РАКЕТНОЙ ОПАСНОСТИ
    # -----------------------------------------------------

    elif detected_status == "red_cancel":

        state["rocket_active"] = False

    # -----------------------------------------------------
    # ОБЩИЙ ОТБОЙ
    # -----------------------------------------------------

    elif detected_status == "general_cancel":

        # Если активен только один тип —
        # снимаем его.

        if state.get("rocket_active", False) and not state.get(
            "uav_active",
            False
        ):

            state["rocket_active"] = False

        elif state.get("uav_active", False) and not state.get(
            "rocket_active",
            False
        ):

            state["uav_active"] = False

        else:

            # Если активны оба, общий отбой
            # не угадывает, что именно отменили.
            print(
                "Общий отбой при двух активных состояниях — "
                "ждём конкретный отбой."
            )

            return False

    else:

        return False

    # -----------------------------------------------------
    # ДАННЫЕ СОБЫТИЯ
    # -----------------------------------------------------

    state["location"] = "Костромская область"

    state["city"] = detect_city(
        post.get("text", "")
    )

    state["territory"] = detect_territory(
        post.get("text", "")
    )

    state["source"] = post.get(
        "source_name",
        ""
    )

    state["source_url"] = post.get(
        "link",
        ""
    )

    state["updated_at"] = datetime.now(
        timezone.utc
    ).isoformat()

    state["event_post_id"] = post.get(
        "post_id",
        ""
    )

    state["event_source_id"] = post.get(
        "source_id",
        ""
    )

    rebuild_state_status()

    return True


# =========================================================
# ФОРМАТ ТЕКУЩЕГО СТАТУСА
# =========================================================

def format_state():

    rebuild_state_status()

    lines = []

    lines.append(
        "🚨 <b>UAV ALERT</b>"
    )

    lines.append("")

    # Ракеты
    if state.get("rocket_active", False):

        lines.append(
            "🔴 <b>Ракетная опасность</b>"
        )

    # БПЛА
    if state.get("uav_active", False):

        lines.append(
            "🟡 <b>Внимание по БПЛА</b>"
        )

    # Ничего нет
    if not state.get(
        "rocket_active",
        False
    ) and not state.get(
        "uav_active",
        False
    ):

        lines.append(
            "🟢 <b>Опасность не объявлена</b>"
        )

    lines.append("")

    lines.append(
        f"📍 {state.get('location', 'Костромская область')}"
    )

    if state.get("city"):

        lines.append(
            f"🏙 {state['city']}"
        )

    if state.get("territory"):

        lines.append(
            f"🗺 {state['territory']}"
        )

    if state.get("source"):

        lines.append(
            f"📡 Источник: {state['source']}"
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
# /start
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    add_subscriber(user_id)

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Гражданский информационный сервис "
        "по сообщениям об угрозах.\n\n"
        "🟢 Опасность не объявлена\n"
        "🟡 Внимание по БПЛА\n"
        "🔴 Ракетная опасность\n\n"
        "Статус «Внимание по БПЛА» "
        "сохраняется до сообщения "
        "«Отбой по БПЛА».",
        reply_markup=main_keyboard()
    )


# =========================================================
# /status
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
# /stop
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    remove_subscriber(user_id)

    await update.message.reply_text(
        "🔕 Вы отписались от уведомлений UAV ALERT."
    )


# =========================================================
# /stats
# =========================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    rebuild_state_status()

    if state.get("uav_active"):

        uav_text = "🟡 Внимание по БПЛА — АКТИВНО"

    else:

        uav_text = "🟢 Внимание по БПЛА — не объявлено"

    if state.get("rocket_active"):

        rocket_text = "🔴 Ракетная опасность — АКТИВНА"

    else:

        rocket_text = "🟢 Ракетная опасность — не объявлена"

    text = (
        "📊 <b>Статистика UAV ALERT</b>\n\n"

        f"{uav_text}\n"
        f"{rocket_text}\n\n"

        f"👥 Подписчиков: {len(subscribers)}\n"
        f"📜 Записей истории: {len(history)}\n\n"

        "ℹ️ Состояние БПЛА сохраняется "
        "до получения сообщения об "
        "отбое по БПЛА."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# =========================================================
# /test
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

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if data == "status":

        await query.edit_message_text(
            format_state(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # -----------------------------------------------------
    # LOCATION
    # -----------------------------------------------------

    elif data == "location":

        await query.edit_message_text(
            "📍 <b>Регион мониторинга</b>\n\n"
            "Костромская область\n\n"
            "Бот учитывает сообщения, "
            "содержащие название региона, "
            "города или муниципального округа.",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    elif data == "stats":

        uav = (
            "🟡 АКТИВНО"
            if state.get("uav_active")
            else "🟢 Не объявлено"
        )

        rocket = (
            "🔴 АКТИВНО"
            if state.get("rocket_active")
            else "🟢 Не объявлено"
        )

        text = (
            "📊 <b>Статистика</b>\n\n"
            f"БПЛА: {uav}\n"
            f"Ракетная опасность: {rocket}\n\n"
            f"👥 Подписчиков: {len(subscribers)}\n"
            f"📜 История: {len(history)}"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

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

                if event_type == "yellow":

                    icon = "🟡"
                    name = "Внимание по БПЛА"

                elif event_type == "uav_cancel":

                    icon = "🟢"
                    name = "Отбой по БПЛА"

                elif event_type == "red":

                    icon = "🔴"
                    name = "Ракетная опасность"

                elif event_type == "red_cancel":

                    icon = "🟢"
                    name = "Отбой ракетной опасности"

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

    # -----------------------------------------------------
    # SOURCES
    # -----------------------------------------------------

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
            "⚠️ Сообщения из сторонних источников "
            "требуют проверки. Официальные сообщения "
            "гражданских органов имеют приоритет."
        )

        await query.edit_message_text(
            text,
            reply_markup=main_keyboard()
        )

    # -----------------------------------------------------
    # CHANNEL
    # -----------------------------------------------------

    elif data == "channel":

        channel_name = CHANNEL

        if channel_name.startswith("@"):

            channel_link = (
                "https://t.me/"
                + channel_name[1:]
            )

        else:

            channel_link = channel_name

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

    for user_id in list(subscribers):

        try:

            await application.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML"
            )

        except Exception as e:

            print(
                f"Ошибка отправки {user_id}: {e}"
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
# ИНИЦИАЛИЗАЦИЯ
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

    # Восстанавливаем состояние
    # по всей доступной истории источников.

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

                if was_post_sent(event):

                    continue

                detected = event.get(
                    "detected_status"
                )

                print(
                    f"Новое событие: "
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

                # История
                add_history(
                    detected,
                    event
                )

                message = format_state()

                # В канал
                await publish_channel(
                    application,
                    message
                )

                # Подписчикам
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
        "uav_active": state.get(
            "uav_active",
            False
        ),
        "rocket_active": state.get(
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
        f"👥 Подписчиков: {len(subscribers)}"
    )

    print(
        f"🟡 БПЛА активно: "
        f"{state.get('uav_active')}"
    )

    print(
        f"🔴 Ракетная опасность активно: "
        f"{state.get('rocket_active')}"
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


if __name__ == "__main__":
    main()
