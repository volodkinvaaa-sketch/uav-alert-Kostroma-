import os
import json
import logging
import asyncio

from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "")

SUBSCRIBERS_FILE = "subscribers.json"
NEWS_FILE = "news.json"

app_web = Flask(__name__)

telegram_app = (
    Application.builder()
    .token(TOKEN)
    .updater(None)
    .build()
)


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def menu():
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
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


def unsubscribe_menu():
    return InlineKeyboardMarkup([
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
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "🚨 UAV ALERT\n\n"
        "📍 Костромская область\n\n"
        "Гражданский информационный сервис "
        "для официальных предупреждений.\n\n"
        "🔔 Подпишитесь, чтобы получать уведомления "
        "о проверенной информации."
    )

    await update.message.reply_text(
        text,
        reply_markup=menu()
    )


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
                "Сообщения будут отправляться "
                "при наличии проверенной официальной информации.",
                reply_markup=unsubscribe_menu()
            )

        else:

            await query.edit_message_text(
                "🔔 Вы уже подписаны на уведомления.",
                reply_markup=unsubscribe_menu()
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
            reply_markup=menu()
        )

    elif query.data == "status":

        await query.edit_message_text(
            "🚨 СТАТУС\n\n"
            "📍 Костромская область\n\n"
            "🟢 Опасность не объявлена\n\n"
            "При появлении проверенного "
            "официального предупреждения "
            "оно будет опубликовано здесь.",
            reply_markup=menu()
        )

    elif query.data == "news":

        news = load_json(
            NEWS_FILE,
            []
        )

        if not news:

            text = (
                "📰 ПОСЛЕДНИЕ СООБЩЕНИЯ\n\n"
                "Сообщений пока нет.\n\n"
                "Непроверенная информация "
                "не публикуется как официальная."
            )

        else:

            text = "📰 ПОСЛЕДНИЕ СООБЩЕНИЯ\n\n"

            for item in news[-5:]:

                text += (
                    f"⚠️ {item['text']}\n"
                    f"Источник: {item['source']}\n\n"
                )

        await query.edit_message_text(
            text,
            reply_markup=menu()
        )

    elif query.data == "about":

        await query.edit_message_text(
            "ℹ️ UAV ALERT\n\n"
            "Гражданский информационный сервис "
            "по Костромской области.\n\n"
            "Сервис предназначен для публикации "
            "проверенных официальных предупреждений.\n\n"
            "Непроверенные сообщения и данные "
            "неофициальных источников не выдаются "
            "за официальную информацию.",
            reply_markup=menu()
        )


async def alert(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if update.effective_user.id != ADMIN_ID:

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n\n"
            "/alert Текст предупреждения | Источник\n\n"
            "Например:\n"
            "/alert Объявлена беспилотная опасность | Официальное сообщение"
        )

        return

    raw = " ".join(context.args)

    if "|" not in raw:

        await update.message.reply_text(
            "❌ Нужно указать источник через символ |"
        )

        return

    alert_text, source = raw.split(
        "|",
        1
    )

    alert_text = alert_text.strip()
    source = source.strip()

    if not alert_text or not source:

        await update.message.reply_text(
            "❌ Нужно указать текст предупреждения "
            "и источник."
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

    subscribers = load_json(
        SUBSCRIBERS_FILE,
        []
    )

    sent = 0

    for user_id in subscribers:

        try:

            await context.bot.send_message(
                chat_id=user_id,
                text=message
            )

            sent += 1

        except Exception as e:

            logging.warning(
                f"Не удалось отправить {user_id}: {e}"
            )

    await update.message.reply_text(
        f"✅ Сообщение отправлено.\n\n"
        f"👥 Получателей: {sent}"
    )


@app_web.get("/")
def home():

    return "UAV ALERT работает!"

@app_web.post("/telegram")
async def telegram_webhook():

    data = request.get_json(
        force=True
    )

    update = Update.de_json(
        data,
        bot=telegram_app.bot
    )

    await telegram_app.update_queue.put(
        update
    )

    return "OK"



async def setup():

    await telegram_app.initialize()

    await telegram_app.start()

    if not RENDER_URL:

        raise RuntimeError(
            "RENDER_EXTERNAL_URL не найден."
        )

    webhook_url = (
        f"{RENDER_URL}/telegram"
    )

    await telegram_app.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES
    )

    logging.info(
        f"Webhook установлен: {webhook_url}"
    )


def main():

    telegram_app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    telegram_app.add_handler(
        CommandHandler(
            "alert",
            alert
        )
    )

    telegram_app.add_handler(
        CallbackQueryHandler(
            button
        )
    )

    asyncio.run(
        setup()
    )

    app_web.run(
        host="0.0.0.0",
        port=PORT
    )


if __name__ == "__main__":
    main()
