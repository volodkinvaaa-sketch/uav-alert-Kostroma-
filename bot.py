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
# РАЙОНЫ / МУНИЦИПАЛЬНЫЕ ОКРУГА
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

    "Антроповский муниципальный район",
    "Буйский муниципальный район",
    "Вохомский муниципальный район",
    "Галичский муниципальный район",
    "Кадыйский муниципальный район",
    "Кологривский муниципальный район",
    "Костромской муниципальный район",
    "Макарьевский муниципальный район",
    "Межевской муниципальный район",
    "Нерехтский муниципальный район",
    "Октябрьский муниципальный район",
    "Островский муниципальный район",
    "Павинский муниципальный район",
    "Парфеньевский муниципальный район",
    "Поназыревский муниципальный район",
    "Пыщугский муниципальный район",
    "Солигаличский муниципальный район",
    "Сусанинский муниципальный район",
    "Чухломский муниципальный район",
    "Шарьинский муниципальный район",

    "Антроповский",
    "Буйский",
    "Вохомский",
    "Галичский",
    "Кадыйский",
    "Кологривский",
    "Костромской",
    "Красносельский",
    "Макарьевский",
    "Межевской",
    "Мантуровский",
    "Нейский",
    "Нерехтский",
    "Октябрьский",
    "Островский",
    "Павинский",
    "Парфеньевский",
    "Поназыревский",
    "Пыщугский",
    "Солигаличский",
    "Сусанинский",
    "Чухломский",
    "Шарьинский",
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
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "Состояние пока отсутствует."


def run_flask():
    app.run(
        host="0.0.0.0",
        port=10000,
        debug=False,
        use_reloader=False,
    )


# =========================================================
# ФАЙЛЫ
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as e:
        print(f"Ошибка сохранения {filename}: {e}")


# =========================================================
# ПОДПИСЧИКИ
# =========================================================

def load_subscribers():
    data = load_json(SUBSCRIBERS_FILE, {})

    # Старый формат:
    # [123456, 654321]
    if isinstance(data, list):
        result = {}

        for user_id in data:
            result[str(user_id)] = {
                "location_type": None,
                "location": None,
            }

        save_json(SUBSCRIBERS_FILE, result)
        return result

    # Новый формат
    if isinstance(data, dict):
        return data

    return {}


def save_subscribers(data):
    save_json(SUBSCRIBERS_FILE, data)


def get_user_location(user_id):
    subscribers = load_subscribers()

    user = subscribers.get(str(user_id))

    if not isinstance(user, dict):
        return None

    return user.get("location")


def set_user_location(user_id, location_type, location):
    subscribers = load_subscribers()

    subscribers[str(user_id)] = {
        "location_type": location_type,
        "location": location,
    }

    save_subscribers(subscribers)


def remove_subscriber(user_id):
    subscribers = load_subscribers()

    subscribers.pop(str(user_id), None)

    save_subscribers(subscribers)


# =========================================================
# КНОПКИ
# =========================================================

def location_type_keyboard():
    keyboard = [
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
    ]

    return InlineKeyboardMarkup(keyboard)


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

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="choose_location",
            )
        ]
    )

    return InlineKeyboardMarkup(keyboard)


def territories_keyboard():
    keyboard = []

    # Telegram callback_data имеет ограничение по длине,
    # поэтому используем индекс вместо длинного названия.
    for index, territory in enumerate(TERRITORIES):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🏘️ {territory}",
                    callback_data=f"territory:{index}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="choose_location",
            )
        ]
    )

    return InlineKeyboardMarkup(keyboard)


def main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton(
                "📍 Моё место",
                callback_data="my_location",
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Изменить место",
                callback_data="choose_location",
            )
        ],
        [
            InlineKeyboardButton(
                "📢 Наш канал",
                url=CHANNEL_URL,
            )
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================================================
# ПРОВЕРКА КОСТРОМСКОЙ ОБЛАСТИ
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

    return any(word in text for word in words)


# =========================================================
# ОПРЕДЕЛЕНИЕ СТАТУСА
# =========================================================

def detect_status(text):
    text = text.lower()

    # Сначала проверяем красный статус.

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

    # Отмена опасности.

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

    # Беспилотная опасность.

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

    has_drone = any(word in text for word in drone_words)
    has_danger = any(word in text for word in danger_words)

    if has_drone and has_danger:
        return "yellow"

    return "green"


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
# TELEGRAM SCRAPER
# =========================================================

def get_telegram_posts(url):
    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "Chrome/120 Safari/537.36"
                )
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

            posts.append(
                {
                    "text": text,
                    "post_id": post_id,
                    "date": date_text,
                    "url": url,
                }
            )

        return posts

    except Exception as e:
        print(
            f"Ошибка получения Telegram-источника "
            f"{url}: {e}"
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

        status = detect_status(text)

        territories = find_territories(text)
        cities = find_cities(text)

        found.append(
            {
                "status": status,
                "post": post,
                "territories": territories,
                "cities": cities,
            }
        )

    if not found:
        return None

    found.sort(
        key=lambda x: (
            x["post"]["post_id"]
            if x["post"]["post_id"] is not None
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

        status = detect_status(text)

        return {
            "status": status,
            "territories": [],
            "cities": [],
        }

    except Exception as e:
        print(
            f"Ошибка RadarMap: {e}"
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

            results[key] = {
                "name": source["name"],
                "status": result["status"],
                "territories": result.get(
                    "territories",
                    [],
                ),
                "cities": result.get(
                    "cities",
                    [],
                ),
            }

            continue

        posts = get_telegram_posts(
            source["url"]
        )

        latest = find_latest_status(posts)

        if latest:
            results[key] = {
                "name": source["name"],
                "status": latest["status"],
                "territories": latest[
                    "territories"
                ],
                "cities": latest[
                    "cities"
                ],
            }
        else:
            results[key] = {
                "name": source["name"],
                "status": "green",
                "territories": [],
                "cities": [],
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
    }


# =========================================================
# ТЕКСТ СТАТУСА
# =========================================================

def status_text(status):

    if status == "red":
        return "🔴 РАКЕТНАЯ ОПАСНОСТЬ"

    if status == "yellow":
        return "🟡 БЕСПИЛОТНАЯ ОПАСНОСТЬ"

    return "🟢 ОПАСНОСТЬ НЕ ОБЪЯВЛЕНА"


# =========================================================
# ПОИСК МЕСТА ВО ВСЕХ ИСТОЧНИКАХ
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
                territories.append(
                    territory
                )

        for city in source.get(
            "cities",
            [],
        ):
            if city not in cities:
                cities.append(city)

    return territories, cities


# =========================================================
# ПРОВЕРКА, КАСАЕТСЯ ЛИ УВЕДОМЛЕНИЕ ПОЛЬЗОВАТЕЛЯ
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

    # Если пользователь выбрал город.

    for city in cities:
        if city.lower() == location_lower:
            return True

    # Если пользователь выбрал район/округ.

    for territory in territories:
        if territory.lower() == location_lower:
            return True

    return False


# =========================================================
# ФОРМИРОВАНИЕ ОБЩЕГО УВЕДОМЛЕНИЯ
# =========================================================

def build_notification(data):

    overall = data["overall"]

    territories, cities = collect_locations(
        data
    )

    text = (
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        f"{status_text(overall)}\n"
    )

    if territories:

        text += (
            "\n🏘️ Районы / муниципальные округа, "
            "указанные в источниках:\n"
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
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if user is None:
        return

    subscribers = load_subscribers()

    if str(user.id) not in subscribers:
        subscribers[str(user.id)] = {
            "location_type": None,
            "location": None,
        }

        save_subscribers(subscribers)

    location = get_user_location(
        user.id
    )

    if location:

        await update.message.reply_text(
            "🚨 UAV ALERT\n\n"
            f"📍 Ваше место: {location}\n\n"
            "Вы будете получать уведомления, "
            "когда выбранное вами место "
            "прямо указано в доступном источнике.\n\n"
            "Выберите действие:",
            reply_markup=main_keyboard(),
        )

    else:

        await update.message.reply_text(
            "🚨 Добро пожаловать в UAV ALERT!\n\n"
            "Это гражданский информационный сервис "
            "по Костромской области.\n\n"
            "📍 Сначала выберите город или "
            "район / муниципальный округ, "
            "в котором вы проживаете.\n\n"
            "Бот не определяет ваше местоположение "
            "автоматически.",
            reply_markup=location_type_keyboard(),
        )


# =========================================================
# ВЫБОР МЕСТА
# =========================================================

async def choose_location(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "📍 Выберите тип места проживания:",
        reply_markup=location_type_keyboard(),
    )


# =========================================================
# МОЁ МЕСТО
# =========================================================

async def my_location(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    location = get_user_location(
        query.from_user.id
    )

    if location:

        await query.edit_message_text(
            "📍 Ваше место проживания:\n\n"
            f"➡️ {location}\n\n"
            "Можно изменить его в любой момент.",
            reply_markup=main_keyboard(),
        )

    else:

        await query.edit_message_text(
            "📍 Вы ещё не выбрали место проживания.",
            reply_markup=location_type_keyboard(),
        )


# =========================================================
# ГОРОДА
# =========================================================

async def show_cities(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏙️ Выберите ваш город:",
        reply_markup=cities_keyboard(),
    )


# =========================================================
# РАЙОНЫ
# =========================================================

async def show_territories(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🏘️ Выберите ваш район / "
        "муниципальный округ:",
        reply_markup=territories_keyboard(),
    )


# =========================================================
# СОХРАНЕНИЕ ГОРОДА
# =========================================================

async def select_city(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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
        "✅ Место проживания сохранено!\n\n"
        f"🏙️ {city}\n\n"
        "Теперь UAV ALERT будет учитывать "
        "ваш выбранный город.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# СОХРАНЕНИЕ РАЙОНА
# =========================================================

async def select_territory(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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
        "✅ Место проживания сохранено!\n\n"
        f"🏘️ {territory}\n\n"
        "Теперь UAV ALERT будет учитывать "
        "ваш район / муниципальный округ.",
        reply_markup=main_keyboard(),
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    data = query.data

    if data == "choose_location":
        await choose_location(
            update,
            context,
        )
        return

    if data == "location_city":
        await show_cities(
            update,
            context,
        )
        return

    if data == "location_territory":
        await show_territories(
            update,
            context,
        )
        return

    if data == "my_location":
        await my_location(
            update,
            context,
        )
        return

    if data.startswith("city:"):
        await select_city(
            update,
            context,
        )
        return

    if data.startswith("territory:"):
        await select_territory(
            update,
            context,
        )
        return


# =========================================================
# /STOP
# =========================================================

async def stop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user = update.effective_user

    if user is None:
        return

    remove_subscriber(user.id)

    await update.message.reply_text(
        "🔕 Вы больше не будете получать "
        "уведомления от UAV ALERT.\n\n"
        "Чтобы снова включить уведомления, "
        "используйте /start."
    )


# =========================================================
# ПРОВЕРКА АДМИНИСТРАТОРА
# =========================================================

def is_admin(update):

    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not is_admin(update):

        await update.message.reply_text(
            "⛔ Команда доступна только администратору."
        )

        return

    data = check_sources()

    text = (
        "📊 UAV ALERT\n\n"
        f"Общий статус: "
        f"{status_text(data['overall'])}\n\n"
    )

    for key, source in data["sources"].items():

        text += (
            f"{source['name']}: "
            f"{status_text(source['status'])}\n"
        )

    territories, cities = collect_locations(
        data
    )

    if territories:

        text += "\n🏘️ Районы:\n"

        for territory in territories:
            text += f"• {territory}\n"

    if cities:

        text += "\n🏙️ Города:\n"

        for city in cities:
            text += f"• {city}\n"

    await update.message.reply_text(text)


# =========================================================
# /TEST
# =========================================================

async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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
        "⚠️ Это тестовое уведомление. "
        "Оно не является реальным сообщением "
        "об опасности.\n\n"
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
    except Exception as e:
        print(
            f"Ошибка отправки теста админу: {e}"
        )

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
        )
    except Exception as e:
        print(
            f"Ошибка отправки теста в канал: {e}"
        )

    await update.message.reply_text(
        "✅ Тест отправлен."
    )


# =========================================================
# УВЕДОМЛЕНИЯ
# =========================================================

async def send_notification(
    application,
    data,
):

    subscribers = load_subscribers()

    text = build_notification(data)

    # Сначала отправляем в канал.
    try:
        await application.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
        )

        print(
            "📢 Уведомление отправлено в канал."
        )

    except Exception as e:

        print(
            f"Ошибка отправки в канал: {e}"
        )

    # Затем пользователям.
    for user_id, user_data in subscribers.items():

        if not isinstance(
            user_data,
            dict,
        ):
            continue

        location = user_data.get(
            "location"
        )

        # Пользователь без выбранного места
        # не получает территориальное уведомление.
        if not location:
            continue

        if not notification_matches_location(
            location,
            data,
        ):
            continue

        try:

            await application.bot.send_message(
                chat_id=int(user_id),
                text=text,
            )

            print(
                f"🔔 Уведомление отправлено "
                f"пользователю {user_id}"
            )

        except Exception as e:

            print(
                f"Ошибка отправки пользователю "
                f"{user_id}: {e}"
            )


# =========================================================
# МОНИТОРИНГ
# =========================================================

async def monitor(application):

    print("📡 Мониторинг источников запущен.")

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

            elif current_state != previous_state:

                print(
                    "🔄 Обнаружено изменение состояния."
                )

                await send_notification(
                    application,
                    data,
                )

                save_json(
                    STATE_FILE,
                    current_state,
                )

                previous_state = current_state

        except Exception as e:

            print(
                f"Ошибка мониторинга: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application,
):

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
