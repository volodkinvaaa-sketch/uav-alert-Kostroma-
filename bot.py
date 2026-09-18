import os
import json
import threading
import requests
import asyncio

from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ТВОЙ TELEGRAM ID
ADMIN_ID = 1421675956

# ТВОЙ TELEGRAM-КАНАЛ
CHANNEL_ID = "@RADAR_Kostroma"
CHANNEL_URL = "https://t.me/RADAR_Kostroma"

CHECK_INTERVAL = 60

# =========================
# ИСТОЧНИКИ
# =========================

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

# =========================
# ТЕРРИТОРИИ
# =========================

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

# =========================
# FLASK
# =========================

app = Flask(__name__)

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"

state_lock = threading.Lock()


@app.route("/")
def home():
    return "UAV ALERT работает."


@app.route("/status")
def web_status():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read(), 200, {
                "Content-Type": "application/json"
            }
    except Exception:
        return '{"overall":"green"}', 200, {
            "Content-Type": "application/json"
        }


def run_flask():
    app.run(
        host="0.0.0.0",
        port=10000
    )


# =========================
# ФАЙЛЫ
# =========================

def load_subscribers():
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


def save_state(data):
    with state_lock:
        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )


def load_state():
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


# =========================
# КОСТРОМСКАЯ ОБЛАСТЬ
# =========================

def is_kostroma(text):
    text = text.lower()

    words = [
        "кострома",
        "костромская область",
        "костромской области",
        "костромская обл",
        "костромской обл",
    ]

    return any(
        word in text
        for word in words
    )


# =========================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================

def detect_status(text):
    text = text.lower()

    red_words = [
        "ракетная опасность",
        "ракетной опасности",
        "ракетная тревога",
        "опасность по ракетам",
        "угроза ракетного нападения",
        "ракетная угроза",
    ]

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

    for word in red_words:
        if word in text:
            return "red"

    for word in green_words:
        if word in text:
            return "green"

    for word in yellow_words:
        if word in text:
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

    if any(
        word in text
        for word in drone_words
    ):
        if any(
            word in text
            for word in danger_words
        ):
            return "yellow"

    return None


# =========================
# ТЕРРИТОРИИ
# =========================

def find_territories(text):
    result = []

    lower_text = text.lower()

    for territory in TERRITORIES:

        if territory.lower() in lower_text:
            result.append(territory)

    return result


# =========================
# ПОЛУЧЕНИЕ TELEGRAM-ПОСТОВ
# =========================

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

            if not text_element:
                continue

            text = text_element.get_text(
                "\n",
                strip=True
            )

            link = message.select_one(
                ".tgme_widget_message_date"
            )

            post_id = 0

            if link and link.get("href"):

                try:

                    post_id = int(
                        link["href"]
                        .rstrip("/")
                        .split("/")[-1]
                    )

                except Exception:
                    pass

            posts.append({
                "id": post_id,
                "text": text
            })

        return posts

    except Exception as e:

        print(
            f"Ошибка получения {url}: {e}"
        )

        return []


def find_latest_status(posts):

    found = []

    for post in posts:

        text = post["text"]

        if not is_kostroma(text):
            continue

        status = detect_status(text)

        if status is None:
            continue

        territories = find_territories(
            text
        )

        found.append({
            "status": status,
            "post": post["id"],
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


# =========================
# RADARMAP
# =========================

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

        if "костром" not in text.lower():

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
            "territories": []
        }

    except Exception as e:

        print(
            f"Ошибка RadarMap: {e}"
        )

        return {
            "status": "green",
            "post": 0,
            "territories": []
        }


# =========================
# ПРОВЕРКА ИСТОЧНИКОВ
# =========================

def check_sources():

    results = {}

    for key in [
        "radar_russia",
        "bpla_russia"
    ]:

        source = SOURCES[key]

        posts = get_telegram_posts(
            source["url"]
        )

        results[key] = find_latest_status(
            posts
        )

    results["radarmap"] = (
        get_radarmap_status()
    )

    statuses = [
        results[key]["status"]
        for key in results
    ]

    if "red" in statuses:

        overall = "red"

    elif "yellow" in statuses:

        overall = "yellow"

    else:

        overall = "green"

    return {
        "overall": overall,
        "sources": results
    }


# =========================
# ТЕКСТ СТАТУСА
# =========================

def status_text(status):

    if status == "red":
        return "🔴 РАКЕТНАЯ ОПАСНОСТЬ"

    if status == "yellow":
        return "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ"

    return "🟢 ОПАСНОСТЬ НЕ ОБЪЯВЛЕНА"


# =========================
# СОЗДАНИЕ УВЕДОМЛЕНИЯ
# =========================

def build_notification(data):

    text = (
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        f"{status_text(data['overall'])}\n"
    )

    territories = set()

    for source in data["sources"].values():

        for territory in source.get(
            "territories",
            []
        ):

            territories.add(
                territory
            )

    if territories:

        text += (
            "\n🏘️ Территории, "
            "указанные в источниках:\n"
        )

        for territory in sorted(
            territories
        ):

            text += (
                f"• {territory}\n"
            )

    text += (
        "\n⚠️ Информационное уведомление.\n"
        "Приоритет имеют официальные "
        "сообщения органов власти и МЧС."
    )

    return text


# =========================
# ОТПРАВКА УВЕДОМЛЕНИЯ
# =========================

async def send_notification(
    application,
    data
):

    message = build_notification(
        data
    )

    subscribers = load_subscribers()

    for user_id in subscribers:

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

    try:

        await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message
        )

    except Exception as e:

        print(
            f"Ошибка отправки в канал: {e}"
        )


# =========================
# МОНИТОРИНГ
# =========================

async def monitor(application):

    print(
        "📡 Мониторинг UAV ALERT запущен"
    )

    first_run = True

    while True:

        try:

            data = check_sources()

            old_state = load_state()

            save_state(data)

            changed = (
                old_state.get("overall")
                != data["overall"]
                or old_state.get("sources")
                != data["sources"]
            )

            if not first_run and changed:

                await send_notification(
                    application,
                    data
                )

            first_run = False

        except Exception as e:

            print(
                f"Ошибка мониторинга: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================
# /START
# =========================

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

    keyboard = [
        [
            InlineKeyboardButton(
                "📢 Наш Telegram-канал",
                url=CHANNEL_URL
            )
        ]
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "Вы подписаны на информационные "
        "уведомления по Костромской области.\n\n"
        "📢 Новости и уведомления также "
        "публикуются в нашем Telegram-канале.\n\n"
        "Команда /stop — отключить уведомления.",
        reply_markup=reply_markup
    )


# =========================
# /STOP
# =========================

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    subscribers = load_subscribers()

    if user_id in subscribers:

        subscribers.remove(
            user_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "🔕 Уведомления отключены."
    )


# =========================
# ПРОВЕРКА АДМИНА
# =========================

def is_admin(update):

    return (
        update.effective_user is not None
        and
        update.effective_user.id == ADMIN_ID
    )


# =========================
# /STATUS — ТОЛЬКО АДМИН
# =========================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Доступ запрещён."
        )

        return

    data = load_state()

    sources = data.get(
        "sources",
        {}
    )

    await update.message.reply_text(

        "🔐 ADMIN STATUS\n\n"

        f"Общий статус:\n"
        f"{status_text(data.get('overall', 'green'))}\n\n"

        "Источники:\n"

        f"📡 Радар Россия: "
        f"{sources.get('radar_russia', {}).get('status', 'green')}\n"

        f"📢 БПЛА Россия: "
        f"{sources.get('bpla_russia', {}).get('status', 'green')}\n"

        f"🗺️ RadarMap: "
        f"{sources.get('radarmap', {}).get('status', 'green')}"
    )


# =========================
# /TEST — ТОЛЬКО АДМИН
# =========================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Доступ запрещён."
        )

        return

    message = (
        "🧪 UAV ALERT — ТЕСТ\n\n"

        "📍 Костромская область\n\n"

        "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ\n\n"

        "🏘️ Пример территорий:\n"
        "• Кострома\n"
        "• Буй\n"
        "• Галич\n\n"

        "⚠️ Это тестовое сообщение.\n"
        "Реальная опасность не объявлена "
        "этим сообщением."
    )

    # Отправляем тест тебе

    await update.message.reply_text(
        message
    )

    # Отправляем тест в канал

    try:

        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message
        )

    except Exception as e:

        print(
            f"Ошибка отправки теста "
            f"в канал: {e}"
        )


# =========================
# ЗАПУСК
# =========================

async def post_init(
    application
):

    asyncio.create_task(
        monitor(application)
    )


def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "Не найдена переменная BOT_TOKEN"
        )

    threading.Thread(
        target=run_flask,
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
        "🤖 UAV ALERT запущен"
    )

    print(
        f"🔐 Администратор: {ADMIN_ID}"
    )

    print(
        f"📢 Канал: {CHANNEL_ID}"
    )

    application.run_polling()


if __name__ == "__main__":
    main()
