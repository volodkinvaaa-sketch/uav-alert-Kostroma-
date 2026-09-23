import os
import json
import asyncio
import threading
import hashlib
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
# НАСЕЛЁННЫЕ ПУНКТЫ
#
# ВАЖНО:
# город сам по себе НЕ означает опасность.
# Он только может быть указан как место события.
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
# =========================================================
# РАЙОНЫ
# =========================================================
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
REGION_WORDS = [
    "костромская область",
    "костромской области",
    "костромской обл",
    "костромская обл",
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
# БПЛА — ВНИМАНИЕ
# =========================================================
UAV_ATTENTION_WORDS = [
    "внимание по бпла",
    "внимание бпла",
    "внимание беспилотник",
    "внимание беспилотники",
    "внимание по беспилотникам",
]
# =========================================================
# БПЛА — УГРОЗА
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
# БПЛА — ОПАСНОСТЬ
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
# ФИКСАЦИЯ
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
    "title": "🟢 Опасность не объявлена",
    # 0 = нет
    # 1 = внимание
    # 2 = угроза
    # 3 = опасность
    "uav_level": 0,
    "rocket_active": False,
    "location": "Костромская область",
    # Список активных районов
    "districts": [],
    # Города отдельно
    "cities": [],
    "source": "",
    "source_url": "",
    "updated_at": "",
    "event_post_id": "",
    "event_source_id": "",
}
# =========================================================
# JSON
# =========================================================
def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Ошибка чтения JSON:", e)
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
        print("Ошибка сохранения JSON:", e)
def save_all():
    save_json(
        STATE_FILE,
        state
    )
def load_state():
    global state
    saved = load_json(
        STATE_FILE,
        {}
    )
    if isinstance(saved, dict):
        state.update(saved)
    # Защита старого формата
    if not isinstance(
        state.get("districts"),
        list
    ):
        state["districts"] = []
    if not isinstance(
        state.get("cities"),
        list
    ):
        state["cities"] = []
    rebuild_state_status()
# =========================================================
# ВРЕМЯ
# =========================================================
def now_string():
    return datetime.now(
        timezone.utc
    ).strftime(
        "%d.%m.%Y %H:%M UTC"
    )
# =========================================================
# УРОВНИ
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
# ОБЩИЙ СТАТУС
# =========================================================
def rebuild_state_status():
    level = int(
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
    if rocket and level > 0:
        state["status"] = "both"
        state["title"] = (
            "🚨 Ракетная опасность + "
            + uav_level_name(level)
        )
        return
    if rocket:
        state["status"] = "red"
        state["title"] = "🚨 Ракетная опасность"
        return
    if level == 3:
        state["status"] = "uav_danger"
        state["title"] = "🔴 Опасность по БПЛА"
        return
    if level == 2:
        state["status"] = "uav_threat"
        state["title"] = "🟠 Угроза по БПЛА"
        return
    if level == 1:
        state["status"] = "uav_attention"
        state["title"] = "🟡 Внимание по БПЛА"
        return
    state["status"] = "green"
    state["title"] = "🟢 Опасность не объявлена"
# =========================================================
# ПРОВЕРКА КОСТРОМСКОЙ ОБЛАСТИ
# =========================================================
def is_kostroma(text):
    text = text.lower().replace(
        "ё",
        "е"
    )
    for word in REGION_WORDS:
        if word in text:
            return True
    # Район сам по себе относится к области
    for district in DISTRICTS:
        if district in text:
            return True
    return False
# =========================================================
# ПОИСК РАЙОНОВ
# =========================================================
def detect_districts(text):
    text = text.lower().replace(
        "ё",
        "е"
    )
    found = []
    for key, name in DISTRICTS.items():
        if key in text:
            if name not in found:
                found.append(name)
    return found
# =========================================================
# ПОИСК ГОРОДОВ
# =========================================================
def detect_cities(text):
    text = text.lower().replace(
        "ё",
        "е"
    )
    found = []
    for city in CITIES:
        if city in text:
            found.append(
                city.capitalize()
            )
    return list(
        dict.fromkeys(found)
    )
# =========================================================
# ОПРЕДЕЛЕНИЕ СОБЫТИЯ
# =========================================================
def detect_status(text):
    text = text.lower().replace(
        "ё",
        "е"
    )
    # Отбой БПЛА
    for word in UAV_CANCEL_WORDS:
        if word in text:
            return "uav_cancel"
    # Отбой ракетной опасности
    for word in RED_CANCEL_WORDS:
        if word in text:
            return "red_cancel"
    # Опасность БПЛА
    for word in UAV_DANGER_WORDS:
        if word in text:
            return "uav_danger"
    # Угроза БПЛА
    for word in UAV_THREAT_WORDS:
        if word in text:
            return "uav_threat"
    # Внимание БПЛА
    for word in UAV_ATTENTION_WORDS:
        if word in text:
            return "uav_attention"
    # Ракетная опасность
    for word in RED_WORDS:
        if word in text:
            return "red"
    # Фиксация БПЛА
    for word in UAV_DETECTION_WORDS:
        if word in text:
            return "uav_attention"
    # Общие сообщения про БПЛА
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
    old_level = int(
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
    text = post.get(
        "text",
        ""
    )
    # Районы, которые реально указаны
    found_districts = detect_districts(
        text
    )
    # Города только для отображения
    found_cities = detect_cities(
        text
    )
    # -----------------------------------------------------
    # УРОВЕНЬ БПЛА
    # -----------------------------------------------------
    if detected_status == "uav_attention":
        state["uav_level"] = max(
            old_level,
            1
        )
    elif detected_status == "uav_threat":
        state["uav_level"] = max(
            old_level,
            2
        )
    elif detected_status == "uav_danger":
        state["uav_level"] = max(
            old_level,
            3
        )
    elif detected_status == "uav_cancel":
        state["uav_level"] = 0
        # При полном отбое очищаем районы
        state["districts"] = []
        state["cities"] = []
    # -----------------------------------------------------
    # РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------
    elif detected_status == "red":
        state["rocket_active"] = True
    elif detected_status == "red_cancel":
        state["rocket_active"] = False
    # -----------------------------------------------------
    # ОБЩИЙ ОТБОЙ
    # -----------------------------------------------------
    elif detected_status == "general_cancel":
        if old_level > 0 and not old_rocket:
            state["uav_level"] = 0
            state["districts"] = []
            state["cities"] = []
        elif old_rocket and old_level == 0:
            state["rocket_active"] = False
    # -----------------------------------------------------
    # ДОБАВЛЯЕМ НОВЫЕ РАЙОНЫ
    #
    # НИКОГДА НЕ ПЕРЕЗАПИСЫВАЕМ старый список.
    # -----------------------------------------------------
    if (
        detected_status
        in (
            "uav_attention",
            "uav_threat",
            "uav_danger",
        )
    ):
        current = state.get(
            "districts",
            []
        )
        if not isinstance(
            current,
            list
        ):
            current = []
        for district in found_districts:
            if district not in current:
                current.append(
                    district
                )
        state["districts"] = current
        # Важно:
        # города НЕ добавляем в список
        # "под опасностью".
        #
        # Поэтому упоминание "Макарьев"
        # само по себе не сделает его
        # городом под опасностью.
    # -----------------------------------------------------
    # ИНФОРМАЦИЯ ОБ ИСТОЧНИКЕ
    # -----------------------------------------------------
    state["location"] = "Костромская область"
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
        post.get(
            "post_id",
            ""
        )
    )
    state["event_source_id"] = post.get(
        "source_id",
        ""
    )
    rebuild_state_status()
    new_level = int(
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
    # Возвращаем, изменился ли основной статус
    return (
        old_level != new_level
        or old_rocket != new_rocket
    )
# =========================================================
# ФОРМАТ СОСТОЯНИЯ
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
        "Костромская область"
    )
    # -----------------------------------------------------
    # РАЙОНЫ
    # -----------------------------------------------------
    districts = state.get(
        "districts",
        []
    )
    if districts:
        lines.append("")
        lines.append(
            "<b>⚠️ Районы:</b>"
        )
        for district in districts:
            lines.append(
                f"• {district}"
            )
    # -----------------------------------------------------
    # ИСТОЧНИК
    # -----------------------------------------------------
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
# =========================================================
# ИСТОРИЯ
# =========================================================
def add_history(
    post,
    event_type
):
    history = load_json(
        HISTORY_FILE,
        []
    )
    if not isinstance(
        history,
        list
    ):
        history = []
    item = {
        "time": now_string(),
        "type": event_type,
        "text": post.get(
            "text",
            ""
        )[:1000],
        "source": post.get(
            "source",
            ""
        ),
        "source_url": post.get(
            "source_url",
            ""
        ),
    }
    history.insert(
        0,
        item
    )
    history = history[:100]
    save_json(
        HISTORY_FILE,
        history
    )
def load_history():
    return load_json(
        HISTORY_FILE,
        []
    )
# =========================================================
# ЗАЩИТА ОТ ПОВТОРНЫХ ПОСТОВ
# =========================================================
def make_post_id(post):
    source_id = str(
        post.get(
            "source_id",
            ""
        )
    )
    post_id = str(
        post.get(
            "post_id",
            ""
        )
    )
    text = post.get(
        "text",
        ""
    )
    # Если Telegram ID отсутствует,
    # создаём стабильный ID из текста.
    if not post_id:
        raw = (
            source_id
            + "|"
            + text
        )
        post_id = hashlib.sha256(
            raw.encode(
                "utf-8"
            )
        ).hexdigest()
    return (
        source_id
        + ":"
        + post_id
    )
def load_sent_posts():
    data = load_json(
        SENT_POSTS_FILE,
        []
    )
    if not isinstance(
        data,
        list
    ):
        return []
    return data
def save_sent_posts(posts):
    save_json(
        SENT_POSTS_FILE,
        posts
    )
# =========================================================
# ПОЛУЧЕНИЕ ПОСТОВ
# =========================================================
def fetch_source(
    source_id,
    source_data
):
    try:
        response = requests.get(
            source_data["url"],
            timeout=20,
            headers={
                "User-Agent":
                    "Mozilla/5.0 "
                    "UAV-ALERT/2.0"
            },
        )
        if response.status_code != 200:
            print(
                f"{source_id}: HTTP "
                f"{response.status_code}"
            )
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
            text_element = (
                message.select_one(
                    ".tgme_widget_message_text"
                )
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
            f"Ошибка источника "
            f"{source_id}: {e}"
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
            detected = detect_status(
                text
            )
            if not detected:
                continue
            # Для региона нужны либо
            # явные слова Костромской области,
            # либо район.
            if not is_kostroma(text):
                continue
            post["detected_status"] = (
                detected
            )
            events.append(post)
    return events
# =========================================================
# PUSH ПОДПИСЧИКАМ
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
                f"Ошибка PUSH "
                f"{user_id}: {e}"
            )
        await asyncio.sleep(
            0.05
        )
# =========================================================
# КАНАЛ
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
# НОВОЕ УВЕДОМЛЕНИЕ
# =========================================================
def notification_key(
    event,
    detected
):
    districts = detect_districts(
        event.get(
            "text",
            ""
        )
    )
    districts = sorted(
        districts
    )
    raw = (
        detected
        + "|"
        + "|".join(districts)
        + "|"
        + event.get(
            "source_id",
            ""
        )
        + "|"
        + event.get(
            "post_id",
            ""
        )
    )
    return hashlib.sha256(
        raw.encode(
            "utf-8"
        )
    ).hexdigest()
# =========================================================
# МОНИТОРИНГ
# =========================================================
async def initialize_from_sources():
    print(
        "Проверка источников "
        "при запуске..."
    )
    events = await asyncio.to_thread(
        collect_events
    )
    if not events:
        print(
            "Событий при запуске нет."
        )
        return
    # Только формируем актуальное состояние.
    # PUSH при запуске НЕ отправляем.
    for event in events:
        detected = event.get(
            "detected_status"
        )
        if detected:
            apply_event(
                event,
                detected
            )
    save_all()
    print(
        f"Инициализация завершена. "
        f"Событий: {len(events)}"
    )
async def monitor_loop(
    application
):
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
            # Очень важно:
            # обработанный пост больше
            # никогда не будет отправлен повторно.
            for event in events:
                post_id = make_post_id(
                    event
                )
                if post_id in sent_posts:
                    continue
                detected = event.get(
                    "detected_status"
                )
                if not detected:
                    continue
                print(
                    "Новое событие:",
                    detected
                )
                changed = apply_event(
                    event,
                    detected
                )
                # Сохраняем историю
                add_history(
                    event,
                    detected
                )
                save_all()
                # -------------------------------------------------
                # PUSH ОТПРАВЛЯЕМ ТОЛЬКО ОДИН РАЗ
                # НА КОНКРЕТНЫЙ НОВЫЙ ПОСТ.
                # -------------------------------------------------
                message = format_state()
                if detected in (
                    "uav_attention",
                    "uav_threat",
                    "uav_danger",
                    "uav_cancel",
                    "red",
                    "red_cancel",
                    "general_cancel",
                ):
                    # Если это новое событие —
                    # один PUSH.
                    await notify_subscribers(
                        application,
                        message
                    )
                    await publish_channel(
                        application,
                        message
                    )
                # Только после успешной обработки
                # записываем ID.
                sent_posts.append(
                    post_id
                )
            # Храним последние 2000 ID
            sent_posts = sent_posts[-2000:]
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
# КЛАВИАТУРА
# =========================================================
def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status"
            ),
            InlineKeyboardButton(
                "📍 Регион",
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
# START
# =========================================================
async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.effective_user:
        return
    user_id = (
        update.effective_user.id
    )
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
        "Гражданский информационный "
        "сервис оповещений.\n\n"
        "📍 Регион: "
        "<b>Костромская область</b>\n\n"
        "Уровни БПЛА:\n"
        "🟡 Внимание по БПЛА\n"
        "🟠 Угроза по БПЛА\n"
        "🔴 Опасность по БПЛА\n\n"
        "🚨 Ракетная опасность\n\n"
        "Вы подписаны на уведомления "
        "о новых событиях."
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
        format_state(),
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
    user_id = (
        update.effective_user.id
    )
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
        "Для повторного подключения "
        "используйте /start."
    )
# =========================================================
# STATS
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
        "<b>📈 UAV ALERT</b>\n\n"
        f"👥 Подписчиков: "
        f"<b>{len(subscribers)}</b>\n"
        f"📜 Событий в истории: "
        f"<b>{len(history)}</b>\n\n"
        "📍 Костромская область"
    )
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )
# =========================================================
# TEST
# =========================================================
async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Только для администратора."
        )
        return
    await update.message.reply_text(
        "🧪 Тест PUSH\n\n"
        "Система уведомлений работает."
    )
# =========================================================
# CALLBACKS
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
        districts = state.get(
            "districts",
            []
        )
        if districts:
            district_text = "\n".join(
                "• " + x
                for x in districts
            )
        else:
            district_text = (
                "• Активных районов "
                "не указано"
            )
        text = (
            "<b>📍 Костромская область</b>\n\n"
            "<b>Активные районы:</b>\n"
            + district_text
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
            f"👥 Подписчиков: "
            f"{len(subscribers)}\n"
            f"📜 Событий: "
            f"{len(history)}"
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
            "<b>📡 Источники</b>",
            ""
        ]
        for source in SOURCES.values():
            lines.append(
                "• " + source["name"]
            )
        lines.extend(
            [
                "",
                "⚠️ Данные агрегируются "
                "автоматически.",
                "Официальные сообщения "
                "имеют приоритет."
            ]
        )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )
        return
    if data == "channel":
        await query.edit_message_text(
            "<b>📢 UAV ALERT</b>\n\n"
            "Канал с обновлениями "
            "сервиса.",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )
# =========================================================
# POST INIT
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
                "Тест"
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
        "districts": state.get(
            "districts",
            []
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
