import os
import re
import json
import asyncio
import hashlib
import logging
import threading
import html
from datetime import datetime, timezone, timedelta
from collections import Counter

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

from ai_core import analyze_text, ai_status_text, ai_history_text
from ai_provider import AIProviderError

"""
UAV ALERT — гражданский информационный бот.

Сохранённые возможности:
- мониторинг публичных Telegram-лент;
- фильтры БПЛА, МВШ и ракетной опасности/отбоя;
- исходное время публикации в МСК;
- срок актуальности записи (TTL 5 часов), без автоматического отбоя;
- подписки, погода, диагностика и HTTP health endpoint;
- отдельная обработка региональных новостей.

Добавлено:
- центр событий;
- статистика за последние 24 часа;
- архив подтверждённых администратором сообщений;
- ручная модерация перед публикацией;
- публикация в канал только после подтверждения администратора;
- уведомления подписчикам после подтверждённой публикации;
- проверка повторов и предупреждение о противоречивых сообщениях;
- явное различение сторонних и официальных источников;
- безопасная фильтрация новостей: оперативные сведения о БПЛА,
  координаты, маршруты, позиции и подобные детали не пересылаются;
- отдельные фотографии для каждого типа события.

ВАЖНО:
Бот не прогнозирует цели, маршруты, траектории или зоны возможного удара.
Он не подтверждает угрозу самостоятельно. Все сигналы требуют сверки
с официальными оповещениями. Публикация в канал выполняется только
после ручного решения администратора.
"""

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "1421675956"))
CHANNEL_ID = os.getenv("CHANNEL_ID", "@RADAR_Kostroma").strip()
PORT = int(os.getenv("PORT", "10000"))
CHECK_INTERVAL = max(5, int(os.getenv("CHECK_INTERVAL", "5")))
REQUEST_TIMEOUT = max(5, int(os.getenv("REQUEST_TIMEOUT", "15")))
ALERT_TTL_HOURS = max(1, int(os.getenv("ALERT_TTL_HOURS", "5")))
ALERT_TTL = timedelta(hours=ALERT_TTL_HOURS)

# Восстановление пропущенных публикаций после перезапуска.
HISTORY_BACKFILL_HOURS = max(1, int(os.getenv("HISTORY_BACKFILL_HOURS", "6")))
HISTORY_MAX_PAGES = max(1, min(10, int(os.getenv("HISTORY_MAX_PAGES", "6"))))

DATA_DIR = os.getenv("DATA_DIR", ".")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MSK = timezone(timedelta(hours=3))

# Отдельные изображения для каждого типа события.
UAV_ATTENTION_IMAGE = os.path.join(BASE_DIR, "uav_attention.jpg")
UAV_DANGER_IMAGE = os.path.join(BASE_DIR, "uav_danger.jpg")
UAV_CANCEL_IMAGE = os.path.join(BASE_DIR, "uav_cancel.jpg")

MWSH_DANGER_IMAGE = os.path.join(BASE_DIR, "mwsh_danger.jpg")
MWSH_CANCEL_IMAGE = os.path.join(BASE_DIR, "mwsh_cancel.jpg")

ROCKET_DANGER_IMAGE = os.path.join(BASE_DIR, "rocket_danger.jpg")
ROCKET_CANCEL_IMAGE = os.path.join(BASE_DIR, "rocket_cancel.jpg")

# Старые изображения используются как запасные.
LEGACY_DANGER_IMAGE = os.path.join(BASE_DIR, "danger.jpg")
LEGACY_CLEAR_IMAGE = os.path.join(BASE_DIR, "all_clear.jpg")

SOURCES = [
    ("locatorru", "https://t.me/s/locatorru"),
    ("radarrussiia", "https://t.me/s/radarrussiia"),
    ("bplarussiaru", "https://t.me/s/bplarussiaru"),
]

# Отдельная лента региональных новостей; не влияет на статусы.
NEWS_SOURCES = [
    ("В курсе Кострома", "https://t.me/s/v_kurse_kostroma"),
]

# Такие публикации не пересылаются как обычные новости.
# Это защищает от распространения оперативных деталей.
NEWS_SENSITIVE_RE = re.compile(
    r"бпла|беспилот|дрон|мвш|мрш|пво|зенитн|перехват|сбит|"
    r"облом|координат|маршрут|траектор|военн\w*\s+объект|"
    r"ракетн\w*\s+опасност|авиаудар|позици\w*\s+(?:военн|пво)",
    re.I
)

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

REGION_RE = re.compile(r"костром\w*\s+(?:област|регион)", re.I)

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
    "г. Кострома": ["город кострома", "г. кострома", "кострома"],
    "г. Буй": ["город буй", "г. буй"],
    "г. Волгореченск": ["город волгореченск", "г. волгореченск", "волгореченск"],
    "г. Галич": ["город галич", "г. галич"],
    "г. Кологрив": ["город кологрив", "г. кологрив"],
    "г. Макарьев": ["город макарьев", "г. макарьев"],
    "г. Мантурово": ["город мантурово", "г. мантурово", "мантурово"],
    "г. Нея": ["город нея", "г. нея", "нея"],
    "г. Нерехта": ["город нерехта", "г. нерехта", "нерехта"],
    "г. Солигалич": ["город солигалич", "г. солигалич"],
    "г. Шарья": ["город шарья", "г. шарья", "шарья"],
    "Антроповский округ": ["антроповский район", "антроповский округ"],
    "Боговаровский округ": ["боговаровский район", "боговаровский округ", "боговаров"],
    "Варнавинский округ": ["варнавинский район", "варнавинский округ", "варнав"],
    "Вохомский округ": ["вохомский район", "вохомский округ"],
    "Галичский район": ["галичский район", "галичский округ"],
    "Кадыйский округ": ["кадыйский район", "кадыйский округ"],
    "Кологривский округ": ["кологривский район", "кологривский округ"],
    "Костромской район": ["костромской район", "костромской округ"],
    "Красносельский район": ["красносельский район", "красносельский округ"],
    "Макарьевский округ": ["макарьевский район", "макарьевский округ"],
    "Мантуровский округ": ["мантуровский район", "мантуровский округ"],
    "Межевской округ": ["межевской район", "межевской округ"],
    "Нейский округ": ["нейский район", "нейский округ"],
    "Нерехтский район": ["нерехтский район", "нерехтский округ"],
    "Октябрьский округ": ["октябрьский район", "октябрьский округ"],
    "Островский район": ["островский район", "островский округ"],
    "Павинский округ": ["павинский район", "павинский округ"],
    "Парфеньевский округ": ["парфеньевский район", "парфеньевский округ"],
    "Поназыревский округ": ["поназыревский район", "поназыревский округ"],
    "Пыщугский округ": ["пыщугский район", "пыщугский округ"],
    "Солигаличский район": ["солигаличский район", "солигаличский округ"],
    "Судиславский округ": ["судиславский район", "судиславский округ"],
    "Сусанинский район": ["сусанинский район", "сусанинский округ"],
    "Чухломский район": ["чухломский район", "чухломский округ"],
    "Шарьинский район": ["шарьинский район", "шарьинский округ"],
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
    "rejected": {},
    "active_alerts": {},
    "news_seen": {},
    "news_initialized": {},
    "news_last_errors": {},
    "event_history": [],
    "news_history": [],
}.items():
    state.setdefault(key, default)

subscribers = set()
for value in load_json(SUBSCRIBERS_FILE, state.get("subscribers", [])):
    try:
        subscribers.add(int(value))
    except (TypeError, ValueError):
        continue

monitor_task = None
# Источники, для которых уже выполнен расширенный поиск после запуска процесса.
startup_backfill_done = set()

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
    """Извлекает упоминания территорий для внутренней проверки текста."""
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
        key=lambda item: max((len(alias) for alias in item[1]), default=0),
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
                logger.debug("Не удалось разобрать время публикации: %r", raw)
    return None

def source_posts(source_url, max_pages=1, cutoff=None):
    """
    Читает публичную веб-ленту Telegram.
    Для восстановления истории использует ?before=<message_id>.
    Возвращает сообщения в хронологическом порядке.
    Публичная лента Telegram ограничена и не гарантирует полную историю.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; UAV-ALERT/1.0)"}
    posts_by_key = {}
    page_url = source_url
    seen_page_ids = set()
    stop_paging = False

    for page_number in range(max(1, int(max_pages))):
        response = requests.get(
            page_url,
            timeout=REQUEST_TIMEOUT,
            headers=headers
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        page_posts = []
        numeric_ids = []

        for node in soup.select(".tgme_widget_message"):
            text_node = node.select_one(".tgme_widget_message_text")
            if not text_node:
                continue

            text = text_node.get_text(" ", strip=True)
            link_node = node.select_one("a.tgme_widget_message_date")
            post_url = link_node.get("href", "") if link_node else source_url
            post_id = node.get("data-post", "") or post_url
            published_at = parse_published_time(node)

            # data-post обычно имеет формат channel/message_id.
            tail = str(post_id).rsplit("/", 1)[-1]
            if tail.isdigit():
                numeric_ids.append(int(tail))

            digest = hashlib.sha256(
                (str(post_id) + "|" + text).encode("utf-8")
            ).hexdigest()

            if text:
                post = {
                    "key": digest,
                    "text": text[:5000],
                    "url": post_url,
                    "published_at": (
                        published_at.isoformat(timespec="minutes")
                        if published_at else None
                    ),
                }
                page_posts.append(post)
                posts_by_key[digest] = post

                if cutoff and published_at and published_at < cutoff:
                    pass

        if not page_posts or page_number + 1 >= max_pages:
            break

        if cutoff:
            dated = [
                parse_iso(post.get("published_at"))
                for post in page_posts
            ]
            dated = [stamp for stamp in dated if stamp]
            if dated and max(dated) < cutoff:
                stop_paging = True
        if stop_paging:
            break

        if not numeric_ids:
            break
        before_id = min(numeric_ids)
        if before_id in seen_page_ids:
            break
        seen_page_ids.add(before_id)

        separator = "&" if "?" in source_url else "?"
        page_url = f"{source_url}{separator}before={before_id}"

    posts = list(posts_by_key.values())
    posts.sort(
        key=lambda post: parse_iso(post.get("published_at"))
        or datetime.min.replace(tzinfo=MSK)
    )
    return posts

def format_event_time(published_at):
    if published_at:
        try:
            parsed = datetime.fromisoformat(published_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=MSK)
            return parsed.astimezone(MSK).strftime("%d.%m.%Y %H:%M МСК")
        except (TypeError, ValueError):
            pass
    return "не указано"

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

def event_image_path(kind):
    """Возвращает изображение для конкретной категории события."""
    image_paths = {
        "attention": UAV_ATTENTION_IMAGE,
        "threat": UAV_DANGER_IMAGE,
        "danger": UAV_DANGER_IMAGE,
        "cancel": UAV_CANCEL_IMAGE,
        "mwsh_danger": MWSH_DANGER_IMAGE,
        "mwsh_cancel": MWSH_CANCEL_IMAGE,
        "rocket_danger": ROCKET_DANGER_IMAGE,
        "rocket_cancel": ROCKET_CANCEL_IMAGE,
    }

    image_path = image_paths.get(kind)

    if image_path and os.path.isfile(image_path):
        return image_path

    # Если отдельного изображения нет, используем старое.
    fallback = LEGACY_CLEAR_IMAGE if event_is_cancel(kind) else LEGACY_DANGER_IMAGE

    if os.path.isfile(fallback):
        return fallback

    return None

def event_title(kind):
    return {
        "mwsh_cancel": ("🟢 Отбой опасности по МВШ", "МВШ"),
        "mwsh_danger": ("🔴 Опасность по МВШ", "МВШ"),
        "rocket_cancel": ("🟢 Отбой ракетной опасности", "Ракетная опасность"),
        "rocket_danger": ("🔴 Ракетная опасность", "Ракетная опасность"),
        "cancel": ("🟢 Отбой опасности по БПЛА", "БПЛА"),
        "threat": ("🟠 Угроза по БПЛА", "БПЛА"),
        "attention": ("🟡 Внимание: БПЛА", "БПЛА"),
        "danger": ("🔴 Опасность по БПЛА", "БПЛА"),
    }.get(kind, ("ℹ️ Сообщение", "Информация"))

def event_message(kind, label, source, url, source_text, published_at=None):
    """
    Готовит нейтральный пост.
    Детальные районы, координаты, маршруты и предполагаемые цели
    в публичный текст автоматически не переносятся.
    """
    title, category = event_title(kind)
    stamp = html.escape(format_event_time(published_at))
    safe_url = html.escape(url, quote=True)
    safe_source = html.escape(source)

    return (
        "<b>🛰 UAV ALERT</b>\n"
        "📍 <b>Регион:</b> Костромская область\n\n"
        f"<b>{title}</b>\n"
        f"Категория: {html.escape(category)}\n"
        f"Время исходного сообщения: {stamp}\n\n"
        "📍 <b>Территории:</b> Костромская область\n\n"
        f'📡 <b>Источник:</b> <a href="{safe_url}">{safe_source}</a>\n\n'
        "⚠️ Сообщение получено из стороннего источника и не является "
        "самостоятельным подтверждением угрозы. Сверяйте информацию "
        "с официальными оповещениями МЧС и органов власти. "
        "Следуйте инструкциям экстренных служб."
    )

def conflict_warning(kind):
    """Ищет недавнее противоположное событие той же категории."""
    family = event_family(kind)
    opposite = event_is_cancel(kind)
    now = now_msk()
    matches = []

    for item in state.get("pending", {}).values():
        if event_family(item.get("kind", "")) != family:
            continue
        if event_is_cancel(item.get("kind", "")) == opposite:
            continue
        try:
            created = datetime.fromisoformat(item.get("created_at", ""))
            if created.tzinfo is None:
                created = created.replace(tzinfo=MSK)
            if now - created.astimezone(MSK) <= timedelta(hours=3):
                matches.append(item)
        except (TypeError, ValueError):
            continue

    if matches:
        return (
            "\n\n⚠️ <b>ВНИМАНИЕ: обнаружено возможное противоречие.</b>\n"
            "В очереди есть недавнее сообщение с другим статусом этой "
            "категории. Проверьте исходные публикации и официальные "
            "оповещения перед принятием решения."
        )
    return ""

def moderation_keyboard(pending_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Подтвердить и опубликовать",
                callback_data=f"approve:{pending_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Отклонить",
                callback_data=f"reject:{pending_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔎 Центр событий",
                callback_data="admin_center"
            ),
        ],
    ])

async def send_text_or_photo(
    bot, chat_id, message, is_cancel=False, kind=None
):
    """
    Отправляет текст с фотографией события.
    Если подпись слишком длинная, фотография отправляется отдельно,
    а полный текст — следующим сообщением.
    """
    if kind is not None:
        image_path = event_image_path(kind)
    else:
        image_path = (
            LEGACY_CLEAR_IMAGE if is_cancel else LEGACY_DANGER_IMAGE
        )

    if image_path and os.path.isfile(image_path):
        if len(message) <= 1024:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=message,
                    parse_mode=ParseMode.HTML,
                    disable_notification=False
                )
        else:
            with open(image_path, "rb") as photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    disable_notification=False
                )

            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
    else:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

def add_history(item, decision, actor_id=None):
    history = state.setdefault("event_history", [])
    record = {
        "kind": item.get("kind"),
        "label": item.get("label"),
        "source": item.get("source"),
        "url": item.get("url"),
        "published_at": item.get("published_at"),
        "created_at": item.get("created_at"),
        "decision_at": now_msk().isoformat(timespec="seconds"),
        "decision": decision,
        "actor_id": actor_id,
    }
    history.append(record)
    state["event_history"] = history[-500:]

def record_approved_event(item):
    """Обновляет статусную запись только после подтверждения администратором."""
    active = state.setdefault("active_alerts", {})
    family = event_family(item["kind"])

    if event_is_cancel(item["kind"]):
        for key in list(active):
            if event_family(key) == family:
                active.pop(key, None)
        return

    if event_is_danger(item["kind"]):
        event_time = now_msk()
        published_at = item.get("published_at")
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
            "expires_at": (event_time + ALERT_TTL).isoformat(timespec="seconds"),
            "source": item["source"],
            "url": item["url"],
            "stale": False,
        }

async def notify_subscribers(bot, message, kind=None):
    """Рассылает подписчикам одобренное сообщение с нужной фотографией."""
    failed = []

    for user_id in list(subscribers):
        try:
            await send_text_or_photo(
                bot,
                user_id,
                message,
                is_cancel=event_is_cancel(kind) if kind else False,
                kind=kind
            )
        except TelegramError as exc:
            logger.info(
                "Не удалось уведомить подписчика %s: %s",
                user_id,
                exc
            )
            failed.append(user_id)

    # Не удаляем подписчика автоматически: ошибка может быть временной.
    return failed

async def send_event(
    application, kind, label, source, url, text, published_at=None
):
    """
    Создаёт карточку на ручную проверку.
    Ничего не публикует в канал и не рассылает подписчикам до решения админа.
    """
    pending_id = hashlib.sha256(
        f"{url}|{text}|{published_at or ''}".encode("utf-8")
    ).hexdigest()[:16]

    if pending_id in state.setdefault("approved", {}):
        return
    if pending_id in state.setdefault("pending", {}):
        return
    if pending_id in state.setdefault("rejected", {}):
        return

    item = {
        "id": pending_id,
        "kind": kind,
        "label": label,
        "source": source,
        "url": url,
        "text": text[:5000],
        "published_at": published_at,
        "message": event_message(kind, label, source, url, text, published_at),
        "created_at": now_msk().isoformat(timespec="seconds"),
    }
    state["pending"][pending_id] = item
    save_state()

    admin_message = (
        "🛡 <b>Новое событие — требуется проверка администратора</b>\n\n"
        f"{item['message']}"
        f"{conflict_warning(kind)}\n\n"
        "Откройте исходную публикацию по ссылке выше и сверяйте "
        "информацию с официальными оповещениями. Не переносите в канал "
        "координаты, маршруты, места обнаружения или другие оперативные детали.\n\n"
        "Публикация в канал и уведомление подписчиков произойдут "
        "только после отдельного решения администратора."
    )

    try:
        await send_text_or_photo(
            application.bot,
            ADMIN_ID,
            admin_message,
            is_cancel=event_is_cancel(kind),
            kind=kind
        )
        logger.info("Событие добавлено в очередь проверки: %s", pending_id)
    except TelegramError:
        logger.exception("Не удалось отправить карточку администратору")

async def approve_event(query, context, pending_id):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "Только администратор может подтвердить событие.",
            show_alert=True
        )
        return

    item = state.setdefault("pending", {}).get(pending_id)
    if not item:
        await query.answer(
            "Событие уже обработано или не найдено.",
            show_alert=True
        )
        return

    try:
        # Публикуется только заранее подготовленный нейтральный текст.
        await send_text_or_photo(
            context.bot,
            CHANNEL_ID,
            item["message"],
            is_cancel=event_is_cancel(item["kind"]),
            kind=item["kind"]
        )
    except TelegramError:
        logger.exception("Не удалось опубликовать событие в канал")
        await query.answer(
            "Не удалось опубликовать. Проверь права бота и CHANNEL_ID.",
            show_alert=True
        )
        return

    state["pending"].pop(pending_id, None)
    state.setdefault("approved", {})[pending_id] = now_msk().isoformat(timespec="seconds")
    record_approved_event(item)
    add_history(item, "published", query.from_user.id)
    save_state()

    failed = await notify_subscribers(
        context.bot,
        item["message"],
        kind=item["kind"]
    )

    try:
        await query.edit_message_text(
            "✅ <b>Событие опубликовано</b>\n\n"
            f"{item['message']}\n\n"
            f"Уведомлено подписчиков: {len(subscribers) - len(failed)}.",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    except (BadRequest, TelegramError):
        pass

    await query.answer("Опубликовано.")

async def reject_event(query, pending_id):
    if query.from_user.id != ADMIN_ID:
        await query.answer(
            "Только администратор может отклонить событие.",
            show_alert=True
        )
        return

    item = state.setdefault("pending", {}).pop(pending_id, None)
    if not item:
        await query.answer(
            "Событие уже обработано или не найдено.",
            show_alert=True
        )
        return

    state.setdefault("rejected", {})[pending_id] = now_msk().isoformat(timespec="seconds")
    add_history(item, "rejected", query.from_user.id)
    save_state()

    try:
        await query.edit_message_text(
            "❌ <b>Событие отклонено</b>\n\n"
            "Оно не отправлено в канал и подписчикам.",
            parse_mode=ParseMode.HTML
        )
    except (BadRequest, TelegramError):
        pass

    await query.answer("Отклонено.")

async def send_backfill_notice(application, name, post, kind, label):
    """
    Отправляет админу компактный готовый текст по найденной старой публикации.
    Исторический характер сохраняется, чтобы сообщение не выглядело текущим.
    """
    published = html.escape(format_event_time(post.get("published_at")))
    title, category = event_title(kind)

    draft = (
        "🛰 <b>UAV ALERT</b>\n"
        "📍 Регион: Костромская область\n\n"
        f"{html.escape(title)}\n"
        f"Категория: {html.escape(category)}\n"
        f"Время исходного сообщения: {published}\n\n"
        "📍 Территории: Костромская область\n\n"
        "⚠️ Информация получена из стороннего источника. "
        "Сверяйте её с актуальными официальными оповещениями МЧС и органов власти.\n\n"
        "<i>Историческая информация.</i>"
    )

    await application.bot.send_message(
        chat_id=ADMIN_ID,
        text=draft,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )

async def monitor_source(application, name, url):
    """
    При старте делает ограниченный просмотр истории за последние N часов.
    Найденные архивные сигналы отправляются только админу как справка:
    они не становятся текущими статусами и не попадают в публикацию.
    Последующие циклы читают только свежую страницу.
    """
    first_scan_after_start = url not in startup_backfill_done
    startup_backfill_done.add(url)

    cutoff = now_msk() - timedelta(hours=HISTORY_BACKFILL_HOURS)
    pages = HISTORY_MAX_PAGES if first_scan_after_start else 1

    posts = await asyncio.to_thread(
        source_posts,
        url,
        pages,
        cutoff if first_scan_after_start else None
    )

    seen_map = state.setdefault("seen", {})
    source_seen_list = seen_map.get(url, [])
    source_seen = set(source_seen_list)
    initialized = bool(state.setdefault("initialized", {}).get(url, False))

    # Сначала обрабатываем только ранее неизвестные публикации.
    for post in posts:
        key = post["key"]
        if key in source_seen:
            continue
        source_seen.add(key)

        published_stamp = parse_iso(post.get("published_at"))
        if first_scan_after_start:
            # Без достоверного времени невозможно понять, попадает ли пост
            # в окно восстановления — такой пост только отмечается как seen.
            if not published_stamp or published_stamp < cutoff:
                continue

        text = post["text"]
        if not mentions_kostroma(text):
            continue

        result = classify(text)
        if not result:
            continue

        kind, label = result

        if first_scan_after_start:
            # Архивные события не предлагаются к публикации.
            if kind not in ("danger", "attention", "cancel"):
                continue
            try:
                await send_backfill_notice(application, name, post, kind, label)
            except TelegramError:
                logger.exception(
                    "Не удалось отправить архивную карточку админу: %s",
                    name
                )
            continue

        # В обычном мониторинге остаётся прежняя ручная модерация.
        await send_event(
            application, kind, label, name, post["url"],
            text, post.get("published_at")
        )

    # При первом запуске отмечаем также старые/неподходящие сообщения,
    # чтобы они не всплывали повторно в следующих циклах.
    ordered = [p["key"] for p in posts if p["key"] in source_seen]
    seen_map[url] = ordered[-500:]
    state.setdefault("initialized", {})[url] = True
    state.setdefault("last_errors", {}).pop(url, None)
    save_state()

def is_safe_regional_news(text):
    """Только общие новости региона без оперативной тематики."""
    return bool(
        text
        and mentions_kostroma(text)
        and not NEWS_SENSITIVE_RE.search(text)
    )

async def monitor_news_source(application, name, url):
    """Новостная лента отдельно от сигналов: только безопасные новости админу."""
    posts = await asyncio.to_thread(source_posts, url)
    seen_map = state.setdefault("news_seen", {})
    source_seen_list = seen_map.get(url, [])
    source_seen = set(source_seen_list)
    initialized = bool(state.setdefault("news_initialized", {}).get(url))

    if not initialized:
        seen_map[url] = [post["key"] for post in posts][-300:]
        state["news_initialized"][url] = True
        state.setdefault("news_last_errors", {}).pop(url, None)
        save_state()
        logger.info(
            "Новостной источник %s инициализирован (%s постов)",
            name,
            len(posts)
        )
        return

    for post in posts:
        if post["key"] in source_seen:
            continue
        source_seen.add(post["key"])

        if not is_safe_regional_news(post["text"]):
            continue

        published = format_event_time(post.get("published_at"))
        news_id = post["key"][:16]
        message = (
            "📰 <b>Региональная новость — на проверку</b>\n"
            f"Источник: {html.escape(name)}\n"
            f"Время публикации: {html.escape(published)}\n\n"
            f"{html.escape(post['text'][:2500])}\n\n"
            "Информация взята из стороннего источника. Проверьте её "
            "самостоятельно. Это не официальное оповещение; "
            "автоматической публикации в канал нет.\n"
            f'<a href="{html.escape(post["url"], quote=True)}">Открыть исходную публикацию</a>'
        )

        try:
            await application.bot.send_message(
                chat_id=ADMIN_ID,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            news_history = state.setdefault("news_history", [])
            news_history.append({
                "id": news_id,
                "source": name,
                "url": post["url"],
                "text": post["text"][:1500],
                "published_at": post.get("published_at"),
                "received_at": now_msk().isoformat(timespec="seconds"),
            })
            state["news_history"] = news_history[-300:]
        except TelegramError:
            logger.exception(
                "Не удалось отправить новость админу: %s",
                name
            )

    ordered = [p["key"] for p in posts if p["key"] in source_seen]
    seen_map[url] = ordered[-300:]
    state.setdefault("news_last_errors", {}).pop(url, None)
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
            if now >= expires.astimezone(MSK) and not item.get("stale", False):
                item["stale"] = True
                item["stale_marked_at"] = now.isoformat(timespec="seconds")
                changed = True
        except (KeyError, TypeError, ValueError):
            item["stale"] = True
            changed = True

    if changed:
        save_state()
