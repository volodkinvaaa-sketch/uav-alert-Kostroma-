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
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL = os.getenv("CHANNEL", "@RADAR_Kostroma")
PORT = int(os.getenv("PORT", "10000"))

CHECK_INTERVAL = 60

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATE_FILE = os.path.join(BASE_DIR, "state.json")
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")
SUBSCRIBERS_FILE = os.path.join(BASE_DIR, "subscribers.json")


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
# ВРЕМЯ
# ============================================================

def moscow_time():
    msk = timezone(timedelta(hours=3))
    return datetime.now(msk).strftime("%d.%m.%Y %H:%M:%S МСК")


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )
    except Exception as e:
        print("Ошибка сохранения:", e)


# ============================================================
# СОСТОЯНИЕ
# ============================================================

DEFAULT_STATE = {
    "status": "green",
    "text": "Опасность не объявлена",
    "source": "",
    "post_id": "",
    "location": "",
    "updated": "",
}


state = load_json(STATE_FILE, DEFAULT_STATE)
history = load_json(HISTORY_FILE, [])
subscribers = load_json(SUBSCRIBERS_FILE, {})


# ============================================================
# ПРОВЕРКИ
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

    return any(word in text for word in words)


def detect_status(text):
    text = text.lower()

    # Красный
    red_words = [
        "ракетная опасность",
        "ракетной опасности",
        "ракетная тревога",
        "опасность по ракетам",
        "угроза ракетного нападения",
        "ракетная угроза",
    ]

    if any(word in text for word in red_words):
        return "red"

    # Отбой
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

    if any(word in text for word in green_words):
        return "green"

    # Желтый
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

    if any(word in text for word in yellow_words):
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
        any(word in text for word in drone_words)
        and any(word in text for word in danger_words)
    ):
        return "yellow"

    return None


def status_name(status):
    if status == "red":
        return "🔴 Ракетная опасность"

    if status == "yellow":
        return "🟡 Беспилотная опасность"

    return "🟢 Опасность не объявлена"


# ============================================================
# TELEGRAM SCRAPER
# ============================================================

def get_telegram_posts(url):
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
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

    except Exception as e:
        print("Ошибка источника:", e)
        return []


def find_location(text):
    text_lower = text.lower()

    # Сначала города
    for city in CITIES:
        if city.lower() in text_lower:
            return city

    # Затем округа
    for territory in TERRITORIES:
        if territory.lower() in text_lower:
            return territory

    # Только область
    if is_kostroma(text):
        return "Костромская область"

    return ""


def find_latest_status():
    candidates = []

    for source_id, source in SOURCES.items():

        posts = get_telegram_posts(
            source["url"]
        )

        for post in posts:

            text = post["text"]

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
                "post_id": post["post_id"],
                "location": find_location(text),
            })

    if not candidates:
        return None

    # Берём первый найденный подтверждённый статус
    return candidates[0]


# ============================================================
# ИСТОРИЯ
# ============================================================

def add_history(new_state):
    global history

    item = {
        "status": new_state["status"],
        "text": new_state["text"],
        "source": new_state["source"],
        "location": new_state["location"],
        "time": moscow_time(),
    }

    history.insert(0, item)

    history = history[:20]

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# ПОДПИСЧИКИ
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
# КЛАВИАТУРА
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="status"
            ),
        ],
        [
            InlineKeyboardButton(
                "📍 Моё место",
                callback_data="my_location"
            ),
            InlineKeyboardButton(
                "🔄 Изменить",
                callback_data="change_location"
            ),
        ],
        [
            InlineKeyboardButton(
                "📚 История",
                callback_data="history"
            ),
            InlineKeyboardButton(
                "📋 Сводка",
                callback_data="summary"
            ),
        ],
        [
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
        ],
        [
            InlineKeyboardButton(
                "📢 Наш канал",
                url="https://t.me/RADAR_Kostroma"
            ),
        ],
    ])


def location_keyboard():
    buttons = []

    for i, city in enumerate(CITIES):
        buttons.append(
            InlineKeyboardButton(
                city,
                callback_data=f"city:{i}"
            )
        )

    rows = []

    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i + 2])

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

    return InlineKeyboardMarkup(rows)


def territory_keyboard():
    buttons = []

    for i, territory in enumerate(TERRITORIES):
        buttons.append(
            InlineKeyboardButton(
                territory,
                callback_data=f"terr:{i}"
            )
        )

    rows = []

    for i in range(0, len(buttons), 1):
        rows.append(buttons[i:i + 1])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="change_location"
        )
    ])

    return InlineKeyboardMarkup(rows)


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
# ФОРМАТИРОВАНИЕ
# ============================================================

def status_text():
    return (
        "🚨 <b>UAV ALERT</b>\n\n"
        f"{status_name(state.get('status'))}\n\n"
        f"📍 Регион: <b>Костромская область</b>\n"
        f"🕐 Обновлено: {state.get('updated', '—')}\n"
        f"📡 Источник: {state.get('source', '—')}\n"
    )


def summary_text():
    return (
        "📋 <b>Сводка UAV ALERT</b>\n\n"
        f"📍 Регион: Костромская область\n"
        f"🚦 Статус: {status_name(state.get('status'))}\n"
        f"📍 Место: {state.get('location') or 'не указано'}\n"
        f"🕐 Последнее обновление: "
        f"{state.get('updated', '—')}\n"
        f"📡 Источник: {state.get('source', '—')}\n\n"
        "⚠️ Информация носит справочный характер. "
        "Приоритет имеют официальные сообщения "
        "органов власти и МЧС."
    )


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if user:
        get_user(user.id)

    text = (
        "🚨 <b>UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "по ситуации в Костромской области.\n\n"
        "Выберите нужный раздел:"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ============================================================
# STATUS
# ============================================================

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        status_text(),
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# ============================================================
# STOP
# ============================================================

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    user = get_user(user_id)

    user["notifications"] = False

    save_json(
        SUBSCRIBERS_FILE,
        subscribers
    )

    await update.message.reply_text(
        "🔕 Уведомления отключены.\n\n"
        "Включить их можно через ⚙️ Настройки."
    )


# ============================================================
# ADMIN TEST
# ============================================================

def is_admin(update):

    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update):
        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    text = (
        "🧪 <b>ТЕСТ UAV ALERT</b>\n\n"
        "🟡 Демонстрационное уведомление.\n"
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
    except Exception as e:
        print("Ошибка отправки теста:", e)


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
    user = get_user(user_id)

    data = query.data

    # ---------------- STATUS ----------------

    if data == "status":

        await query.edit_message_text(
            status_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # ---------------- MY LOCATION ----------------

    elif data == "my_location":

        location = user.get(
            "location",
            "Кострома"
        )

        await query.edit_message_text(
            f"📍 <b>Ваше место</b>\n\n"
            f"Вы выбрали: <b>{location}</b>",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # ---------------- CHANGE LOCATION ----------------

    elif data == "change_location":

        await query.edit_message_text(
            "📍 <b>Выберите город:</b>",
            parse_mode="HTML",
            reply_markup=location_keyboard()
        )

    # ---------------- CITY ----------------

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
                f"✅ Место сохранено.\n\n"
                f"📍 <b>{location}</b>",
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )

        except Exception:
            await query.edit_message_text(
                "Ошибка выбора города."
            )

    # ---------------- TERRITORIES ----------------

    elif data == "territories":

        await query.edit_message_text(
            "🏘 <b>Выберите муниципальный округ:</b>",
            parse_mode="HTML",
            reply_markup=territory_keyboard()
        )

    # ---------------- TERRITORY ----------------

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
                f"✅ Место сохранено.\n\n"
                f"📍 <b>{location}</b>",
                parse_mode="HTML",
                reply_markup=main_keyboard()
            )

        except Exception:
            await query.edit_message_text(
                "Ошибка выбора округа."
            )

    # ---------------- HISTORY ----------------

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
                    f"📍 {item.get('location') or 'Костромская область'}\n"
                )

            text = "\n".join(lines)

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # ---------------- SUMMARY ----------------

    elif data == "summary":

        await query.edit_message_text(
            summary_text(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # ---------------- SOURCES ----------------

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
            "⚠️ Указанные источники могут быть "
            "вторичными. Приоритет имеют официальные "
            "сообщения органов власти и МЧС."
        )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

    # ---------------- SETTINGS ----------------

    elif data == "settings":

        notifications = (
            "включены"
            if user.get("notifications", True)
            else "выключены"
        )

        green = (
            "включены"
            if user.get("green_notifications", True)
            else "выключены"
        )

        text = (
            "⚙️ <b>Настройки</b>\n\n"
            f"🔔 Уведомления: <b>{notifications}</b>\n"
            f"🟢 Уведомления об отбое: <b>{green}</b>"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=settings_keyboard()
        )

    # ---------------- TOGGLE NOTIFICATIONS ----------------

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

    # ---------------- TOGGLE GREEN ----------------

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

    # ---------------- BACK ----------------

    elif data == "back":

        await query.edit_message_text(
            "🚨 <b>UAV ALERT</b>\n\n"
            "Выберите нужный раздел:",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )


# ============================================================
# УВЕДОМЛЕНИЯ
# ============================================================

async def notify_subscribers(bot, new_state):

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

        # Отправляем уведомление только если
        # выбранное место явно встречается
        # в исходном сообщении.
        if location.lower() not in source_text.lower():

            if location != "Костромская область":
                continue

        status = new_state["status"]

        if (
            status == "green"
            and not user.get(
                "green_notifications",
                True
            )
        ):
            continue

        message = (
            f"🚨 <b>UAV ALERT</b>\n\n"
            f"{status_name(status)}\n\n"
            f"📍 {location}\n"
            f"🕐 {new_state.get('updated', moscow_time())}\n"
            f"📡 {new_state.get('source', '—')}\n\n"
            "⚠️ Информация носит справочный характер. "
            "Приоритет имеют официальные сообщения "
            "органов власти и МЧС."
        )

        try:

            await bot.send_message(
                chat_id=int(user_id),
                text=message,
                parse_mode="HTML"
            )

        except Exception as e:

            print(
                "Ошибка уведомления:",
                user_id,
                e
            )


# ============================================================
# МОНИТОРИНГ
# ============================================================

async def monitor_loop(bot):

    global state

    while True:

        try:

            result = find_latest_status()

            if result:

                new_state = {
                    "status": result["status"],
                    "text": result["text"],
                    "source": result["source"],
                    "post_id": result["post_id"],
                    "location": result["location"],
                    "updated": moscow_time(),
                }

                old_status = state.get(
                    "status"
                )

                old_post = state.get(
                    "post_id"
                )

                if (
                    new_state["status"] != old_status
                    or new_state["post_id"] != old_post
                ):

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

                    channel_message = (
                        f"🚨 <b>UAV ALERT</b>\n\n"
                        f"{status_name(state['status'])}\n\n"
                        f"📍 {state['location'] or 'Костромская область'}\n"
                        f"🕐 {state['updated']}\n"
                        f"📡 {state['source']}\n\n"
                        "⚠️ Информация носит "
                        "справочный характер.\n"
                        "Приоритет имеют официальные "
                        "сообщения органов власти и МЧС."
                    )

                    try:

                        await bot.send_message(
                            chat_id=CHANNEL,
                            text=channel_message,
                            parse_mode="HTML"
                        )

                    except Exception as e:

                        print(
                            "Ошибка канала:",
                            e
                        )

        except Exception as e:

            print(
                "Ошибка мониторинга:",
                e
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# СТАТИСТИКА АДМИНА
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
        f"👥 Пользователей: {len(subscribers)}\n"
        f"🔔 Активных уведомлений: {active}\n"
        f"📡 Источников: {len(SOURCES)}\n"
        f"📚 Записей истории: {len(history)}\n"
        f"📍 Городов: {len(CITIES)}\n"
        f"🏘 Округов: {len(TERRITORIES)}\n"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
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

    return jsonify(state)


def run_flask():

    app_web.run(
        host="0.0.0.0",
        port=PORT
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):

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
            "ОШИБКА: BOT_TOKEN не найден."
        )

        return

    # Flask работает отдельно,
    # чтобы Render видел активный web-сервис.
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

    application.add_handler(
        CallbackQueryHandler(
            callback_router
        )
    )

    print(
        "🚨 UAV ALERT запущен"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
