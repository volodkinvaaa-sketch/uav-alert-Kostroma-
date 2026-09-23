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

CHANNEL = os.getenv(
    "CHANNEL",
    "@RADAR_Kostroma"
)

PORT = int(os.getenv("PORT", "10000"))

CHECK_INTERVAL = 60

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

STATE_FILE = os.path.join(
    BASE_DIR,
    "state.json"
)

HISTORY_FILE = os.path.join(
    BASE_DIR,
    "history.json"
)

SUBSCRIBERS_FILE = os.path.join(
    BASE_DIR,
    "subscribers.json"
)

SENT_POSTS_FILE = os.path.join(
    BASE_DIR,
    "sent_posts.json"
)

USER_LOCATIONS_FILE = os.path.join(
    BASE_DIR,
    "user_locations.json"
)


# =========================================================
# ИСТОЧНИКИ
# =========================================================

SOURCES = {
    "locator": {
        "name": "📡 Локатор России",
        "url": "https://t.me/s/locatorru",
        "type": "secondary",
    },

    "monitoring": {
        "name": "🛰️ Мониторинг БПЛА",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
        "type": "secondary",
    },

    "radar": {
        "name": "📡 Радар Россия",
        "url": "https://t.me/s/radarrussiia",
        "type": "secondary",
    },

    "bpla": {
        "name": "📢 БПЛА Россия",
        "url": "https://t.me/s/bplarussiaru",
        "type": "secondary",
    },
}


# =========================================================
# ГОРОДА
# =========================================================

CITIES = [
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
]


# =========================================================
# МУНИЦИПАЛЬНЫЕ ОКРУГА
# =========================================================

TERRITORIES = [
    "Антроповский муниципальный округ",
    "Буйский муниципальный округ",
    "Вохомский муниципальный округ",
    "Галичский муниципальный округ",
    "Кадыйский муниципальный округ",
    "Кологривский муниципальный округ",
    "Макарьевский муниципальный округ",
    "Мантуровский муниципальный округ",
    "Межевской муниципальный округ",
    "Нейский муниципальный округ",
    "Октябрьский муниципальный округ",
    "Островский муниципальный округ",
    "Павинский муниципальный округ",
    "Парфеньевский муниципальный округ",
    "Поназыревский муниципальный округ",
    "Пыщугский муниципальный округ",
    "Солигаличский муниципальный округ",
    "Сусанинский муниципальный округ",
    "Чухломский муниципальный округ",
    "Шарьинский муниципальный округ",
]


# =========================================================
# КЛЮЧЕВЫЕ СЛОВА
# =========================================================

# РАКЕТНАЯ ОПАСНОСТЬ
RED_WORDS = [
    "ракетная опасность",
    "ракетной опасности",
    "ракетная тревога",
    "опасность по ракетам",
    "угроза ракетного нападения",
    "ракетная угроза",
]


# ОТБОЙ БПЛА
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
]


# ОБЩИЙ ОТБОЙ ОПАСНОСТИ
GENERAL_CANCEL_WORDS = [
    "отбой опасности",
    "опасность снята",
    "опасность отменена",
    "угроза снята",
    "угроза отменена",
]


# ПРЯМЫЕ ФРАЗЫ ТРЕВОГИ БПЛА
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
]


# Слова, которые сами по себе тревогу НЕ создают.
DRONE_WORDS = [
    "бпла",
    "беспилотник",
    "беспилотников",
    "беспилотника",
    "беспилотные",
    "дрон",
    "дроны",
]


# =========================================================
# ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# =========================================================

state = {
    "status": "green",
    "title": "Опасность не объявлена",
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

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        return data

    except Exception as error:

        print(
            f"Ошибка чтения {filename}:",
            error
        )

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )

    except Exception as error:

        print(
            f"Ошибка сохранения {filename}:",
            error
        )


def load_all_data():

    global state
    global history
    global subscribers
    global sent_posts

    state = load_json(
        STATE_FILE,
        state
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

    if not isinstance(
        history,
        list
    ):
        history = []

    if not isinstance(
        subscribers,
        list
    ):
        subscribers = []

    if not isinstance(
        sent_posts,
        list
    ):
        sent_posts = []


# =========================================================
# ИСТОРИЯ
# =========================================================

def add_history(new_state):

    global history

    item = {
        "status": new_state.get(
            "status",
            "green"
        ),

        "title": new_state.get(
            "title",
            ""
        ),

        "location": new_state.get(
            "location",
            ""
        ),

        "city": new_state.get(
            "city",
            ""
        ),

        "territory": new_state.get(
            "territory",
            ""
        ),

        "source": new_state.get(
            "source",
            ""
        ),

        "source_url": new_state.get(
            "source_url",
            ""
        ),

        "updated_at": new_state.get(
            "updated_at",
            ""
        ),

        "event_post_id": new_state.get(
            "event_post_id",
            ""
        ),

        "event_source_id": new_state.get(
            "event_source_id",
            ""
        ),
    }

    history.append(item)

    history = history[-100:]

    save_json(
        HISTORY_FILE,
        history
    )


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def add_subscriber(user_id):

    global subscribers

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

    global subscribers

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
# ЗАЩИТА ОТ ПОВТОРОВ
# =========================================================

def make_post_key(
    source_id,
    post_id
):

    if not source_id or not post_id:
        return ""

    return (
        f"{source_id}:"
        f"{post_id}"
    )


def was_post_sent(
    source_id,
    post_id
):

    key = make_post_key(
        source_id,
        post_id
    )

    if not key:
        return False

    return key in sent_posts


def mark_post_sent(
    source_id,
    post_id
):

    global sent_posts

    key = make_post_key(
        source_id,
        post_id
    )

    if not key:
        return

    if key not in sent_posts:

        sent_posts.append(
            key
        )

    sent_posts = sent_posts[-1000:]

    save_json(
        SENT_POSTS_FILE,
        sent_posts
    )


# =========================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА СООБЩЕНИЯ
# =========================================================

def detect_status(text):

    text_lower = text.lower()

    # -----------------------------------------------------
    # 1. РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

    for word in RED_WORDS:

        if word in text_lower:
            return "red"


    # -----------------------------------------------------
    # 2. ОТБОЙ БПЛА
    # -----------------------------------------------------

    for word in UAV_CANCEL_WORDS:

        if word in text_lower:
            return "uav_cancel"


    # -----------------------------------------------------
    # 3. ПРЯМАЯ ТРЕВОГА БПЛА
    # -----------------------------------------------------

    for word in UAV_ALERT_WORDS:

        if word in text_lower:
            return "yellow"


    # -----------------------------------------------------
    # 4. НЕЯВНАЯ ФОРМУЛИРОВКА:
    # БПЛА + ОПАСНОСТЬ / УГРОЗА / ТРЕВОГА
    # -----------------------------------------------------

    has_drone = any(
        word in text_lower
        for word in DRONE_WORDS
    )

    danger_words = [
        "опасность",
        "угроза",
        "тревога",
    ]

    has_danger = any(
        word in text_lower
        for word in danger_words
    )

    if has_drone and has_danger:

        return "yellow"


    # -----------------------------------------------------
    # 5. ОБЩИЙ ОТБОЙ
    # -----------------------------------------------------

    for word in GENERAL_CANCEL_WORDS:

        if word in text_lower:

            # Общий отбой сам по себе не считаем
            # именно отбоем БПЛА.
            #
            # Это сделано для того, чтобы обычное
            # "отбой тревоги" случайно не сбрасывало
            # статус БПЛА.

            return None


    return None


# =========================================================
# КОСТРОМСКАЯ ОБЛАСТЬ
# =========================================================

def is_kostroma(text):

    text_lower = text.lower()

    words = [
        "кострома",
        "костромская область",
        "костромской области",
        "костромская обл",
        "костромской обл",
    ]

    return any(
        word in text_lower
        for word in words
    )


# =========================================================
# ГОРОД
# =========================================================

def detect_city(text):

    text_lower = text.lower()

    for city in CITIES:

        if city.lower() in text_lower:

            return city

    return ""


# =========================================================
# МУНИЦИПАЛЬНЫЙ ОКРУГ
# =========================================================

def detect_territory(text):

    text_lower = text.lower()

    for territory in TERRITORIES:

        if territory.lower() in text_lower:

            return territory

    return ""


# =========================================================
# TELEGRAM SCRAPER
# =========================================================

def get_telegram_posts(
    source_id,
    source
):

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140.0 Safari/537.36"
        )
    }

    try:

        response = requests.get(
            source["url"],
            headers=headers,
            timeout=20
        )

        response.raise_for_status()

    except Exception as error:

        print(
            f"Ошибка загрузки "
            f"{source_id}:",
            error
        )

        return []


    try:

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        elements = soup.select(
            ".tgme_widget_message"
        )

        posts = []

        for element in elements:

            text_element = element.select_one(
                ".tgme_widget_message_text"
            )

            if text_element:

                text = text_element.get_text(
                    "\n",
                    strip=True
                )

            else:

                text = ""


            data_post = element.get(
                "data-post",
                ""
            )

            post_id = ""

            if "/" in data_post:

                post_id = (
                    data_post
                    .split("/")[-1]
                )


            date_element = element.select_one(
                ".tgme_widget_message_date time"
            )

            published_at = ""

            if date_element:

                published_at = date_element.get(
                    "datetime",
                    ""
                )


            link = ""

            if data_post:

                link = (
                    "https://t.me/"
                    + data_post
                )


            if text:

                posts.append({

                    "source_id":
                        source_id,

                    "source_name":
                        source["name"],

                    "source_url":
                        source["url"],

                    "source_type":
                        source.get(
                            "type",
                            "secondary"
                        ),

                    "post_id":
                        post_id,

                    "post_key":
                        make_post_key(
                            source_id,
                            post_id
                        ),

                    "text":
                        text,

                    "url":
                        link,

                    "published_at":
                        published_at,
                })


        return posts

    except Exception as error:

        print(
            f"Ошибка обработки "
            f"{source_id}:",
            error
        )

        return []


# =========================================================
# DATETIME
# =========================================================

def parse_datetime(value):

    if not value:
        return None

    try:

        value = value.strip()

        if value.endswith("Z"):

            value = (
                value[:-1]
                + "+00:00"
            )

        result = datetime.fromisoformat(
            value
        )

        if result.tzinfo is None:

            result = result.replace(
                tzinfo=timezone.utc
            )

        return result

    except Exception:

        return None


# =========================================================
# СОБЫТИЯ ПО ИСТОЧНИКАМ
# =========================================================

def find_latest_status_per_source():

    result = {}

    for source_id, source in SOURCES.items():

        posts = get_telegram_posts(
            source_id,
            source
        )

        candidates = []

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

            if detected is None:
                continue

            post["detected_status"] = detected

            candidates.append(
                post
            )


        if not candidates:
            continue


        candidates.sort(
            key=lambda item: (
                parse_datetime(
                    item.get(
                        "published_at",
                        ""
                    )
                )
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
            reverse=True
        )

        result[source_id] = candidates[0]


    return result


# =========================================================
# САМОЕ СВЕЖЕЕ СОБЫТИЕ
# =========================================================

def find_latest_status():

    events = find_latest_status_per_source()

    if not events:
        return None

    candidates = list(
        events.values()
    )

    candidates.sort(
        key=lambda item: (
            parse_datetime(
                item.get(
                    "published_at",
                    ""
                )
            )
            or datetime.min.replace(
                tzinfo=timezone.utc
            )
        ),
        reverse=True
    )

    return candidates[0]


# =========================================================
# МЕСТО
# =========================================================

def get_location_text(post):

    text = post.get(
        "text",
        ""
    )

    city = detect_city(
        text
    )

    territory = detect_territory(
        text
    )

    return city, territory


# =========================================================
# НАЗВАНИЕ СТАТУСА
# =========================================================

def status_title(status):

    if status == "red":

        return (
            "🔴 Ракетная опасность"
        )

    if status == "yellow":

        return (
            "🟡 Внимание по БПЛА"
        )

    return (
        "🟢 Опасность не объявлена"
    )


# =========================================================
# НОВОЕ СОСТОЯНИЕ
# =========================================================

def make_state(
    post,
    status_override=None
):

    detected = post.get(
        "detected_status",
        "green"
    )

    if status_override:

        status = status_override

    elif detected == "uav_cancel":

        status = "green"

    else:

        status = detected


    city, territory = get_location_text(
        post
    )


    return {

        "status":
            status,

        "title":
            status_title(
                status
            ),

        "location":
            "Костромская область",

        "city":
            city,

        "territory":
            territory,

        "source":
            post.get(
                "source_name",
                ""
            ),

        "source_url":
            post.get(
                "url",
                ""
            ),

        "updated_at":
            post.get(
                "published_at",
                ""
            ),

        "event_post_id":
            post.get(
                "post_id",
                ""
            ),

        "event_source_id":
            post.get(
                "source_id",
                ""
            ),
    }


# =========================================================
# ФОРМАТ СТАТУСА
# =========================================================

def format_state(current_state):

    status = current_state.get(
        "status",
        "green"
    )

    if status == "red":

        emoji = "🔴"

    elif status == "yellow":

        emoji = "🟡"

    else:

        emoji = "🟢"


    title = current_state.get(
        "title",
        "Опасность не объявлена"
    )

    city = current_state.get(
        "city",
        ""
    )

    territory = current_state.get(
        "territory",
        ""
    )

    source = current_state.get(
        "source",
        ""
    )


    lines = [

        "🚨 <b>UAV ALERT</b>",

        "",

        f"<b>Статус:</b> "
        f"{emoji} {title}",

        "<b>Регион:</b> "
        "Костромская область",
    ]


    if city:

        lines.append(
            f"<b>Населённый пункт:</b> "
            f"{city}"
        )


    if territory:

        lines.append(
            f"<b>Территория:</b> "
            f"{territory}"
        )


    if source:

        lines.append(
            f"<b>Источник:</b> "
            f"{source}"
        )


    lines.extend([

        "",

        "⚠️ Информация носит "
        "справочный характер.",

        "Приоритет имеют официальные "
        "сообщения органов власти и МЧС.",
    ])


    return "\n".join(lines)


# =========================================================
# КЛАВИАТУРА
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="status"
            ),

            InlineKeyboardButton(
                "📍 Моё место",
                callback_data="location"
            ),

        ],

        [

            InlineKeyboardButton(
                "🔄 Изменить",
                callback_data="change"
            ),

            InlineKeyboardButton(
                "📚 История",
                callback_data="history"
            ),

        ],

        [

            InlineKeyboardButton(
                "📋 Сводка",
                callback_data="summary"
            ),

            InlineKeyboardButton(
                "⚙️ Настройки",
                callback_data="settings"
            ),

        ],

        [

            InlineKeyboardButton(
                "📡 Источники",
                callback_data="sources"
            ),

            InlineKeyboardButton(
                "📢 Наш канал",
                url="https://t.me/RADAR_Kostroma"
            ),

        ],
    ])


# =========================================================
# ГОРОДА
# =========================================================

def cities_keyboard():

    buttons = []

    for index, city in enumerate(
        CITIES
    ):

        buttons.append([

            InlineKeyboardButton(
                city,
                callback_data=f"city:{index}"
            )

        ])


    buttons.append([

        InlineKeyboardButton(
            "📋 Муниципальные округа",
            callback_data="territories"
        )

    ])


    buttons.append([

        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="main"
        )

    ])


    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# ОКРУГА
# =========================================================

def territories_keyboard():

    buttons = []

    for index, territory in enumerate(
        TERRITORIES
    ):

        buttons.append([

            InlineKeyboardButton(
                territory,
                callback_data=f"terr:{index}"
            )

        ])


    buttons.append([

        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="change"
        )

    ])


    return InlineKeyboardMarkup(
        buttons
    )


# =========================================================
# START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user:

        add_subscriber(
            user.id
        )


    text = (

        "🚨 <b>UAV ALERT</b>\n\n"

        "Гражданский информационный "
        "сервис по Костромской области.\n\n"

        "Выберите нужный раздел:"
    )


    await update.message.reply_text(

        text,

        parse_mode="HTML",

        reply_markup=main_keyboard()

    )


# =========================================================
# STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        format_state(
            state
        ),

        parse_mode="HTML",

        reply_markup=main_keyboard()

    )


# =========================================================
# STOP
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user:

        remove_subscriber(
            user.id
        )


    await update.message.reply_text(

        "🔕 Уведомления отключены.\n\n"
        "Чтобы снова включить их, "
        "используйте /start."

    )


# =========================================================
# TEST
# =========================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if (
        not user
        or user.id != ADMIN_ID
    ):

        await update.message.reply_text(
            "Нет доступа."
        )

        return


    await update.message.reply_text(

        "🧪 <b>Тестовый режим</b>\n\n"
        "Бот работает нормально.",

        parse_mode="HTML"

    )


# =========================================================
# STATS
# =========================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if (
        not user
        or user.id != ADMIN_ID
    ):

        await update.message.reply_text(
            "Нет доступа."
        )

        return


    current_status = state.get(
        "status",
        "green"
    )


    if current_status == "yellow":

        status_text = (
            "🟡 Внимание по БПЛА"
        )

    elif current_status == "red":

        status_text = (
            "🔴 Ракетная опасность"
        )

    else:

        status_text = (
            "🟢 Опасность не объявлена"
        )


    text = (

        "📊 <b>Статистика UAV ALERT</b>\n\n"

        f"👥 Подписчиков: "
        f"<b>{len(subscribers)}</b>\n"

        f"📚 Записей истории: "
        f"<b>{len(history)}</b>\n"

        f"🛡 Обработанных сообщений: "
        f"<b>{len(sent_posts)}</b>\n\n"

        f"📌 Текущий статус:\n"
        f"<b>{status_text}</b>\n\n"

        "ℹ️ Статус сохраняется до "
        "сообщения об отбое соответствующей "
        "опасности.\n\n"

        "Эта команда только показывает "
        "статистику и ничего не публикует."

    )


    await update.message.reply_text(

        text,

        parse_mode="HTML"

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
    # MAIN
    # -----------------------------------------------------

    if data == "main":

        await query.edit_message_text(

            "🚨 <b>UAV ALERT</b>\n\n"
            "Выберите нужный раздел:",

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if data == "status":

        await query.edit_message_text(

            format_state(
                state
            ),

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # LOCATION
    # -----------------------------------------------------

    if data == "location":

        await query.edit_message_text(

            "📍 <b>Выберите город:</b>",

            parse_mode="HTML",

            reply_markup=cities_keyboard()

        )

        return


    # -----------------------------------------------------
    # CHANGE
    # -----------------------------------------------------

    if data == "change":

        await query.edit_message_text(

            "🔄 <b>Изменить место</b>\n\n"
            "Выберите город или "
            "муниципальный округ:",

            parse_mode="HTML",

            reply_markup=cities_keyboard()

        )

        return


    # -----------------------------------------------------
    # CITY
    # -----------------------------------------------------

    if data.startswith("city:"):

        try:

            index = int(
                data.split(":")[1]
            )

            city = CITIES[index]

        except Exception:

            await query.edit_message_text(

                "Ошибка выбора города.",

                reply_markup=main_keyboard()

            )

            return


        user_id = query.from_user.id

        user_locations = load_json(

            USER_LOCATIONS_FILE,

            {}

        )


        user_locations[
            str(user_id)
        ] = {

            "type":
                "city",

            "name":
                city,

        }


        save_json(

            USER_LOCATIONS_FILE,

            user_locations

        )


        await query.edit_message_text(

            f"📍 Место установлено:\n\n"
            f"<b>{city}</b>\n\n"
            "Теперь уведомления будут "
            "учитывать выбранный населённый пункт.",

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # TERRITORIES
    # -----------------------------------------------------

    if data == "territories":

        await query.edit_message_text(

            "📋 <b>Выберите муниципальный округ:</b>",

            parse_mode="HTML",

            reply_markup=territories_keyboard()

        )

        return


    # -----------------------------------------------------
    # TERRITORY
    # -----------------------------------------------------

    if data.startswith("terr:"):

        try:

            index = int(
                data.split(":")[1]
            )

            territory = TERRITORIES[index]

        except Exception:

            await query.edit_message_text(

                "Ошибка выбора округа.",

                reply_markup=main_keyboard()

            )

            return


        user_id = query.from_user.id

        user_locations = load_json(

            USER_LOCATIONS_FILE,

            {}

        )


        user_locations[
            str(user_id)
        ] = {

            "type":
                "territory",

            "name":
                territory,

        }


        save_json(

            USER_LOCATIONS_FILE,

            user_locations

        )


        await query.edit_message_text(

            f"📍 Территория установлена:\n\n"
            f"<b>{territory}</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    if data == "history":

        if not history:

            text = (

                "📚 <b>История</b>\n\n"
                "Пока событий нет."

            )

        else:

            items = history[-10:]

            lines = [

                "📚 <b>Последние события</b>",
                ""

            ]


            for item in reversed(
                items
            ):

                status = item.get(
                    "status",
                    "green"
                )


                if status == "red":

                    emoji = "🔴"

                elif status == "yellow":

                    emoji = "🟡"

                else:

                    emoji = "🟢"


                title = item.get(
                    "title",
                    ""
                )

                city = item.get(
                    "city",
                    ""
                )


                location = (

                    f" — {city}"

                    if city

                    else ""

                )


                lines.append(

                    f"{emoji} "
                    f"{title}"
                    f"{location}"

                )


            text = "\n".join(
                lines
            )


        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------

    if data == "summary":

        current = state.get(
            "status",
            "green"
        )


        if current == "yellow":

            status_text = (
                "🟡 Внимание по БПЛА"
            )

        elif current == "red":

            status_text = (
                "🔴 Ракетная опасность"
            )

        else:

            status_text = (
                "🟢 Опасность не объявлена"
            )


        text = (

            "📋 <b>Сводка UAV ALERT</b>\n\n"

            "Регион: "
            "<b>Костромская область</b>\n"

            f"Текущий статус: "
            f"<b>{status_text}</b>\n"

            f"Подписчиков: "
            f"<b>{len(subscribers)}</b>\n"

            f"Событий в истории: "
            f"<b>{len(history)}</b>\n\n"

            "Статус сохраняется до "
            "получения сообщения об отбое."

        )


        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # SETTINGS
    # -----------------------------------------------------

    if data == "settings":

        text = (

            "⚙️ <b>Настройки</b>\n\n"

            "🔔 Уведомления включаются "
            "после запуска бота.\n\n"

            "🔕 Для отключения используйте "
            "/stop."

        )


        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


    # -----------------------------------------------------
    # SOURCES
    # -----------------------------------------------------

    if data == "sources":

        lines = [

            "📡 <b>Источники информации</b>",
            ""

        ]


        for source in SOURCES.values():

            lines.append(

                f"• {source['name']}"

            )


        lines.extend([

            "",

            "⚠️ Источники могут содержать "
            "непроверенную информацию.",

            "",

            "Приоритет имеют официальные "
            "сообщения органов власти и МЧС.",

        ])


        await query.edit_message_text(

            "\n".join(lines),

            parse_mode="HTML",

            reply_markup=main_keyboard()

        )

        return


# =========================================================
# УВЕДОМЛЕНИЕ ПОДПИСЧИКОВ
# =========================================================

async def notify_subscribers(
    bot,
    new_state
):

    if not subscribers:
        return


    message = format_state(
        new_state
    )


    for user_id in list(
        subscribers
    ):

        try:

            await bot.send_message(

                chat_id=user_id,

                text=message,

                parse_mode="HTML"

            )

        except Exception as error:

            print(

                f"Ошибка отправки "
                f"{user_id}:",
                error

            )


# =========================================================
# СТАРТОВАЯ ЗАЩИТА
# =========================================================

def create_startup_baseline():

    print(
        "🛡 Создание стартовой защиты..."
    )


    try:

        events = (
            find_latest_status_per_source()
        )


        if not events:

            print(
                "ℹ️ Событий для стартовой "
                "защиты не найдено."
            )

            return


        for source_id, post in events.items():

            post_id = post.get(
                "post_id",
                ""
            )


            if post_id:

                mark_post_sent(

                    source_id,

                    post_id

                )


                print(

                    "🛡 Уже существующее "
                    "сообщение отмечено:",
                    make_post_key(
                        source_id,
                        post_id
                    )

                )


        print(
            "✅ Стартовая защита создана."
        )


    except Exception as error:

        print(
            "❌ Ошибка стартовой защиты:",
            error
        )


# =========================================================
# ОБНОВЛЕНИЕ ТЕКУЩЕГО СОСТОЯНИЯ
# =========================================================

def apply_event(
    post,
    detected_status
):

    global state


    # -----------------------------------------------------
    # ОТБОЙ БПЛА
    # -----------------------------------------------------

    if detected_status == "uav_cancel":

        new_state = {

            "status":
                "green",

            "title":
                "🟢 Опасность не объявлена",

            "location":
                "Костромская область",

            "city":
                "",

            "territory":
                "",

            "source":
                post.get(
                    "source_name",
                    ""
                ),

            "source_url":
                post.get(
                    "url",
                    ""
                ),

            "updated_at":
                post.get(
                    "published_at",
                    ""
                ),

            "event_post_id":
                post.get(
                    "post_id",
                    ""
                ),

            "event_source_id":
                post.get(
                    "source_id",
                    ""
                ),

        }


        return new_state


    # -----------------------------------------------------
    # НОВОЕ ВНИМАНИЕ БПЛА
    # -----------------------------------------------------

    if detected_status == "yellow":

        new_state = make_state(
            post,
            "yellow"
        )

        return new_state


    # -----------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

    if detected_status == "red":

        new_state = make_state(
            post,
            "red"
        )

        return new_state


    return None


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitor_loop(bot):

    global state


    print(
        "📡 Подготовка мониторинга..."
    )


    # -----------------------------------------------------
    # STARTUP BASELINE
    # -----------------------------------------------------

    await asyncio.to_thread(
        create_startup_baseline
    )


    print(
        "📡 Мониторинг запущен."
    )


    # -----------------------------------------------------
    # ОСНОВНОЙ ЦИКЛ
    # -----------------------------------------------------

    while True:

        try:

            result = await asyncio.to_thread(
                find_latest_status
            )


            if result is not None:

                source_id = result.get(
                    "source_id",
                    ""
                )

                post_id = result.get(
                    "post_id",
                    ""
                )


                post_key = make_post_key(
                    source_id,
                    post_id
                )


                # -------------------------------------------------
                # УЖЕ ОБРАБОТАНО
                # -------------------------------------------------

                if was_post_sent(
                    source_id,
                    post_id
                ):

                    print(
                        "⏭ Старое событие:",
                        post_key
                    )


                else:

                    detected_status = result.get(
                        "detected_status"
                    )


                    print(
                        "🆕 Новое событие:",
                        post_key,
                        "|",
                        detected_status
                    )


                    new_state = apply_event(

                        result,

                        detected_status

                    )


                    # -------------------------------------------------
                    # ЕСЛИ СТАТУС РАСПОЗНАН
                    # -------------------------------------------------

                    if new_state is not None:

                        channel_message = (
                            format_state(
                                new_state
                            )
                        )


                        try:

                            # -----------------------------------------
                            # ПУБЛИКАЦИЯ
                            # -----------------------------------------

                            await bot.send_message(

                                chat_id=CHANNEL,

                                text=channel_message,

                                parse_mode="HTML"

                            )


                            print(

                                "📢 Сообщение "
                                "опубликовано:",
                                post_key

                            )


                            # -----------------------------------------
                            # ПОСЛЕ УСПЕШНОЙ ПУБЛИКАЦИИ
                            # -----------------------------------------

                            mark_post_sent(

                                source_id,

                                post_id

                            )


                            state = new_state


                            save_json(

                                STATE_FILE,

                                state

                            )


                            add_history(
                                state
                            )


                            await notify_subscribers(

                                bot,

                                state

                            )


                        except Exception as error:

                            print(

                                "❌ Ошибка "
                                "публикации:",
                                error

                            )


        except Exception as error:

            print(

                "❌ Ошибка мониторинга:",
                error

            )


        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application: Application
):

    commands = [

        BotCommand(
            "start",
            "Запустить бота"
        ),

        BotCommand(
            "status",
            "Текущий статус"
        ),

        BotCommand(
            "stop",
            "Отключить уведомления"
        ),

        BotCommand(
            "test",
            "Тестовый режим"
        ),

        BotCommand(
            "stats",
            "Статистика"
        ),

    ]


    await application.bot.set_my_commands(
        commands
    )


    application.create_task(

        monitor_loop(
            application.bot
        )

    )


    print(
        "🤖 Telegram-бот запущен."
    )


# =========================================================
# FLASK
# =========================================================

flask_app = Flask(
    __name__
)


@flask_app.route("/")
def home():

    return (
        "UAV ALERT is running",
        200
    )


@flask_app.route("/health")
def health():

    return {

        "status":
            "ok",

        "service":
            "UAV ALERT",

    }, 200


def run_flask():

    flask_app.run(

        host="0.0.0.0",

        port=PORT,

        debug=False,

        use_reloader=False

    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ ОШИБКА: BOT_TOKEN не найден."
        )

        return


    # -----------------------------------------------------
    # ЗАГРУЗКА ДАННЫХ
    # -----------------------------------------------------

    load_all_data()


    # -----------------------------------------------------
    # FLASK
    # -----------------------------------------------------

    flask_thread = threading.Thread(

        target=run_flask,

        daemon=True

    )


    flask_thread.start()


    print(
        f"🌐 Flask запущен "
        f"на порту {PORT}"
    )


    # -----------------------------------------------------
    # TELEGRAM APPLICATION
    # -----------------------------------------------------

    application = (

        Application

        .builder()

        .token(
            BOT_TOKEN
        )

        .post_init(
            post_init
        )

        .build()

    )


    # -----------------------------------------------------
    # COMMAND HANDLERS
    # -----------------------------------------------------

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

        CommandHandler(
            "stats",
            stats_command
        )

    )


    # -----------------------------------------------------
    # CALLBACK HANDLER
    # -----------------------------------------------------

    application.add_handler(

        CallbackQueryHandler(
            callback_handler
        )

    )


    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    print(
        "🚀 Запуск Telegram polling..."
    )


    application.run_polling(

        drop_pending_updates=True

    )


# =========================================================
# ЗАПУСК
# =========================================================

if __name__ == "__main__":

    main()
