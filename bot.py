import os
import json
import threading
import time

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHANNEL_USERNAME = os.getenv(
    "CHANNEL_USERNAME",
    "@UAV_ALERT_Kostroma"
)

SOURCE_USERNAME = "@bplarussiaru"
SOURCE_URL = "https://t.me/s/bplarussiaru"

PORT = int(os.getenv("PORT", "5001"))

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


# =========================================================
# СОСТОЯНИЕ
# =========================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {
            "status": "green",
            "last_post": 0
        }

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except Exception:
        return {
            "status": "green",
            "last_post": 0
        }


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def load_subscribers():

    if not os.path.exists(SUBSCRIBERS_FILE):
        return []

    try:

        with open(
            SUBSCRIBERS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except Exception:

        return []


def save_subscribers(subscribers):

    with open(
        SUBSCRIBERS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            subscribers,
            file,
            ensure_ascii=False,
            indent=2
        )


# =========================================================
# КОМАНДЫ TELEGRAM
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

        "Вы подписаны на информационные "
        "уведомления по Костромской области.\n\n"

        f"📡 Источник мониторинга: "
        f"{SOURCE_USERNAME}\n\n"

        "⚠️ Информация носит "
        "информационный характер. "
        "Следуйте официальным указаниям "
        "государственных служб."
    )


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


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    state = load_state()

    current_status = state.get(
        "status",
        "green"
    )

    if current_status == "yellow":

        text = (
            "🟡 Беспилотная опасность "
            "по данным источника мониторинга."
        )

    elif current_status == "red":

        text = (
            "🔴 Ракетная опасность "
            "по данным источника мониторинга."
        )

    else:

        text = (
            "🟢 Опасность по данным "
            "источника не выявлена."
        )

    await update.message.reply_text(

        "UAV ALERT\n\n"
        + text
        + "\n\n"
        f"📡 Источник: {SOURCE_USERNAME}"
    )


# =========================================================
# ОТПРАВКА СООБЩЕНИЙ
# =========================================================

def send_message(
    bot_token,
    chat_id,
    message
):

    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    try:

        response = requests.post(

            url,

            json={
                "chat_id": chat_id,
                "text": message
            },

            timeout=20
        )

        print(
            f"📨 Отправка {chat_id}: "
            f"{response.status_code}"
        )

    except Exception as error:

        print(
            f"❌ Ошибка отправки: {error}"
        )


def notify_users(message):

    subscribers = load_subscribers()

    print(
        f"👥 Подписчиков: "
        f"{len(subscribers)}"
    )

    for chat_id in subscribers:

        send_message(
            BOT_TOKEN,
            chat_id,
            message
        )

    if CHANNEL_USERNAME:

        send_message(
            BOT_TOKEN,
            CHANNEL_USERNAME,
            message
        )


# =========================================================
# ПОЛУЧЕНИЕ ПОСТОВ ИЗ ИСТОЧНИКА
# =========================================================

def get_source_posts():

    print(
        "🌐 Открываем источник:"
    )

    print(SOURCE_URL)

    try:

        headers = {

            "User-Agent":
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/140 Safari/537.36"
        }

        response = requests.get(

            SOURCE_URL,

            headers=headers,

            timeout=30
        )

        print(
            f"🌐 Ответ источника: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "❌ Источник вернул ошибку."
            )

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

    except Exception as error:

        print(
            f"❌ Ошибка чтения источника: "
            f"{error}"
        )

        return []


# =========================================================
# ФИЛЬТР КОСТРОМСКОЙ ОБЛАСТИ
# =========================================================

def is_kostroma(text):

    text = text.lower()

    text = " ".join(
        text.split()
    )

    keywords = [

        "костромская область",

        "костромской области",

        "костромская обл.",

        "костромской обл.",

        "костромская обл",

        "костромской обл",

        "кострома"
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

    text = " ".join(
        text.split()
    )


    # 🔴 РАКЕТНАЯ ОПАСНОСТЬ

    missile_words = [

        "ракетная опасность",

        "ракетной опасности",

        "ракетная тревога",

        "опасность по ракетам"
    ]

    if any(
        word in text
        for word in missile_words
    ):

        return "red"


    # СЛОВА ПРО БПЛА

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


    # 🟢 ОТБОЙ

    cancel_words = [

        "отбой",

        "отмена опасности",

        "опасность снята",

        "опасность отменена",

        "угроза снята",

        "угроза отменена"
    ]

    has_cancel = any(
        word in text
        for word in cancel_words
    )


    if has_cancel and has_drone:

        return "green"


    # 🟡 ОПАСНОСТЬ

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
# ПРИНУДИТЕЛЬНОЕ ОПРЕДЕЛЕНИЕ ТЕКУЩЕГО СТАТУСА
# =========================================================

def initialize_current_status(posts):

    print("")
    print(
        "🔄 ПРИНУДИТЕЛЬНАЯ ПРОВЕРКА "
        "ТЕКУЩЕГО СТАТУСА"
    )

    kostroma_posts = []

    for post in posts:

        if is_kostroma(post["text"]):

            detected = detect_status(
                post["text"]
            )

            print(
                f"🔎 Пост #{post['id']} "
                f"Кострома → {detected}"
            )

            kostroma_posts.append({

                "id": post["id"],

                "text": post["text"],

                "status": detected
            })


    if not kostroma_posts:

        print(
            "⚠️ Постов про Костромскую "
            "область не найдено."
        )

        return


    # Сортируем от нового к старому

    kostroma_posts.sort(
        key=lambda x: x["id"],
        reverse=True
    )


    latest = kostroma_posts[0]


    print(
        f"📌 Последний пост Костромы: "
        f"#{latest['id']}"
    )

    print(
        f"🧠 Определён статус: "
        f"{latest['status']}"
    )


    if latest["status"] is None:

        print(
            "⚠️ Последний пост найден, "
            "но статус не распознан."
        )

        return


    state = load_state()

    old_status = state.get(
        "status",
        "green"
    )


    # ВАЖНО:
    # при запуске статус обновляется
    # независимо от last_post

    state["status"] = latest["status"]

    state["last_post"] = latest["id"]

    save_state(state)


    print(
        f"💾 Текущий статус сохранён: "
        f"{latest['status']}"
    )


    # При запуске не отправляем старое
    # уведомление повторно

    if old_status != latest["status"]:

        print(
            "ℹ️ Статус изменился во время "
            "инициализации."
        )

    else:

        print(
            "ℹ️ Статус остался прежним."
        )


# =========================================================
# ПРОВЕРКА НОВЫХ ПОСТОВ
# =========================================================

def check_new_posts(posts):

    state = load_state()

    old_post = state.get(
        "last_post",
        0
    )

    old_status = state.get(
        "status",
        "green"
    )


    new_posts = [

        post

        for post in posts

        if post["id"] > old_post
    ]


    print(
        f"🆕 Новых постов: "
        f"{len(new_posts)}"
    )


    if not new_posts:
        return


    for post in sorted(
        new_posts,
        key=lambda x: x["id"]
    ):

        text = post["text"]


        if not is_kostroma(text):

            continue


        detected = detect_status(text)


        print(
            f"🧠 Новый пост #{post['id']} "
            f"→ {detected}"
        )


        if detected is None:

            continue


        if detected == old_status:

            state["last_post"] = post["id"]

            continue


        state["status"] = detected

        state["last_post"] = post["id"]

        old_status = detected


        if detected == "yellow":

            message = (

                "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ\n\n"

                "В источнике мониторинга "
                "обнаружено сообщение "
                "об опасности БПЛА "
                "в Костромской области.\n\n"

                f"📡 Источник: "
                f"{SOURCE_USERNAME}\n\n"

                "⚠️ Информация носит "
                "информационный характер. "
                "Следуйте официальным "
                "указаниям государственных служб."
            )

            notify_users(message)


        elif detected == "red":

            message = (

                "🔴 РАКЕТНАЯ ОПАСНОСТЬ\n\n"

                "В источнике мониторинга "
                "обнаружено сообщение "
                "о ракетной опасности "
                "для Костромской области.\n\n"

                f"📡 Источник: "
                f"{SOURCE_USERNAME}\n\n"

                "⚠️ Следуйте официальным "
                "указаниям государственных служб."
            )

            notify_users(message)


        elif detected == "green":

            message = (

                "🟢 ОТБОЙ "
                "БЕСПИЛОТНОЙ ОПАСНОСТИ\n\n"

                "В источнике мониторинга "
                "обнаружено сообщение "
                "об отмене опасности "
                "в Костромской области.\n\n"

                f"📡 Источник: "
                f"{SOURCE_USERNAME}"
            )

            notify_users(message)


        save_state(state)


# =========================================================
# ОСНОВНАЯ ПРОВЕРКА
# =========================================================

def check_source(first_run=False):

    print("")
    print(
        "🔎 Проверка источника "
        f"{SOURCE_USERNAME}"
    )


    posts = get_source_posts()


    if not posts:

        print(
            "⚠️ Посты не получены."
        )

        return


    # ПРИ КАЖДОМ ЗАПУСКЕ
    # принудительно определяем статус

    if first_run:

        initialize_current_status(
            posts
        )

        return


    # После запуска —
    # только новые сообщения

    check_new_posts(posts)


# =========================================================
# ФОНОВЫЙ ЦИКЛ
# =========================================================

def source_loop():

    print(
        "🚨 UAV ALERT запускается..."
    )

    print(
        f"📡 Источник: "
        f"{SOURCE_USERNAME}"
    )


    # ==========================================
    # ПЕРВАЯ ПРОВЕРКА
    # ==========================================

    try:

        check_source(
            first_run=True
        )

    except Exception as error:

        print(
            f"❌ Ошибка первой проверки: "
            f"{error}"
        )


    # ==========================================
    # ДАЛЬШЕ КАЖДЫЕ 60 СЕКУНД
    # ==========================================

    while True:

        time.sleep(60)

        try:

            check_source(
                first_run=False
            )

        except Exception as error:

            print(
                f"❌ Ошибка проверки: "
                f"{error}"
            )


# =========================================================
# FLASK THREAD
# =========================================================

def run_flask():

    app.run(

        host="0.0.0.0",

        port=PORT
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        print(
            "❌ ОШИБКА: BOT_TOKEN "
            "не задан."
        )

        return


    threading.Thread(

        target=run_flask,

        daemon=True

    ).start()


    threading.Thread(

        target=source_loop,

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


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
