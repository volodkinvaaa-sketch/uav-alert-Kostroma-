import os
import json
import asyncio
import threading
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify

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


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

ADMIN_ID = int(
    os.getenv("ADMIN_ID", "1421675956")
)

CHANNEL = os.getenv(
    "CHANNEL",
    "@RADAR_Kostroma"
)

PORT = int(
    os.getenv("PORT", "10000")
)

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


# ============================================================
# ИСТОЧНИКИ
# ============================================================

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


# ============================================================
# ГОРОДА
# ============================================================

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


# ============================================================
# МУНИЦИПАЛЬНЫЕ ОКРУГА
# ============================================================

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


# ============================================================
# ВРЕМЯ МСК
# ============================================================

def moscow_time():
    msk = timezone(
        timedelta(hours=3)
    )

    return datetime.now(
        msk
    ).strftime(
        "%d.%m.%Y %H:%M:%S МСК"
    )


# ============================================================
# JSON
# ============================================================

def load_json(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception as error:

        print(
            "Ошибка чтения JSON:",
            error
        )

        return default


def save_json(path, data):

    try:

        with open(
            path,
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
            "Ошибка сохранения JSON:",
            error
        )


# ============================================================
# ДАННЫЕ
# ============================================================

DEFAULT_STATE = {
    "status": "green",
    "text": "Опасность не объявлена",
    "source": "",
    "source_id": "",
    "post_id": "",
    "location": "",
    "updated": "",
}


state = load_json(
    STATE_FILE,
    DEFAULT_STATE
)

history = load_json(
    HISTORY_FILE,
    []
)

subscribers = load_json(
    SUBSCRIBERS_FILE,
    {}
)

sent_posts = load_json(
    SENT_POSTS_FILE,
    []
)


if not isinstance(history, list):
    history = []


if not isinstance(subscribers, dict):
    subscribers = {}


if not isinstance(sent_posts, list):
    sent_posts = []


# ============================================================
# ЗАЩИТА ОТ ПОВТОРНЫХ СООБЩЕНИЙ
# ============================================================

def make_post_key(
    source_id,
    post_id
):

    if not source_id or not post_id:
        return ""

    return (
        f"{source_id}:{post_id}"
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

        sent_posts.append(key)

    # Храним последние 1000 обработанных постов
    sent_posts = sent_posts[-1000:]

    save_json(
        SENT_POSTS_FILE,
        sent_posts
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ КОСТРОМСКОЙ ОБЛАСТИ
# ============================================================

def is_kostroma(text):

    text = text.lower()

    words = [
        "кострома",
        "костромская область",
        "костромской области",
        "костромская обл",
        "костромской обл",
    ]

    return any(
        word in text
        for word in words
    )


# ============================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# ============================================================

def detect_status(text):

    text = text.lower()

    # РАКЕТНАЯ ОПАСНОСТЬ
    red_words = [
        "ракетная опасность",
        "ракетной опасности",
        "ракетная тревога",
        "опасность по ракетам",
        "угроза ракетного нападения",
        "ракетная угроза",
    ]

    if any(
        word in text
        for word in red_words
    ):
        return "red"

    # ОТБОЙ
    green_words = [
        "отбой",
        "отмена опасности",
        "опасность снята",
        "опасность отменена",
        "угроза снята",
        "угроза отменена",
        "угроза по бпла снята",
        "опасность по бпла снята",
        "отбой опасности бпла",
        "отбой беспилотной опасности",
    ]

    if any(
        word in text
        for word in green_words
    ):
        return "green"

    # БПЛА
    yellow_words = [
        "угроза по бпла",
        "угроза бпла",
        "опасность по бпла",
        "опасность бпла",
        "угроза беспилотников",
        "угроза беспилотника",
        "опасность беспилотников",
        "опасность беспилотника",
        "беспилотная опасность",
        "опасность беспилотной атаки",
    ]

    if any(
        word in text
        for word in yellow_words
    ):
        return "yellow"

    drone_words = [
        "бпла",
        "беспилотник",
        "беспилотников",
        "беспилотная",
    ]

    danger_words = [
        "опасность",
        "угроза",
        "тревога",
        "внимание",
    ]

    if (
        any(
            word in text
            for word in drone_words
        )
        and
        any(
            word in text
            for word in danger_words
        )
    ):
        return "yellow"

    return None


# ============================================================
# НАЗВАНИЕ СТАТУСА
# ============================================================

def status_name(status):

    if status == "red":
        return "🔴 Ракетная опасность"

    if status == "yellow":
        return "🟡 Беспилотная опасность"

    return "🟢 Опасность не объявлена"


# ============================================================
# ПОИСК МЕСТА
# ============================================================

def find_location(text):

    text_lower = text.lower()

    for city in CITIES:

        if city.lower() in text_lower:
            return city

    for territory in TERRITORIES:

        if territory.lower() in text_lower:
            return territory

    if is_kostroma(text):
        return "Костромская область"

    return ""


# ============================================================
# ПОЛУЧЕНИЕ ПОСТОВ TELEGRAM
# ============================================================

def get_telegram_posts(url):

    try:

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                    "Mozilla/5.0"
            }
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        posts = []

        for element in soup.select(
            ".tgme_widget_message"
        ):

            text_element = element.select_one(
                ".tgme_widget_message_text"
            )

            if not text_element:
                continue

            text = text_element.get_text(
                " ",
                strip=True
            )

            post_id = element.get(
                "data-post",
                ""
            )

            posts.append({
                "text": text,
                "post_id": post_id,
            })

        return posts

    except Exception as error:

        print(
            "Ошибка чтения Telegram:",
            error
        )

        return []


# ============================================================
# ПОИСК ПОСЛЕДНЕГО СОБЫТИЯ
# ============================================================

def find_latest_status():

    candidates = []

    for source_id, source in SOURCES.items():

        posts = get_telegram_posts(
            source["url"]
        )

        for post in posts:

            text = post.get(
                "text",
                ""
            )

            if not is_kostroma(text):
                continue

            status = detect_status(text)

            if not status:
                continue

            candidates.append({
                "status": status,
                "text": text,
                "source": source["name"],
                "source_id": source_id,
                "post_id": post.get(
                    "post_id",
                    ""
                ),
                "location": find_location(
                    text
                ),
            })

    if not candidates:
        return None

    # ВАЖНО:
    # возвращаем первое найденное событие.
    # Повторная публикация блокируется
    # через sent_posts.json.
    return candidates[0]


# ============================================================
# ИСТОРИЯ
# ============================================================

def add_history(new_state):

    global history

    item = {
        "status": new_state.get(
            "status",
            "green"
        ),

        "text": new_state.get(
            "text",
            ""
        ),

        "source": new_state.get(
            "source",
            ""
        ),

        "location": new_state.get(
            "location",
            ""
        ),

        "time": moscow_time(),
    }

    history.insert(
        0,
        item
    )

    history = history[:20]

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# ПОЛЬЗОВАТЕЛЬ
# ============================================================

def get_user(user_id):

    key = str(user_id)

    if key not in subscribers:

        subscribers[key] = {
            "location": "Кострома",
            "notifications": True,
            "green_notifications": True,
        }

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )

    return subscribers[key]


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="status"
            )
        ],

        [
            InlineKeyboardButton(
                "📍 Моё место",
                callback_data="my_location"
            ),

            InlineKeyboardButton(
                "🔄 Изменить",
                callback_data="change_location"
            )
        ],

        [
            InlineKeyboardButton(
                "📚 История",
                callback_data="history"
            ),

            InlineKeyboardButton(
                "📋 Сводка",
                callback_data="summary"
            )
        ],

        [
            InlineKeyboardButton(
                "⚙️ Настройки",
                callback_data="settings"
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
                "📢 Наш канал",
                url="https://t.me/RADAR_Kostroma"
            )
        ],
    ])


# ============================================================
# ВЫБОР ГОРОДА
# ============================================================

def location_keyboard():

    buttons = []

    for index, city in enumerate(CITIES):

        buttons.append(
            InlineKeyboardButton(
                city,
                callback_data=f"city:{index}"
            )
        )

    rows = []

    for i in range(
        0,
        len(buttons),
        2
    ):

        rows.append(
            buttons[i:i + 2]
        )

    rows.append([
        InlineKeyboardButton(
            "🏘 Округа",
            callback_data="territories"
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="back"
        )
    ])

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# ВЫБОР ОКРУГА
# ============================================================

def territory_keyboard():

    rows = []

    for index, territory in enumerate(
        TERRITORIES
    ):

        rows.append([
            InlineKeyboardButton(
                territory,
                callback_data=f"terr:{index}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="change_location"
        )
    ])

    return InlineKeyboardMarkup(
        rows
    )


# ============================================================
# НАСТРОЙКИ
# ============================================================

def settings_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🔔 Уведомления",
                callback_data="toggle_notifications"
            )
        ],

        [
            InlineKeyboardButton(
                "🟢 Уведомления об отбое",
                callback_data="toggle_green"
            )
        ],

        [
            InlineKeyboardButton(
                "📍 Изменить место",
                callback_data="change_location"
            )
        ],

        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back"
            )
        ],
    ])


# ============================================================
# ТЕКУЩИЙ СТАТУС
# ============================================================

def status_text():

    return (
        "🚨 <b>UAV ALERT</b>\n\n"

        f"{status_name(state.get('status'))}\n\n"

        "📍 Регион: "
        "<b>Костромская область</b>\n"

        f"🕐 Обновлено: "
        f"{state.get('updated', '—')}\n"

        f"📡 Источник: "
        f"{state.get('source', '—')}\n"
    )


# ============================================================
# СВОДКА
# ============================================================

def summary_text():

    return (
        "📋 <b>Сводка UAV ALERT</b>\n\n"

        "📍 Регион: "
        "Костромская область\n"

        f"🚦 Статус: "
        f"{status_name(state.get('status'))}\n"

        f"📍 Место: "
        f"{state.get('location') or 'не указано'}\n"

        f"🕐 Последнее обновление: "
        f"{state.get('updated', '—')}\n"

        f"📡 Источник: "
        f"{state.get('source', '—')}\n\n"

        "⚠️ Информация носит "
        "справочный характер.\n"

        "Приоритет имеют официальные "
        "сообщения органов власти и МЧС."
    )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user:
        get_user(user.id)

    text = (
        "🚨 <b>UAV ALERT</b>\n\n"

        "Гражданский информационный "
        "сервис по ситуации "
        "в Костромской области.\n\n"

        "Выберите нужный раздел:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ============================================================
# STATUS COMMAND
# ============================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ============================================================
# STOP
# ============================================================

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    user = get_user(
        user_id
    )

    user["notifications"] = False

    save_json(
        SUBSCRIBERS_FILE,
        subscribers
    )

    await update.message.reply_text(
        "🔕 Уведомления отключены.\n\n"
        "Включить их можно через "
        "⚙️ Настройки."
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(update):

    return (
        update.effective_user is not None
        and
        update.effective_user.id == ADMIN_ID
    )


# ============================================================
# TEST
# ============================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Команда доступна "
            "только администратору."
        )

        return

    text = (
        "🧪 <b>ТЕСТ UAV ALERT</b>\n\n"

        "🟡 Демонстрационное "
        "уведомление.\n"

        "Реальной угрозы нет.\n\n"

        f"🕐 {moscow_time()}"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )

    try:

        await context.bot.send_message(
            chat_id=CHANNEL,
            text=text,
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            "Ошибка тестовой отправки:",
            error
        )


# ============================================================
# СТАТИСТИКА
# ============================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return

    active = 0

    for user in subscribers.values():

        if user.get(
            "notifications",
            True
        ):
            active += 1

    text = (
        "📊 <b>UAV ALERT — статистика</b>\n\n"

        f"👥 Пользователей: "
        f"{len(subscribers)}\n"

        f"🔔 Активных уведомлений: "
        f"{active}\n"

        f"📡 Источников: "
        f"{len(SOURCES)}\n"

        f"📚 Записей истории: "
        f"{len(history)}\n"

        f"📍 Городов: "
        f"{len(CITIES)}\n"

        f"🏘 Округов: "
        f"{len(TERRITORIES)}\n"

        f"🛡 Обработанных постов: "
        f"{len(sent_posts)}"
    )

    # ВАЖНО:
    # здесь НЕТ вызова find_latest_status()
    # и НЕТ отправки в канал.

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# ============================================================
# CALLBACK
# ============================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(
        user_id
    )

    data = query.data

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        await query.edit_message_text(
            status_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # --------------------------------------------------------
    # МОЁ МЕСТО
    # --------------------------------------------------------

    elif data == "my_location":

        location = user.get(
            "location",
            "Кострома"
        )

        await query.edit_message_text(
            "📍 <b>Ваше место</b>\n\n"

            f"Вы выбрали: "
            f"<b>{location}</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )

    # --------------------------------------------------------
    # ИЗМЕНИТЬ МЕСТО
    # --------------------------------------------------------

    elif data == "change_location":

        await query.edit_message_text(
            "📍 <b>Выберите город:</b>",

            parse_mode="HTML",

            reply_markup=location_keyboard()
        )

    # --------------------------------------------------------
    # ГОРОД
    # --------------------------------------------------------

    elif data.startswith("city:"):

        try:

            index = int(
                data.split(":")[1]
            )

            location = CITIES[index]

            user["location"] = location

            save_json(
                SUBSCRIBERS_FILE,
                subscribers
            )

            await query.edit_message_text(
                "✅ Место сохранено.\n\n"

                f"📍 <b>{location}</b>",

                parse_mode="HTML",

                reply_markup=main_keyboard()
            )

        except Exception:

            await query.edit_message_text(
                "❌ Ошибка выбора города."
            )

    # --------------------------------------------------------
    # ОКРУГА
    # --------------------------------------------------------

    elif data == "territories":

        await query.edit_message_text(
            "🏘 <b>Выберите "
            "муниципальный округ:</b>",

            parse_mode="HTML",

            reply_markup=territory_keyboard()
        )

    # --------------------------------------------------------
    # ОКРУГ
    # --------------------------------------------------------

    elif data.startswith("terr:"):

        try:

            index = int(
                data.split(":")[1]
            )

            location = TERRITORIES[index]

            user["location"] = location

            save_json(
                SUBSCRIBERS_FILE,
                subscribers
            )

            await query.edit_message_text(
                "✅ Место сохранено.\n\n"

                f"📍 <b>{location}</b>",

                parse_mode="HTML",

                reply_markup=main_keyboard()
            )

        except Exception:

            await query.edit_message_text(
                "❌ Ошибка выбора округа."
            )

    # --------------------------------------------------------
    # ИСТОРИЯ
    # --------------------------------------------------------

    elif data == "history":

        if not history:

            text = (
                "📚 <b>История</b>\n\n"
                "История пока пустая."
            )

        else:

            lines = [
                "📚 <b>Последние события</b>\n"
            ]

            for item in history[:10]:

                lines.append(
                    f"{item.get('time', '')} — "
                    f"{status_name(item.get('status'))}\n"
                    f"📍 "
                    f"{item.get('location') or 'Костромская область'}\n"
                )

            text = "\n".join(
                lines
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # --------------------------------------------------------
    # СВОДКА
    # --------------------------------------------------------

    elif data == "summary":

        await query.edit_message_text(
            summary_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # --------------------------------------------------------
    # ИСТОЧНИКИ
    # --------------------------------------------------------

    elif data == "sources":

        lines = [
            "📡 <b>Источники UAV ALERT</b>\n"
        ]

        for source in SOURCES.values():

            lines.append(
                f"{source['name']}\n"
                f"{source['url']}\n"
            )

        lines.append(
            "⚠️ Источники могут быть "
            "вторичными.\n"
            "Приоритет имеют официальные "
            "сообщения органов власти и МЧС."
        )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # --------------------------------------------------------
    # НАСТРОЙКИ
    # --------------------------------------------------------

    elif data == "settings":

        notifications = (
            "включены"
            if user.get(
                "notifications",
                True
            )
            else "выключены"
        )

        green = (
            "включены"
            if user.get(
                "green_notifications",
                True
            )
            else "выключены"
        )

        text = (
            "⚙️ <b>Настройки</b>\n\n"

            f"🔔 Уведомления: "
            f"<b>{notifications}</b>\n"

            f"🟢 Уведомления об отбое: "
            f"<b>{green}</b>"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=settings_keyboard()
        )

    # --------------------------------------------------------
    # УВЕДОМЛЕНИЯ
    # --------------------------------------------------------

    elif data == "toggle_notifications":

        user["notifications"] = not user.get(
            "notifications",
            True
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )

        await query.edit_message_text(
            "⚙️ Настройки обновлены.",
            reply_markup=settings_keyboard()
        )

    # --------------------------------------------------------
    # ОТБОЙ
    # --------------------------------------------------------

    elif data == "toggle_green":

        user["green_notifications"] = not user.get(
            "green_notifications",
            True
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )

        await query.edit_message_text(
            "⚙️ Настройки обновлены.",
            reply_markup=settings_keyboard()
        )

    # --------------------------------------------------------
    # НАЗАД
    # --------------------------------------------------------

    elif data == "back":

        await query.edit_message_text(
            "🚨 <b>UAV ALERT</b>\n\n"
            "Выберите нужный раздел:",

            parse_mode="HTML",

            reply_markup=main_keyboard()
        )


# ============================================================
# УВЕДОМЛЕНИЯ ПОДПИСЧИКОВ
# ============================================================

async def notify_subscribers(
    bot,
    new_state
):

    for user_id, user in list(
        subscribers.items()
    ):

        if not user.get(
            "notifications",
            True
        ):
            continue

        location = user.get(
            "location",
            "Кострома"
        )

        source_text = new_state.get(
            "text",
            ""
        )

        # Для конкретного города/округа
        # уведомление отправляется только если
        # выбранное место явно присутствует
        # в исходном сообщении.
        if (
            location != "Костромская область"
            and
            location.lower()
            not in source_text.lower()
        ):
            continue

        status = new_state.get(
            "status",
            "green"
        )

        if (
            status == "green"
            and
            not user.get(
                "green_notifications",
                True
            )
        ):
            continue

        message = (
            "🚨 <b>UAV ALERT</b>\n\n"

            f"{status_name(status)}\n\n"

            f"📍 {location}\n"

            f"🕐 "
            f"{new_state.get('updated', moscow_time())}\n"

            f"📡 "
            f"{new_state.get('source', '—')}\n\n"

            "⚠️ Информация носит "
            "справочный характер.\n"

            "Приоритет имеют официальные "
            "сообщения органов власти и МЧС."
        )

        try:

            await bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "Ошибка уведомления:",
                user_id,
                error
            )


# ============================================================
# МОНИТОРИНГ
# ============================================================

async def monitor_loop(bot):

    global state

    print(
        "📡 Мониторинг запущен."
    )

    while True:

        try:

            result = find_latest_status()

            if result is not None:

                source_id = result.get(
                    "source_id",
                    ""
                )

                post_id = result.get(
                    "post_id",
                    ""
                )

                # ------------------------------------------------
                # ГЛАВНАЯ ЗАЩИТА ОТ ДУБЛЕЙ
                # ------------------------------------------------

                if was_post_sent(
                    source_id,
                    post_id
                ):

                    # Уже отправляли это событие.
                    # Ничего не публикуем.
                    print(
                        "⏭ Уже обработано:",
                        make_post_key(
                            source_id,
                            post_id
                        )
                    )

                else:

                    new_state = {
                        "status": result.get(
                            "status",
                            "green"
                        ),

                        "text": result.get(
                            "text",
                            ""
                        ),

                        "source": result.get(
                            "source",
                            ""
                        ),

                        "source_id": source_id,

                        "post_id": post_id,

                        "location": result.get(
                            "location",
                            ""
                        ),

                        "updated": moscow_time(),
                    }

                    # ------------------------------------------------
                    # СНАЧАЛА ПУБЛИКАЦИЯ В КАНАЛ
                    # ------------------------------------------------

                    channel_message = (
                        "🚨 <b>UAV ALERT</b>\n\n"

                        f"{status_name(new_state['status'])}\n\n"

                        f"📍 "
                        f"{new_state['location'] or 'Костромская область'}\n"

                        f"🕐 "
                        f"{new_state['updated']}\n"

                        f"📡 "
                        f"{new_state['source']}\n\n"

                        "⚠️ Информация носит "
                        "справочный характер.\n"

                        "Приоритет имеют официальные "
                        "сообщения органов власти и МЧС."
                    )

                    channel_success = False

                    try:

                        await bot.send_message(
                            chat_id=CHANNEL,
                            text=channel_message,
                            parse_mode="HTML"
                        )

                        channel_success = True

                    except Exception as error:

                        print(
                            "Ошибка отправки в канал:",
                            error
                        )

                    # ------------------------------------------------
                    # ТОЛЬКО ПОСЛЕ УСПЕШНОЙ ПУБЛИКАЦИИ
                    # ЗАПОМИНАЕМ POST_ID
                    # ------------------------------------------------

                    if channel_success:

                        state = new_state

                        save_json(
                            STATE_FILE,
                            state
                        )

                        add_history(
                            state
                        )

                        # Запоминаем пост.
                        # Теперь повторная проверка
                        # его больше не опубликует.
                        mark_post_sent(
                            source_id,
                            post_id
                        )

                        # Отправляем подписчикам
                        await notify_subscribers(
                            bot,
                            state
                        )

                        print(
                            "✅ Новое событие обработано:",
                            make_post_key(
                                source_id,
                                post_id
                            )
                        )

        except Exception as error:

            print(
                "❌ Ошибка мониторинга:",
                error
            )

        # Проверка раз в минуту
        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# FLASK
# ============================================================

app_web = Flask(__name__)


@app_web.route("/")
def home():

    return "UAV ALERT работает."


@app_web.route("/status")
def web_status():

    return jsonify(
        state
    )


def run_flask():

    app_web.run(
        host="0.0.0.0",
        port=PORT
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application
):

    await application.bot.set_my_commands([

        BotCommand(
            "start",
            "Главное меню"
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
            "Тестовое уведомление"
        ),

        BotCommand(
            "stats",
            "Статистика"
        ),
    ])

    # Мониторинг запускается ОДИН РАЗ
    # при старте приложения.
    asyncio.create_task(
        monitor_loop(
            application.bot
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ ОШИБКА: BOT_TOKEN не найден."
        )

        return

    # Flask для Render
    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Команды
    application.add_handler(
        CommandHandler(
            "start",
            start
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
            stop
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

    # Кнопки
    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    print(
        "🚨 UAV ALERT запущен."
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    main()
