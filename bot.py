import os
import json
import re
import asyncio
import threading
import requests

from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()

PORT = int(os.getenv("PORT", "10000"))

# Публичная лента информационного канала
RADAR_CHANNEL = "radarrussiia"
RADAR_URL = f"https://t.me/s/{RADAR_CHANNEL}"

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
# КОМАНДА /START
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
        "📍 Регион: Костромская область\n\n"
        "Источник мониторинга:\n"
        "Radar / radarrussiia\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/stop — отключить уведомления"
    )


# =========================================================
# /STOP
# =========================================================

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    users = load_subscribers()

    if user_id in users:
        users.remove(user_id)
        save_subscribers(users)

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


# =========================================================
# /STATUS
# =========================================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        f"Текущий статус:\n"
        f"{state.get('status', '🟢 Опасность не объявлена')}\n\n"
        "📍 Костромская область\n\n"
        "Источник мониторинга:\n"
        "Radar / radarrussiia\n\n"
        "⚠️ Для официальных указаний "
        "используйте сообщения государственных служб."
    )


# =========================================================
# ОТПРАВКА УВЕДОМЛЕНИЯ
# =========================================================

async def send_alert(application, message):

    users = load_subscribers()

    # Личные уведомления
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

    # Публичный канал
    if CHANNEL_USERNAME:

        try:
            await application.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=message
            )

            print("✅ Отправлено в канал.")

        except Exception as e:
            print(
                f"❌ Ошибка отправки в канал: {e}"
            )


# =========================================================
# ЗАГРУЗКА RADAR ЛЕНТЫ
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
# ПОЛУЧЕНИЕ ПОСЛЕДНИХ ПОСТОВ
# =========================================================

def parse_posts(html):

    posts = []

    pattern = re.compile(
        r'data-post="'
        + re.escape(RADAR_CHANNEL)
        + r'/(\d+)"'
        r'.*?'
        r'class="tgme_widget_message_text[^"]*"'
        r'>(.*?)</div>',
        re.S
    )

    matches = pattern.findall(html)

    for post_id, raw_text in matches:

        text = re.sub(
            r"<br\s*/?>",
            "\n",
            raw_text
        )

        text = re.sub(
            r"<[^>]+>",
            "",
            text
        )

        text = (
            text
            .replace("&nbsp;", " ")
            .replace("&quot;", '"')
            .replace("&amp;", "&")
        )

        text = text.strip()

        posts.append({
            "id": int(post_id),
            "text": text
        })

    return posts


# =========================================================
# ФИЛЬТР КОСТРОМСКОЙ ОБЛАСТИ
# =========================================================

def check_post(text):

    text_lower = text.lower()

    # Только Костромская область
    kostroma = (
        "костромская область" in text_lower
        or "костромской области" in text_lower
    )

    if not kostroma:
        return None

    # -----------------------------------------------------
    # ОТБОЙ
    # -----------------------------------------------------

    if (
        "отбой опасности по бпла" in text_lower
        or "отбой беспилотной опасности" in text_lower
        or "отбой опасности бпла" in text_lower
    ):
        return "🟢 Отбой беспилотной опасности."

    # -----------------------------------------------------
    # ОПАСНОСТЬ
    # -----------------------------------------------------

    if (
        "опасность по бпла" in text_lower
        or "беспилотная опасность" in text_lower
        or "опасность бпла" in text_lower
    ):
        return "🟡 Беспилотная опасность объявлена."

    return None


# =========================================================
# АВТОМАТИЧЕСКАЯ ПРОВЕРКА
# =========================================================

async def automatic_check(application):

    print("🔎 Автоматическая проверка Radar запущена.")

    while True:

        try:

            html = get_radar_page()

            posts = parse_posts(html)

            # Самые новые сначала
            posts.sort(
                key=lambda x: x["id"],
                reverse=True
            )

            for post in posts[:20]:

                post_id = post["id"]

                # Уже обработанный пост
                if post_id <= int(
                    state.get("last_post", 0)
                ):
                    continue

                text = post["text"]

                print(
                    f"📨 Новый пост #{post_id}: "
                    f"{text[:150]}"
                )

                result = check_post(text)

                # Запоминаем пост,
                # даже если он нам не подходит
                state["last_post"] = post_id
                save_state()

                if result:

                    old_status = state.get(
                        "status",
                        "🟢 Опасность не объявлена"
                    )

                    if result != old_status:

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
                            f"🚨 ИЗМЕНЕНИЕ СТАТУСА: "
                            f"{result}"
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
# ЗАПУСК
# =========================================================

async def post_init(application):

    application.create_task(
        automatic_check(application)
    )


def main():

    if not BOT_TOKEN:

        print(
            "❌ ОШИБКА: BOT_TOKEN не задан."
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

    print("🚨 UAV ALERT запускается...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
