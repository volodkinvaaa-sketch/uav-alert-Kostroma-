import os
import json
import asyncio
import threading
import requests

from bs4 import BeautifulSoup
from flask import Flask, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 60

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"


# =========================================================
# ТРИ ИНФОРМАТОРА
# =========================================================

SOURCES = {
    "radar_russia": {
        "name": "📡 Радар Россия",
        "url": "https://t.me/s/radarrussiia"
    },

    "bpla_russia": {
        "name": "📢 БПЛА Россия",
        "url": "https://t.me/s/bplarussiaru"
    },

    "radarmap": {
        "name": "🗺️ RadarMap",
        "url": "https://radar-map.ru/"
    }
}


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
# СОСТОЯНИЕ
# =========================================================

def default_state():

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


def load_state():

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        # Если структура старая —
        # начинаем с новой
        if "sources" not in data:
            return default_state()

        return data

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
# TELEGRAM
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
        "📡 Информаторы:\n"
        "• Радар Россия\n"
        "• БПЛА Россия\n"
        "• RadarMap\n\n"
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

    overall = state.get(
        "overall",
        "green"
    )

    if overall == "red":

        overall_text = (
            "🔴 РАКЕТНАЯ ОПАСНОСТЬ"
        )

    elif overall == "yellow":

        overall_text = (
            "🟡 ОПАСНОСТЬ ПО БПЛА"
        )

    else:

        overall_text = (
            "🟢 ОПАСНОСТИ НЕ ОБЪЯВЛЕНО"
        )


    text = (
        "🛰 UAV ALERT\n\n"
        f"Общий статус:\n"
        f"{overall_text}\n\n"
        "ИНФОРМАТОРЫ:\n\n"
    )


    source_names = [
        "radar_russia",
        "bpla_russia",
        "radarmap"
    ]


    for key in source_names:

        source = SOURCES[key]

        source_state = state[
            "sources"
        ].get(
            key,
            {}
        )

        status = source_state.get(
            "status",
            "green"
        )


        if status == "red":

            status_text = (
                "🔴 Ракетная опасность"
            )

        elif status == "yellow":

            status_text = (
                "🟡 Опасность БПЛА"
            )

        else:

            status_text = (
                "🟢 Опасность не обнаружена"
            )


        text += (
            f"{source['name']}: "
            f"{status_text}\n"
        )


    text += (
        "\n⚠️ Информация является "
        "гражданским мониторингом. "
        "Для действий ориентируйся "
        "на официальные оповещения."
    )


    await update.message.reply_text(
        text
    )


# =========================================================
# ОПРЕДЕЛЕНИЕ КОСТРОМЫ
# =========================================================

def is_kostroma(text):

    text = text.lower()

    words = [

        "кострома",

        "костромская область",

        "костромской области",

        "костромская обл",

        "костромской обл"

    ]

    return any(
        word in text
        for word in words
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

        "отбой беспилотной опасности",

        "отбой опасности бпла"

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
    # 🟡 ПРЯМЫЕ ФОРМУЛИРОВКИ
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
    # 🟡 БПЛА + ОПАСНОСТЬ
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
# TELEGRAM-ПОСТЫ
# =========================================================

def get_telegram_posts(
    source_key
):

    source = SOURCES[
        source_key
    ]

    url = source["url"]


    try:

        response = requests.get(
            url,
            headers={
                "User-Agent":
                "Mozilla/5.0"
            },
            timeout=20
        )


        print(
            f"🌐 {source['name']}: "
            f"{response.status_code}"
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


            text_element = (
                message.select_one(
                    ".tgme_widget_message_text"
                )
            )


            if text_element:

                text = (
                    text_element
                    .get_text(
                        "\n",
                        strip=True
                    )
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


        print(
            f"📡 {source['name']}: "
            f"{len(posts)} сообщений"
        )


        return posts


    except Exception as e:

        print(
            f"❌ {source['name']}: "
            f"{e}"
        )

        return []


# =========================================================
# ПОИСК ПОСЛЕДНЕГО СТАТУСА
# =========================================================

def find_latest_status(
    posts
):

    candidates = []


    for post in posts:

        text = post[
            "text"
        ]


        if not text:
            continue


        if not is_kostroma(
            text
        ):
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
# RADARMAP
# =========================================================

def get_radarmap_status():

    try:

        response = requests.get(
            SOURCES["radarmap"]["url"],
            headers={
                "User-Agent":
                "Mozilla/5.0"
            },
            timeout=20
        )


        print(
            "🌐 🗺️ RadarMap:",
            response.status_code
        )


        if response.status_code != 200:

            return None


        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )


        text = soup.get_text(
            " ",
            strip=True
        )


        text_lower = text.lower()


        if "костром" not in text_lower:

            print(
                "🗺️ RadarMap: "
                "Кострома в странице не найдена."
            )

            return None


        status = detect_status(
            text_lower
        )


        print(
            "🗺️ RadarMap статус:",
            status
        )


        return status


    except Exception as e:

        print(
            "❌ RadarMap:",
            e
        )

        return None


# =========================================================
# ОБЩИЙ СТАТУС
# =========================================================

def calculate_overall(
    statuses
):

    # Ракетная опасность имеет
    # более высокий приоритет
    if "red" in statuses:

        return "red"


    if "yellow" in statuses:

        return "yellow"


    return "green"


# =========================================================
# ПРОВЕРКА ВСЕХ ТРЁХ
# =========================================================

async def check_all_sources(
    application,
    first_run=False
):

    state = load_state()

    old_overall = state.get(
        "overall",
        "green"
    )


    statuses = []


    # =====================================================
    # 1. РАДАР РОССИЯ
    # =====================================================

    posts = get_telegram_posts(
        "radar_russia"
    )


    latest = find_latest_status(
        posts
    )


    if latest:

        state["sources"][
            "radar_russia"
        ] = {

            "status":
            latest["status"],

            "post":
            latest["id"]
        }


        statuses.append(
            latest["status"]
        )

    else:

        print(
            "⚠️ Радар Россия: "
            "статус Костромы не найден."
        )


    # =====================================================
    # 2. БПЛА РОССИЯ
    # =====================================================

    posts = get_telegram_posts(
        "bpla_russia"
    )


    latest = find_latest_status(
        posts
    )


    if latest:

        state["sources"][
            "bpla_russia"
        ] = {

            "status":
            latest["status"],

            "post":
            latest["id"]
        }


        statuses.append(
            latest["status"]
        )

    else:

        print(
            "⚠️ БПЛА Россия: "
            "статус Костромы не найден."
        )


    # =====================================================
    # 3. RADARMAP
    # =====================================================

    radar_status = (
        get_radarmap_status()
    )


    if radar_status:

        state["sources"][
            "radarmap"
        ] = {

            "status":
            radar_status,

            "post":
            0
        }


        statuses.append(
            radar_status
        )

    else:

        print(
            "⚠️ RadarMap: "
            "статус не определён."
        )


    # =====================================================
    # ОБЩИЙ СТАТУС
    # =====================================================

    new_overall = calculate_overall(
        statuses
    )


    state["overall"] = (
        new_overall
    )


    save_state(state)


    print(
        "\n=============================="
    )

    print(
        "📊 СТАТУС ИНФОРМАТОРОВ"
    )

    print(
        "📡 Радар Россия:",
        state["sources"]
        ["radar_russia"]
        ["status"]
    )

    print(
        "📢 БПЛА Россия:",
        state["sources"]
        ["bpla_russia"]
        ["status"]
    )

    print(
        "🗺️ RadarMap:",
        state["sources"]
        ["radarmap"]
        ["status"]
    )

    print(
        "🚦 ОБЩИЙ:",
        new_overall
    )

    print(
        "==============================\n"
    )


    # =====================================================
    # ПЕРВЫЙ ЗАПУСК
    # =====================================================

    if first_run:

        print(
            "ℹ️ Первый запуск: "
            "уведомление не отправляем."
        )

        return


    # =====================================================
    # ИЗМЕНЕНИЕ ОБЩЕГО СТАТУСА
    # =====================================================

    if new_overall != old_overall:

        print(
            f"🚨 Общий статус изменился: "
            f"{old_overall} → "
            f"{new_overall}"
        )


        await send_notification(
            application,
            new_overall
        )


# =========================================================
# УВЕДОМЛЕНИЕ
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
            "UAV ALERT\n\n"
            "Информаторы:\n"
            "📡 Радар Россия\n"
            "📢 БПЛА Россия\n"
            "🗺️ RadarMap\n\n"
            "⚠️ Гражданский мониторинг. "
            "Ориентируйся на официальные "
            "оповещения."
        )


    elif status == "yellow":

        message = (
            "🟡 ОПАСНОСТЬ ПО БПЛА\n\n"
            "Костромская область.\n\n"
            "UAV ALERT\n\n"
            "Информаторы:\n"
            "📡 Радар Россия\n"
            "📢 БПЛА Россия\n"
            "🗺️ RadarMap\n\n"
            "⚠️ Гражданский мониторинг. "
            "Ориентируйся на официальные "
            "оповещения."
        )


    else:

        message = (
            "🟢 ОТБОЙ БЕСПИЛОТНОЙ "
            "ОПАСНОСТИ\n\n"
            "Костромская область.\n\n"
            "UAV ALERT\n\n"
            "Информаторы:\n"
            "📡 Радар Россия\n"
            "📢 БПЛА Россия\n"
            "🗺️ RadarMap\n\n"
            "⚠️ Гражданский мониторинг. "
            "Ориентируйся на официальные "
            "оповещения."
        )


    for user_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=user_id,
                text=message
            )


            print(
                f"📨 Уведомление отправлено "
                f"{user_id}"
            )


        except Exception as e:

            print(
                f"❌ Ошибка отправки "
                f"{user_id}: {e}"
            )


# =========================================================
# ЦИКЛ
# =========================================================

def source_loop(
    application
):

    async def loop():

        print(
            "🚀 UAV ALERT запущен."
        )


        print(
            "🔎 Проверяю три информатора..."
        )


        await check_all_sources(
            application,
            first_run=True
        )


        while True:

            try:

                await asyncio.sleep(
                    CHECK_INTERVAL
                )


                print(
                    "\n🔄 Новая проверка..."
                )


                await check_all_sources(
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
