import os
import json
import asyncio
import threading
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_ID = "@RADAR_Kostoma"

SOURCES = {
    "radar_russia": {
        "name": "📡 Радар Россия",
        "url": "https://t.me/s/radarrussiia",
    },
    "bpla_russia": {
        "name": "📢 БПЛА Россия",
        "url": "https://t.me/s/bplarussiaru",
    },
    "radarmap": {
        "name": "🗺️ RadarMap",
        "url": "https://radar-map.ru/",
    },
}


# =========================================================
# ФАЙЛЫ
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

SUBSCRIBERS_FILE = BASE_DIR / "subscribers.json"
STATE_FILE = BASE_DIR / "state.json"


# =========================================================
# МУНИЦИПАЛЬНЫЕ ТЕРРИТОРИИ
# =========================================================

TERRITORIES = [
    "Кострома",
    "Буй",
    "Волгореченск",
    "Галич",
    "Шарья",

    "Антроповский муниципальный округ",
    "Буйский муниципальный район",
    "Вохомский муниципальный район",
    "Галичский муниципальный район",
    "Кадыйский муниципальный округ",
    "Кологривский муниципальный округ",
    "Костромской муниципальный район",
    "Мантуровский муниципальный округ",
    "Межевской муниципальный округ",
    "Нейский муниципальный округ",
    "Островский муниципальный округ",
    "Павинский муниципальный округ",
    "Парфеньевский муниципальный округ",
    "Поназыревский муниципальный округ",
    "Пыщугский муниципальный округ",
    "Солигаличский муниципальный округ",
]


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "UAV ALERT работает."


@app.route("/status")
def web_status():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({
            "overall": "green",
            "sources": {}
        })


def run_flask():
    app.run(
        host="0.0.0.0",
        port=10000,
        use_reloader=False
    )


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def load_subscribers():
    if not SUBSCRIBERS_FILE.exists():
        return []

    try:
        with open(
            SUBSCRIBERS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        return data if isinstance(data, list) else []

    except Exception:
        return []


def save_subscribers(subscribers):
    with open(
        SUBSCRIBERS_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            subscribers,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# СОСТОЯНИЕ
# =========================================================

def default_state():
    return {
        "overall": "green",
        "territories": [],
        "sources": {
            "radar_russia": {
                "status": "green",
                "post": 0,
                "territories": []
            },
            "bpla_russia": {
                "status": "green",
                "post": 0,
                "territories": []
            },
            "radarmap": {
                "status": "green",
                "post": 0,
                "territories": []
            }
        }
    }


def load_state():
    if not STATE_FILE.exists():
        return default_state()

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return default_state()


def save_state(state):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# ПОЛУЧЕНИЕ ПОСТОВ TELEGRAM
# =========================================================

def get_telegram_posts(url):
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        posts = []

        for message in soup.select(
            ".tgme_widget_message"
        ):
            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            text = ""

            if text_element:
                text = text_element.get_text(
                    " ",
                    strip=True
                )

            link = message.select_one(
                "a.tgme_widget_message_date"
            )

            post_id = 0

            if link:
                href = link.get("href", "")

                try:
                    post_id = int(
                        href.rstrip("/")
                        .split("/")[-1]
                    )
                except Exception:
                    post_id = 0

            posts.append({
                "text": text,
                "post_id": post_id
            })

        return posts

    except Exception as e:
        print(f"[SOURCE ERROR] {url}: {e}")
        return []


# =========================================================
# КОСТРОМСКАЯ ОБЛАСТЬ
# =========================================================

def is_kostroma(text):
    text = text.lower()

    keywords = [
        "кострома",
        "костромская область",
        "костромской области",
        "костромская обл",
        "костромской обл",
    ]

    return any(
        word in text
        for word in keywords
    )


# =========================================================
# ОПРЕДЕЛЕНИЕ ТЕРРИТОРИЙ
# =========================================================

def find_territories(text):
    text_lower = text.lower()

    found = []

    for territory in TERRITORIES:
        if territory.lower() in text_lower:
            if territory not in found:
                found.append(territory)

    return found


# =========================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================================================

def detect_status(text):
    text = text.lower()

    # 🔴 РАКЕТНАЯ ОПАСНОСТЬ

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

    # 🟢 ОТБОЙ

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

    # 🟡 БПЛА

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
        "беспилот",
        "беспилотник",
        "беспилотники",
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
        and
        any(word in text for word in danger_words)
    ):
        return "yellow"

    return None


# =========================================================
# ПОИСК ПОСЛЕДНЕГО СТАТУСА
# =========================================================

def find_latest_status(posts):
    found = []

    for post in posts:
        text = post.get("text", "")

        if not is_kostroma(text):
            continue

        status = detect_status(text)

        if status is None:
            continue

        territories = find_territories(text)

        found.append({
            "status": status,
            "post": post.get("post_id", 0),
            "territories": territories
        })

    if not found:
        return {
            "status": "green",
            "post": 0,
            "territories": []
        }

    found.sort(
        key=lambda x: x["post"],
        reverse=True
    )

    return found[0]


# =========================================================
# RADARMAP
# =========================================================

def get_radarmap_status():
    try:
        response = requests.get(
            SOURCES["radarmap"]["url"],
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        response.raise_for_status()

        text = BeautifulSoup(
            response.text,
            "html.parser"
        ).get_text(
            " ",
            strip=True
        )

        if not is_kostroma(text):
            return {
                "status": "green",
                "post": 0,
                "territories": []
            }

        status = detect_status(text)

        if status is None:
            status = "green"

        return {
            "status": status,
            "post": 0,
            "territories": find_territories(text)
        }

    except Exception as e:
        print(f"[RADARMAP ERROR] {e}")

        return {
            "status": "green",
            "post": 0,
            "territories": []
        }


# =========================================================
# ПРОВЕРКА ИСТОЧНИКОВ
# =========================================================

def check_sources():
    results = {}

    for source_id in [
        "radar_russia",
        "bpla_russia"
    ]:
        source = SOURCES[source_id]

        posts = get_telegram_posts(
            source["url"]
        )

        results[source_id] = find_latest_status(
            posts
        )

    results["radarmap"] = get_radarmap_status()

    return results


# =========================================================
# ОБЩИЙ СТАТУС
# =========================================================

def calculate_overall(results):
    statuses = [
        item["status"]
        for item in results.values()
    ]

    if "red" in statuses:
        return "red"

    if "yellow" in statuses:
        return "yellow"

    return "green"


# =========================================================
# ТЕРРИТОРИИ ИЗ ВСЕХ ИСТОЧНИКОВ
# =========================================================

def collect_territories(results):
    territories = []

    for result in results.values():
        for territory in result.get(
            "territories",
            []
        ):
            if territory not in territories:
                territories.append(territory)

    return territories


# =========================================================
# НАЗВАНИЕ СТАТУСА
# =========================================================

def status_text(status):
    if status == "red":
        return "🔴 РАКЕТНАЯ ОПАСНОСТЬ"

    if status == "yellow":
        return "🟡 ОПАСНОСТЬ БПЛА"

    return "🟢 ОПАСНОСТИ НЕ ОБЪЯВЛЕНО"


# =========================================================
# ФОРМИРОВАНИЕ УВЕДОМЛЕНИЯ
# =========================================================

def make_notification(
    overall,
    territories
):
    text = (
        "🚨 <b>UAV ALERT</b>\n\n"
        "📍 <b>Костромская область</b>\n\n"
        f"{status_text(overall)}\n\n"
    )

    if territories:
        text += (
            "🏘️ <b>Территории, указанные "
            "в источниках:</b>\n"
        )

        for territory in territories:
            text += f"• {territory}\n"

        text += "\n"

    else:
        text += (
            "🏘️ Конкретные территории "
            "в сообщении не указаны.\n\n"
        )

    text += (
        "ℹ️ Информационное уведомление.\n"
        "Если официальные сообщения доступны, "
        "ориентируйтесь прежде всего на них."
    )

    return text


# =========================================================
# ОТПРАВКА В КАНАЛ
# =========================================================

async def send_to_channel(
    application,
    text
):
    try:
        await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True
        )

        print(
            "[CHANNEL] Сообщение отправлено."
        )

        return True

    except Exception as e:
        print(
            f"[CHANNEL ERROR] {e}"
        )

        return False


# =========================================================
# ОТПРАВКА ПОДПИСЧИКАМ
# =========================================================

async def send_to_subscribers(
    application,
    text
):
    subscribers = load_subscribers()

    for user_id in subscribers:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="HTML"
            )

        except Exception as e:
            print(
                f"[USER ERROR] {user_id}: {e}"
            )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id not in subscribers:
        subscribers.append(user_id)
        save_subscribers(subscribers)

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Ты подписан на информационные уведомления "
        "по Костромской области.\n\n"
        "📡 Радар Россия\n"
        "📢 БПЛА Россия\n"
        "🗺️ RadarMap\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/test — тест\n"
        "/stop — отключить уведомления"
    )


# =========================================================
# /STOP
# =========================================================

async def stop_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id in subscribers:
        subscribers.remove(user_id)
        save_subscribers(subscribers)

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    state = load_state()

    overall = state.get(
        "overall",
        "green"
    )

    territories = state.get(
        "territories",
        []
    )

    text = (
        "🚨 <b>UAV ALERT</b>\n\n"
        "📍 <b>Костромская область</b>\n\n"
        f"{status_text(overall)}\n\n"
    )

    if territories:
        text += (
            "🏘️ <b>Территории:</b>\n"
        )

        for territory in territories:
            text += f"• {territory}\n"

    else:
        text += (
            "🏘️ Конкретные территории "
            "не указаны."
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================================================
# /TEST
# =========================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id not in subscribers:
        await update.message.reply_text(
            "Сначала нажми /start"
        )
        return

    test_message = (
        "🟡 <b>ТЕСТОВОЕ УВЕДОМЛЕНИЕ</b>\n\n"
        "🚨 <b>UAV ALERT</b>\n"
        "📍 Костромская область\n\n"
        "🏘️ Пример отображения территорий:\n"
        "• Кострома\n"
        "• Буй\n"
        "• Мантуровский муниципальный округ\n\n"
        "⚠️ Это тестовое сообщение.\n"
        "Реальной опасности оно не означает."
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=test_message,
            parse_mode="HTML"
        )
    except Exception as e:
        print(
            f"[TEST USER ERROR] {e}"
        )

    channel_ok = await send_to_channel(
        context.application,
        test_message
    )

    if channel_ok:
        await update.message.reply_text(
            "✅ Тест отправлен тебе и в @RADAR_Kostoma."
        )
    else:
        await update.message.reply_text(
            "❌ Не удалось отправить тест в канал.\n\n"
            "Проверь права администратора бота "
            "и разрешение на публикацию сообщений."
        )


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitoring_loop(
    application
):
    first_run = True

    while True:
        try:
            print(
                "[MONITOR] Проверка источников..."
            )

            results = check_sources()

            overall = calculate_overall(
                results
            )

            territories = collect_territories(
                results
            )

            old_state = load_state()

            old_overall = old_state.get(
                "overall",
                "green"
            )

            old_territories = old_state.get(
                "territories",
                []
            )

            new_state = {
                "overall": overall,
                "territories": territories,
                "sources": results
            }

            save_state(new_state)

            print(
                f"[MONITOR] Статус: {overall}"
            )

            print(
                f"[MONITOR] Территории: "
                f"{territories}"
            )

            if first_run:
                first_run = False

            else:
                status_changed = (
                    overall != old_overall
                )

                territories_changed = (
                    territories != old_territories
                )

                if (
                    status_changed
                    or territories_changed
                ):
                    message = make_notification(
                        overall,
                        territories
                    )

                    print(
                        "[MONITOR] Есть изменение — "
                        "отправляем уведомление."
                    )

                    await send_to_subscribers(
                        application,
                        message
                    )

                    await send_to_channel(
                        application,
                        message
                    )

        except Exception as e:
            print(
                f"[MONITOR ERROR] {e}"
            )

        await asyncio.sleep(60)


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application
):
    asyncio.create_task(
        monitoring_loop(application)
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        print(
            "❌ ОШИБКА: BOT_TOKEN не задан."
        )
        return

    print(
        "===================================="
    )
    print(
        "🚨 UAV ALERT запускается..."
    )
    print(
        "📢 Канал: @RADAR_Kostoma"
    )
    print(
        "===================================="
    )

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    application = (
        Application.builder()
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
            "stop",
            stop_command
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
            "test",
            test_command
        )
    )

    print(
        "🤖 Бот запущен!"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
