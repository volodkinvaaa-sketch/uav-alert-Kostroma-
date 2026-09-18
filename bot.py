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

# Твой публичный Telegram-канал
CHANNEL_ID = "@RADAR_Kostoma"

# Источники
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
# РАБОТА С ПОДПИСЧИКАМИ
# =========================================================

def load_subscribers():
    if not SUBSCRIBERS_FILE.exists():
        return []

    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

    except Exception:
        pass

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
# РАБОТА СО СТАТУСОМ
# =========================================================

def load_state():
    if not STATE_FILE.exists():
        return {
            "overall": "green",
            "sources": {
                "radar_russia": {
                    "status": "green",
                    "post": 0
                },
                "bpla_russia": {
                    "status": "green",
                    "post": 0
                },
                "radarmap": {
                    "status": "green",
                    "post": 0
                }
            }
        }

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            return json.load(f)

    except Exception:
        return {
            "overall": "green",
            "sources": {}
        }


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
# ПОЛУЧЕНИЕ TELEGRAM-КАНАЛОВ
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

        for message in soup.select(".tgme_widget_message"):
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
                        href.rstrip("/").split("/")[-1]
                    )
                except Exception:
                    post_id = 0

            posts.append({
                "text": text,
                "post_id": post_id
            })

        return posts

    except Exception as e:
        print(
            f"[SOURCE ERROR] {url}: {e}"
        )
        return []


# =========================================================
# ПРОВЕРКА КОСТРОМСКОЙ ОБЛАСТИ
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
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================================================

def detect_status(text):
    text = text.lower()

    # -----------------------------------------------------
    # КРАСНЫЙ — РАКЕТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # ЗЕЛЁНЫЙ — ОТБОЙ
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # ЖЁЛТЫЙ — БПЛА / БЕСПИЛОТНАЯ ОПАСНОСТЬ
    # -----------------------------------------------------

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

    # Более общий вариант
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

    has_drone = any(
        word in text
        for word in drone_words
    )

    has_danger = any(
        word in text
        for word in danger_words
    )

    if has_drone and has_danger:
        return "yellow"

    return None


# =========================================================
# ПОИСК ПОСЛЕДНЕГО СТАТУСА
# =========================================================

def find_latest_status(posts):
    found = []

    for post in posts:
        text = post.get(
            "text",
            ""
        )

        if not is_kostroma(text):
            continue

        status = detect_status(text)

        if status is None:
            continue

        found.append({
            "status": status,
            "post": post.get(
                "post_id",
                0
            )
        })

    if not found:
        return {
            "status": "green",
            "post": 0
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
                "post": 0
            }

        status = detect_status(text)

        if status is None:
            status = "green"

        return {
            "status": status,
            "post": 0
        }

    except Exception as e:
        print(
            f"[RADARMAP ERROR] {e}"
        )

        return {
            "status": "green",
            "post": 0
        }


# =========================================================
# ПРОВЕРКА ВСЕХ ИСТОЧНИКОВ
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

        result = find_latest_status(
            posts
        )

        results[source_id] = result

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
# ТЕКСТ СТАТУСА
# =========================================================

def status_text(status):
    if status == "red":
        return "🔴 РАКЕТНАЯ ОПАСНОСТЬ"

    if status == "yellow":
        return "🟡 ОПАСНОСТЬ БПЛА"

    return "🟢 ОПАСНОСТИ НЕ ОБЪЯВЛЕНО"


def make_notification(overall):
    return (
        "🚨 <b>UAV ALERT</b>\n\n"
        "📍 <b>Костромская область</b>\n\n"
        f"{status_text(overall)}\n\n"
        "ℹ️ Информационное уведомление.\n"
        "Ориентируйтесь на официальные сообщения "
        "органов власти и МЧС."
    )


# =========================================================
# ОТПРАВКА В КАНАЛ
# =========================================================

async def send_to_channel(application, text):
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

    if not subscribers:
        return

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
# КОМАНДА /START
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
        "Источники:\n"
        "📡 Радар Россия\n"
        "📢 БПЛА Россия\n"
        "🗺️ RadarMap\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/test — тестовое уведомление\n"
        "/stop — отключить уведомления"
    )


# =========================================================
# КОМАНДА /STOP
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
# КОМАНДА /STATUS
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

    sources = state.get(
        "sources",
        {}
    )

    text = (
        "🚨 <b>UAV ALERT</b>\n\n"
        "📍 <b>Костромская область</b>\n\n"
        f"<b>Общий статус:</b>\n"
        f"{status_text(overall)}\n\n"
        "<b>Источники:</b>\n"
    )

    for source_id, source in SOURCES.items():
        source_status = sources.get(
            source_id,
            {}
        ).get(
            "status",
            "green"
        )

        text += (
            f"{source['name']}: "
            f"{status_text(source_status)}\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML"
    )


# =========================================================
# КОМАНДА /TEST
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
        "⚠️ Это тестовое сообщение.\n"
        "Реальной опасности оно не означает.\n\n"
        "📡 Радар Россия\n"
        "📢 БПЛА Россия\n"
        "🗺️ RadarMap\n\n"
        "✅ Система уведомлений работает."
    )

    # Отправляем пользователю
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=test_message,
            parse_mode="HTML"
        )

        print(
            "[TEST] Сообщение отправлено пользователю."
        )

    except Exception as e:
        print(
            f"[TEST USER ERROR] {e}"
        )

    # Отправляем в канал
    channel_ok = await send_to_channel(
        context.application,
        test_message
    )

    if channel_ok:
        try:
            await update.message.reply_text(
                "✅ Тест отправлен тебе и в канал @RADAR_Kostoma."
            )
        except Exception:
            pass

    else:
        try:
            await update.message.reply_text(
                "❌ В канал отправить не получилось.\n\n"
                "Проверь, что бот является администратором "
                "канала и имеет право публиковать сообщения."
            )
        except Exception:
            pass


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitoring_loop(application):
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

            old_state = load_state()

            old_overall = old_state.get(
                "overall",
                "green"
            )

            new_state = {
                "overall": overall,
                "sources": results
            }

            save_state(new_state)

            print(
                f"[MONITOR] Статус: {overall}"
            )

            # Первый запуск ничего не отправляем
            if first_run:
                first_run = False

            # Отправляем только при изменении
            elif overall != old_overall:

                message = make_notification(
                    overall
                )

                print(
                    "[MONITOR] Статус изменился!"
                )

                # Пользователям
                await send_to_subscribers(
                    application,
                    message
                )

                # В канал
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
# ЗАПУСК МОНИТОРИНГА
# =========================================================

async def post_init(application):
    asyncio.create_task(
        monitoring_loop(application)
    )


# =========================================================
# MAIN
# =========================================================

def main():
    if not BOT_TOKEN:
        print(
            "❌ ОШИБКА: переменная BOT_TOKEN не задана."
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

    # Flask запускаем отдельно
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    # Telegram
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
