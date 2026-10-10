
import os
import re
import json
import html
import asyncio
import logging
import threading
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

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
from telegram.error import TelegramError, Forbidden, BadRequest
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956").strip())
except (ValueError, TypeError):
    ADMIN_ID = 1421675956

PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(15, int(os.getenv("CHECK_INTERVAL", "30")))
HISTORY_WINDOW_HOURS = max(
    1, int(os.getenv("HISTORY_WINDOW_HOURS", "5"))
)
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
SOURCE_LIMIT = max(10, int(os.getenv("SOURCE_LIMIT", "100")))
DATA_DIR = os.getenv("DATA_DIR", ".").strip() or "."

os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "state.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
SUBSCRIBERS_FILE = os.path.join(DATA_DIR, "subscribers.json")
SENT_POSTS_FILE = os.path.join(DATA_DIR, "sent_posts.json")
NOTIFICATION_EVENTS_FILE = os.path.join(
    DATA_DIR, "notification_events.json"
)
SOURCE_STATE_FILE = os.path.join(DATA_DIR, "source_state.json")

MSK = timezone(timedelta(hours=3))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("UAV_ALERT")


# ============================================================
# TELEGRAM SOURCES
# ============================================================

SOURCES = {
    "locator": {
        "name": "LOCATOR",
        "url": "https://t.me/s/locatorru",
    },
    "monitoring": {
        "name": "RUSSIA MONITORING",
        "url": "https://t.me/s/russiamonitoring_radar_bpla",
    },
    "radar": {
        "name": "RADAR",
        "url": "https://t.me/s/radarrussiia",
    },
    "bpla": {
        "name": "BPLA",
        "url": "https://t.me/s/bplarussiaru",
    },
}

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


# ============================================================
# REGION FILTER
# ============================================================

REGION_NAME = "Костромская область"

KOSTROMA_TERMS = [
    "костромская область",
    "костромской области",
    "костромской областью",
    "костромская обл",
    "кострома",
    "костромской",
    "нерехта",
    "нерехтский",
    "буй",
    "буйский",
    "волгореченск",
    "галич",
    "галичский",
    "шарья",
    "шарьинский",
    "мантурово",
    "чухлома",
    "чухломский",
    "макарьев",
    "макарьевский",
    "солигалич",
    "кологрив",
    "нея",
    "костромской район",
    "нерехтский район",
    "шарьинский район",
    "красносельский район",
    "судиславский район",
    "сусанинский район",
    "островский район",
    "буйский район",
    "галичский район",
    "макарьевский район",
    "чухломский район",
    "межевской район",
    "поназыревский район",
    "парфеньевский район",
    "антроповский район",
    "кадыйский район",
]

OTHER_REGION_TERMS = [
    "московская область",
    "тверская область",
    "ивановская область",
    "владимирская область",
    "ярославская область",
    "кировская область",
    "нижегородская область",
    "рязанская область",
    "калужская область",
    "смоленская область",
    "орловская область",
    "брянская область",
    "курская область",
    "белгородская область",
    "воронежская область",
    "липецкая область",
    "тамбовская область",
    "ростовская область",
    "краснодарский край",
    "ленинградская область",
    "санкт-петербург",
    "псковская область",
    "новгородская область",
    "мурманская область",
    "архангельская область",
    "республика татарстан",
    "татарстан",
    "удмуртия",
    "пермский край",
    "свердловская область",
]


# ============================================================
# UAV / MВШ / МРШ KEYWORDS
# ============================================================

UAV_TERMS = [
    "бпла",
    "беспилотник",
    "беспилотники",
    "беспилотного летательного аппарата",
    "беспилотный летательный аппарат",
    "беспилотные летательные аппараты",
    "беспилотных летательных аппаратов",
    "дрон",
    "дроны",
    "безэкипажный",
    "мрлс",
    "мрш",
    "мрщ",
    "мвш",
    "малоразмерный воздушный шар",
    "малоразмерные воздушные шары",
    "малоразмерных воздушных шаров",
    "малоразмерным воздушным шарам",
    "малоразмерными воздушными шарами",
    "малоразмерный разведывательный воздушный шар",
    "малоразмерные разведывательные воздушные шары",
    "малоразмерных разведывательных воздушных шаров",
    "воздушный шар",
    "воздушные шары",
    "воздушных шаров",
    "воздушным шарам",
    "воздушными шарами",
    "шары с аппаратурой",
    "воздушная цель",
    "воздушные цели",
    "летательный аппарат",
]

ATTENTION_TERMS = [
    "внимание по бпла",
    "внимание бпла",
    "внимание беспилотн",
    "внимание по беспилот",
    "внимание по мвш",
    "внимание мвш",
    "внимание по мрш",
    "внимание мрш",
    "внимание по воздушным шарам",
    "внимание по малоразмерным воздушным шарам",
    "внимание малоразмерные воздушные шары",
]

THREAT_TERMS = [
    "угроза по бпла",
    "угроза бпла",
    "угроза беспилотн",
    "угроза по беспилот",
    "угроза по мвш",
    "угроза мвш",
    "угроза по мрш",
    "угроза мрш",
    "угроза по воздушным шарам",
    "угроза по малоразмерным воздушным шарам",
    "угроза малоразмерных воздушных шаров",
]

DANGER_TERMS = [
    "опасность по бпла",
    "опасность бпла",
    "опасность беспилотн",
    "опасность по беспилот",
    "опасность по мвш",
    "опасность мвш",
    "опасность по мрш",
    "опасность мрш",
    "опасность по воздушным шарам",
    "опасность по воздушному шару",
    "опасность по малоразмерным воздушным шарам",
    "опасность по малоразмерному воздушному шару",
    "опасность малоразмерных воздушных шаров",
    "опасность от малоразмерных воздушных шаров",
    "опасность атаки бпла",
]

ROCKET_TERMS = [
    "ракетная опасность",
    "ракетной опасности",
    "ракетная угроза",
    "угроза ракетного удара",
    "опасность ракетного удара",
]

CANCEL_TERMS = [
    "отбой",
    "отмен",
    "снята",
    "снято",
    "сняты",
    "не подтверждается",
    "угроза миновала",
    "опасность миновала",
    "режим снят",
    "режим отменен",
    "режим отменён",
]


# ============================================================
# DEFAULT STATE AND SHARED DATA
# ============================================================

DEFAULT_STATE = {
    "uav_level": 0,
    "uav_active": False,
    "uav_locations": [],
    "uav_message": "",
    "uav_source": "",
    "uav_post_date": "",
    "uav_post_key": "",
    "uav_event_type": "",
    "uav_category": "",
    "rocket_active": False,
    "rocket_message": "",
    "rocket_source": "",
    "rocket_post_date": "",
    "rocket_post_key": "",
    "last_check": "",
    "last_successful_check": "",
    "source_errors": {},
    "started_at": datetime.now(MSK).isoformat(),
}

state: Dict[str, Any] = {}
history: List[Dict[str, Any]] = []
subscribers: List[int] = []
sent_posts: Dict[str, Any] = {}
notification_events: Dict[str, Any] = {}
source_state: Dict[str, Any] = {}

state_lock = threading.RLock()
file_lock = threading.RLock()


# ============================================================
# JSON STORAGE
# ============================================================

def read_json(path: str, default: Any) -> Any:
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Ошибка чтения %s: %s", path, exc)
        return default


def write_json(path: str, data: Any) -> bool:
    temporary_path = path + ".tmp"

    try:
        with file_lock:
            with open(temporary_path, "w", encoding="utf-8") as file:
                json.dump(
                    data,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temporary_path, path)

        return True

    except OSError as exc:
        logger.error("Ошибка записи %s: %s", path, exc)

        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            pass

        return False


def load_data() -> None:
    global state, history, subscribers
    global sent_posts, notification_events, source_state

    loaded_state = read_json(STATE_FILE, {})
    state = dict(DEFAULT_STATE)

    if isinstance(loaded_state, dict):
        state.update(loaded_state)

    loaded_history = read_json(HISTORY_FILE, [])
    history = loaded_history if isinstance(loaded_history, list) else []

    loaded_subscribers = read_json(SUBSCRIBERS_FILE, [])
    subscribers = []

    if isinstance(loaded_subscribers, list):
        for item in loaded_subscribers:
            try:
                user_id = int(item)
                if user_id not in subscribers:
                    subscribers.append(user_id)
            except (ValueError, TypeError):
                continue

    loaded_sent = read_json(SENT_POSTS_FILE, {})
    sent_posts = loaded_sent if isinstance(loaded_sent, dict) else {}

    loaded_events = read_json(NOTIFICATION_EVENTS_FILE, {})
    notification_events = (
        loaded_events if isinstance(loaded_events, dict) else {}
    )

    loaded_sources = read_json(SOURCE_STATE_FILE, {})
    source_state = (
        loaded_sources if isinstance(loaded_sources, dict) else {}
    )

    save_state()
    save_subscribers()


def save_state() -> None:
    with state_lock:
        snapshot = dict(state)
    write_json(STATE_FILE, snapshot)


def save_history() -> None:
    write_json(HISTORY_FILE, history[-500:])


def save_subscribers() -> None:
    write_json(SUBSCRIBERS_FILE, subscribers)


def save_sent_posts() -> None:
    write_json(SENT_POSTS_FILE, sent_posts)


def save_notification_events() -> None:
    write_json(NOTIFICATION_EVENTS_FILE, notification_events)


def save_source_state() -> None:
    write_json(SOURCE_STATE_FILE, source_state)


# ============================================================
# DATE AND TEXT HELPERS
# ============================================================

def now_msk() -> datetime:
    return datetime.now(MSK)


def parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None

    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)

    return result.astimezone(MSK)


def iso_datetime(value: Optional[datetime] = None) -> str:
    return (value or now_msk()).isoformat()


def normalize_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"@\w+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip().lower()


def contains_any(text: str, terms: List[str]) -> bool:
    normalized = normalize_text(text)
    return any(normalize_text(term) in normalized for term in terms)


def contains_uav(text: str) -> bool:
    return contains_any(text, UAV_TERMS)


def contains_rocket(text: str) -> bool:
    return contains_any(text, ROCKET_TERMS)


def contains_mwsh(text: str) -> bool:
    normalized = normalize_text(text)

    mwsh_terms = [
        "мвш",
        "малоразмерный воздушный шар",
        "малоразмерные воздушные шары",
        "малоразмерных воздушных шаров",
        "малоразмерным воздушным шарам",
        "малоразмерными воздушными шарами",
    ]

    return contains_any(normalized, mwsh_terms)


def get_uav_category(text: str) -> str:
    if contains_mwsh(text):
        return "МВШ — малоразмерные воздушные шары"

    normalized = normalize_text(text)

    if "мрш" in normalized or "разведывательный воздушный шар" in normalized:
        return "МРШ — малоразмерные разведывательные воздушные шары"

    if contains_uav(text):
        return "БПЛА и другие воздушные цели"

    return "Воздушная опасность"


def has_clear_cancel(text: str) -> bool:
    normalized = normalize_text(text)

    continuation_patterns = [
        r"опасность\s+сохраняется",
        r"угроза\s+сохраняется",
        r"режим\s+действует",
        r"опасность\s+продолжается",
        r"угроза\s+продолжается",
        r"отбой\s+не\s+объявлен",
        r"отмены\s+не\s+было",
        r"отбой\s+не\s+подтвержден",
    ]

    if any(
        re.search(pattern, normalized)
        for pattern in continuation_patterns
    ):
        return False

    return contains_any(normalized, CANCEL_TERMS)


def split_sentences(text: str) -> List[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [piece.strip() for piece in pieces if piece.strip()]


def event_fragments(text: str) -> List[str]:
    fragments = split_sentences(text)

    for line in (text or "").splitlines():
        line = line.strip()
        if line and line not in fragments:
            fragments.append(line)

    if not fragments and text.strip():
        fragments = [text.strip()]

    return fragments


# ============================================================
# REGION FILTER
# ============================================================

def has_kostroma_reference(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        normalize_text(term) in normalized
        for term in KOSTROMA_TERMS
    )


def has_other_region_reference(text: str) -> bool:
    normalized = normalize_text(text)
    return any(
        normalize_text(term) in normalized
        for term in OTHER_REGION_TERMS
    )


def is_kostroma_post(text: str) -> bool:
    return has_kostroma_reference(text)


def event_matches_region(text: str, event_type: str) -> bool:
    """
    Событие должно быть привязано к Костромской области.
    Не применяем общий отбой из другого региона к Костроме.
    """
    if has_kostroma_reference(text):
        return True

    lines = [
        line.strip()
        for line in (text or "").splitlines()
        if line.strip()
    ]

    for index, line in enumerate(lines):
        if has_kostroma_reference(line):
            nearby = " ".join(lines[index:index + 4])

            if event_type == "rocket" and contains_rocket(nearby):
                return True

            if event_type == "uav" and contains_uav(nearby):
                return True

    return False


# ============================================================
# UAV LEVEL DETECTION: БПЛА / МВШ / МРШ
# ============================================================

def detect_uav_level(text: str) -> int:
    """
    Возвращает:
      0 — явный отбой;
      1 — внимание;
      2 — угроза;
      3 — опасность;
     -1 — уровень не определён.

    Уровень не повышается автоматически только из-за того,
    что в публикации встретилось слово «опасность».
    """
    fragments = event_fragments(text)

    for fragment in fragments:
        if contains_uav(fragment) and has_clear_cancel(fragment):
            return 0

    for fragment in fragments:
        normalized = normalize_text(fragment)

        if not contains_uav(fragment):
            continue

        if has_clear_cancel(fragment):
            continue

        if contains_any(normalized, DANGER_TERMS):
            return 3

        if contains_any(normalized, THREAT_TERMS):
            return 2

        if contains_any(normalized, ATTENTION_TERMS):
            return 1

        if re.search(r"\bопасност\w*", normalized):
            if not re.search(r"\bнет\s+опасност", normalized):
                return 3

        if re.search(r"\bугроз\w*", normalized):
            if not re.search(r"\bугроз\w*\s+нет", normalized):
                return 2

        if re.search(r"\bвнимани\w*", normalized):
            return 1

    return -1


def detect_rocket(text: str) -> Optional[bool]:
    """
    True — активное сообщение о ракетной опасности.
    False — явный отбой/отмена.
    None — событие не определено.
    """
    for fragment in event_fragments(text):
        if not contains_rocket(fragment):
            continue

        normalized = normalize_text(fragment)

        if has_clear_cancel(fragment):
            return False

        negative_patterns = [
            r"ракетной опасности\s+нет",
            r"ракетная опасность\s+не\s+объявлена",
            r"угрозы\s+ракетного\s+удара\s+нет",
            r"ракетная угроза\s+отсутствует",
        ]

        if any(
            re.search(pattern, normalized)
            for pattern in negative_patterns
        ):
            return False

        active_patterns = [
            r"объявлен\w*\s+ракетн",
            r"ракетная опасность",
            r"ракетной опасности",
            r"угроза ракетного удара",
            r"опасность ракетного удара",
        ]

        if any(
            re.search(pattern, normalized)
            for pattern in active_patterns
        ):
            return True

    return None


def extract_locations(text: str) -> List[str]:
    normalized = normalize_text(text)

    location_names = [
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
        "Костромской район",
        "Нерехтский район",
        "Шарьинский район",
        "Красносельский район",
        "Судиславский район",
        "Сусанинский район",
        "Островский район",
        "Буйский район",
        "Галичский район",
        "Макарьевский район",
        "Чухломский район",
        "Межевской район",
        "Поназыревский район",
        "Парфеньевский район",
        "Антроповский район",
        "Кадыйский район",
    ]

    found = []

    for location in location_names:
        if normalize_text(location) in normalized and location not in found:
            found.append(location)

    return found


# ============================================================
# PUBLIC TELEGRAM SOURCE SCRAPER
# ============================================================

def fetch_source_sync(
    source_key: str,
    source_info: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    url = source_info["url"]

    try:
        response = requests.get(
            url,
            headers=HTTP_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        messages = []

        for node in soup.select(".tgme_widget_message"):
            text_node = node.select_one(".tgme_widget_message_text")

            if not text_node:
                continue

            text = text_node.get_text("\n", strip=True)

            if not text:
                continue

            time_node = node.select_one("time")
            date_value = None

            if time_node:
                date_value = (
                    time_node.get("datetime")
                    or time_node.get("title")
                )

            post_date = parse_datetime(date_value)

            if post_date is None:
                # Не подменяем неизвестное время текущим временем:
                # иначе старая публикация может выглядеть новой.
                continue

            link_node = node.select_one(".tgme_widget_message_date")
            post_url = link_node.get("href", "") if link_node else ""

            if not post_url:
                post_url = url

            key_material = (
                source_key + "|" + post_url + "|" + text
            ).encode("utf-8", errors="ignore")

            post_key = hashlib.sha256(key_material).hexdigest()

            messages.append({
                "source_key": source_key,
                "source_name": source_info["name"],
                "source_url": post_url,
                "text": text,
                "date": post_date.isoformat(),
                "key": post_key,
            })

        cutoff = now_msk() - timedelta(
            hours=HISTORY_WINDOW_HOURS
        )

        recent = [
            item
            for item in messages
            if parse_datetime(item["date"]) is not None
            and parse_datetime(item["date"]) >= cutoff
        ]

        recent.sort(
            key=lambda item: parse_datetime(item["date"])
            or datetime.min.replace(tzinfo=MSK)
        )

        return recent[-SOURCE_LIMIT:], None

    except requests.RequestException as exc:
        return [], str(exc)

    except Exception as exc:
        logger.exception("Ошибка разбора источника %s", source_key)
        return [], str(exc)


async def fetch_source(
    source_key: str,
    source_info: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    return await asyncio.to_thread(
        fetch_source_sync,
        source_key,
        source_info,
    )


# ============================================================
# HISTORY AND STATUS
# ============================================================

def add_history(
    event_type: str,
    title: str,
    source_name: str,
    source_url: str,
    post_date: Optional[datetime] = None,
    locations: Optional[List[str]] = None,
    post_key: str = "",
) -> None:
    history.append({
        "time": iso_datetime(),
        "post_date": (
            post_date.isoformat() if post_date else iso_datetime()
        ),
        "type": event_type,
        "title": title,
        "source": source_name,
        "url": source_url,
        "locations": locations or [],
        "key": post_key,
    })

    if len(history) > 500:
        del history[:-500]

    save_history()


def status_level_text(level: int) -> str:
    return {
        0: "🟢 Опасность не объявлена",
        1: "🟡 Внимание по БПЛА/МВШ",
        2: "🟠 Угроза по БПЛА/МВШ",
        3: "🔴 Опасность по БПЛА/МВШ",
    }.get(level, "🟢 Опасность не объявлена")


def get_state_snapshot() -> Dict[str, Any]:
    with state_lock:
        return dict(state)


def format_status() -> str:
    current = get_state_snapshot()
    level = int(current.get("uav_level", 0))
    category = current.get("uav_category") or "БПЛА / МВШ / МРШ"

    if level == 0:
        uav_line = "🟢 Опасность по БПЛА/МВШ не объявлена"
    else:
        uav_line = f"{status_level_text(level)}"

    lines = [
        "🛰 <b>UAV ALERT</b>",
        f"📍 Регион: <b>{html.escape(REGION_NAME)}</b>",
        "",
        uav_line,
    ]

    if level > 0:
        lines.append(f"Категория: {html.escape(str(category))}")

    locations = current.get("uav_locations", [])

    if locations:
        lines.append("")
        lines.append("<b>Указанные территории:</b>")
        for location in locations:
            lines.append(f"• {html.escape(str(location))}")

    lines.extend([
        "",
        (
            "🟥 Ракетная опасность объявлена"
            if current.get("rocket_active")
            else "🟢 Ракетная опасность не подтверждена"
        ),
    ])

    if current.get("uav_message"):
        lines.extend([
            "",
            "<b>Последнее сообщение по БПЛА/МВШ:</b>",
            html.escape(str(current["uav_message"])[:700]),
        ])

    if current.get("rocket_message"):
        lines.extend([
            "",
            "<b>Последнее сообщение по ракетной опасности:</b>",
            html.escape(str(current["rocket_message"])[:500]),
        ])

    last_check = parse_datetime(current.get("last_check"))

    if last_check:
        lines.extend([
            "",
            "🕒 Последняя проверка: "
            + last_check.strftime("%d.%m.%Y %H:%M:%S МСК"),
        ])

    last_success = parse_datetime(
        current.get("last_successful_check")
    )

    if last_success:
        lines.append(
            "✅ Последний успешный опрос: "
            + last_success.strftime("%d.%m.%Y %H:%M:%S МСК")
        )
    else:
        lines.append("⚠️ Успешных проверок источников пока нет.")

    errors = current.get("source_errors", {})
    if errors:
        error_names = [
            name for name, error in errors.items() if error
        ]
        if error_names:
            lines.append(
                "⚠️ Ошибки источников: "
                + html.escape(", ".join(error_names))
            )

    lines.extend([
        "",
        "ℹ️ UAV ALERT анализирует открытые публикации. "
        "Информация может быть неполной или запаздывать. "
        "Проверяйте официальные оповещения экстренных служб.",
    ])

    return "\n".join(lines)


def format_history(limit: int = 15) -> str:
    if not history:
        return "📚 История событий пока пуста."

    entries = history[-limit:]
    entries.reverse()

    lines = ["📚 <b>История UAV ALERT</b>", ""]

    for entry in entries:
        date = parse_datetime(entry.get("post_date"))
        date_text = (
            date.strftime("%d.%m %H:%M МСК")
            if date
            else "время неизвестно"
        )

        title = html.escape(str(entry.get("title", "Событие")))
        source = html.escape(str(entry.get("source", "Источник")))

        lines.append(f"• <b>{date_text}</b> — {title}")
        lines.append(f"  Источник: {source}")

        url = entry.get("url")

        if url and str(url).startswith("https://"):
            lines.append(
                f'<a href="{html.escape(str(url), quote=True)}">'
                "Открыть сообщение</a>"
            )

        lines.append("")

    return "\n".join(lines)


# ============================================================
# MAIN MENU
# ============================================================

def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📡 Подписаться на Костромскую область",
                callback_data="subscribe",
            )
        ],
        [
            InlineKeyboardButton(
                "🔕 Отписаться",
                callback_data="unsubscribe",
            )
        ],
        [
            InlineKeyboardButton(
                "📊 Текущий статус",
                callback_data="status",
            ),
            InlineKeyboardButton(
                "📚 История",
                callback_data="history",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔎 Источники",
                callback_data="sources",
            ),
        ],
    ])


# ============================================================
# PUSH NOTIFICATION BUILDERS
# ============================================================

def notification_key(
    event_type: str,
    post_key: str,
    event_value: str,
) -> str:
    raw = f"{event_type}|{post_key}|{event_value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_uav_push(
    level: int,
    message: str,
    source_name: str,
    source_url: str,
    locations: List[str],
    cancelled: bool = False,
    category: str = "",
) -> str:
    category = category or get_uav_category(message)

    if cancelled:
        title = f"🟢 <b>Отбой по {html.escape(category)}</b>"
    elif contains_mwsh(message):
        if level == 1:
            title = "🟡 <b>Внимание по МВШ — малоразмерным воздушным шарам</b>"
        elif level == 2:
            title = "🟠 <b>Угроза по МВШ — малоразмерным воздушным шарам</b>"
        else:
            title = "🔴 <b>Опасность по МВШ — малоразмерным воздушным шарам</b>"
    elif level == 1:
        title = "🟡 <b>Внимание по БПЛА</b>"
    elif level == 2:
        title = "🟠 <b>Угроза по БПЛА</b>"
    else:
        title = "🔴 <b>Опасность по БПЛА</b>"

    lines = [
        title,
        "",
        f"📍 Регион: <b>{html.escape(REGION_NAME)}</b>",
        f"Категория: <b>{html.escape(category)}</b>",
    ]

    if locations:
        lines.extend(["", "<b>Указанные территории:</b>"])
        for location in locations:
            lines.append(f"• {html.escape(location)}")

    lines.extend([
        "",
        "<b>Сообщение источника:</b>",
        html.escape(message[:1200]),
        "",
        f"📰 Источник: <b>{html.escape(source_name)}</b>",
    ])

    if source_url and source_url.startswith("https://"):
        lines.append(
            f'<a href="{html.escape(source_url, quote=True)}">'
            "Открыть публикацию</a>"
        )

    lines.extend([
        "",
        "⚠️ Информационное сообщение. Проверяйте официальные "
        "оповещения экстренных служб.",
    ])

    return "\n".join(lines)


def make_rocket_push(
    active: bool,
    message: str,
    source_name: str,
    source_url: str,
) -> str:
    title = (
        "🟥 <b>Сообщение о ракетной опасности</b>"
        if active
        else "🟢 <b>Сообщение об отбое ракетной опасности</b>"
    )

    lines = [
        title,
        "",
        f"📍 Регион: <b>{html.escape(REGION_NAME)}</b>",
        "",
        "<b>Сообщение источника:</b>",
        html.escape(message[:1200]),
        "",
        f"📰 Источник: <b>{html.escape(source_name)}</b>",
    ]

    if source_url and source_url.startswith("https://"):
        lines.append(
            f'<a href="{html.escape(source_url, quote=True)}">'
            "Открыть публикацию</a>"
        )

    lines.extend([
        "",
        "⚠️ Сверяйте информацию с официальными сообщениями.",
    ])

    return "\n".join(lines)


# ============================================================
# PUSH DELIVERY AND RETRIES
# ============================================================

async def send_push(
    application: Application,
    key: str,
    message: str,
    event_type: str,
    event_data: Optional[Dict[str, Any]] = None,
) -> int:
    event = notification_events.get(key)

    if not isinstance(event, dict):
        event = {
            "key": key,
            "type": event_type,
            "message": message,
            "data": event_data or {},
            "delivered": [],
            "created_at": iso_datetime(),
            "sent": False,
        }
        notification_events[key] = event
        save_notification_events()

    delivered = set()

    for item in event.get("delivered", []):
        try:
            delivered.add(int(item))
        except (ValueError, TypeError):
            continue

    sent_count = 0
    failed_count = 0

    for user_id in list(subscribers):
        if user_id in delivered:
            continue

        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )

            delivered.add(user_id)
            sent_count += 1

        except Forbidden:
            if user_id in subscribers:
                subscribers.remove(user_id)
                save_subscribers()

            # Заблокировавший бота пользователь не сможет получить push.
            delivered.add(user_id)

        except BadRequest as exc:
            logger.warning(
                "Telegram BadRequest для %s: %s",
                user_id,
                exc,
            )
            failed_count += 1

        except TelegramError as exc:
            logger.warning(
                "Ошибка отправки пользователю %s: %s",
                user_id,
                exc,
            )
            failed_count += 1

        event["delivered"] = sorted(delivered)
        event["sent"] = all(
            user_id in delivered for user_id in subscribers
        )
        event["last_attempt"] = iso_datetime()
        event["last_error_count"] = failed_count

        notification_events[key] = event
        save_notification_events()

        await asyncio.sleep(0.05)

    event["delivered"] = sorted(delivered)
    event["sent"] = all(
        user_id in delivered for user_id in subscribers
    )
    event["last_attempt"] = iso_datetime()
    event["last_error_count"] = failed_count

    notification_events[key] = event
    save_notification_events()

    logger.info(
        "Push %s: отправлено=%s, ошибок=%s, подписчиков=%s",
        key[:12],
        sent_count,
        failed_count,
        len(subscribers),
    )

    return sent_count


async def retry_pending_notifications(
    application: Application,
) -> None:
    for key, event in list(notification_events.items()):
        if not isinstance(event, dict) or event.get("sent"):
            continue

        message = event.get("message")

        if not message:
            continue

        try:
            await send_push(
                application=application,
                key=key,
                message=str(message),
                event_type=str(event.get("type", "unknown")),
                event_data=event.get("data", {}),
            )
        except Exception:
            logger.exception(
                "Ошибка повторной отправки уведомления %s",
                key[:12],
            )


# ============================================================
# EVENT FRESHNESS AND DEDUPLICATION
# ============================================================

def post_is_newer(
    post_date: Optional[datetime],
    previous_date_value: Any,
) -> bool:
    if post_date is None:
        return False

    previous_date = parse_datetime(previous_date_value)

    if previous_date is None:
        return True

    return post_date > previous_date


def mark_post_processed(post: Dict[str, Any]) -> None:
    key = str(post.get("key", ""))

    if not key:
        return

    sent_posts[key] = {
        "date": post.get("date", ""),
        "source": post.get("source_name", ""),
        "url": post.get("source_url", ""),
        "processed_at": iso_datetime(),
    }

    if len(sent_posts) > 2000:
        sorted_items = sorted(
            sent_posts.items(),
            key=lambda item: str(
                item[1].get("processed_at", "")
                if isinstance(item[1], dict)
                else ""
            ),
        )
        sent_posts.clear()
        sent_posts.update(dict(sorted_items[-1500:]))

    save_sent_posts()


def was_post_processed(post_key: str) -> bool:
    return post_key in sent_posts


# ============================================================
# APPLY UAV / MВШ EVENTS
# ============================================================

async def apply_uav_event(
    application: Application,
    post: Dict[str, Any],
    level: int,
) -> bool:
    post_date = parse_datetime(post.get("date"))
    post_key = str(post.get("key", ""))

    if not post_date or not post_key:
        return False

    current = get_state_snapshot()

    if not post_is_newer(
        post_date,
        current.get("uav_post_date"),
    ):
        return False

    message = str(post.get("text", ""))
    source_name = str(post.get("source_name", "UNKNOWN"))
    source_url = str(post.get("source_url", ""))
    locations = extract_locations(message)
    cancelled = level == 0
    category = get_uav_category(message)

    with state_lock:
        state["uav_level"] = level
        state["uav_active"] = not cancelled
        state["uav_locations"] = locations
        state["uav_message"] = message[:1500]
        state["uav_source"] = source_name
        state["uav_post_date"] = post_date.isoformat()
        state["uav_post_key"] = post_key
        state["uav_event_type"] = (
            "cancel" if cancelled else "active"
        )
        state["uav_category"] = category

    save_state()

    if cancelled:
        history_title = f"Отбой по {category}"
        event_value = "cancel"
    else:
        if contains_mwsh(message):
            if level == 1:
                history_title = "Внимание по МВШ"
            elif level == 2:
                history_title = "Угроза по МВШ"
            else:
                history_title = "Опасность по МВШ"
        else:
            history_title = status_level_text(level)

        event_value = f"level_{level}"

    add_history(
        event_type="uav",
        title=history_title,
        source_name=source_name,
        source_url=source_url,
        post_date=post_date,
        locations=locations,
        post_key=post_key,
    )

    key = notification_key("uav", post_key, event_value)

    push_text = make_uav_push(
        level=level,
        message=message,
        source_name=source_name,
        source_url=source_url,
        locations=locations,
        cancelled=cancelled,
        category=category,
    )

    await send_push(
        application,
        key,
        push_text,
        "uav",
        {
            "post_key": post_key,
            "post_date": post_date.isoformat(),
            "level": level,
            "cancelled": cancelled,
            "category": category,
        },
    )

    mark_post_processed(post)

    logger.info(
        "Обновлён статус воздушной опасности: level=%s category=%s post=%s",
        level,
        category,
        post_key[:12],
    )

    return True


# ============================================================
# APPLY ROCKET EVENTS
# ============================================================

async def apply_rocket_event(
    application: Application,
    post: Dict[str, Any],
    active: bool,
) -> bool:
    post_date = parse_datetime(post.get("date"))
    post_key = str(post.get("key", ""))

    if not post_date or not post_key:
        return False

    current = get_state_snapshot()

    if not post_is_newer(
        post_date,
        current.get("rocket_post_date"),
    ):
        return False

    message = str(post.get("text", ""))
    source_name = str(post.get("source_name", "UNKNOWN"))
    source_url = str(post.get("source_url", ""))

    with state_lock:
        state["rocket_active"] = active
        state["rocket_message"] = message[:1500]
        state["rocket_source"] = source_name
        state["rocket_post_date"] = post_date.isoformat()
        state["rocket_post_key"] = post_key

    save_state()

    title = (
        "Сообщение о ракетной опасности"
        if active
        else "Отбой ракетной опасности"
    )

    add_history(
        event_type="rocket",
        title=title,
        source_name=source_name,
        source_url=source_url,
        post_date=post_date,
        locations=extract_locations(message),
        post_key=post_key,
    )

    event_value = "active" if active else "cancel"
    key = notification_key("rocket", post_key, event_value)

    push_text = make_rocket_push(
        active=active,
        message=message,
        source_name=source_name,
        source_url=source_url,
    )

    await send_push(
        application,
        key,
        push_text,
        "rocket",
        {
            "post_key": post_key,
            "post_date": post_date.isoformat(),
            "active": active,
        },
    )

    mark_post_processed(post)

    logger.info(
        "Обновлён статус ракетной опасности: active=%s",
        active,
    )

    return True


# ============================================================
# PARSE POSTS
# ============================================================

def get_uav_event_for_post(
    post: Dict[str, Any],
) -> Optional[int]:
    text = str(post.get("text", ""))

    if not contains_uav(text):
        return None

    if not is_kostroma_post(text):
        return None

    fragments = event_fragments(text)

    for fragment in fragments:
        if not contains_uav(fragment):
            continue

        level = detect_uav_level(fragment)

        if level == -1:
            continue

        # Если в событии прямо назван регион — используем этот фрагмент.
        if event_matches_region(fragment, "uav"):
            return level

        # Если регион вынесен в заголовок, учитываем всю публикацию.
        if event_matches_region(text, "uav"):
            return level

    level = detect_uav_level(text)

    if level != -1 and event_matches_region(text, "uav"):
        return level

    return None


def get_rocket_event_for_post(
    post: Dict[str, Any],
) -> Optional[bool]:
    text = str(post.get("text", ""))

    if not contains_rocket(text):
        return None

    if not is_kostroma_post(text):
        return None

    fragments = event_fragments(text)

    for fragment in fragments:
        if not contains_rocket(fragment):
            continue

        active = detect_rocket(fragment)

        if active is None:
            continue

        if event_matches_region(fragment, "rocket"):
            return active

        if event_matches_region(text, "rocket"):
            return active

    active = detect_rocket(text)

    if active is not None and event_matches_region(text, "rocket"):
        return active

    return None


async def process_post(
    application: Application,
    post: Dict[str, Any],
) -> None:
    post_key = str(post.get("key", ""))

    if not post_key:
        return

    uav_level = get_uav_event_for_post(post)

    if uav_level is not None:
        await apply_uav_event(
            application,
            post,
            uav_level,
        )

    rocket_active = get_rocket_event_for_post(post)

    if rocket_active is not None:
        await apply_rocket_event(
            application,
            post,
            rocket_active,
        )


# ============================================================
# SOURCE MONITORING LOOP
# ============================================================

async def check_sources(application: Application) -> None:
    check_time = now_msk()
    source_errors = {}
    all_posts = []
    successful_sources = 0

    for source_key, source_info in SOURCES.items():
        posts, error = await fetch_source(
            source_key,
            source_info,
        )

        source_state[source_key] = {
            "last_check": check_time.isoformat(),
            "last_error": error or "",
            "posts_found": len(posts),
            "source_url": source_info["url"],
        }

        if error:
            source_errors[source_info["name"]] = error
            logger.warning(
                "Источник %s недоступен: %s",
                source_info["name"],
                error,
            )
        else:
            successful_sources += 1

        all_posts.extend(posts)

    # Старые сообщения обрабатываются раньше новых.
    # Сравнение с сохранённой датой не даёт старому событию
    # перезаписать уже установленный более свежий статус.
    all_posts.sort(
        key=lambda post: parse_datetime(post.get("date"))
        or datetime.min.replace(tzinfo=MSK)
    )

    with state_lock:
        state["last_check"] = check_time.isoformat()
        state["source_errors"] = {
            name: str(error)[:250]
            for name, error in source_errors.items()
        }

        if successful_sources:
            state["last_successful_check"] = check_time.isoformat()

    save_state()
    save_source_state()

    for post in all_posts:
        try:
            await process_post(application, post)
        except Exception:
            logger.exception(
                "Ошибка обработки публикации %s",
                str(post.get("key", ""))[:12],
            )

    await retry_pending_notifications(application)

    logger.info(
        "Проверка завершена: успешно %s/%s источников, публикаций %s",
        successful_sources,
        len(SOURCES),
        len(all_posts),
    )


async def monitoring_loop(application: Application) -> None:
    logger.info(
        "Мониторинг запущен. Интервал: %s секунд",
        CHECK_INTERVAL,
    )

    while True:
        try:
            await check_sources(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка основного цикла мониторинга")

        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Запустить UAV ALERT"),
        BotCommand("status", "Текущий статус"),
        BotCommand("history", "История событий"),
        BotCommand("sources", "Источники информации"),
        BotCommand("stats", "Статистика сервиса"),
        BotCommand("help", "Помощь"),
    ]

    try:
        await application.bot.set_my_commands(commands)
    except TelegramError:
        logger.exception("Не удалось установить команды Telegram")

    application.create_task(
        monitoring_loop(application),
        name="uav_alert_monitor",
    )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    text = (
        "🛰 <b>Добро пожаловать в UAV ALERT</b>\n\n"
        "Гражданский информационный сервис для отслеживания "
        "публикаций об опасностях в Костромской области.\n\n"
        "Поддерживается распознавание сообщений о БПЛА, "
        "МВШ — малоразмерных воздушных шарах, МРШ и "
        "ракетной опасности.\n\n"
        "Чтобы получать уведомления, нажми кнопку подписки.\n\n"
        "⚠️ Бот не является официальной системой оповещения "
        "и не заменяет сообщения экстренных служб."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    text = (
        "ℹ️ <b>Помощь UAV ALERT</b>\n\n"
        "/start — главное меню\n"
        "/status — текущий статус\n"
        "/history — история событий\n"
        "/sources — источники информации\n"
        "/stats — статистика сервиса\n"
        "/subscribe — подписаться на уведомления\n"
        "/unsubscribe — отключить уведомления\n\n"
        "Администратору доступны:\n"
        "/admin — панель администратора\n"
        "/test — тестовое уведомление\n"
        "/check — проверить источники\n\n"
        "Сервис ищет публикации о БПЛА, МВШ, МРШ и "
        "ракетной опасности. Всегда сверяй информацию "
        "с официальными оповещениями."
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        format_status(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def history_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    await update.effective_message.reply_text(
        format_history(),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def sources_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    lines = [
        "🔎 <b>Источники UAV ALERT</b>",
        "",
        "Бот проверяет публичные страницы Telegram:",
        "",
    ]

    for source_info in SOURCES.values():
        name = html.escape(source_info["name"])
        url = html.escape(source_info["url"], quote=True)

        lines.append(
            f'• <b>{name}</b>\n  <a href="{url}">Открыть источник</a>'
        )

    lines.extend([
        "",
        "⚠️ Публикации не обязательно являются официальными "
        "подтверждениями событий.",
    ])

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
        disable_web_page_preview=True,
    )


async def stats_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.effective_message:
        return

    current = get_state_snapshot()

    uav_events = sum(
        1 for item in history if item.get("type") == "uav"
    )
    rocket_events = sum(
        1 for item in history if item.get("type") == "rocket"
    )

    errors = current.get("source_errors", {})
    error_count = sum(1 for value in errors.values() if value)

    text = (
        "📊 <b>Статистика UAV ALERT</b>\n\n"
        f"👥 Подписчиков: <b>{len(subscribers)}</b>\n"
        f"📚 Записей истории: <b>{len(history)}</b>\n"
        f"🛩 Событий БПЛА/МВШ/МРШ: <b>{uav_events}</b>\n"
        f"🚀 Событий ракетной опасности: <b>{rocket_events}</b>\n"
        f"🔎 Источников: <b>{len(SOURCES)}</b>\n"
        f"⚠️ Источников с ошибками: <b>{error_count}</b>\n"
        f"⏱ Интервал проверки: <b>{CHECK_INTERVAL} сек.</b>"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not user or not update.effective_message:
        return

    if user.id not in subscribers:
        subscribers.append(user.id)
        save_subscribers()
        result = (
            "✅ Ты подписался на уведомления UAV ALERT "
            "по Костромской области."
        )
    else:
        result = "ℹ️ Ты уже подписан на уведомления."

    await update.effective_message.reply_text(
        result,
        reply_markup=main_keyboard(),
    )


async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not user or not update.effective_message:
        return

    if user.id in subscribers:
        subscribers.remove(user.id)
        save_subscribers()
        result = "🔕 Ты отписался от уведомлений."
    else:
        result = "ℹ️ Ты не был подписан на уведомления."

    await update.effective_message.reply_text(
        result,
        reply_markup=main_keyboard(),
    )


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not update.effective_message:
        return

    if not user or user.id != ADMIN_ID:
        await update.effective_message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    current = get_state_snapshot()

    text = (
        "🛠 <b>Панель администратора UAV ALERT</b>\n\n"
        f"👥 Подписчиков: {len(subscribers)}\n"
        f"📚 Записей истории: {len(history)}\n"
        f"🛩 Уровень БПЛА/МВШ: {current.get('uav_level', 0)}\n"
        f"📌 Категория: "
        f"{html.escape(str(current.get('uav_category', 'не определена')))}\n"
        f"🚀 Ракетная опасность: "
        f"{'активна' if current.get('rocket_active') else 'не активна'}\n\n"
        "Команды:\n"
        "/test — тестовое уведомление\n"
        "/check — выполнить проверку источников"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def test_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not update.effective_message:
        return

    if not user or user.id != ADMIN_ID:
        await update.effective_message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    test_key = notification_key(
        "test",
        hashlib.sha256(iso_datetime().encode("utf-8")).hexdigest(),
        "test",
    )

    text = (
        "🧪 <b>Тестовое уведомление UAV ALERT</b>\n\n"
        "Это техническая проверка доставки сообщений.\n"
        "Официальная опасность этим сообщением не объявляется."
    )

    await send_push(
        context.application,
        test_key,
        text,
        "test",
        {"created_at": iso_datetime()},
    )

    await update.effective_message.reply_text(
        "✅ Тестовая отправка запущена. Проверь журнал Render "
        "и доступность бота для подписчиков."
    )


async def check_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user

    if not update.effective_message:
        return

    if not user or user.id != ADMIN_ID:
        await update.effective_message.reply_text(
            "⛔ Команда доступна только администратору."
        )
        return

    await update.effective_message.reply_text(
        "🔄 Начинаю проверку источников..."
    )

    await check_sources(context.application)

    await update.effective_message.reply_text(
        "✅ Проверка завершена.\n\n" + format_status(),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ============================================================
# INLINE BUTTON CALLBACKS
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query:
        return

    await query.answer()

    user = query.from_user
    data = query.data or ""

    if data == "subscribe":
        if user.id not in subscribers:
            subscribers.append(user.id)
            save_subscribers()
            text = (
                "✅ Подписка оформлена. "
                "Ты будешь получать уведомления UAV ALERT."
            )
        else:
            text = "ℹ️ Ты уже подписан на уведомления."

        await query.message.reply_text(
            text,
            reply_markup=main_keyboard(),
        )
        return

    if data == "unsubscribe":
        if user.id in subscribers:
            subscribers.remove(user.id)
            save_subscribers()
            text = "🔕 Подписка отключена."
        else:
            text = "ℹ️ Ты не подписан на уведомления."

        await query.message.reply_text(
            text,
            reply_markup=main_keyboard(),
        )
        return

    if data == "status":
        await query.message.reply_text(
            format_status(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "history":
        await query.message.reply_text(
            format_history(),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "sources":
        lines = ["🔎 <b>Источники</b>", ""]

        for info in SOURCES.values():
            lines.append(
                f'• <a href="{html.escape(info["url"], quote=True)}">'
                f'{html.escape(info["name"])}</a>'
            )

        await query.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
            disable_web_page_preview=True,
        )


# ============================================================
# FLASK WEB SERVER
# ============================================================

web_app = Flask(__name__)


@web_app.get("/")
def index():
    return jsonify({
        "service": "UAV ALERT",
        "status": "running",
        "region": REGION_NAME,
        "message": (
            "Civilian information service. "
            "This endpoint is not an official emergency alert."
        ),
    })


@web_app.get("/health")
def health():
    current = get_state_snapshot()

    return jsonify({
        "ok": True,
        "service": "UAV ALERT",
        "last_check": current.get("last_check"),
        "last_successful_check": current.get(
            "last_successful_check"
        ),
    })


@web_app.get("/status")
def status_endpoint():
    current = get_state_snapshot()

    return jsonify({
        "service": "UAV ALERT",
        "region": REGION_NAME,
        "uav_level": current.get("uav_level", 0),
        "uav_active": current.get("uav_active", False),
        "uav_category": current.get("uav_category", ""),
        "uav_locations": current.get("uav_locations", []),
        "rocket_active": current.get("rocket_active", False),
        "last_check": current.get("last_check"),
        "last_successful_check": current.get(
            "last_successful_check"
        ),
        "source_errors": current.get("source_errors", {}),
    })


def run_web_server() -> None:
    web_app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


# ============================================================
# APPLICATION STARTUP
# ============================================================

def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Добавь токен бота в Environment на Render."
        )

    load_data()

    logger.info("Запуск UAV ALERT...")
    logger.info("Регион мониторинга: %s", REGION_NAME)
    logger.info("Подписчиков загружено: %s", len(subscribers))
    logger.info("Источников мониторинга: %s", len(SOURCES))

    flask_thread = threading.Thread(
        target=run_web_server,
        name="uav-alert-web",
        daemon=True,
    )
    flask_thread.start()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("sources", sources_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("check", check_command))

    application.add_handler(
        CallbackQueryHandler(callback_handler)
    )

    application.run_polling(
        drop_pending_updates=False,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
