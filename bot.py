import os
import json
import asyncio
import threading
import requests

from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

SOURCE_USERNAME = "@bplarussiaru"
SOURCE_URL = "https://t.me/s/bplarussiaru"

CHECK_INTERVAL = 60

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"


# =========================================================
# FLASK
# =========================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "UAV ALERT работает."


@app.route("/status")
def status_page():
    return jsonify(load_state())


def run_flask():
    app.run(
        host="0.0.0.0",
        port=10000
    )


# =========================================================
# STATE
# =========================================================

def load_state():

    if not os.path.exists(STATE_FILE):

        return {
            "status": "green",
            "last_post": 0,
            "last_text": ""
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
            "status": "green",
            "last_post": 0,
            "last_text": ""
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
# ПОДПИСЧИКИ
# =========================================================

def load_subscribers():

    if not os.path.exists(
        SUBSCRIBERS_FILE
    ):

        return []

    try:

        with open(
            SUBSCRIBERS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return []


def save_subscribers(
    subscribers
):

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
# TELEGRAM КОМАНДЫ
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id not in subscribers:

        subscribers.append(user_id)

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "🛰 UAV ALERT\n\n"
        "Ты подписан на гражданские "
        "информационные уведомления.\n\n"
        "Источник мониторинга: "
        "@bplarussiaru\n\n"
        "/status — текущий статус\n"
        "/stop — отключить уведомления"
    )


async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id in subscribers:

        subscribers.remove(user_id)

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = load_state()

    status = state.get(
        "status",
        "green"
    )

    if status == "red":

        text = (
            "🔴 РАКЕТНАЯ ОПАСНОСТЬ"
        )

    elif status == "yellow":

        text = (
            "🟡 ОПАСНОСТЬ ПО БПЛА"
        )

    else:

        text = (
            "🟢 ОПАСНОСТИ НЕ ОБЪЯВЛЕНО"
        )

    await update.message.reply_text(
        "UAV ALERT\n\n"
        f"Текущий статус:\n{text}\n\n"
        "Источник: @bplarussiaru"
    )


# =========================================================
# ПОЛУЧЕНИЕ СООБЩЕНИЙ
# =========================================================

def get_source_posts():

    try:

        headers = {
            "User-Agent": "Mozilla/5.0"
        }

        response = requests.get(
            SOURCE_URL,
            headers=headers,
            timeout=20
        )

        print(
            "🌐 Ответ источника:",
            response.status_code
        )

        if response.status_code != 200:

            return []

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        messages = soup.select(
            ".tgme_widget_message"
        )

        print(
            "📡 Найдено сообщений:",
            len(messages)
        )

        posts = []

        for message in messages:

            data_post = message.get(
                "data-post"
            )

            if not data_post:
                continue

            try:

                post_id = int(
                    data_post.split("/")[-1]
                )

            except Exception:

                continue

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            if text_element:

                text = text_element.get_text(
                    "\n",
                    strip=True
                )

            else:

                text = ""

            posts.append(
                {
                    "id": post_id,
                    "text": text
                }
            )

        posts.sort(
            key=lambda x: x["id"]
        )

        return posts

    except Exception as e:

        print(
            "❌ Ошибка получения сообщений:",
            e
        )

        return []


# =========================================================
# КОСТРОМА
# =========================================================

def is_kostroma(text):

    text = text.lower()

    kostroma_words = [

        "кострома",

        "костромская область",

        "костромской области",

        "костромская обл",

        "костромской обл"

    ]

    return any(
        word in text
        for word in kostroma_words
    )


# =========================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================================================

def detect_status(text):

    text = text.lower()


    # =====================================================
    # 🔴 РАКЕТНАЯ ОПАСНОСТЬ
    # =====================================================

    rocket_words = [

        "ракетная опасность",

        "ракетной опасности",

        "ракетная тревога",

        "опасность по ракетам",

        "угроза ракетного нападения",

        "ракетная угроза"

    ]

    if any(
        word in text
        for word in rocket_words
    ):

        return "red"


    # =====================================================
    # 🟢 ОТБОЙ
    # =====================================================

    cancel_words = [

        "отбой",

        "отмена опасности",

        "опасность снята",

        "опасность отменена",

        "угроза снята",

        "угроза отменена",

        "угроза по бпла снята",

        "опасность по бпла снята",

        "отбой опасности бпла",

        "отбой беспилотной опасности"

    ]


    drone_words = [

        "бпла",

        "беспилот",

        "беспилотник",

        "беспилотники",

        "беспилотников",

        "беспилотная"

    ]


    has_drone = any(
        word in text
        for word in drone_words
    )


    if has_drone:

        if any(
            word in text
            for word in cancel_words
        ):

            return "green"


    # =====================================================
    # 🟡 ПРЯМЫЕ ФОРМУЛИРОВКИ БПЛА
    # =====================================================

    uav_danger_words = [

        "угроза по бпла",

        "угроза бпла",

        "опасность по бпла",

        "опасность бпла",

        "угроза беспилотников",

        "угроза беспилотника",

        "опасность беспилотников",

        "опасность беспилотника",

        "беспилотная опасность",

        "опасность беспилотной атаки"

    ]


    if any(
        word in text
        for word in uav_danger_words
    ):

        return "yellow"


    # =====================================================
    # 🟡 ОБЩАЯ ПРОВЕРКА БПЛА + ОПАСНОСТЬ
    # =====================================================

    danger_words = [

        "опасность",

        "угроза",

        "тревога",

        "внимание"

    ]


    has_danger = any(
        word in text
        for word in danger_words
    )


    if has_drone and has_danger:

        return "yellow"


    return None


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

async def send_notification(
    application,
    status
):

    subscribers = load_subscribers()


    if status == "red":

        message = (

            "🔴 РАКЕТНАЯ ОПАСНОСТЬ\n\n"

            "Костромская область.\n\n"

            "Источник: @bplarussiaru\n\n"

            "Информация носит "
            "гражданский информационный характер."
        )


    elif status == "yellow":

        message = (

            "🟡 ОПАСНОСТЬ ПО БПЛА\n\n"

            "Костромская область.\n\n"

            "Источник: @bplarussiaru\n\n"

            "Информация носит "
            "гражданский информационный характер."
        )


    else:

        message = (

            "🟢 ОТБОЙ БЕСПИЛОТНОЙ ОПАСНОСТИ\n\n"

            "Костромская область.\n\n"

            "Источник: @bplarussiaru\n\n"

            "Информация носит "
            "гражданский информационный характер."
        )


    for user_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=user_id,
                text=message
            )

            print(
                f"📨 Уведомление отправлено: "
                f"{user_id}"
            )

        except Exception as e:

            print(
                f"❌ Ошибка отправки "
                f"{user_id}: {e}"
            )


# =========================================================
# ПОИСК ПОСЛЕДНЕГО СТАТУСА КОСТРОМЫ
# =========================================================

def find_latest_kostroma_status(
    posts
):

    candidates = []


    for post in posts:

        text = post["text"]

        if not text:
            continue


        if not is_kostroma(text):
            continue


        status = detect_status(
            text
        )


        print(
            f"🔎 Post #{post['id']} | "
            f"Кострома: ДА | "
            f"Статус: {status}"
        )


        if status is not None:

            candidates.append(
                {
                    "id": post["id"],
                    "text": text,
                    "status": status
                }
            )


    if not candidates:

        return None


    candidates.sort(
        key=lambda x: x["id"],
        reverse=True
    )


    return candidates[0]


# =========================================================
# ПРОВЕРКА ИСТОЧНИКА
# =========================================================

async def check_source(
    application,
    first_run=False
):

    posts = get_source_posts()


    if not posts:

        print(
            "⚠️ Сообщения источника "
            "не получены."
        )

        return


    state = load_state()


    print(
        f"📨 Всего сообщений "
        f"для проверки: {len(posts)}"
    )


    latest = find_latest_kostroma_status(
        posts
    )


    if latest is None:

        print(
            "⚠️ Сообщение со статусом "
            "для Костромы не найдено."
        )

        return


    print(
        f"📌 Последний статус Костромы: "
        f"Post #{latest['id']}"
    )


    print(
        f"🧠 Определён статус: "
        f"{latest['status']}"
    )


    # =====================================================
    # ПЕРВЫЙ ЗАПУСК
    # =====================================================

    if first_run:

        state["status"] = (
            latest["status"]
        )

        state["last_post"] = (
            latest["id"]
        )

        state["last_text"] = (
            latest["text"]
        )

        save_state(state)


        print(
            "💾 Текущий статус сохранён."
        )

        print(
            "ℹ️ Уведомление при первом "
            "запуске не отправляется."
        )

        return


    old_post = state.get(
        "last_post",
        0
    )

    old_status = state.get(
        "status",
        "green"
    )


    # =====================================================
    # СТАРЫЙ ПОСТ
    # =====================================================

    if latest["id"] <= old_post:

        print(
            "ℹ️ Нового статусного "
            "сообщения нет."
        )

        return


    # =====================================================
    # СТАТУС ИЗМЕНИЛСЯ
    # =====================================================

    if latest["status"] != old_status:

        print(
            f"🚨 Смена статуса: "
            f"{old_status} → "
            f"{latest['status']}"
        )


        state["status"] = (
            latest["status"]
        )

        state["last_post"] = (
            latest["id"]
        )

        state["last_text"] = (
            latest["text"]
        )

        save_state(state)


        await send_notification(
            application,
            latest["status"]
        )


    else:

        state["last_post"] = (
            latest["id"]
        )

        state["last_text"] = (
            latest["text"]
        )

        save_state(state)


        print(
            "ℹ️ Статус не изменился."
        )


# =========================================================
# ЦИКЛ МОНИТОРИНГА
# =========================================================

def source_loop(
    application
):

    async def loop():

        print(
            "🚀 UAV ALERT запущен."
        )


        print(
            "🔎 Выполняю полную "
            "первичную проверку..."
        )


        await check_source(
            application,
            first_run=True
        )


        while True:

            try:

                await asyncio.sleep(
                    CHECK_INTERVAL
                )


                print(
                    "\n🔄 Проверка "
                    "новых сообщений..."
                )


                await check_source(
                    application,
                    first_run=False
                )


            except Exception as e:

                print(
                    "❌ Ошибка цикла:",
                    e
                )


    asyncio.run(
        loop()
    )


# =========================================================
# ЗАПУСК
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ BOT_TOKEN не найден!"
        )

        return


    # Flask
    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()


    # Telegram
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
            status_command
        )
    )


    # Мониторинг
    monitor_thread = threading.Thread(
        target=source_loop,
        args=(application,),
        daemon=True
    )

    monitor_thread.start()


    print(
        "🤖 Telegram-бот запущен!"
    )


    application.run_polling()


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
