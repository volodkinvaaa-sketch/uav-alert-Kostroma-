import os
import re
import json
import asyncio
import hashlib
import logging
import threading
import html
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

"""
UAV ALERT — гражданский информационный бот.

Возможности:
- мониторинг публичных Telegram-лент;
- фильтры БПЛА, МВШ и ракетной опасности/отбоя;
- немедленная отправка готового поста администратору без модерации;
- время исходной публикации в МСК;
- хранение состояния опасности с TTL 5 часов;
- TTL не создаёт автоматический отбой: он лишь помечает состояние устаревшим;
- подписки, погода, диагностика и HTTP health endpoint.

Источники в SOURCES — сторонние каналы, а не подтверждённые официальные
государственные источники. Перед распространением проверяйте официальные
оповещения.
"""

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip()
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL", "5")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
ALERT_TTL_HOURS = max(1, int(os.getenv("ALERT_TTL_HOURS", "5")))
ALERT_TTL = timedelta(hours=ALERT_TTL_HOURS)
DATA_DIR = os.getenv("DATA_DIR", ".")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MSK = timezone(timedelta(hours=3))

DANGER_IMAGE = os.path.join(BASE_DIR, "danger.jpg")
ALL_CLEAR_IMAGE = os.path.join(BASE_DIR, "all_clear.jpg")

SOURCES = [
    ("locatorru", "https://t.me/s/locatorru"),
    ("radarrussiia", "https://t.me/s/radarrussiia"),
    ("bplarussiaru", "https://t.me/s/bplarussiaru"),
]

OFFICIAL_SOURCES = [
    ("МЧС по Костромской области", "https://44.mchs.gov.ru/"),
    ("Администрация Костромской области", "https://adm44.ru/"),
]

TERRITORY_TERMS = [
    "костром", "нерехт", "бую", "буй", "волгоречен", "галич", "шарь",
    "манту", "чухлом", "макарьев", "солигалич", "кологрив", "нея",
    "антропов", "боговаров", "кадый", "красное-на-волге", "красносель",
    "межев", "октябрьск", "островск", "павин", "парфеньев", "поназырев",
    "пышуг", "сусанин", "судислав", "вохом", "варнав", "ветлуг"
]

REGION_RE = re.compile(
    r"костром\w*\s+(?:област|регион)", re.I
)

# БПЛА
UAV_ALERT_RE = re.compile(
    r"беспилотн\w*\s+опасност|опасност\w*\s+по\s+бпла|"
    r"угроз\w*\s+беспилот|угроз\w*\s+бпла|"
    r"внимани\w*.{0,30}бпла|тревог\w*.{0,30}(?:бпла|беспилот)|"
    r"бпла.{0,40}(?:внимани|угроз|опасност|тревог)|"
    r"опасност\w*.{0,30}(?:беспилот|бпла)|"
    r"беспилотн\w*.{0,30}(?:угроз|опасност|внимани)",
    re.I | re.S
)

UAV_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}(?:беспилотн|бпла|опасност|угроз)|"
    r"(?:беспилотн|бпла|опасност|угроз).{0,70}"
    r"(?:отбой|отмен[аы]|снят[аоы]?)",
    re.I | re.S
)

UAV_DANGER_RE = re.compile(
    r"(?:опасност\w*\s+по\s+бпла|беспилотн\w*\s+опасност|красн\w*\s+уров)",
    re.I
)

UAV_THREAT_RE = re.compile(
    r"(?:угроз\w*\s+(?:беспилот|бпла)|бпла.{0,30}угроз)",
    re.I | re.S
)

UAV_ATTENTION_RE = re.compile(
    r"(?:внимани\w*.{0,30}бпла|бпла.{0,30}внимани)",
    re.I | re.S
)

# МВШ
MWSH_DANGER_RE = re.compile(
    r"(?:опасност\w*.{0,80}\bмвш\b|"
    r"\bмвш\b.{0,80}(?:опасност|угроз)|"
    r"опасност\w*.{0,100}малоразмерн\w*.{0,50}шар|"
    r"малоразмерн\w*.{0,50}шар\w*.{0,80}(?:опасност|угроз))",
    re.I | re.S
)

MWSH_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,100}(?:\bмвш\b|малоразмерн\w*.{0,50}шар)|"
    r"(?:\bмвш\b|малоразмерн\w*.{0,50}шар)"
    r".{0,100}(?:отбой|отмен[аы]|снят[аоы]?)",
    re.I | re.S
)

# Ракетная опасность
ROCKET_RE = re.compile(
    r"ракетн\w*.{0,50}(?:опасност|угроз|тревог|объявлен)|"
    r"(?:опасност|угроз|тревог).{0,50}ракетн\w*",
    re.I | re.S
)

ROCKET_CANCEL_RE = re.compile(
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)"
    r".{0,70}ракетн\w*|ракетн\w*.{0,70}"
    r"(?:отбой|отмен[аы]|снят[аоы]?|прекращен[аоы]?)",
    re.I | re.S
)

ROCKET_NEGATION_RE = re.compile(
    r"ракетн\w*.{0,50}(?:не\s+(?:объявлен\w*|введен\w*|подтвержден\w*)|"
    r"отсутствует|нет)|"
    r"(?:нет|не\s+(?:объявлен\w*|введен\w*|подтвержден\w*)).{0,50}ракетн\w*",
    re.I | re.S
)

TERRITORY_ALIASES = {
    "г. Кострома": [
        "город кострома", "г. кострома", "кострома"
    ],
    "г. Буй": ["город буй", "г. буй"],
    "г. Волгореченск": [
        "город волгореченск", "г. волгореченск", "волгореченск"
    ],
    "г. Галич": ["город галич", "г. галич"],
    "г. Кологрив": ["город кологрив", "г. кологрив"],
    "г. Макарьев": ["город макарьев", "г. макарьев"],
    "г. Мантурово": [
        "город мантурово", "г. мантурово", "мантурово"
    ],
    "г. Нея": ["город нея", "г. нея", "нея"],
    "г. Нерехта": ["город нерехта", "г. нерехта", "нерехта"],
    "г. Солигалич": ["город солигалич", "г. солигалич"],
    "г. Шарья": ["город шарья", "г. шарья", "шарья"],
    "Антроповский округ": [
        "антроповский район", "антроповский округ"
    ],
    "Боговаровский округ": [
        "боговаровский район", "боговаровский округ", "боговаров"
    ],
    "Варнавинский округ": [
        "варнавинский район", "варнавинский округ", "варнав"
    ],
    "Вохомский округ": ["вохомский район", "вохомский округ"],
    "Галичский район": ["галичский район", "галичский округ"],
    "Кадыйский округ": ["кадыйский район", "кадыйский округ"],
    "Кологривский округ": [
        "кологривский район", "кологривский округ"
    ],
    "Костромской район": [
        "костромской район", "костромской округ"
    ],
    "Красносельский район": [
        "красносельский район", "красносельский округ"
    ],
    "Макарьевский округ": [
        "макарьевский район", "макарьевский округ"
    ],
    "Мантуровский округ": [
        "мантуровский район", "мантуровский округ"
    ],
    "Межевской округ": ["межевской район", "межевской округ"],
    "Нейский округ": ["нейский район", "нейский округ"],
    "Нерехтский район": [
        "нерехтский район", "нерехтский округ"
    ],
    "Октябрьский округ": [
        "октябрьский район", "октябрьский округ"
    ],
    "Островский район": [
        "островский район", "островский округ"
    ],
    "Павинский округ": ["павинский район", "павинский округ"],
    "Парфеньевский округ": [
        "парфеньевский район", "парфеньевский округ"
    ],
    "Поназыревский округ": [
        "поназыревский район", "поназыревский округ"
    ],
    "Пыщугский округ": ["пыщугский район", "пыщугский округ"],
    "Солигаличский район": [
        "солигаличский район", "солигаличский округ"
    ],
    "Судиславский округ": [
        "судиславский район", "судиславский округ"
    ],
    "Сусанинский район": [
        "сусанинский район", "сусанинский округ"
    ],
    "Чухломский район": [
        "чухломский район", "чухломский округ"
    ],
    "Шарьинский район": [
        "шарьинский район", "шарьинский округ"
    ],
}

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
KOSTROMA_LAT = 57.7679
KOSTROMA_LON = 40.9269

os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "uav_alert_state.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "uav_alert_subscribers.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("uav-alert")


def now_msk():
    return datetime.now(MSK)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        return default


def save_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


state = load_json(STATE_FILE, {})
if not isinstance(state, dict):
    state = {}

for key, default in {
    "seen": {},
    "subscribers": [],
    "last_scan": None,
    "last_errors": {},
    "initialized": {},
    "pending": {},
    "approved": {},
    "active_alerts": {},
}.items():
    state.setdefault(key, default)

subscribers = set(load_json(
    SUBSCRIBERS_FILE, state.get("subscribers", [])
))
monitor_task = None


def save_state():
    state["subscribers"] = sorted(subscribers)
    save_json(STATE_FILE, state)
    save_json(SUBSCRIBERS_FILE, sorted(subscribers))


def normalize(text):
    return re.sub(
        r"\s+", " ",
        (text or "").casefold().replace("ё", "е")
    ).strip()


def mentions_kostroma(text):
    value = normalize(text)
    if REGION_RE.search(value):
        return True
    return any(term in value for term in TERRITORY_TERMS)


def extract_territories(text):
    value = normalize(text)

    if re.search(
        r"(?:на\s+)?(?:всей\s+)?территории\s+костромской\s+области|"
        r"по\s+всей\s+костромской\s+области|по\s+костромской\s+области|"
        r"во\s+всей\s+костромской\s+области",
        value
    ):
        return ["Костромская область"]

    found = []
    entries = sorted(
        TERRITORY_ALIASES.items(),
        key=lambda item: max(
            (len(alias) for alias in item[1]), default=0
        ),
        reverse=True
    )

    for display_name, aliases in entries:
        for alias in aliases:
            alias_norm = normalize(alias)
            pattern = r"(?<!\w)" + re.escape(alias_norm) + r"(?!\w)"
            if re.search(pattern, value) and display_name not in found:
                found.append(display_name)
                break

    if found:
        return found
    if mentions_kostroma(text):
        return ["Костромская область"]
    return []


def classify_mwsh(text):
    value = normalize(text)

    if MWSH_CANCEL_RE.search(value):
        return "mwsh_cancel", "Отбой опасности по МВШ"
    if MWSH_DANGER_RE.search(value):
        return "mwsh_danger", "Опасность по МВШ"
    return None


def classify_rocket(text):
    value = normalize(text)

    if ROCKET_CANCEL_RE.search(value):
        return "rocket_cancel", "Отбой ракетной опасности"
    if ROCKET_NEGATION_RE.search(value):
        return None
    if ROCKET_RE.search(value):
        return "rocket_danger", "Ракетная опасность"
    return None


def classify_uav(text):
    value = normalize(text)

    if UAV_CANCEL_RE.search(value) or "отбой беспилотной опасности" in value:
        return "cancel", "Отбой опасности по БПЛА"

    if not UAV_ALERT_RE.search(value):
        return None

    if UAV_DANGER_RE.search(value) or "беспилотной опасности" in value:
        return "danger", "Опасность по БПЛА"

    if UAV_THREAT_RE.search(value):
        return "threat", "Угроза по БПЛА"

    if UAV_ATTENTION_RE.search(value):
        return "attention", "Внимание: БПЛА"

    return None


def classify(text):
    return classify_mwsh(text) or classify_rocket(text) or classify_uav(text)


def parse_published_time(node):
    """Извлекает время исходного поста Telegram и переводит в МСК."""
    time_node = node.select_one("a.tgme_widget_message_date time")

    if time_node:
        raw = time_node.get("datetime")
        if raw:
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(MSK)
            except ValueError:
                logger.debug(
                    "Не удалось разобрать время публикации: %r", raw
                )

    return None


def source_posts(source_url):
    response = requests.get(
        source_url,
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; UAV-ALERT/1.0)"
        }
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    posts = []

    for node in soup.select(".tgme_widget_message"):
        text_node = node.select_one(".tgme_widget_message_text")
        if not text_node:
            continue

        text = text_node.get_text(" ", strip=True)
        link_node = node.select_one("a.tgme_widget_message_date")
        url = link_node.get("href", "") if link_node else source_url
        post_id = node.get("data-post", "") or url
        published_at = parse_published_time(node)

        digest = hashlib.sha256(
            (post_id + "|" + text).encode("utf-8")
        ).hexdigest()

        if text:
            posts.append({
                "key": digest,
                "text": text[:5000],
                "url": url,
                "published_at": (
                    published_at.isoformat(timespec="minutes")
                    if published_at else None
                ),
            })

    return posts[-30:]


def format_event_time(published_at):
    if published_at:
        try:
            parsed = datetime.fromisoformat(published_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=MSK)

            return parsed.astimezone(MSK).strftime(
                "%d.%m.%Y %H:%M МСК"
            )
        except (TypeError, ValueError):
            pass

    return "не указано"


def event_message(kind, label, source, url, source_text, published_at=None):
    territories = extract_territories(source_text) or ["Костромская область"]
    territory_text = ", ".join(
        html.escape(name) for name in territories
    )
    stamp = html.escape(format_event_time(published_at))

    if kind == "mwsh_cancel":
        title, category = "🟢 Отбой опасности по МВШ", "МВШ"
    elif kind == "mwsh_danger":
        title, category = "🔴 Опасность по МВШ", "МВШ"
    elif kind == "rocket_cancel":
        title, category = "🟢 Отбой ракетной опасности", "Ракетная опасность"
    elif kind == "rocket_danger":
        title, category = "🔴 Ракетная опасность", "Ракетная опасность"
    elif kind == "cancel":
        title, category = "🟢 Отбой опасности по БПЛА", "БПЛА"
    elif kind == "threat":
        title, category = "🟠 Угроза по БПЛА", "БПЛА"
    elif kind == "attention":
        title, category = "🟡 Внимание: БПЛА", "БПЛА"
    else:
        title, category = "🔴 Опасность по БПЛА", "БПЛА"

    safe_url = html.escape(url, quote=True)
    safe_source = html.escape(source)

    return (
        "<b>🛰 UAV ALERT</b>\n"
        "📍 <b>Регион:</b> Костромская область\n\n"
        f"<b>{title}</b>\n"
        f"Категория: {category}\n"
        f"Время исходного сообщения: {stamp}\n\n"
        f"📍 <b>Территории:</b> {territory_text}\n\n"
        f'📡 <b>Источник:</b> <a href="{safe_url}">{safe_source}</a>\n\n'
        "⚠️ Информация получена из стороннего источника. "
        "Сверяйте её с актуальными официальными оповещениями МЧС "
        "и органов власти."
    )


def moderation_keyboard(pending_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Опубликовать", callback_data=f"approve:{pending_id}"
        ),
        InlineKeyboardButton(
            "❌ Отклонить", callback_data=f"reject:{pending_id}"
        )
    ]])


async def send_text_or_photo(bot, chat_id, message, is_cancel=False):
    image_path = ALL_CLEAR_IMAGE if is_cancel else DANGER_IMAGE

    # Telegram ограничивает подпись к фотографии 1024 символами.
    if os.path.isfile(image_path) and len(message) <= 1024:
        with open(image_path, "rb") as photo:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=message,
                parse_mode=ParseMode.HTML,
                disable_notification=False
            )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


def event_family(kind):
    if kind.startswith("mwsh_"):
        return "mwsh"
    if kind.startswith("rocket_"):
        return "rocket"
    return "uav"


def event_is_cancel(kind):
    return kind in ("cancel", "rocket_cancel", "mwsh_cancel")


def event_is_danger(kind):
    return kind in (
        "danger", "threat", "attention",
        "mwsh_danger", "rocket_danger"
    )


def record_approved_event(item):
    """Обновляет состояние после отправки найденного события."""
    active = state.setdefault("active_alerts", {})
    family = event_family(item["kind"])

    if event_is_cancel(item["kind"]):
        for key in list(active):
            if event_family(key) == family:
                active.pop(key, None)
        return

    if event_is_danger(item["kind"]):
        published_at = item.get("published_at")
        event_time = now_msk()

        if published_at:
            try:
                parsed = datetime.fromisoformat(published_at)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=MSK)
                event_time = parsed.astimezone(MSK)
            except (TypeError, ValueError):
                pass

        active[item["kind"]] = {
            "label": item["label"],
            "published_at": event_time.isoformat(timespec="seconds"),
            "expires_at": (
                event_time + ALERT_TTL
            ).isoformat(timespec="seconds"),
            "source": item["source"],
            "url": item["url"],
            "stale": False,
        }


async def send_event(
    application, kind, label, source, url, text, published_at=None
):
    """
    Немедленно отправляет готовый пост администратору.
    Кнопки подтверждения не используются.
    Повторные события с тем же идентификатором не отправляются повторно.
    """
    pending_id = hashlib.sha256(
        f"{url}|{text}|{published_at or ''}".encode("utf-8")
    ).hexdigest()[:16]

    approved = state.setdefault("approved", {})
    pending = state.setdefault("pending", {})

    if pending_id in approved:
        return

    # Совместимость с ранее сохранёнными событиями на модерации:
    # не отправляем дубликат, если запись уже есть в pending.
    if pending_id in pending:
        return

    message = event_message(
        kind, label, source, url, text, published_at
    )

    item = {
        "kind": kind,
        "label": label,
        "source": source,
        "url": url,
        "text": text[:5000],
        "published_at": published_at,
        "message": message,
        "created_at": now_msk().isoformat(timespec="seconds"),
    }

    try:
        # Отправляем админу именно готовый пост, а не запрос на модерацию.
        await send_text_or_photo(
            application.bot,
            ADMIN_ID,
            message,
            is_cancel=event_is_cancel(kind)
        )

        # Фиксируем событие только после успешной отправки.
        approved[pending_id] = now_msk().isoformat(timespec="seconds")
        record_approved_event(item)
        save_state()

        logger.info(
            "Готовый пост отправлен администратору: id=%s, source=%s",
            pending_id,
            source
        )

    except TelegramError:
        logger.exception(
            "Не удалось отправить готовый пост администратору"
        )


async def monitor_source(application, name, url):
    posts = await asyncio.to_thread(source_posts, url)

    seen_map = state.setdefault("seen", {})
    source_seen = set(seen_map.get(url, []))
    initialized = bool(
        state.setdefault("initialized", {}).get(url, False)
    )

    # При первом запуске фиксируем текущие посты, чтобы не отправлять архив.
    if not initialized:
        seen_map[url] = [p["key"] for p in posts][-300:]
        state["initialized"][url] = True
        state["last_errors"].pop(url, None)
        save_state()

        logger.info(
            "Источник %s инициализирован (%s постов)",
            name, len(posts)
        )
        return

    # Идём от старых сообщений к новым, сохраняя порядок обнаружения.
    for post in posts:
        if post["key"] in source_seen:
            continue

        source_seen.add(post["key"])
        text = post["text"]

        if not mentions_kostroma(text):
            continue

        result = classify(text)
        if not result:
            continue

        kind, label = result

        await send_event(
            application,
            kind,
            label,
            name,
            post["url"],
            text,
            post.get("published_at")
        )

    # Сохраняем последние ключи, не полагаясь на порядок множества.
    seen_map[url] = list(source_seen)[-300:]
    state["last_errors"].pop(url, None)
    save_state()


def expire_active_alerts():
    """Помечает истёкшие состояния устаревшими; отбой не публикуется."""
    active = state.setdefault("active_alerts", {})
    now = now_msk()
    changed = False

    for key, item in list(active.items()):
        try:
            expires = datetime.fromisoformat(item["expires_at"])

            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=MSK)

            if (
                now >= expires.astimezone(MSK)
                and not item.get("stale", False)
            ):
                item["stale"] = True
                item["stale_marked_at"] = now.isoformat(
                    timespec="seconds"
                )
                changed = True

        except (KeyError, TypeError, ValueError):
            item["stale"] = True
            changed = True

    if changed:
        save_state()


async def monitor_loop(application):
    logger.info(
        "Мониторинг запущен; интервал=%s сек.; TTL=%s ч.",
        CHECK_INTERVAL,
        ALERT_TTL_HOURS
    )

    while True:
        for name, url in SOURCES:
            try:
                await monitor_source(application, name, url)

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                state.setdefault("last_errors", {})[url] = str(exc)[:250]
                logger.warning(
                    "Источник %s недоступен: %s", name, exc
                )

        expire_active_alerts()
        state["last_scan"] = now_msk().isoformat(timespec="seconds")
        save_state()

        await asyncio.sleep(CHECK_INTERVAL)


async def regional_alert_status():
    expire_active_alerts()

    lines = [
        "🛰 <b>UAV ALERT — Костромская область</b>",
        f"Срок актуальности записи: {ALERT_TTL_HOURS} ч.",
        ""
    ]

    active = state.get("active_alerts", {})
    families = [
        ("БПЛА", {"danger", "threat", "attention"}),
        ("МВШ", {"mwsh_danger"}),
        ("Ракетная опасность", {"rocket_danger"}),
    ]

    for label, keys in families:
        entries = [
            (key, item)
            for key, item in active.items()
            if key in keys
        ]

        if not entries:
            lines.append(
                f"{label}: подтверждённое активное состояние не установлено."
            )
            continue

        key, item = max(
            entries,
            key=lambda pair: pair[1].get("published_at", "")
        )

        if item.get("stale"):
            lines.append(
                f"{label}: <b>запись устарела</b> — срок хранения истёк; "
                "текущий статус не установлен."
            )
        else:
            url = html.escape(item.get("url", ""), quote=True)
            source = html.escape(item.get("source", "источник"))

            lines.append(
                f'{label}: <b>{html.escape(item.get("label", "событие"))}</b>. '
                f'<a href="{url}">Источник: {source}</a>'
            )

    lines.append(
        "\n⚠️ Это автоматическая сводка по выбранным Telegram-источникам. "
        "Истечение срока хранения не означает отбоя. Проверяйте официальные "
        "оповещения МЧС и органов власти."
    )

    return "\n".join(lines)


def keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Подписаться", callback_data="subscribe"
            ),
            InlineKeyboardButton(
                "🔕 Отписаться", callback_data="unsubscribe"
            ),
        ],
        [
            InlineKeyboardButton(
                "📡 Источники", callback_data="sources"
            ),
            InlineKeyboardButton(
                "ℹ️ Статус", callback_data="status"
            ),
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "🛰 UAV ALERT — информационный сервис Костромской области.\n\n"
        "Бот проверяет публичные сообщения выбранных Telegram-каналов. "
        "Найденные подходящие события автоматически отправляются "
        "администратору в виде готового поста без модерации.\n\n"
        f"Интервал проверки: {CHECK_INTERVAL} сек.\n"
        f"Срок актуальности записи: {ALERT_TTL_HOURS} ч. "
        "Истечение срока не означает отбоя.\n\n"
        "Команды: /weather, /status, /sources, /health.",
        reply_markup=keyboard()
    )

    try:
        await message.reply_text(
            await regional_alert_status(),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except Exception:
        logger.exception("Ошибка формирования статуса при /start")


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    subscribers.add(user.id)
    save_state()

    await message.reply_text(
        "🔔 Подписка включена.",
        reply_markup=keyboard()
    )


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return

    subscribers.discard(user.id)
    save_state()

    await message.reply_text(
        "🔕 Подписка отключена.",
        reply_markup=keyboard()
    )


def sources_text():
    lines = [
        "📡 Источники мониторинга",
        "",
        *[
            f"• {name}: https://t.me/{name}"
            for name, _ in SOURCES
        ],
        "",
        "Указанные Telegram-каналы — сторонние источники, "
        "не подтверждённые как официальные государственные каналы.",
        "Официальные ресурсы для сверки:",
        *[
            f"• {name}: {url}"
            for name, url in OFFICIAL_SOURCES
        ],
    ]

    return "\n".join(lines)


def status_text():
    errors = state.get("last_errors", {})

    lines = [
        "🩺 UAV ALERT — диагностика",
        f"Последний цикл: {state.get('last_scan') or 'ещё не завершён'}",
        f"Интервал проверки: {CHECK_INTERVAL} сек.",
        f"Срок актуальности: {ALERT_TTL_HOURS} ч.",
        f"Подписчиков: {len(subscribers)}",
        f"Источников: {len(SOURCES)}",
        "Режим: автоматическая отправка админу без модерации",
        f"Ожидают модерации (старые записи): {len(state.get('pending', {}))}",
        f"Записей активных событий: {len(state.get('active_alerts', {}))}",
        "",
    ]

    for name, url in SOURCES:
        error = errors.get(url)

        lines.append(
            f"• {name}: "
            f"{'ошибка — ' + error if error else 'нет последней ошибки'}"
        )

    return "\n".join(lines)


def weather_code_text(code):
    mapping = {
        0: "ясно",
        1: "преимущественно ясно",
        2: "переменная облачность",
        3: "пасмурно",
        45: "туман",
        48: "изморозевый туман",
        51: "слабая морось",
        53: "морось",
        55: "сильная морось",
        56: "слабая ледяная морось",
        57: "сильная ледяная морось",
        61: "небольшой дождь",
        63: "дождь",
        65: "сильный дождь",
        66: "слабый ледяной дождь",
        67: "сильный ледяной дождь",
        71: "небольшой снег",
        73: "снег",
        75: "сильный снег",
        77: "снежные зёрна",
        80: "ливневый дождь",
        81: "ливень",
        82: "сильный ливень",
        85: "снегопад",
        86: "сильный снегопад",
        95: "гроза",
        96: "гроза с небольшим градом",
        99: "гроза с градом",
    }

    return mapping.get(code, "условия не определены")


async def get_weather_text():
    params = {
        "latitude": KOSTROMA_LAT,
        "longitude": KOSTROMA_LON,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max"
        ),
        "forecast_days": 2,
        "timezone": "Europe/Moscow",
    }

    response = await asyncio.to_thread(
        lambda: requests.get(
            WEATHER_URL,
            params=params,
            timeout=REQUEST_TIMEOUT
        )
    )

    response.raise_for_status()

    data = response.json()
    current, daily = data["current"], data["daily"]
    today, tomorrow = daily["time"][0], daily["time"][1]

    return (
        "🌦 <b>Погода — Кострома</b>\n"
        f"Сейчас: {current['temperature_2m']:+.1f} °C, "
        f"{weather_code_text(current['weather_code'])}\n"
        f"Ощущается как: {current['apparent_temperature']:+.1f} °C\n"
        f"Влажность: {current['relative_humidity_2m']}%, "
        f"ветер: {current['wind_speed_10m']} км/ч\n\n"
        f"Сегодня ({today}): "
        f"{weather_code_text(daily['weather_code'][0])}, "
        f"{daily['temperature_2m_min'][0]:+.0f}…"
        f"{daily['temperature_2m_max'][0]:+.0f} °C, "
        f"вероятность осадков до "
        f"{daily['precipitation_probability_max'][0]}%\n"
        f"Завтра ({tomorrow}): "
        f"{weather_code_text(daily['weather_code'][1])}, "
        f"{daily['temperature_2m_min'][1]:+.0f}…"
        f"{daily['temperature_2m_max'][1]:+.0f} °C, "
        f"вероятность осадков до "
        f"{daily['precipitation_probability_max'][1]}%\n\n"
        "Источник: Open-Meteo. Прогноз может обновляться."
    )


async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.effective_message.reply_text(
            await get_weather_text(),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Ошибка получения погоды")
        await update.effective_message.reply_text(
            "Не удалось получить прогноз погоды для Костромы. Попробуй позже."
        )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(status_text())


async def sources_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message:
        await update.effective_message.reply_text(
            sources_text(),
            disable_web_page_preview=True
        )


async def admin_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID:
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ Команда только для администратора."
            )
        return

    await update.effective_message.reply_text(status_text())


async def incoming_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Ручная передача текста администратором.
    Подходящий текст отправляется админу в виде готового поста.
    """
    message = update.effective_message
    user = update.effective_user

    if (
        not message
        or not user
        or user.id != ADMIN_ID
        or not message.text
    ):
        return

    text = message.text.strip()

    if not mentions_kostroma(text):
        return

    result = classify(text)

    if not result:
        return

    kind, label = result

    await send_event(
        context.application,
        kind,
        label,
        "Текст, переданный администратором",
        "https://44.mchs.gov.ru/",
        text,
        now_msk().isoformat(timespec="minutes"),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Сохраняем кнопки меню подписки, источников и статуса.
    Старые кнопки модерации больше не используются.
    """
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    # Старые сообщения модерации могут оставаться в чате.
    if data.startswith(("approve:", "reject:")):
        await query.answer(
            "Модерация отключена: события отправляются автоматически.",
            show_alert=True
        )
        return

    await query.answer()

    if data == "subscribe":
        subscribers.add(query.from_user.id)
        save_state()
        text = "🔔 Подписка включена."

    elif data == "unsubscribe":
        subscribers.discard(query.from_user.id)
        save_state()
        text = "🔕 Подписка отключена."

    elif data == "sources":
        text = sources_text()

    elif data == "status":
        text = status_text()

    else:
        text = "Неизвестная команда."

    try:
        await query.edit_message_text(
            text,
            reply_markup=keyboard(),
            disable_web_page_preview=True
        )
    except BadRequest:
        if query.message:
            await query.message.reply_text(
                text,
                reply_markup=keyboard(),
                disable_web_page_preview=True
            )


async def post_init(application: Application):
    global monitor_task

    await application.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("subscribe", "Подписаться"),
        BotCommand("unsubscribe", "Отписаться"),
        BotCommand("status", "Статус мониторинга"),
        BotCommand("weather", "Погода в Костроме"),
        BotCommand("sources", "Список источников"),
        BotCommand("health", "Диагностика администратора"),
    ])

    try:
        await application.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                "🟢 UAV ALERT запущен.\n"
                f"Источников: {len(SOURCES)}\n"
                f"Интервал проверки: {CHECK_INTERVAL} сек.\n"
                f"Срок актуальности записи: {ALERT_TTL_HOURS} ч.\n"
                "Режим: готовые посты отправляются администратору "
                "автоматически, без модерации."
            ),
        )
    except TelegramError:
        logger.exception(
            "Не удалось отправить сообщение о запуске администратору"
        )

    monitor_task = asyncio.create_task(monitor_loop(application))


async def post_shutdown(application: Application):
    global monitor_task

    if monitor_task and not monitor_task.done():
        monitor_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


web_app = Flask(__name__)


@web_app.get("/")
def index():
    return jsonify({
        "service": "UAV ALERT",
        "status": "running",
        "last_scan": state.get("last_scan"),
    })


@web_app.get("/status")
def health():
    return jsonify({
        "status": "ok",
        "last_scan": state.get("last_scan"),
        "sources": len(SOURCES),
        "errors": state.get("last_errors", {}),
        "ttl_hours": ALERT_TTL_HOURS,
    })


def run_flask():
    web_app.run(
        host="0.0.0.0",
        port=PORT,
        use_reloader=False
    )


def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Укажи BOT_TOKEN в переменных окружения хостинга."
        )

    threading.Thread(
        target=run_flask,
        daemon=True,
        name="uav-alert-health"
    ).start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("weather", weather_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("health", admin_health))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            incoming_text
        )
    )

    application.add_handler(CallbackQueryHandler(callback))

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
