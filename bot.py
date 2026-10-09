import os
import re
import json
import html
import asyncio
import hashlib
import logging
import threading

from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, Forbidden, BadRequest, RetryAfter
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ============================================================
# UAV ALERT — CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
except ValueError:
    ADMIN_ID = 1421675956

PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(15, int(os.getenv("CHECK_INTERVAL", "30")))
HISTORY_WINDOW_HOURS = max(
    1, int(os.getenv("HISTORY_WINDOW_HOURS", "5"))
)
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
SOURCE_LIMIT = max(30, int(os.getenv("SOURCE_LIMIT", "100")))

DATA_DIR = os.getenv("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0 Safari/537.36"
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("uav_alert")

MSK = timezone(timedelta(hours=3))
UTC = timezone.utc


def now_msk():
    return datetime.now(MSK)


def parse_datetime(value):
    if not value:
        return None

    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if result.tzinfo is None:
            result = result.replace(tzinfo=UTC)

        return result.astimezone(MSK)

    except (ValueError, TypeError, OverflowError):
        return None


def format_time(value):
    parsed = parse_datetime(value)

    if parsed is None:
        return "неизвестно"

    return parsed.strftime("%d.%m.%Y %H:%M МСК")


def json_path(filename):
    return os.path.join(DATA_DIR, filename)


STATE_FILE = json_path("state.json")
HISTORY_FILE = json_path("history.json")
SUBSCRIBERS_FILE = json_path("subscribers.json")
SENT_POSTS_FILE = json_path("sent_posts.json")
NOTIFICATION_EVENTS_FILE = json_path("notification_events.json")
SOURCE_STATE_FILE = json_path("source_state.json")


# ============================================================
# JSON STORAGE
# ============================================================

def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def save_json(filename, value):
    temp = filename + ".tmp"

    try:
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(
                value,
                file,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp, filename)

    except OSError:
        logger.exception("Не удалось сохранить %s", filename)

        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass


DEFAULT_STATE = {
    "uav_level": 0,
    "uav_cycle": 0,
    "rocket_active": False,
    "rocket_cycle": 0,
    "active_locations": [],
    "last_uav_time": None,
    "last_uav_source": None,
    "last_uav_post": None,
    "last_rocket_time": None,
    "last_rocket_source": None,
    "last_rocket_post": None,
    "startup_completed": False,
    "last_scan_time": None,
    "last_scan_count": 0,
}

state = load_json(STATE_FILE, DEFAULT_STATE.copy())

if not isinstance(state, dict):
    state = DEFAULT_STATE.copy()

for key, value in DEFAULT_STATE.items():
    state.setdefault(key, value)

history = load_json(HISTORY_FILE, [])

if not isinstance(history, list):
    history = []

raw_subscribers = load_json(SUBSCRIBERS_FILE, [])

if isinstance(raw_subscribers, dict):
    raw_subscribers = raw_subscribers.get("subscribers", [])

subscribers = set()

for item in raw_subscribers:
    try:
        subscribers.add(int(item))
    except (ValueError, TypeError):
        pass

sent_posts = load_json(SENT_POSTS_FILE, {})
if not isinstance(sent_posts, dict):
    sent_posts = {}

notification_events = load_json(NOTIFICATION_EVENTS_FILE, {})
if not isinstance(notification_events, dict):
    notification_events = {}

source_state = load_json(SOURCE_STATE_FILE, {})
if not isinstance(source_state, dict):
    source_state = {}


def save_state():
    save_json(STATE_FILE, state)


def save_history():
    global history
    history = history[-500:]
    save_json(HISTORY_FILE, history)


def save_subscribers():
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))


def save_sent_posts():
    save_json(SENT_POSTS_FILE, sent_posts)


def save_notification_events():
    if len(notification_events) > 3000:
        ordered = sorted(
            notification_events.items(),
            key=lambda item: item[1].get("attempted_at", ""),
        )

        for old_key, _ in ordered[:-2500]:
            notification_events.pop(old_key, None)

    save_json(NOTIFICATION_EVENTS_FILE, notification_events)


def save_source_state():
    save_json(SOURCE_STATE_FILE, source_state)


# ============================================================
# TELEGRAM SOURCES
# ============================================================

SOURCES = {
    "locator": {
        "name": "Locator",
        "url": "https://t.me/s/locatorru",
        "channel": "@locatorru",
    },
    "monitoring": {
        "name": "Russia Monitoring Radar BPLA",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
        "channel": "@russiamonitoring_radar_bpla",
    },
    "radar": {
        "name": "Radar Russia",
        "url": "https://t.me/s/radarrussiia",
        "channel": "@radarrussiia",
    },
    "bpla": {
        "name": "BPLA Russia",
        "url": "https://t.me/s/bplarussiaru",
        "channel": "@bplarussiaru",
    },
}


# ============================================================
# NORMALIZATION AND LOCATION DETECTION
# ============================================================

def normalize_text(value):
    value = value or ""
    value = value.replace("\xa0", " ")
    value = value.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", value).strip().lower()


LOCATION_PATTERNS = [
    (r"\bкостромск\w*\s+област\w*\b", "Костромская область"),
    (r"\bкостром(?:а|ы|е|у|ой|ою)\b", "Кострома"),
    (r"\bнерехт\w*\b", "Нерехта"),
    (r"\bбу(?:й|я|е|ю|ем)\b", "Буй"),
    (r"\bволгореченск\w*\b", "Волгореченск"),
    (r"\bгалич\w*\b", "Галич"),
    (r"\bшарь\w*\b", "Шарья"),
    (r"\bмантуров\w*\b", "Мантурово"),
    (r"\bчухлом\w*\b", "Чухлома"),
    (r"\bмакарьев\w*\b", "Макарьев"),
    (r"\bсолигалич\w*\b", "Солигалич"),
    (r"\bкологрив\w*\b", "Кологрив"),
    (r"\bне[яе]\b", "Нея"),
    (r"\bкостромск\w*\s+район\w*\b", "Костромской район"),
    (r"\bнерехтск\w*\s+район\w*\b", "Нерехтский район"),
    (r"\bшарьинск\w*\s+район\w*\b", "Шарьинский район"),
]


def find_locations(text):
    normalized = normalize_text(text)
    found = []

    for pattern, label in LOCATION_PATTERNS:
        if re.search(pattern, normalized, re.IGNORECASE):
            if label not in found:
                found.append(label)

    if "Костромская область" in found:
        return ["Костромская область"]

    return found


# ============================================================
# UAV AND ROCKET CONTEXT
# ============================================================

UAV_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bбпла\b|"
    r"\bбеспилот\w*\b|"
    r"\bдрон\w*\b|"
    r"\bбезэкипажн\w*\b|"
    r"\bмрлс\b|"
    r"\bмрш\b|"
    r"\bмрл\b|"
    r"\bна шарах\b|"
    r"\bшар(?:ы|ов|ами)?\s+с\s+аппаратурой\b|"
    r"\bвоздушн\w*\s+цел\w*\b|"
    r"\bлетательн\w*\s+аппарат\w*\b"
    r")",
    re.IGNORECASE,
)

ROCKET_CONTEXT_RE = re.compile(
    r"(?:"
    r"\bракетн\w*\s+опасност\w*\b|"
    r"\bракетн\w*\s+тревог\w*\b|"
    r"\bугроз\w*\s+ракетн\w*\b|"
    r"\bракет\w*\s+опасност\w*\b"
    r")",
    re.IGNORECASE,
)


def contains_uav(text):
    return bool(UAV_CONTEXT_RE.search(normalize_text(text)))


def contains_rocket(text):
    return bool(ROCKET_CONTEXT_RE.search(normalize_text(text)))


# ============================================================
# STATUS AND CANCELLATION DETECTION
# ============================================================

CANCEL_PATTERNS = [
    r"\bотбой\b",
    r"\bопасност\w*\s+отменен\w*\b",
    r"\bугроз\w*\s+отменен\w*\b",
    r"\bснят\w*\s+угроз\w*\b",
    r"\bугроз\w*\s+снят\w*\b",
    r"\bотмен\w*\s+режим\w*\b",
    r"\bрежим\s+опасност\w*\s+отменен\w*\b",
    r"\bопасност\w*\s+больше\s+нет\b",
    r"\bугроз\w*\s+больше\s+нет\b",
    r"\bтревог\w*\s+отменен\w*\b",
    r"\bсигнал\s+отбой\b",
    r"\bопасност\w*\s+снят\w*\b",
    r"\bугроз\w*\s+больше\s+не\s+актуальн\w*\b",
]


def detect_cancel(text):
    value = normalize_text(text)

    return any(
        re.search(pattern, value)
        for pattern in CANCEL_PATTERNS
    )


def detect_uav_level(text):
    value = normalize_text(text)

    # Сначала проверяем отмену.
    if detect_cancel(value):
        return 0

    # Уровень 3 — опасность или тревога.
    danger_patterns = [
        r"\bопасност\w*\s+по\s+бпла\b",
        r"\bопасност\w*\s+бпла\b",
        r"\bопасност\w*\s+по\s+мрш\b",
        r"\bопасност\w*\s+мрш\b",
        r"\bопасност\w*\s+беспилот\w*\b",
        r"\bобъявлен\w*\s+опасност\w*\s+по\s+бпла\b",
        r"\bкрасн\w*\s+уровен\w*\s+бпла\b",
        r"\bтревог\w*\s+по\s+бпла\b",
        r"\bтревог\w*\s+беспилот\w*\b",
    ]

    if any(re.search(p, value) for p in danger_patterns):
        return 3

    # Уровень 2 — угроза.
    threat_patterns = [
        r"\bугроз\w*\s+по\s+бпла\b",
        r"\bугроз\w*\s+бпла\b",
        r"\bугроз\w*\s+по\s+мрш\b",
        r"\bугроз\w*\s+мрш\b",
        r"\bугроз\w*\s+беспилот\w*\b",
        r"\bобъявлен\w*\s+угроз\w*\s+бпла\b",
        r"\bоранжев\w*\s+уровен\w*\s+бпла\b",
    ]

    if any(re.search(p, value) for p in threat_patterns):
        return 2

    # Уровень 1 — внимание.
    attention_patterns = [
        r"\bвнимание\s+по\s+бпла\b",
        r"\bвнимание\s+бпла\b",
        r"\bвнимание\s+по\s+мрш\b",
        r"\bвнимание\s+мрш\b",
        r"\bвнимание\s+беспилот\w*\b",
        r"\bжелт\w*\s+уровен\w*\s+бпла\b",
        r"\bвнимание\s*:\s*бпла\b",
    ]

    if any(re.search(p, value) for p in attention_patterns):
        return 1

    # Более свободные формулировки:
    # «Внимание: в регионе замечены беспилотники».
    if contains_uav(value):
        if re.search(r"\b(опасност\w*|тревог\w*)\b", value):
            return 3

        if re.search(r"\bугроз\w*\b", value):
            return 2

        if re.search(r"\bвнимание\b", value):
            return 1

    return 0


def detect_rocket(text):
    value = normalize_text(text)

    if detect_cancel(value):
        return False

    return contains_rocket(value)


def detect_rocket_cancel(text):
    value = normalize_text(text)

    return (
        detect_cancel(value)
        and bool(re.search(r"\bракет\w*\b", value))
    )


# ============================================================
# TELEGRAM PUBLIC PAGE PARSER
# ============================================================

def fetch_source(source_key, source_info):
    response = requests.get(
        source_info["url"],
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    posts = []

    for node in soup.select(".tgme_widget_message"):
        data_post = node.get("data-post", "")

        if "/" not in data_post:
            continue

        try:
            post_id = int(data_post.rsplit("/", 1)[1])
        except (ValueError, IndexError):
            continue

        text_node = node.select_one(".tgme_widget_message_text")
        text = text_node.get_text(" ", strip=True) if text_node else ""

        time_node = node.select_one("time[datetime]")
        date_value = time_node.get("datetime") if time_node else None
        published = parse_datetime(date_value)

        posts.append({
            "key": f"{source_key}:{post_id}",
            "source": source_key,
            "source_name": source_info["name"],
            "channel": source_info["channel"],
            "post_id": post_id,
            "text": text,
            "date": (
                published.isoformat(timespec="seconds")
                if published else None
            ),
            "url": f"https://t.me/{data_post}",
        })

    posts.sort(
        key=lambda item: (
            parse_datetime(item.get("date"))
            or datetime.min.replace(tzinfo=MSK),
            int(item.get("post_id", 0)),
        )
    )

    return posts[-SOURCE_LIMIT:]


# ============================================================
# DEDUPLICATION
# ============================================================

def post_was_processed(post):
    key = post.get("key")
    return bool(key and key in sent_posts)


def mark_post_processed(post):
    key = post.get("key")

    if not key:
        return

    sent_posts[key] = {
        "processed_at": now_msk().isoformat(timespec="seconds"),
        "date": post.get("date"),
        "source": post.get("source"),
        "url": post.get("url"),
    }

    if len(sent_posts) > 3000:
        ordered = sorted(
            sent_posts.items(),
            key=lambda item: item[1].get("processed_at", ""),
        )

        remove_count = len(sent_posts) - 2500

        for old_key, _ in ordered[:remove_count]:
            sent_posts.pop(old_key, None)


def notification_key(event_type, level, cycle, post):
    raw = "|".join([
        event_type,
        str(level),
        str(cycle),
        str(post.get("key", "")),
    ])

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ============================================================
# EVENT HISTORY
# ============================================================

def add_history(event_type, title, post=None):
    history.append({
        "time": now_msk().isoformat(timespec="seconds"),
        "event": event_type,
        "title": title,
        "source": post.get("source_name") if post else None,
        "url": post.get("url") if post else None,
        "post_time": post.get("date") if post else None,
    })

    save_history()


def level_title(level):
    return {
        0: "🟢 Опасность по БПЛА не объявлена",
        1: "🟡 Внимание по БПЛА",
        2: "🟠 Угроза по БПЛА",
        3: "🔴 Опасность по БПЛА",
    }.get(level, "Статус БПЛА неизвестен")


def source_line(post):
    if not post:
        return "Источник: автоматическая проверка UAV ALERT"

    name = html.escape(str(post.get("source_name", "Telegram")))
    url = html.escape(str(post.get("url", "")), quote=True)
    published = format_time(post.get("date"))

    if url:
        return (
            f'Источник: <a href="{url}">{name}</a>\n'
            f"Публикация: {published}"
        )

    return f"Источник: {name}\nПубликация: {published}"


# ============================================================
# PUSH MESSAGE BUILDERS
# ============================================================

def build_uav_push(level, locations, post):
    title = level_title(level)

    territory = (
        ", ".join(locations)
        if locations
        else "Костромская область"
    )

    return (
        f"<b>{title}</b>\n\n"
        f"Территория: {html.escape(territory)}\n\n"
        f"{source_line(post)}\n\n"
        "Информация собрана из публичных сообщений. "
        "Проверяйте официальные оповещения и сообщения экстренных служб."
    )


def build_uav_cancel_push(post):
    return (
        "<b>🟢 Отбой по опасности БПЛА</b>\n\n"
        "В обнаруженных сообщениях опубликована информация об отбое. "
        "Сверяйтесь с официальными оповещениями региона.\n\n"
        f"{source_line(post)}"
    )


def build_rocket_push(active, post):
    if active:
        title = "🟥 Ракетная опасность"
        note = "Следуйте официальным инструкциям экстренных служб."
    else:
        title = "🟢 Отбой ракетной опасности"
        note = "Сверяйтесь с официальными оповещениями региона."

    return f"<b>{title}</b>\n\n{note}\n\n{source_line(post)}"


# ============================================================
# PUSH DELIVERY
# ============================================================

async def send_push(
    application,
    message,
    event_type,
    level=0,
    cycle=0,
    post=None,
):
    post = post or {}

    key = notification_key(event_type, level, cycle, post)

    # Отмечаем как отправленное только при успешной доставке
    # хотя бы одному подписчику.
    previous = notification_events.get(key, {})

    if previous.get("sent"):
        logger.info(
            "Уведомление %s уже было успешно отправлено.",
            event_type,
        )
        return int(previous.get("sent_count", 0))

    if not subscribers:
        logger.warning(
            "Push %s не отправлен: нет подписчиков.",
            event_type,
        )

        notification_events[key] = {
            "sent": False,
            "sent_count": 0,
            "attempted_at": now_msk().isoformat(timespec="seconds"),
            "event": event_type,
            "post": post.get("key"),
            "error": "Нет подписчиков",
        }

        save_notification_events()
        return 0

    sent_count = 0
    dead_users = []
    errors = []

    for user_id in list(subscribers):
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            sent_count += 1

        except RetryAfter as exc:
            delay = getattr(exc, "retry_after", 2)

            try:
                await asyncio.sleep(float(delay))

                await application.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                sent_count += 1

            except Forbidden:
                dead_users.append(user_id)

            except TelegramError as retry_exc:
                errors.append(str(retry_exc))
                logger.exception(
                    "Повторная отправка push пользователю %s не удалась",
                    user_id,
                )

        except Forbidden:
            dead_users.append(user_id)

        except (BadRequest, TelegramError) as exc:
            errors.append(str(exc))
            logger.exception(
                "Не удалось отправить push пользователю %s",
                user_id,
            )

    for user_id in dead_users:
        subscribers.discard(user_id)

    if dead_users:
        save_subscribers()

    notification_events[key] = {
        "sent": sent_count > 0,
        "sent_count": sent_count,
        "attempted_at": now_msk().isoformat(timespec="seconds"),
        "event": event_type,
        "post": post.get("key"),
        "error": "; ".join(errors[:5]) if errors else None,
    }

    save_notification_events()

    logger.info(
        "Push %s: доставлено %s; ошибок %s; подписчиков %s",
        event_type,
        sent_count,
        len(errors),
        len(subscribers),
    )

    return sent_count


# ============================================================
# APPLY UAV STATUS
# ============================================================

async def apply_uav_event(
    application,
    new_level,
    locations,
    post,
    force_push=False,
):
    old_level = int(state.get("uav_level", 0))
    old_locations = list(state.get("active_locations") or [])

    # Отбой.
    if new_level <= 0:
        if old_level <= 0 and not force_push:
            logger.info("Отбой обнаружен, активного статуса нет.")
            return False

        state["uav_level"] = 0
        state["active_locations"] = []
        state["uav_cycle"] = int(state.get("uav_cycle", 0)) + 1
        state["last_uav_time"] = now_msk().isoformat(timespec="seconds")
        state["last_uav_source"] = (
            post.get("source_name") if post else None
        )
        state["last_uav_post"] = post.get("url") if post else None

        save_state()

        add_history("uav_cancel", "Отбой по опасности БПЛА", post)

        await send_push(
            application,
            build_uav_cancel_push(post),
            "uav_cancel",
            0,
            int(state["uav_cycle"]),
            post,
        )

        return True

    locations = locations or ["Костромская область"]

    # Не отправляем повторный push, если уровень и территория
    # не изменились. При изменении уровня обновление обязательно.
    if (
        old_level == new_level
        and set(old_locations) == set(locations)
    ):
        logger.info(
            "Статус БПЛА не изменился: уровень %s, территория %s",
            new_level,
            locations,
        )
        return False

    new_cycle = old_level == 0

    state["uav_level"] = new_level
    state["active_locations"] = locations

    if new_cycle:
        state["uav_cycle"] = int(state.get("uav_cycle", 0)) + 1

    state["last_uav_time"] = now_msk().isoformat(timespec="seconds")
    state["last_uav_source"] = (
        post.get("source_name") if post else None
    )
    state["last_uav_post"] = post.get("url") if post else None

    save_state()

    event_type = "uav_active" if new_cycle else "uav_update"

    add_history(event_type, level_title(new_level), post)

    await send_push(
        application,
        build_uav_push(new_level, locations, post),
        event_type,
        new_level,
        int(state["uav_cycle"]),
        post,
    )

    return True


# ============================================================
# APPLY ROCKET STATUS
# ============================================================

async def apply_rocket_event(application, active, post):
    old_active = bool(state.get("rocket_active", False))

    if old_active == active:
        logger.info("Ракетный статус не изменился.")
        return False

    state["rocket_active"] = active

    if active:
        state["rocket_cycle"] = int(state.get("rocket_cycle", 0)) + 1

    state["last_rocket_time"] = now_msk().isoformat(timespec="seconds")
    state["last_rocket_source"] = (
        post.get("source_name") if post else None
    )
    state["last_rocket_post"] = post.get("url") if post else None

    save_state()

    event_type = "rocket_active" if active else "rocket_cancel"
    title = (
        "Ракетная опасность"
        if active
        else "Отбой ракетной опасности"
    )

    add_history(event_type, title, post)

    await send_push(
        application,
        build_rocket_push(active, post),
        event_type,
        1 if active else 0,
        int(state["rocket_cycle"]),
        post,
    )

    return True


# ============================================================
# CLASSIFY PUBLICATION
# ============================================================

def get_post_event(post, inherited_locations=None):
    text = post.get("text", "")
    locations = find_locations(text)

    uav_event = "none"
    uav_level = 0

    uav_related = contains_uav(text)

    # Если есть явная формулировка про БПЛА и отмену,
    # можно распознать её даже без полного совпадения фразы.
    uav_cancel_text = (
        detect_cancel(text)
        and (
            uav_related
            or bool(re.search(
                r"\b(бпла|беспилот\w*|дрон\w*|мрш|мрлс)\b",
                normalize_text(text),
            ))
        )
    )

    if uav_related:
        if locations:
            if detect_cancel(text):
                uav_event = "cancel"
            else:
                uav_level = detect_uav_level(text)

                if uav_level > 0:
                    uav_event = "active"

        elif uav_cancel_text:
            if inherited_locations:
                locations = list(inherited_locations)
                uav_event = "cancel"

            else:
                last_source = state.get("last_uav_source")
                current_source_name = post.get("source_name")

                if (
                    int(state.get("uav_level", 0)) > 0
                    and last_source
                    and current_source_name == last_source
                ):
                    locations = list(
                        state.get("active_locations") or []
                    )
                    uav_event = "cancel"

    # Не принимаем общую фразу «Отбой» за отбой БПЛА,
    # если в публикации нет соответствующего контекста.
    rocket_event = "none"

    if detect_rocket_cancel(text):
        rocket_event = "cancel"
    elif detect_rocket(text):
        rocket_event = "active"

    return {
        "locations": locations,
        "uav_event": uav_event,
        "uav_level": uav_level,
        "rocket_event": rocket_event,
    }


# ============================================================
# MONITORING — LAST FIVE HOURS
# ============================================================

monitor_lock = None


async def check_sources(application):
    global monitor_lock

    if monitor_lock is None:
        monitor_lock = asyncio.Lock()

    if monitor_lock.locked():
        logger.warning("Предыдущая проверка ещё выполняется.")
        return

    async with monitor_lock:
        collected = []

        for source_key, source_info in SOURCES.items():
            try:
                posts = await asyncio.to_thread(
                    fetch_source,
                    source_key,
                    source_info,
                )

                for post in posts:
                    published = parse_datetime(post.get("date"))

                    if published is None:
                        continue

                    age = now_msk() - published

                    if (
                        timedelta(0)
                        <= age
                        <= timedelta(hours=HISTORY_WINDOW_HOURS)
                    ):
                        collected.append(post)

            except requests.RequestException:
                logger.exception(
                    "Ошибка запроса к источнику %s",
                    source_key,
                )

            except Exception:
                logger.exception(
                    "Ошибка обработки источника %s",
                    source_key,
                )

        collected.sort(
            key=lambda post: (
                parse_datetime(post.get("date"))
                or datetime.min.replace(tzinfo=MSK),
                int(post.get("post_id", 0)),
                post.get("source", ""),
            )
        )

        state["last_scan_time"] = now_msk().isoformat(timespec="seconds")
        state["last_scan_count"] = len(collected)
        save_state()

        logger.info(
            "Найдено публикаций за последние %s ч.: %s",
            HISTORY_WINDOW_HOURS,
            len(collected),
        )

        # Контекст региона отдельно для каждого источника.
        source_locations = {}

        latest_uav = None
        latest_rocket = None

        for post in collected:
            source_key = post.get("source", "")

            event = get_post_event(
                post,
                inherited_locations=source_locations.get(source_key),
            )

            if event["locations"]:
                source_locations[source_key] = list(event["locations"])

            if event["uav_event"] != "none":
                latest_uav = (post, event)

            if event["rocket_event"] != "none":
                latest_rocket = (post, event)

        # Применяем только последнее найденное событие БПЛА
        # в окне анализа, чтобы старое сообщение не перебивало новое.
        if latest_uav:
            post, event = latest_uav

            if event["uav_event"] == "cancel":
                if int(state.get("uav_level", 0)) > 0:
                    await apply_uav_event(
                        application,
                        0,
                        event["locations"],
                        post,
                    )
                else:
                    logger.info(
                        "Последнее событие БПЛА — отбой, "
                        "активного статуса нет."
                    )

            elif event["uav_event"] == "active":
                await apply_uav_event(
                    application,
                    int(event["uav_level"]),
                    event["locations"],
                    post,
                )

        if latest_rocket:
            post, event = latest_rocket

            await apply_rocket_event(
                application,
                event["rocket_event"] == "active",
                post,
            )

        for post in collected:
            mark_post_processed(post)

        save_sent_posts()

        for post in collected:
            key = post["source"]
            previous = source_state.get(key, {})

            try:
                previous_id = int(previous.get("last_post_id", 0))
            except (ValueError, TypeError):
                previous_id = 0

            source_state[key] = {
                "last_post_id": max(
                    previous_id,
                    int(post.get("post_id", 0)),
                ),
                "last_scan_time": now_msk().isoformat(
                    timespec="seconds"
                ),
            }

        save_source_state()

        state["startup_completed"] = True
        save_state()


async def monitor_loop(application):
    logger.info(
        "Мониторинг запущен. Интервал: %s сек.; окно: %s часов.",
        CHECK_INTERVAL,
        HISTORY_WINDOW_HOURS,
    )

    while True:
        try:
            await check_sources(application)

        except asyncio.CancelledError:
            logger.info("Мониторинг остановлен.")
            raise

        except Exception:
            logger.exception("Ошибка цикла мониторинга.")

        await asyncio.sleep(CHECK_INTERVAL)


# ============================================================
# TELEGRAM KEYBOARD
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Подписаться",
                callback_data="subscribe",
            ),
            InlineKeyboardButton(
                "🔕 Отписаться",
                callback_data="unsubscribe",
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status",
            ),
            InlineKeyboardButton(
                "📚 История",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "📡 Источники",
                callback_data="sources",
            ),
            InlineKeyboardButton(
                "ℹ️ Помощь",
                callback_data="help",
            ),
        ],
    ])


# ============================================================
# STATUS, HISTORY, SOURCES, STATS
# ============================================================

def format_status():
    level = int(state.get("uav_level", 0))
    locations = state.get("active_locations") or []

    territory = (
        ", ".join(locations)
        if locations
        else "Костромская область"
    )

    rocket = (
        "🟥 Активна"
        if state.get("rocket_active")
        else "🟢 Не объявлена"
    )

    return (
        "<b>UAV ALERT — текущий статус</b>\n\n"
        f"{html.escape(level_title(level))}\n"
        f"Территория: {html.escape(territory)}\n"
        f"Последнее изменение: "
        f"{format_time(state.get('last_uav_time'))}\n\n"
        f"<b>Ракетная опасность:</b> {rocket}\n"
        f"Последняя проверка: "
        f"{format_time(state.get('last_scan_time'))}\n"
        f"Публикаций в окне: "
        f"{int(state.get('last_scan_count', 0))}\n\n"
        "<i>Автоматический мониторинг публичных сообщений. "
        "Не заменяет официальные оповещения.</i>"
    )


def format_history(limit=10):
    if not history:
        return "<b>📚 История событий</b>\n\nСобытий пока нет."

    lines = ["<b>📚 Последние события UAV ALERT</b>", ""]

    for item in reversed(history[-limit:]):
        title = html.escape(
            str(item.get("title", item.get("event", "Событие")))
        )

        lines.append(f"• <b>{title}</b>")
        lines.append(f"  {format_time(item.get('time'))}")

        if item.get("source"):
            lines.append(
                f"  Источник: {html.escape(str(item['source']))}"
            )

        if item.get("url"):
            url = html.escape(str(item["url"]), quote=True)
            lines.append(f'  <a href="{url}">Открыть публикацию</a>')

        lines.append("")

    return "\n".join(lines)


def format_sources():
    lines = ["<b>📡 Источники мониторинга</b>", ""]

    for info in SOURCES.values():
        name = html.escape(info["name"])
        url = html.escape(info["url"], quote=True)
        lines.append(f'• <a href="{url}">{name}</a>')

    lines.extend([
        "",
        "Публичные страницы могут показывать не все публикации. "
        "Сообщения могут быть неполными или неофициальными.",
    ])

    return "\n".join(lines)


def format_stats():
    return (
        "<b>📊 Статистика UAV ALERT</b>\n\n"
        f"Подписчиков: {len(subscribers)}\n"
        f"Записей истории: {len(history)}\n"
        f"Обработанных публикаций: {len(sent_posts)}\n"
        f"Событий уведомлений: {len(notification_events)}\n"
        f"Окно анализа: {HISTORY_WINDOW_HOURS} ч.\n"
        f"Интервал проверки: {CHECK_INTERVAL} сек."
    )


# ============================================================
# COMMAND HANDLERS
# ============================================================

def is_admin(update):
    user = update.effective_user
    return bool(user and user.id == ADMIN_ID)


async def start_command(update, context):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "🚨 <b>UAV ALERT</b>\n"
        "Гражданский информационный сервис мониторинга публичных "
        "сообщений о БПЛА в Костромской области.\n\n"
        "Нажми «Подписаться», чтобы получать уведомления.\n"
        "Бот не заменяет официальные сообщения экстренных служб.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def subscribe_command(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    subscribers.add(user.id)
    save_subscribers()

    await message.reply_text(
        "🔔 Подписка включена. Ты будешь получать уведомления UAV ALERT.",
        reply_markup=main_keyboard(),
    )


async def unsubscribe_command(update, context):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    subscribers.discard(user.id)
    save_subscribers()

    await message.reply_text(
        "🔕 Подписка отключена.",
        reply_markup=main_keyboard(),
    )


async def status_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(
            format_status(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


async def history_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(
            format_history(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


async def sources_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(
            format_sources(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


async def stats_command(update, context):
    if update.effective_message:
        await update.effective_message.reply_text(
            format_stats(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )


async def test_command(update, context):
    if not update.effective_message:
        return

    if not is_admin(update):
        await update.effective_message.reply_text(
            "Команда доступна только администратору."
        )
        return

    await update.effective_message.reply_text(
        "✅ Тестовая проверка Telegram-бота прошла успешно.",
        reply_markup=main_keyboard(),
    )


async def admin_command(update, context):
    if not update.effective_message:
        return

    if not is_admin(update):
        await update.effective_message.reply_text(
            "Команда доступна только администратору."
        )
        return

    await update.effective_message.reply_text(
        "<b>Админ-панель UAV ALERT</b>\n\n"
        f"Подписчиков: {len(subscribers)}\n"
        f"Статус БПЛА: "
        f"{html.escape(level_title(int(state.get('uav_level', 0))))}\n"
        f"Ракетная опасность: "
        f"{'активна' if state.get('rocket_active') else 'не объявлена'}\n"
        f"Последняя проверка: "
        f"{format_time(state.get('last_scan_time'))}",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def help_command(update, context):
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        "<b>Команды UAV ALERT</b>\n\n"
        "/start — главное меню\n"
        "/subscribe — подписаться\n"
        "/unsubscribe — отписаться\n"
        "/status — текущий статус\n"
        "/history — история событий\n"
        "/sources — источники\n"
        "/stats — статистика\n"
        "/test — тест (администратор)\n"
        "/admin — админ-информация\n"
        "/help — помощь\n\n"
        "Автоматические сообщения основаны на публичных источниках "
        "и могут требовать проверки по официальным каналам.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# INLINE BUTTON CALLBACKS
# ============================================================

async def callback_handler(update, context):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user
    data = query.data or ""

    if data == "subscribe":
        subscribers.add(user.id)
        save_subscribers()
        text = "🔔 Подписка включена."

    elif data == "unsubscribe":
        subscribers.discard(user.id)
        save_subscribers()
        text = "🔕 Подписка отключена."

    elif data == "status":
        text = format_status()

    elif data == "history":
        text = format_history()

    elif data == "sources":
        text = format_sources()

    elif data == "help":
        text = (
            "<b>UAV ALERT</b>\n\n"
            "Используй кнопки меню или команды "
            "/status, /history, /sources, /subscribe и /unsubscribe.\n"
            "Бот не заменяет официальные оповещения."
        )

    else:
        text = "Неизвестная команда."

    try:
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )

    except BadRequest as exc:
        if "Message is not modified" not in str(exc):
            logger.warning("Не удалось обновить меню: %s", exc)


# ============================================================
# FLASK HEALTH SERVER — RENDER
# ============================================================

app = Flask(__name__)


@app.get("/")
def index():
    return jsonify({
        "service": "UAV ALERT",
        "status": "running",
        "monitor_interval_seconds": CHECK_INTERVAL,
        "history_window_hours": HISTORY_WINDOW_HOURS,
        "subscribers": len(subscribers),
        "last_scan_time": state.get("last_scan_time"),
    }), 200


@app.get("/status")
def health_status():
    return jsonify({
        "status": "ok",
        "uav_level": state.get("uav_level", 0),
        "rocket_active": state.get("rocket_active", False),
        "last_scan_time": state.get("last_scan_time"),
        "last_scan_count": state.get("last_scan_count", 0),
    }), 200


def run_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False,
    )


# ============================================================
# APPLICATION LIFECYCLE
# ============================================================

async def post_init(application):
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Текущий статус"),
        BotCommand("history", "История событий"),
        BotCommand("sources", "Источники"),
        BotCommand("stats", "Статистика"),
        BotCommand("test", "Тест уведомления"),
        BotCommand("admin", "Админ-информация"),
        BotCommand("help", "Помощь"),
    ]

    try:
        await application.bot.set_my_commands(commands)

    except TelegramError:
        logger.exception("Не удалось установить команды Telegram.")

    application.create_task(
        monitor_loop(application),
        name="uav-alert-monitor",
    )

    logger.info("UAV ALERT готов к работе.")


async def post_shutdown(application):
    logger.info("UAV ALERT завершает работу.")


# ============================================================
# MAIN
# ============================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. "
            "Добавь токен бота в Environment на Render."
        )

    flask_thread = threading.Thread(
        target=run_flask,
        name="uav-alert-flask",
        daemon=True,
    )
    flask_thread.start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("Запускаем Telegram polling.")

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
