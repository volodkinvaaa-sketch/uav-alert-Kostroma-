import os
import json
import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
# =========================
# НАСТРОЙКИ
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID_TEXT = os.getenv("ADMIN_ID", "0")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "")
PORT = int(os.getenv("PORT", "10000"))
try:
    ADMIN_ID = int(ADMIN_ID_TEXT)
except ValueError:
    ADMIN_ID = 0
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)
# =========================
# WEB-СЕРВЕР
# =========================
web = Flask(__name__)
@web.route("/")
def home():
    return "UAV ALERT работает!"
def start_web():
    web.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )
# =========================
# ФАЙЛЫ
# =========================
SUBSCRIBERS_FILE = "subscribers.json"
NEWS_FILE = "news.json"
def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default
def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2
            )
    except Exception as error:
        logger.error(f"Ошибка сохранения: {error}")
# =========================
# МЕНЮ
# =========================
def main_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "🔔 Подписаться",
                callback_data="subscribe"
            )
        ],
        [
            InlineKeyboardButton(
                "🚨 Статус",
                callback_data="status"
            ),
            InlineKeyboardButton(
                "📰 Сообщения",
                callback_data="news"
            )
        ],
        [
            InlineKeyboardButton(
                "ℹ️ О сервисе",
                callback_data="about"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
def subscribed_menu():
    keyboard = [
        [
            InlineKeyboardButton(
                "🔕 Отписаться",
                callback_data="unsubscribe"
            )
        ],
        [
            InlineKeyboardButton(
                "🚨 Статус",
                callback_data="status"
            )
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
# =========================
# /START
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        "Гражданский информационный сервис "
        "для получения проверенных официальных "
        "предупреждений.\n\n"
        "Выберите действие:",
        reply_markup=main_menu()
    )
# =========================
# КНОПКИ
# =========================
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )
    if query.data == "subscribe":
        if user_id not in subscribers:
            subscribers.append(user_id)
            save_json(
                SUBSCRIBERS_FILE,
                subscribers
            )
            await query.edit_message_text(
                "🔔 Вы подписались на уведомления!\n\n"
                "📍 Костромская область\n\n"
                "При появлении проверенного "
                "официального предупреждения "
                "вы получите уведомление.",
                reply_markup=subscribed_menu()
            )
        else:
            await query.edit_message_text(
                "🔔 Вы уже подписаны.",
                reply_markup=subscribed_menu()
            )
    elif query.data == "unsubscribe":
        if user_id in subscribers:
            subscribers.remove(user_id)
        save_json(
            SUBSCRIBERS_FILE,
            subscribers
        )
        await query.edit_message_text(
            "🔕 Вы отписались от уведомлений.",
            reply_markup=main_menu()
        )
    elif query.data == "status":
        await query.edit_message_text(
            "🚨 СТАТУС\n\n"
            "📍 Костромская область\n\n"
            "🟢 Опасность не объявлена\n\n"
            "Статус изменяется после "
            "получения подтверждённой "
            "официальной информации.",
            reply_markup=main_menu()
        )
    elif query.data == "news":
        news = load_json(
            NEWS_FILE,
            []
        )
        if not news:
            text = (
                "📰 ПОСЛЕДНИЕ СООБЩЕНИЯ\n\n"
                "Сообщений пока нет."
            )
        else:
            text = "📰 ПОСЛЕДНИЕ СООБЩЕНИЯ\n\n"
            for item in news[-5:]:
                text += (
                    f"⚠️ {item.get('text', '')}\n"
                    f"Источник: {item.get('source', '')}\n\n"
                )
        await query.edit_message_text(
            text,
            reply_markup=main_menu()
        )
    elif query.data == "about":
        await query.edit_message_text(
            "ℹ️ UAV ALERT\n\n"
            "Гражданский информационный сервис "
            "по Костромской области.\n\n"
            "Публикуется только проверенная "
            "официальная информация.",
            reply_markup=main_menu()
        )
# =========================
# /ALERT
# =========================
async def alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return
    if not context.args:
        await update.message.reply_text(
            "Использование:\n\n"
            "/alert Текст | Источник"
        )
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text(
            "❌ Используй символ | между текстом и источником."
        )
        return
    alert_text, source = raw.split("|", 1)
    alert_text = alert_text.strip()
    source = source.strip()
    if not alert_text or not source:
        await update.message.reply_text(
            "❌ Заполни текст и источник."
        )
        return
    message = (
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        f"⚠️ {alert_text}\n\n"
        f"Источник: {source}\n\n"
        "Следуйте официальным указаниям "
        "экстренных служб."
    )
    # Сохраняем сообщение
    news = load_json(
        NEWS_FILE,
        []
    )
    news.append({
        "text": alert_text,
        "source": source
    })
    save_json(
        NEWS_FILE,
        news[-20:]
    )
    # Отправляем подписчикам
    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )
    sent = 0
    for subscriber_id in subscribers:
        try:
            await context.bot.send_message(
                chat_id=subscriber_id,
                text=message
            )
            sent += 1
        except Exception as error:
            logger.warning(
                f"Не удалось отправить {subscriber_id}: {error}"
            )
    # Отправляем в канал
    channel_sent = False
    if CHANNEL_USERNAME:
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_USERNAME,
                text=message
            )
            channel_sent = True
        except Exception as error:
            logger.error(
                f"Ошибка отправки в канал: {error}"
            )
    await update.message.reply_text(
        "✅ Сообщение обработано.\n\n"
        f"👥 Подписчиков уведомлено: {sent}\n"
        f"📢 Канал: {'отправлено' if channel_sent else 'не отправлено'}"
    )
# =========================
# ЗАПУСК
# =========================
def main():
    print("")
    print("==============================")
    print("🚨 UAV ALERT")
    print("==============================")
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не задан."
        )
    if ADMIN_ID == 0:
        raise RuntimeError(
            "ADMIN_ID не задан или указан неправильно."
        )
    if not CHANNEL_USERNAME:
        logger.warning(
            "CHANNEL_USERNAME не задан. "
            "Сообщения в канал отправляться не будут."
        )
    print("✅ Настройки найдены")
    # Веб-сервер Render
    web_thread = threading.Thread(
        target=start_web,
        daemon=True
    )
    web_thread.start()
    print("✅ Web-сервер запущен")
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
            "alert",
            alert
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            button
        )
    )
    print("🤖 Бот запускается...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )
if __name__ == "__main__":
    main()
