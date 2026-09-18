import os
import re
import json
import threading
import time

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@UAV_ALERT_Kostroma")

RADAR_URL = "https://t.me/s/radarrussiia"

PORT = int(os.getenv("PORT", "5001"))

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"


# =========================
# FLASK
# =========================

app = Flask(__name__)


@app.route("/")
def home():
    return "UAV ALERT работает."


@app.route("/status")
def status_page():
    state = load_state()
    return jsonify(state)


# =========================
# ФАЙЛЫ СОСТОЯНИЯ
# =========================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "status": "green",
            "last_post": 0
        }

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "status": "green",
            "last_post": 0
        }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def load_subscribers():
    if not os.path.exists(SUBSCRIBERS_FILE):
        return []

    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_subscribers(subscribers):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subscribers, f, ensure_ascii=False, indent=2)


# =========================
# КОМАНДЫ БОТА
# =========================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id not in subscribers:
        subscribers.append(user_id)
        save_subscribers(subscribers)

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Вы подписаны на информационные уведомления "
        "по Костромской области.\n\n"
        "Источник мониторинга: @radarrussiia\n\n"
        "⚠️ Информация носит информационный характер. "
        "Следуйте официальным указаниям государственных служб."
    )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id in subscribers:
        subscribers.remove(user_id)
        save_subscribers(subscribers)

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    state = load_state()

    if state["status"] == "yellow":
        text = "🟡 Беспилотная опасность по данным источника мониторинга."
    elif state["status"] == "red":
        text = "🔴 Ракетная опасность по данным источника мониторинга."
    else:
        text = "🟢 Опасность по данным источника не выявлена."

    await update.message.reply_text(
        "UAV ALERT\n\n" + text
    )


# =========================
# ОТПРАВКА УВЕДОМЛЕНИЙ
# =========================

def send_message(bot_token, chat_id, text):

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text
            },
            timeout=20
        )

        print(
            f"📨 Отправка {chat_id}: "
            f"{response.status_code}"
        )

    except Exception as e:
        print(f"❌ Ошибка отправки: {e}")


def notify_users(text):

    subscribers = load_subscribers()

    print(
        f"👥 Подписчиков для уведомления: "
        f"{len(subscribers)}"
    )

    for chat_id in subscribers:
        send_message(
            BOT_TOKEN,
            chat_id,
            text
        )

    if CHANNEL_USERNAME:

        send_message(
            BOT_TOKEN,
            CHANNEL_USERNAME,
            text
        )


# =========================
# ПОЛУЧЕНИЕ RADAR
# =========================

def get_radar_posts():

    print("🌐 Открываем источник:")
    print(RADAR_URL)

    try:

        headers = {
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "Chrome/140 Safari/537.36"
            )
        }

        response = requests.get(
            RADAR_URL,
            headers=headers,
            timeout=30
        )

        print(
            f"🌐 Ответ источника: "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            print("❌ Источник вернул ошибку.")
            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        messages = soup.select(
            ".tgme_widget_message"
        )

        print(
            f"📡 Найдено сообщений: "
            f"{len(messages)}"
        )

        posts = []

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

            data_post = message.get(
                "data-post",
                ""
            )

            post_id = 0

            if data_post:

                try:
                    post_id = int(
                        data_post.split("/")[-1]
                    )
                except Exception:
                    post_id = 0

            if text:

                posts.append({
                    "id": post_id,
                    "text": text
                })

        return posts

    except Exception as e:

        print(
            f"❌ Ошибка чтения источника: "
            f"{e}"
        )

        return []


# =========================
# РАСПОЗНАВАНИЕ КОСТРОМСКОЙ ОБЛАСТИ
# =========================

def is_kostroma(text):

    text = text.lower()

    words = [
        "костромская область",
        "костромской области",
        "костромская обл",
        "костромской обл",
        "кострома"
    ]

    return any(
        word in text
        for word in words
    )


def detect_status(text):

    text = text.lower()

    # Ракетная опасность
    missile_words = [
        "ракетная опасность",
        "ракетной опасности",
        "опасность по ракетам",
        "ракетная тревога"
    ]

    for word in missile_words:

        if word in text:
            return "red"

    # Отбой
    cancel_words = [
        "отбой опасности по бпла",
        "отбой опасности бпла",
        "отбой беспилотной опасности",
        "опасность бпла отменена",
        "беспилотная опасность отменена",
        "опасность по бпла снята",
        "опасность бпла снята",
        "отбой бпла"
    ]

    for word in cancel_words:

        if word in text:
            return "green"

    # Беспилотная опасность
    danger_words = [
        "опасность по бпла",
        "опасность бпла",
        "беспилотная опасность",
        "опасность беспилотников",
        "угроза бпла",
        "угроза беспилотников",
        "тревога по бпла",
        "внимание по бпла"
    ]

    for word in danger_words:

        if word in text:
            return "yellow"

    return None


# =========================
# ПРОВЕРКА ИСТОЧНИКА
# =========================

def check_radar():

    print("")
    print("🔎 Проверка Radar запущена.")

    posts = get_radar_posts()

    if not posts:

        print(
            "⚠️ Сообщения не найдены."
        )

        return

    state = load_state()

    old_status = state.get(
        "status",
        "green"
    )

    old_post = state.get(
        "last_post",
        0
    )

    print(
        f"📌 Предыдущий статус: "
        f"{old_status}"
    )

    print(
        f"📌 Последний обработанный пост: "
        f"{old_post}"
    )

    # Показываем последние сообщения в логах
    for post in posts[:10]:

        preview = post["text"]

        if len(preview) > 250:
            preview = preview[:250] + "..."

        print(
            f"📨 Пост #{post['id']}: "
            f"{preview}"
        )

    # Ищем новые сообщения
    new_posts = [
        post
        for post in posts
        if post["id"] > old_post
    ]

    print(
        f"🆕 Новых сообщений: "
        f"{len(new_posts)}"
    )

    # Анализируем новые сообщения
    for post in reversed(new_posts):

        text = post["text"]

        if not is_kostroma(text):

            print(
                f"⏭ Пост #{post['id']} "
                f"не относится к Костромской области."
            )

            continue

        detected = detect_status(text)

        print(
            f"🧠 Пост #{post['id']} "
            f"Кострома → статус: "
            f"{detected}"
        )

        if detected is None:
            continue

        if detected == old_status:

            print(
                "ℹ️ Статус не изменился."
            )

            continue

        # Обновляем статус
        state["status"] = detected
        old_status = detected

        if detected == "yellow":

            message = (
                "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ\n\n"
                "В Костромской области "
                "зафиксировано сообщение "
                "об опасности БПЛА.\n\n"
                "📡 Источник мониторинга: "
                "@radarrussiia\n\n"
                "⚠️ Информация носит "
                "информационный характер. "
                "Следуйте официальным указаниям "
                "государственных служб."
            )

            print(
                "🟡 ОБНАРУЖЕНА БЕСПИЛОТНАЯ ОПАСНОСТЬ!"
            )

            notify_users(message)

        elif detected == "red":

            message = (
                "🔴 РАКЕТНАЯ ОПАСНОСТЬ\n\n"
                "В источнике обнаружено "
                "сообщение о ракетной опасности "
                "для Костромской области.\n\n"
                "📡 Источник мониторинга: "
                "@radarrussiia\n\n"
                "⚠️ Следуйте официальным "
                "указаниям государственных служб."
            )

            print(
                "🔴 ОБНАРУЖЕНА РАКЕТНАЯ ОПАСНОСТЬ!"
            )

            notify_users(message)

        elif detected == "green":

            message = (
                "🟢 ОТБОЙ БЕСПИЛОТНОЙ ОПАСНОСТИ\n\n"
                "В источнике обнаружено "
                "сообщение об отмене "
                "беспилотной опасности "
                "в Костромской области.\n\n"
                "📡 Источник мониторинга: "
                "@radarrussiia"
            )

            print(
                "🟢 ОБНАРУЖЕН ОТБОЙ!"
            )

            notify_users(message)

    # Запоминаем самый новый пост
    if posts:

        newest_id = max(
            post["id"]
            for post in posts
        )

        if newest_id > old_post:
            state["last_post"] = newest_id

    save_state(state)

    print(
        f"💾 Сохранено. "
        f"Статус: {state['status']}"
    )


# =========================
# ФОНОВАЯ ПРОВЕРКА
# =========================

def radar_loop():

    print(
        "🚨 UAV ALERT запускается..."
    )

    while True:

        try:

            check_radar()

        except Exception as e:

            print(
                f"❌ Ошибка проверки: {e}"
            )

        print(
            "⏳ Следующая проверка через 60 секунд."
        )

        time.sleep(60)


# =========================
# ЗАПУСК
# =========================

def run_flask():

    app.run(
        host="0.0.0.0",
        port=PORT
    )


def main():

    if not BOT_TOKEN:

        print(
            "❌ ОШИБКА: BOT_TOKEN не задан."
        )

        return

    # Flask
    threading.Thread(
        target=run_flask,
        daemon=True
    ).start()

    # Radar
    threading.Thread(
        target=radar_loop,
        daemon=True
    ).start()

    # Telegram
    application = (
        Application.builder()
        .token(BOT_TOKEN)
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

    print(
        "🤖 Telegram-бот запущен."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
