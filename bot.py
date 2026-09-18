import os
import json
import time
import threading
import requests
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()

PORT = int(os.getenv("PORT", "10000"))

# Официальная лента МЧС Костромской области
MCHS_URL = "https://44.mchs.gov.ru/deyatelnost/press-centr/novosti"

CHECK_INTERVAL = 60  # проверка каждую минуту

# =========================
# FLASK ДЛЯ RENDER
# =========================

web = Flask(__name__)


@web.route("/")
def home():
    return "UAV ALERT работает."


def run_web():
    web.run(
        host="0.0.0.0",
        port=PORT
    )


# =========================
# ХРАНЕНИЕ
# =========================

STATE_FILE = "state.json"


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "last_news": "",
            "status": "🟢 Опасность не объявлена"
        }


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


state = load_state()


# =========================
# ПОДПИСЧИКИ
# =========================

SUBSCRIBERS_FILE = "subscribers.json"


def load_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_subscribers(users):
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            users,
            f,
            ensure_ascii=False,
            indent=2
        )


# =========================
# КОМАНДЫ
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    users = load_subscribers()

    if user_id not in users:
        users.append(user_id)
        save_subscribers(users)

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Ты подписан на уведомления.\n\n"
        "Источник: официальная информация МЧС России "
        "по Костромской области.\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/stop — отключить уведомления"
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    users = load_subscribers()

    if user_id in users:
        users.remove(user_id)
        save_subscribers(users)

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        f"🚨 UAV ALERT\n\n"
        f"Текущий статус:\n"
        f"{state.get('status', '🟢 Опасность не объявлена')}\n\n"
        f"Источник: МЧС России по Костромской области"
    )


# =========================
# ОТПРАВКА СООБЩЕНИЯ
# =========================

async def send_alert(application, text):

    users = load_subscribers()

    for user_id in users.copy():

        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=text
            )

        except Exception as e:
            print(
                f"Не удалось отправить пользователю "
                f"{user_id}: {e}"
            )

    # Отправка в публичный канал
    if CHANNEL_USERNAME:

        try:
            await application.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=text
            )

            print("Сообщение отправлено в канал.")

        except Exception as e:
            print(
                f"Ошибка отправки в канал: {e}"
            )


# =========================
# ПРОВЕРКА МЧС
# =========================

def get_mchs_page():

    headers = {
        "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "Chrome/120 Safari/537.36"
    }

    response = requests.get(
        MCHS_URL,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    return response.text


def find_alert(text):

    text_lower = text.lower()

    # Сначала проверяем отбой
    if (
        "отбой беспилотной опасности"
        in text_lower
        or
        "опасность отменена"
        in text_lower
    ):
        return "🟢 Беспилотная опасность отменена."

    # Затем объявление опасности
    if (
        "беспилотная опасность"
        in text_lower
        or
        "опасность атаки беспилотников"
        in text_lower
        or
        "угроза беспилотной опасности"
        in text_lower
    ):
        return "🟡 Беспилотная опасность объявлена."

    return None


# =========================
# АВТОПРОВЕРКА
# =========================

async def automatic_check(application):

    global state

    print("Автоматическая проверка МЧС запущена.")

    while True:

        try:

            html = get_mchs_page()

            result = find_alert(html)

            if result:

                old_status = state.get(
                    "status",
                    "🟢 Опасность не объявлена"
                )

                if result != old_status:

                    state["status"] = result

                    save_state(state)

                    message = (
                        "🚨 UAV ALERT\n\n"
                        f"{result}\n\n"
                        "📍 Костромская область\n\n"
                        "Источник:\n"
                        "ГУ МЧС России "
                        "по Костромской области\n\n"
                        "⚠️ Следите за официальными "
                        "сообщениями экстренных служб."
                    )

                    print(
                        "Найдено изменение статуса:",
                        result
                    )

                    await send_alert(
                        application,
                        message
                    )

        except Exception as e:

            print(
                "Ошибка проверки МЧС:",
                e
            )

        await __import__("asyncio").sleep(
            CHECK_INTERVAL
        )


# =========================
# ЗАПУСК
# =========================

def main():

    if not BOT_TOKEN:

        print(
            "ОШИБКА: не задан BOT_TOKEN"
        )

        return

    if not CHANNEL_USERNAME:

        print(
            "ВНИМАНИЕ: CHANNEL_USERNAME "
            "не задан."
        )

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
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
            "stop",
            stop
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    async def post_init(app):
        app.create_task(
            automatic_check(app)
        )

    application.post_init = post_init

    print("🚨 UAV ALERT запускается...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
