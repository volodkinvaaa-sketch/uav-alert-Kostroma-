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
# ОТБОЙ БПЛА
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
# УРОВЕНЬ 1 — ВНИМАНИЕ
# =========================================================

UAV_ATTENTION_WORDS = [
    "внимание по бпла",
    "внимание бпла",
    "внимание беспилотник",
    "внимание беспилотники",
    "внимание по беспилотникам",
]


# =========================================================
# УРОВЕНЬ 2 — УГРОЗА
# =========================================================

UAV_THREAT_WORDS = [
    "угроза по бпла",
    "угроза бпла",
    "угроза беспилотников",
    "угроза беспилотника",
    "угроза беспилотных",
    "угроза атаки бпла",
]


# =========================================================
# УРОВЕНЬ 3 — ОПАСНОСТЬ
# =========================================================

UAV_DANGER_WORDS = [
    "опасность по бпла",
    "опасность бпла",
    "опасность беспилотников",
    "опасность беспилотника",
    "беспилотная опасность",
    "опасность беспилотной атаки",
]


# =========================================================
# ФИКСАЦИЯ БПЛА
# Считаем как "Внимание"
# =========================================================

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


# =========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =========================================================

def now_string():
    return datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


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
                indent=2,
            )
    except Exception as e:
        print("Ошибка сохранения:", e)


def save_all():
    save_json(STATE_FILE, state)

    subscribers = load_json(SUBSCRIBERS_FILE, [])
    save_json(SUBSCRIBERS_FILE, subscribers)


def load_state():
    global state

    saved = load_json(STATE_FILE, {})

    if isinstance(saved, dict):
        state.update(saved)

    rebuild_state_status()


def load_history():
    return load_json(HISTORY_FILE, [])


def load_sent_posts():
    return load_json(SENT_POSTS_FILE, [])


def save_sent_posts(posts):
    save_json(SENT_POSTS_FILE, posts)


# =========================================================
# НАЗВАНИЯ УРОВНЕЙ
# =========================================================

def uav_level_name(level):
    if level == 1:
        return "🟡 Внимание по БПЛА"

    if level == 2:
        return "🟠 Угроза по БПЛА"

    if level == 3:
        return "🔴 Опасность по БПЛА"

    return "🟢 Опасность не объявлена"


# =========================================================
# ОБНОВЛЕНИЕ ОБЩЕГО СТАТУСА
# =========================================================

def rebuild_state_status():

    uav_level = int(state.get("uav_level", 0))
    rocket = bool(state.get("rocket_active", False))

    if rocket and uav_level > 0:

        state["status"] = "both"

        state["title"] = (
            "🚨 Ракетная опасность + "
            + uav_level_name(uav_level)
        )

        return

    if rocket:

        state["status"] = "red"
        state["title"] = "🚨 Ракетная опасность"

        return

    if uav_level == 3:

        state["status"] = "uav_danger"
        state["title"] = "🔴 Опасность по БПЛА"

        return

    if uav_level == 2:

        state["status"] = "uav_threat"
        state["title"] = "🟠 Угроза по БПЛА"

        return

    if uav_level == 1:

        state["status"] = "uav_attention"
        state["title"] = "🟡 Внимание по БПЛА"

        return

    state["status"] = "green"
    state["title"] = "🟢 Опасность не объявлена"


# =========================================================
# ОПРЕДЕЛЕНИЕ РЕГИОНА
# =========================================================

def is_kostroma(text):

    text = text.lower()

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

    text = text.lower()

    for city in CITIES:

        if city in text:

            return city.capitalize()

    return ""


# =========================================================
# ОПРЕДЕЛЕНИЕ РАЙОНА
# =========================================================

def detect_territory(text):

    text = text.lower()

    for territory in TERRITORIES:

        if territory in text:

            return territory.capitalize()

    return ""


# =========================================================
# ОПРЕДЕЛЕНИЕ СОБЫТИЯ
# =========================================================

def detect_status(text):

    text = text.lower().replace("ё", "е")

    # Сначала проверяем отбой БПЛА
    for word in UAV_CANCEL_WORDS:

        if word in text:
            return "uav_cancel"

    # Потом отбой ракетной опасности
    for word in RED_CANCEL_WORDS:

        if word in text:
            return "red_cancel"

    # ОПАСНОСТЬ БПЛА
    for word in UAV_DANGER_WORDS:

        if word in text:
            return "uav_danger"

    # УГРОЗА БПЛА
    for word in UAV_THREAT_WORDS:

        if word in text:
            return "uav_threat"

    # ВНИМАНИЕ БПЛА
    for word in UAV_ATTENTION_WORDS:

        if word in text:
            return "uav_attention"

    # РАКЕТНАЯ ОПАСНОСТЬ
    for word in RED_WORDS:

        if word in text:
            return "red"

    # ФИКСАЦИЯ БПЛА
    for word in UAV_DETECTION_WORDS:

        if word in text:
            return "uav_attention"

    # Дополнительное определение
    has_drone = any(
        word in text
        for word in DRONE_WORDS
    )

    if has_drone:

        if "опасность" in text:
            return "uav_danger"

        if "угроза" in text:
            return "uav_threat"

        return "uav_attention"

    # Общий отбой
    for word in GENERAL_CANCEL_WORDS:

        if word in text:
            return "general_cancel"

    return None


# =========================================================
# ПРИМЕНЕНИЕ СОБЫТИЯ
# =========================================================

def apply_event(post, detected_status):

    old_uav = int(state.get("uav_level", 0))
    old_rocket = bool(state.get("rocket_active", False))

    text = post.get("text", "")

    city = detect_city(text)
    territory = detect_territory(text)

    if detected_status == "uav_attention":

        state["uav_level"] = max(
            old_uav,
            1
        )

    elif detected_status == "uav_threat":

        state["uav_level"] = max(
            old_uav,
            2
        )

    elif detected_status == "uav_danger":

        state["uav_level"] = max(
            old_uav,
            3
        )

    elif detected_status == "uav_cancel":

        state["uav_level"] = 0

    elif detected_status == "red":

        state["rocket_active"] = True

    elif detected_status == "red_cancel":

        state["rocket_active"] = False

    elif detected_status == "general_cancel":

        # Если активен только БПЛА
        if old_uav > 0 and not old_rocket:

            state["uav_level"] = 0

        # Если активна только ракетная опасность
        elif old_rocket and old_uav == 0:

            state["rocket_active"] = False

    # Информация о событии
    state["location"] = "Костромская область"

    if city:
        state["city"] = city

    if territory:
        state["territory"] = territory

    state["source"] = post.get(
        "source",
        ""
    )

    state["source_url"] = post.get(
        "source_url",
        ""
    )

    state["updated_at"] = now_string()

    state["event_post_id"] = str(
        post.get("post_id", "")
    )

    state["event_source_id"] = post.get(
        "source_id",
        ""
    )

    rebuild_state_status()

    new_uav = int(
        state.get("uav_level", 0)
    )

    new_rocket = bool(
        state.get("rocket_active", False)
    )

    return (
        old_uav != new_uav
        or old_rocket != new_rocket
    )


# =========================================================
# ФОРМАТ ТЕКУЩЕГО СОСТОЯНИЯ
# =========================================================

def format_state():

    rebuild_state_status()

    lines = []

    lines.append(
        "<b>🚨 UAV ALERT</b>"
    )

    lines.append("")

    lines.append(
        f"<b>Статус:</b> {state['title']}"
    )

    lines.append("")

    lines.append(
        "📍 <b>Регион:</b> "
        + state.get(
            "location",
            "Костромская область"
        )
    )

    if state.get("city"):

        lines.append(
            f"🏙 <b>Город:</b> {state['city']}"
        )

    if state.get("territory"):

        lines.append(
            f"🗺 <b>Район:</b> {state['territory']}"
        )

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


# =========================================================
# ИСТОРИЯ
# =========================================================

def add_history(post, event_type):

    history = load_history()

    item = {
        "time": now_string(),
        "type": event_type,
        "text": post.get("text", "")[:1000],
        "source": post.get("source", ""),
        "source_url": post.get("source_url", ""),
    }

    history.insert(0, item)

    history = history[:100]

    save_json(
        HISTORY_FILE,
        history
    )


# =========================================================
# ПОЛУЧЕНИЕ ПОСТОВ TELEGRAM
# =========================================================

def fetch_source(source_id, source_data):

    try:

        response = requests.get(
            source_data["url"],
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "UAV-ALERT/1.0"
                )
            },
        )

        if response.status_code != 200:
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        posts = []

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

            if not text:
                continue

            data_post = message.get(
                "data-post",
                ""
            )

            posts.append(
                {
                    "post_id": data_post,
                    "source_id": source_id,
                    "source": source_data["name"],
                    "source_url": source_data["url"],
                    "text": text,
                }
            )

        return posts

    except Exception as e:

        print(
            f"Ошибка источника {source_id}:",
            e
        )

        return []


# =========================================================
# СБОР СОБЫТИЙ
# =========================================================

def collect_events():

    events = []

    for source_id, source_data in SOURCES.items():

        posts = fetch_source(
            source_id,
            source_data
        )

        for post in posts:

            text = post.get(
                "text",
                ""
            )

            # Нам нужны только сообщения
            # относящиеся к Костромской области
            if not is_kostroma(text):
                continue

            detected = detect_status(text)

            if not detected:
                continue

            post["detected_status"] = detected

            events.append(post)

    return events


# =========================================================
# ОТПРАВКА ПОЛЬЗОВАТЕЛЯМ
# =========================================================

async def notify_subscribers(
    application,
    text
):

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    if not isinstance(
        subscribers,
        list
    ):
        return

    for user_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=int(user_id),
                text=text,
                parse_mode="HTML",
            )

        except Exception as e:

            print(
                f"Ошибка отправки {user_id}:",
                e
            )

        await asyncio.sleep(0.05)


# =========================================================
# ОТПРАВКА В КАНАЛ
# =========================================================

async def publish_channel(
    application,
    text
):

    if not CHANNEL:
        return

    try:

        await application.bot.send_message(
            chat_id=CHANNEL,
            text=text,
            parse_mode="HTML",
        )

    except Exception as e:

        print(
            "Ошибка отправки в канал:",
            e
        )


# =========================================================
# ИНИЦИАЛИЗАЦИЯ
# =========================================================

async def initialize_from_sources():

    print(
        "Проверяем источники при запуске..."
    )

    events = await asyncio.to_thread(
        collect_events
    )

    if not events:

        print(
            "Новых событий при запуске нет."
        )

        return

    # Старые события сначала
    # применяем к состоянию
    for event in events:

        detected = event.get(
            "detected_status"
        )

        if not detected:
            continue

        apply_event(
            event,
            detected
        )

    save_all()

    print(
        f"Обработано событий: {len(events)}"
    )


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitor_loop(application):

    await initialize_from_sources()

    while True:

        try:

            print(
                "Проверяем источники..."
            )

            events = await asyncio.to_thread(
                collect_events
            )

            sent_posts = load_sent_posts()

            for event in events:

                post_id = (
                    event.get("source_id", "")
                    + ":"
                    + str(
                        event.get(
                            "post_id",
                            ""
                        )
                    )
                )

                # Уже обрабатывали
                if post_id in sent_posts:
                    continue

                detected = event.get(
                    "detected_status"
                )

                if not detected:
                    continue

                print(
                    "Новое событие:",
                    detected,
                    event.get("text", "")[:150]
                )

                changed = apply_event(
                    event,
                    detected
                )

                # Сохраняем историю,
                # если событие новое
                if changed:

                    save_all()

                    add_history(
                        event,
                        detected
                    )

                # =================================================
                # ВАЖНО:
                #
                # Даже если уровень уже активен,
                # НОВОЕ сообщение должно отправить PUSH.
                # =================================================

                if detected in (
                    "uav_attention",
                    "uav_threat",
                    "uav_danger",
                    "uav_cancel",
                    "red",
                    "red_cancel",
                    "general_cancel",
                ):

                    message = format_state()

                    await publish_channel(
                        application,
                        message
                    )

                    await notify_subscribers(
                        application,
                        message
                    )

                # Помечаем пост обработанным
                sent_posts.append(
                    post_id
                )

            # Ограничиваем размер базы
            sent_posts = sent_posts[-1000:]

            save_sent_posts(
                sent_posts
            )

        except Exception as e:

            print(
                "Ошибка мониторинга:",
                e
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# TELEGRAM КЛАВИАТУРА
# =========================================================

def main_keyboard():

    keyboard = [

        [
            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status"
            ),

            InlineKeyboardButton(
                "📍 Костромская область",
                callback_data="location"
            ),
        ],

        [
            InlineKeyboardButton(
                "📈 Статистика",
                callback_data="stats"
            ),

            InlineKeyboardButton(
                "📜 История",
                callback_data="history"
            ),
        ],

        [
            InlineKeyboardButton(
                "📡 Источники",
                callback_data="sources"
            ),

            InlineKeyboardButton(
                "📢 Канал",
                callback_data="channel"
            ),
        ],

    ]

    return InlineKeyboardMarkup(
        keyboard
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    user_id = user.id

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    if user_id not in subscribers:

        subscribers.append(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )

    text = (
        "<b>🚨 UAV ALERT</b>\n\n"
        "Гражданский информационный сервис "
        "оповещений.\n\n"
        "📍 Регион: <b>Костромская область</b>\n\n"
        "Бот отслеживает сообщения источников "
        "и показывает текущий статус.\n\n"
        "🟡 Внимание по БПЛА\n"
        "🟠 Угроза по БПЛА\n"
        "🔴 Опасность по БПЛА\n"
        "🚨 Ракетная опасность\n\n"
        "Вы автоматически получаете уведомления "
        "о новых событиях."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
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

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    if user_id in subscribers:

        subscribers.remove(
            user_id
        )

        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )

    await update.message.reply_text(
        "🔕 Уведомления отключены.\n\n"
        "Чтобы включить их снова — отправьте /start."
    )


# =========================================================
# /STATS
# =========================================================

async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    history = load_history()

    text = (
        "<b>📈 Статистика UAV ALERT</b>\n\n"
        f"👥 Подписчиков: <b>{len(subscribers)}</b>\n"
        f"📜 Событий в истории: <b>{len(history)}</b>\n"
        "📍 Регион: <b>Костромская область</b>"
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
        "🧪 Тестовое уведомление UAV ALERT\n\n"
        "Проверка системы уведомлений выполнена."
    )


# =========================================================
# CALLBACK
# =========================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "status":

        await query.edit_message_text(
            format_state(),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "location":

        text = (
            "<b>📍 Регион мониторинга</b>\n\n"
            "Костромская область\n\n"
            "Отслеживаются населённые пункты "
            "и районы Костромской области."
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "stats":

        subscribers = load_json(
            SUBSCRIBERS_FILE,
            []
        )

        history = load_history()

        text = (
            "<b>📈 Статистика</b>\n\n"
            f"👥 Подписчиков: {len(subscribers)}\n"
            f"📜 Событий: {len(history)}"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "history":

        history = load_history()

        if not history:

            text = (
                "<b>📜 История</b>\n\n"
                "История пока пуста."
            )

        else:

            lines = [
                "<b>📜 Последние события</b>",
                ""
            ]

            for item in history[:10]:

                lines.append(
                    f"🕐 {item.get('time', '')}"
                )

                lines.append(
                    f"📌 {item.get('type', '')}"
                )

                lines.append(
                    f"📡 {item.get('source', '')}"
                )

                lines.append("")

            text = "\n".join(lines)

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "sources":

        lines = [
            "<b>📡 Источники информации</b>",
            ""
        ]

        for source in SOURCES.values():

            lines.append(
                f"• {source['name']}"
            )

        lines.append("")
        lines.append(
            "⚠️ Информация автоматически "
            "проверяется ботом. "
            "Приоритет следует отдавать "
            "официальным сообщениям властей."
        )

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "channel":

        await query.edit_message_text(
            "<b>📢 Канал UAV ALERT</b>\n\n"
            "Следите за обновлениями "
            "в канале.",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return


# =========================================================
# TELEGRAM POST INIT
# =========================================================

async def post_init(
    application: Application
):

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Включить уведомления"
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
                "Отключить уведомления"
            ),
            BotCommand(
                "test",
                "Тест уведомлений"
            ),
        ]
    )

    asyncio.create_task(
        monitor_loop(application)
    )

    print(
        "Мониторинг UAV ALERT запущен."
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
        "uav_level": state.get(
            "uav_level",
            0
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

        print(
            "ОШИБКА: BOT_TOKEN не найден."
        )

        return

    load_state()

    print(
        "Текущее состояние:"
    )

    print(
        format_state()
    )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

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
            stop_command
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
            "test",
            test_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    print(
        "UAV ALERT запускается..."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
