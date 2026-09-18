import os
import json
import asyncio
import threading
import requests

from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()

PORT = int(os.getenv("PORT", "10000"))

RADAR_URL = "https://t.me/s/radarrussiia"

CHECK_INTERVAL = 60

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"


# =========================================================
# FLASK
# =========================================================

web = Flask(__name__)


@web.route("/")
def home():
    return "UAV ALERT работает."


def run_web():
    web.run(
        host="0.0.0.0",
        port=PORT
    )


# =========================================================
# СОСТОЯНИЕ
# =========================================================

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "status": "🟢 Опасность не объявлена",
            "last_post": 0
        }


def save_state():
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


state = load_state()


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

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


# =========================================================
# КОМАНДЫ
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    users = load_subscribers()

    if user_id not in users:
        users.append(user_id)
        save_subscribers(users)

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Ты подписан на уведомления.\n\n"
        "📍 Костромская область\n\n"
        "📡 Источник мониторинга:\n"
        "Radar / @radarrussiia\n\n"
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
        "🚨 UAV ALERT\n\n"
        f"Текущий статус:\n"
        f"{state.get('status', '🟢 Опасность не объявлена')}\n\n"
        "📍 Костромская область\n\n"
        "📡 Источник мониторинга:\n"
        "Radar / @radarrussiia\n\n"
        "⚠️ Информация является "
        "информационным мониторингом."
    )


# =========================================================
# ОТПРАВКА
# =========================================================

async def send_alert(application, message):

    users = load_subscribers()

    for user_id in users.copy():

        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message
            )

        except Exception as e:
            print(
                f"Ошибка отправки пользователю "
                f"{user_id}: {e}"
            )

    if CHANNEL_USERNAME:

        try:
            await application.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=message
            )

            print("✅ Сообщение отправлено в канал.")

        except Exception as e:
            print(
                f"❌ Ошибка отправки в канал: {e}"
            )


# =========================================================
# ПОЛУЧЕНИЕ ЛЕНТЫ RADAR
# =========================================================

def get_radar_page():

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/140 Safari/537.36"
        )
    }

    response = requests.get(
        RADAR_URL,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    return response.text


# =========================================================
# РАЗБОР ПОСТОВ TELEGRAM
# =========================================================

def parse_posts(html):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    posts = []

    for message in soup.select(
        ".tgme_widget_message"
    ):

        post_link = message.get(
            "data-post",
            ""
        )

        if not post_link:
            continue

        try:
            post_id = int(
                post_link.split("/")[-1]
            )
        except Exception:
            continue

        text_block = message.select_one(
            ".tgme_widget_message_text"
        )

        if not text_block:
            continue

        text = text_block.get_text(
            "\n",
            strip=True
        )

        posts.append({
            "id": post_id,
            "text": text
        })

    return posts


# =========================================================
# ФИЛЬТР
# =========================================================

def check_post(text):

    text = text.lower()

    # -----------------------------------------------------
    # КОСТРОМСКАЯ ОБЛАСТЬ
    # -----------------------------------------------------

    kostroma = (
        "костромская область" in text
        or "костромской области" in text
    )

    if not kostroma:
        return None

    # -----------------------------------------------------
    # ОТБОЙ
    # -----------------------------------------------------

    if (
        "отбой опасности по бпла" in text
        or "отбой опасности бпла" in text
        or "отбой беспилотной опасности" in text
    ):
        return "🟢 Отбой беспилотной опасности."

    # -----------------------------------------------------
    # ОПАСНОСТЬ
    # -----------------------------------------------------

    if (
        "опасность по бпла" in text
        or "опасность по бпла сохраняется" in text
        or "беспилотная опасность" in text
        or "опасность бпла" in text
        or "тревога по бпла" in text
        or "внимание по бпла" in text
    ):
        return "🟡 Беспилотная опасность объявлена."

    return None


# =========================================================
# АВТОМАТИЧЕСКАЯ ПРОВЕРКА
# =========================================================

async def automatic_check(application):

    print("🔎 Проверка Radar запущена.")

    while True:

        try:

            html = get_radar_page()

            posts = parse_posts(html)

            posts.sort(
                key=lambda x: x["id"],
                reverse=True
            )

            print(
                f"📡 Получено постов: {len(posts)}"
            )

            for post in posts:

                post_id = post["id"]

                if post_id <= int(
                    state.get("last_post", 0)
                ):
                    continue

                text = post["text"]

                print(
                    f"📨 Новый пост #{post_id}: "
                    f"{text[:200]}"
                )

                result = check_post(text)

                state["last_post"] = post_id
                save_state()

                if result is None:
                    continue

                old_status = state.get(
                    "status",
                    "🟢 Опасность не объявлена"
                )

                if result == old_status:
                    print(
                        "ℹ️ Статус не изменился."
                    )
                    continue

                state["status"] = result
                save_state()

                message = (
                    "🚨 UAV ALERT\n\n"
                    f"{result}\n\n"
                    "📍 Костромская область\n\n"
                    "📡 Источник мониторинга:\n"
                    "Radar / @radarrussiia\n\n"
                    "⚠️ Информация носит "
                    "информационный характер. "
                    "Следуйте официальным "
                    "указаниям государственных служб."
                )

                print(
                    f"🚨 НОВЫЙ СТАТУС: {result}"
                )

                await send_alert(
                    application,
                    message
                )

        except Exception as e:

            print(
                f"❌ Ошибка проверки Radar: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# POST INIT
# =========================================================

async def post_init(application):

    application.create_task(
        automatic_check(application)
    )


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN не задан."
        )

        return

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("stop", stop)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    print(
        "🚨 UAV ALERT запускается..."
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
