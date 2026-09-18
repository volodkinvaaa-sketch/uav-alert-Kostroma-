import os
import json
import asyncio
import threading
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

ADMIN_ID = 1421675956

CHANNEL_ID = "@RADAR_Kostroma"
CHANNEL_URL = "https://t.me/RADAR_Kostroma"

AD_URL = "https://t.me/soraveobot_bot?start=_tgr_09OvCARhMDhi"

CHECK_INTERVAL = 60

STATE_FILE = "state.json"
SUBSCRIBERS_FILE = "subscribers.json"
HISTORY_FILE = "history.json"

MAX_HISTORY = 20


# =========================================================
# ИСТОЧНИКИ
# =========================================================

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
# ГОРОДА
# =========================================================

CITIES = [
    "Кострома",
    "Буй",
    "Волгореченск",
    "Галич",
    "Шарья",
    "Мантурово",
    "Нерехта",
    "Чухлома",
    "Макарьев",
    "Солигалич",
    "Кологрив",
    "Нея",
]


# =========================================================
# РАЙОНЫ / ОКРУГА
# =========================================================

TERRITORIES = [
    "Антроповский муниципальный округ",
    "Буйский муниципальный округ",
    "Вохомский муниципальный округ",
    "Галичский муниципальный округ",
    "Кадыйский муниципальный округ",
    "Кологривский муниципальный округ",
    "Макарьевский муниципальный округ",
    "Мантуровский муниципальный округ",
    "Межевской муниципальный округ",
    "Нейский муниципальный округ",
    "Октябрьский муниципальный округ",
    "Островский муниципальный округ",
    "Павинский муниципальный округ",
    "Парфеньевский муниципальный округ",
    "Поназыревский муниципальный округ",
    "Пыщугский муниципальный округ",
    "Солигаличский муниципальный округ",
    "Сусанинский муниципальный округ",
    "Чухломский муниципальный округ",
    "Шарьинский муниципальный округ",

    "Антроповский район",
    "Буйский район",
    "Вохомский район",
    "Галичский район",
    "Кадыйский район",
    "Кологривский район",
    "Макарьевский район",
    "Красносельский район",
    "Костромской район",
    "Межевской район",
    "Нерехтский район",
    "Октябрьский район",
    "Островский район",
    "Павинский район",
    "Парфеньевский район",
    "Поназыревский район",
    "Пыщугский район",
    "Солигаличский район",
    "Сусанинский район",
    "Чухломский район",
    "Шарьинский район",
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
    data = load_json(STATE_FILE, {})
    return json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
    )


def run_flask():
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# JSON
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)

    except Exception as error:
        print(f"Ошибка чтения {filename}: {error}")
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as error:
        print(f"Ошибка сохранения {filename}: {error}")


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def load_subscribers():
    data = load_json(
        SUBSCRIBERS_FILE,
        {},
    )

    if isinstance(data, list):
        result = {}

        for user_id in data:
            result[str(user_id)] = {
                "location_type": None,
                "location": None,
                "notifications": True,
                "send_green": True,
            }

        save_subscribers(result)
        return result

    if not isinstance(data, dict):
        return {}

    changed = False

    for user_id, user in data.items():

        if not isinstance(user, dict):
            data[user_id] = {
                "location_type": None,
                "location": None,
                "notifications": True,
                "send_green": True,
            }
            changed = True
            continue

        if "notifications" not in user:
            user["notifications"] = True
            changed = True

        if "send_green" not in user:
            user["send_green"] = True
            changed = True

    if changed:
        save_subscribers(data)

    return data


def save_subscribers(data):
    save_json(
        SUBSCRIBERS_FILE,
        data,
    )


def get_user_data(user_id):
    subscribers = load_subscribers()

    return subscribers.get(
        str(user_id),
        {},
    )


def set_user_location(
    user_id,
    location_type,
    location,
):
    subscribers = load_subscribers()
    key = str(user_id)

    old = subscribers.get(key, {})

    subscribers[key] = {
        "location_type": location_type,
        "location": location,
        "notifications": old.get(
            "notifications",
            True,
        ),
        "send_green": old.get(
            "send_green",
            True,
        ),
    }

    save_subscribers(subscribers)


def remove_subscriber(user_id):
    subscribers = load_subscribers()

    subscribers.pop(
        str(user_id),
        None,
    )

    save_subscribers(subscribers)


# =========================================================
# ИСТОРИЯ
# =========================================================

def add_history(data):
    history = load_json(
        HISTORY_FILE,
        [],
    )

    territories, cities = collect_locations(data)

    item = {
        "time": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
        "status": data["overall"],
        "territories": territories,
        "cities": cities,
    }

    history.insert(
        0,
        item,
    )

    history = history[:MAX_HISTORY]

    save_json(
        HISTORY_FILE,
        history,
    )


def get_history():
    return load_json(
        HISTORY_FILE,
        [],
    )


# =========================================================
# КНОПКИ
# =========================================================

def location_type_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏙️ Город",
                callback_data="location_city",
            )
        ],
        [
            InlineKeyboardButton(
                "🏘️ Район / округ",
                callback_data="location_territory",
            )
        ],
    ])


def cities_keyboard():
    keyboard = []
    row = []

    for city in CITIES:
        row.append(
            InlineKeyboardButton(
                f"🏙️ {city}",
                callback_data=f"city:{city}",
            )
        )

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="choose_location",
        )
    ])

    return InlineKeyboardMarkup(keyboard)


def territories_keyboard():
    keyboard = []

    for index, territory in enumerate(TERRITORIES):
        keyboard.append([
            InlineKeyboardButton(
                f"🏘️ {territory}",
                callback_data=f"territory:{index}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            "⬅️ Назад",
            callback_data="choose_location",
        )
    ])

    return InlineKeyboardMarkup(keyboard)


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="current_status",
            )
        ],
        [
            InlineKeyboardButton(
                "📍 Моё место",
                callback_data="my_location",
            ),
            InlineKeyboardButton(
                "🔄 Изменить",
                callback_data="choose_location",
            ),
        ],
        [
            InlineKeyboardButton(
                "📚 История",
                callback_data="history",
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ Настройки",
                callback_data="settings",
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Наш канал",
                url=CHANNEL_URL,
            )
        ],
    ])


def settings_keyboard(user_id):
    user = get_user_data(user_id)

    notifications = user.get(
        "notifications",
        True,
    )

    send_green = user.get(
        "send_green",
        True,
    )

    notifications_text = (
        "🔔 Уведомления: ВКЛ"
        if notifications
        else "🔕 Уведомления: ВЫКЛ"
    )

    green_text = (
        "🟢 Отбой: ВКЛ"
        if send_green
        else "⚪ Отбой: ВЫКЛ"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                notifications_text,
                callback_data="toggle_notifications",
            )
        ],
        [
            InlineKeyboardButton(
                green_text,
                callback_data="toggle_green",
            )
        ],
        [
            InlineKeyboardButton(
                "📍 Изменить место",
                callback_data="choose_location",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="main_menu",
            )
        ],
    ])


# =========================================================
# ОПРЕДЕЛЕНИЕ КОСТРОМСКОЙ ОБЛАСТИ
# =========================================================

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


# =========================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================================================

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

    for word in red_words:
        if word in text:
            return "red"

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

    for word in green_words:
        if word in text:
            return "green"

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

    for word in yellow_words:
        if word in text:
            return "yellow"

    drone_words = [
        "бпла",
        "беспилотник",
        "беспилотников",
        "беспилотного аппарата",
        "беспилотные аппараты",
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

    return "green"


def status_text(status):
    if status == "red":
        return "🔴 РАКЕТНАЯ ОПАСНОСТЬ"

    if status == "yellow":
        return "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ"

    return "🟢 ОПАСНОСТЬ НЕ ОБЪЯВЛЕНА"


# =========================================================
# ПОИСК РАЙОНОВ
# =========================================================

def find_territories(text):
    result = []
    lower_text = text.lower()

    for territory in TERRITORIES:
        if territory.lower() in lower_text:
            if territory not in result:
                result.append(territory)

    return result


# =========================================================
# ПОИСК ГОРОДОВ
# =========================================================

def find_cities(text):
    result = []
    lower_text = text.lower()

    for city in CITIES:
        if city.lower() in lower_text:
            if city not in result:
                result.append(city)

    return result


# =========================================================
# ПОЛУЧЕНИЕ TELEGRAM ПОСТОВ
# =========================================================

def get_telegram_posts(url):
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        posts = []

        for message in soup.select(
            ".tgme_widget_message"
        ):

            text_element = message.select_one(
                ".tgme_widget_message_text"
            )

            date_element = message.select_one(
                ".tgme_widget_message_date"
            )

            if not text_element:
                continue

            text = text_element.get_text(
                "\n",
                strip=True,
            )

            post_id = None

            data_post = message.get(
                "data-post"
            )

            if data_post:
                try:
                    post_id = int(
                        data_post.split("/")[-1]
                    )
                except Exception:
                    pass

            date_text = ""

            if date_element:
                date_text = date_element.get_text(
                    " ",
                    strip=True,
                )

            posts.append({
                "text": text,
                "post_id": post_id,
                "date": date_text,
                "url": url,
            })

        return posts

    except Exception as error:
        print(
            f"Ошибка получения {url}: {error}"
        )
        return []


# =========================================================
# ПОИСК ПОСЛЕДНЕГО СТАТУСА
# =========================================================

def find_latest_status(posts):
    found = []

    for post in posts:
        text = post["text"]

        if not is_kostroma(text):
            continue

        found.append({
            "status": detect_status(text),
            "post": post,
            "territories": find_territories(text),
            "cities": find_cities(text),
        })

    if not found:
        return None

    found.sort(
        key=lambda item: (
            item["post"]["post_id"]
            if item["post"]["post_id"] is not None
            else 0
        ),
        reverse=True,
    )

    return found[0]


# =========================================================
# RADARMAP
# =========================================================

def get_radarmap_status():
    try:
        response = requests.get(
            "https://radar-map.ru/",
            timeout=20,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        response.raise_for_status()

        text = response.text.lower()

        if "костром" not in text:
            return {
                "status": "green",
                "territories": [],
                "cities": [],
            }

        return {
            "status": detect_status(text),
            "territories": [],
            "cities": [],
        }

    except Exception as error:
        print(
            f"Ошибка RadarMap: {error}"
        )

        return {
            "status": "green",
            "territories": [],
            "cities": [],
        }


# =========================================================
# ПРОВЕРКА ИСТОЧНИКОВ
# =========================================================

def check_sources():
    results = {}

    for key, source in SOURCES.items():

        if key == "radarmap":
            result = get_radarmap_status()

        else:
            posts = get_telegram_posts(
                source["url"]
            )

            latest = find_latest_status(
                posts
            )

            if latest:
                result = {
                    "status": latest["status"],
                    "territories": latest["territories"],
                    "cities": latest["cities"],
                }
            else:
                result = {
                    "status": "green",
                    "territories": [],
                    "cities": [],
                }

        results[key] = {
            "name": source["name"],
            "status": result["status"],
            "territories": result["territories"],
            "cities": result["cities"],
        }

    overall = "green"

    for result in results.values():

        if result["status"] == "red":
            overall = "red"
            break

        if (
            result["status"] == "yellow"
            and overall != "red"
        ):
            overall = "yellow"

    return {
        "overall": overall,
        "sources": results,
        "checked_at": datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        ),
    }


# =========================================================
# СОБИРАЕМ ЛОКАЦИИ
# =========================================================

def collect_locations(data):
    territories = []
    cities = []

    for source in data["sources"].values():

        for territory in source.get(
            "territories",
            [],
        ):
            if territory not in territories:
                territories.append(territory)

        for city in source.get(
            "cities",
            [],
        ):
            if city not in cities:
                cities.append(city)

    return territories, cities


# =========================================================
# ПРОВЕРКА МЕСТА
# =========================================================

def notification_matches_location(
    location,
    data,
):
    if not location:
        return False

    territories, cities = collect_locations(
        data
    )

    location_lower = location.lower()

    for city in cities:
        if city.lower() == location_lower:
            return True

    for territory in territories:
        if territory.lower() == location_lower:
            return True

    return False


# =========================================================
# УВЕДОМЛЕНИЕ
# =========================================================

def build_notification(data):
    territories, cities = collect_locations(
        data
    )

    text = (
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        f"{status_text(data['overall'])}\n"
    )

    if territories:
        text += (
            "\n🏘️ Районы / муниципальные "
            "округа, указанные в источниках:\n"
        )

        for territory in territories:
            text += f"• {territory}\n"

    if cities:
        text += (
            "\n🏙️ Населённые пункты, "
            "указанные в источниках:\n"
        )

        for city in cities:
            text += f"• {city}\n"

    if not territories and not cities:
        text += (
            "\n📍 Конкретный район или город "
            "в найденном сообщении не указан.\n"
        )

    text += (
        "\n🕐 Проверено: "
        f"{data['checked_at']}\n"
    )

    text += (
        "\n━━━━━━━━━━━━━━\n"
        "📢 Партнёрская рекомендация\n"
        "🤖 SORAVEO BOT\n"
        f"🔗 {AD_URL}\n"
        "━━━━━━━━━━━━━━\n"
    )

    text += (
        "\n⚠️ Информационное уведомление.\n"
        "Бот показывает только сведения, "
        "указанные в доступных источниках.\n"
        "Приоритет имеют официальные "
        "сообщения органов власти и МЧС."
    )

    return text


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user

    if not user:
        return

    subscribers = load_subscribers()

    if str(user.id) not in subscribers:
        subscribers[str(user.id)] = {
            "location_type": None,
            "location": None,
            "notifications": True,
            "send_green": True,
        }

        save_subscribers(subscribers)

    user_data = get_user_data(user.id)
    location = user_data.get("location")

    if location:
        await update.message.reply_text(
            "🚨 UAV ALERT\n\n"
            f"📍 Ваше место: {location}\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard(),
        )
    else:
        await update.message.reply_text(
            "🚨 Добро пожаловать в UAV ALERT!\n\n"
            "📍 Сначала выберите город "
            "или район / муниципальный округ.\n\n"
            "Бот не определяет ваше "
            "местоположение автоматически.",
            reply_markup=location_type_keyboard(),
        )


# =========================================================
# ВЫБОР МЕСТА
# =========================================================

async def choose_location(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "📍 Выберите тип места:",
        reply_markup=location_type_keyboard(),
    )


async def show_cities(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏙️ Выберите ваш город:",
        reply_markup=cities_keyboard(),
    )


async def show_territories(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏘️ Выберите ваш район / "
        "муниципальный округ:",
        reply_markup=territories_keyboard(),
    )


async def select_city(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    city = query.data.split(
        ":",
        1,
    )[1]

    set_user_location(
        query.from_user.id,
        "city",
        city,
    )

    await query.edit_message_text(
        "✅ Место сохранено!\n\n"
        f"🏙️ {city}\n\n"
        "Теперь бот будет учитывать "
        "выбранный город.",
        reply_markup=main_keyboard(),
    )


async def select_territory(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    try:
        index = int(
            query.data.split(
                ":",
                1,
            )[1]
        )

        territory = TERRITORIES[index]

    except Exception:
        await query.edit_message_text(
            "❌ Не удалось выбрать район.",
            reply_markup=location_type_keyboard(),
        )
        return

    set_user_location(
        query.from_user.id,
        "territory",
        territory,
    )

    await query.edit_message_text(
        "✅ Место сохранено!\n\n"
        f"🏘️ {territory}\n\n"
        "Теперь бот будет учитывать "
        "выбранный район / округ.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# МОЁ МЕСТО
# =========================================================

async def my_location(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user = get_user_data(
        query.from_user.id
    )

    location = user.get(
        "location"
    )

    if location:
        await query.edit_message_text(
            "📍 МОЁ МЕСТО\n\n"
            f"➡️ {location}\n\n"
            "Бот не определяет ваше "
            "местоположение автоматически.",
            reply_markup=main_keyboard(),
        )
    else:
        await query.edit_message_text(
            "📍 Место ещё не выбрано.",
            reply_markup=location_type_keyboard(),
        )


# =========================================================
# ТЕКУЩИЙ СТАТУС
# =========================================================

async def current_status(
    update,
    context,
):
    query = update.callback_query

    await query.answer(
        "Проверяем источники..."
    )

    data = check_sources()

    territories, cities = collect_locations(
        data
    )

    text = (
        "📊 ТЕКУЩИЙ СТАТУС\n\n"
        "📍 Костромская область\n\n"
        f"{status_text(data['overall'])}\n\n"
        f"🕐 Проверено: {data['checked_at']}\n"
    )

    if territories:
        text += "\n🏘️ Районы:\n"

        for territory in territories:
            text += f"• {territory}\n"

    if cities:
        text += "\n🏙️ Города:\n"

        for city in cities:
            text += f"• {city}\n"

    text += (
        "\n⚠️ Приоритет имеют официальные "
        "сообщения органов власти и МЧС."
    )

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard(),
    )


# =========================================================
# ИСТОРИЯ
# =========================================================

async def history_command(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    history = get_history()

    if not history:
        await query.edit_message_text(
            "📚 История пока пустая.\n\n"
            "Она появится после обнаружения "
            "изменений состояния.",
            reply_markup=main_keyboard(),
        )
        return

    text = "📚 ПОСЛЕДНИЕ ОБНОВЛЕНИЯ\n\n"

    for item in history[:10]:

        text += (
            f"🕐 {item.get('time', '—')}\n"
            f"{status_text(item.get('status', 'green'))}\n"
        )

        if item.get("territories"):
            text += (
                "🏘️ "
                + ", ".join(
                    item["territories"][:3]
                )
                + "\n"
            )

        if item.get("cities"):
            text += (
                "🏙️ "
                + ", ".join(
                    item["cities"][:3]
                )
                + "\n"
            )

        text += "\n"

    await query.edit_message_text(
        text,
        reply_markup=main_keyboard(),
    )


# =========================================================
# НАСТРОЙКИ
# =========================================================

async def settings_command(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    user = get_user_data(
        query.from_user.id
    )

    location = user.get(
        "location",
        "не выбрано",
    )

    await query.edit_message_text(
        "⚙️ НАСТРОЙКИ\n\n"
        f"📍 Место: {location}\n\n"
        "Настройте уведомления:",
        reply_markup=settings_keyboard(
            query.from_user.id
        ),
    )


async def toggle_notifications(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    subscribers = load_subscribers()

    user = subscribers.get(
        str(query.from_user.id)
    )

    if not user:
        return

    user["notifications"] = not user.get(
        "notifications",
        True,
    )

    save_subscribers(subscribers)

    await settings_command(
        update,
        context,
    )


async def toggle_green(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    subscribers = load_subscribers()

    user = subscribers.get(
        str(query.from_user.id)
    )

    if not user:
        return

    user["send_green"] = not user.get(
        "send_green",
        True,
    )

    save_subscribers(subscribers)

    await settings_command(
        update,
        context,
    )


# =========================================================
# CALLBACK
# =========================================================

async def callback_router(
    update,
    context,
):
    query = update.callback_query

    if not query:
        return

    data = query.data

    if data == "main_menu":
        await query.answer()
        await query.edit_message_text(
            "🚨 UAV ALERT\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard(),
        )

    elif data == "choose_location":
        await choose_location(
            update,
            context,
        )

    elif data == "location_city":
        await show_cities(
            update,
            context,
        )

    elif data == "location_territory":
        await show_territories(
            update,
            context,
        )

    elif data == "my_location":
        await my_location(
            update,
            context,
        )

    elif data == "current_status":
        await current_status(
            update,
            context,
        )

    elif data == "history":
        await history_command(
            update,
            context,
        )

    elif data == "settings":
        await settings_command(
            update,
            context,
        )

    elif data == "toggle_notifications":
        await toggle_notifications(
            update,
            context,
        )

    elif data == "toggle_green":
        await toggle_green(
            update,
            context,
        )

    elif data.startswith("city:"):
        await select_city(
            update,
            context,
        )

    elif data.startswith("territory:"):
        await select_territory(
            update,
            context,
        )


# =========================================================
# STOP
# =========================================================

async def stop(
    update,
    context,
):
    user = update.effective_user

    if not user:
        return

    remove_subscriber(
        user.id
    )

    await update.message.reply_text(
        "🔕 Уведомления отключены.\n\n"
        "Чтобы снова подключиться — /start"
    )


# =========================================================
# АДМИН
# =========================================================

def is_admin(update):
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


async def status_command(
    update,
    context,
):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    data = check_sources()

    text = (
        "📊 UAV ALERT\n\n"
        f"{status_text(data['overall'])}\n\n"
        f"🕐 {data['checked_at']}\n\n"
    )

    for source in data["sources"].values():
        text += (
            f"{source['name']}: "
            f"{status_text(source['status'])}\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# ТЕСТ
# =========================================================

async def test_command(
    update,
    context,
):
    if not is_admin(update):
        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    text = (
        "🧪 ТЕСТ UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ\n\n"
        "🏘️ Район / округ:\n"
        "• Тестовый муниципальный округ\n\n"
        "🏙️ Населённый пункт:\n"
        "• Кострома\n\n"
        "⚠️ Это тестовое сообщение. "
        "Оно НЕ является реальным "
        "сообщением об опасности.\n\n"
        "━━━━━━━━━━━━━━\n"
        "📢 Партнёрская рекомендация\n"
        "🤖 SORAVEO BOT\n"
        f"🔗 {AD_URL}\n"
        "━━━━━━━━━━━━━━"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
        )
    except Exception as error:
        print(
            f"Ошибка отправки теста админу: {error}"
        )

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
        )
    except Exception as error:
        print(
            f"Ошибка отправки теста в канал: {error}"
        )

    await update.message.reply_text(
        "✅ Тест отправлен."
    )


# =========================================================
# ОТПРАВКА УВЕДОМЛЕНИЙ
# =========================================================

async def send_notification(
    application,
    data,
):
    subscribers = load_subscribers()

    text = build_notification(
        data
    )

    try:
        await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
        )

        print(
            "📢 Сообщение отправлено в канал."
        )

    except Exception as error:
        print(
            f"Ошибка отправки в канал: {error}"
        )

    for user_id, user in subscribers.items():

        if not isinstance(user, dict):
            continue

        if not user.get(
            "notifications",
            True,
        ):
            continue

        location = user.get(
            "location"
        )

        if not location:
            continue

        if not notification_matches_location(
            location,
            data,
        ):
            continue

        if (
            data["overall"] == "green"
            and not user.get(
                "send_green",
                True,
            )
        ):
            continue

        try:
            await application.bot.send_message(
                chat_id=int(user_id),
                text=text,
            )

            print(
                f"🔔 Уведомление отправлено "
                f"{user_id}"
            )

        except Exception as error:
            print(
                f"Ошибка пользователю "
                f"{user_id}: {error}"
            )


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitor(application):

    print(
        "📡 Мониторинг источников запущен."
    )

    previous_state = load_json(
        STATE_FILE,
        None,
    )

    first_run = previous_state is None

    while True:

        try:
            data = check_sources()

            current_state = {
                "overall": data["overall"],
                "sources": data["sources"],
                "checked_at": data["checked_at"],
            }

            if first_run:

                save_json(
                    STATE_FILE,
                    current_state,
                )

                previous_state = current_state
                first_run = False

                print(
                    "💾 Первичное состояние сохранено."
                )

            else:

                old_compare = {
                    "overall": previous_state.get(
                        "overall"
                    ),
                    "sources": previous_state.get(
                        "sources",
                        {},
                    ),
                }

                new_compare = {
                    "overall": current_state["overall"],
                    "sources": current_state["sources"],
                }

                if old_compare != new_compare:

                    print(
                        "🔄 Обнаружено изменение состояния."
                    )

                    add_history(data)

                    await send_notification(
                        application,
                        data,
                    )

                    save_json(
                        STATE_FILE,
                        current_state,
                    )

                    previous_state = current_state

                else:

                    save_json(
                        STATE_FILE,
                        current_state,
                    )

                    previous_state[
                        "checked_at"
                    ] = current_state[
                        "checked_at"
                    ]

        except Exception as error:

            print(
                f"Ошибка мониторинга: {error}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# POST INIT
# =========================================================

async def post_init(application):

    asyncio.create_task(
        monitor(application)
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:
        raise RuntimeError(
            "Не найдена переменная BOT_TOKEN"
        )

    threading.Thread(
        target=run_flask,
        daemon=True,
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
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "stop",
            stop,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "test",
            test_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_router
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
