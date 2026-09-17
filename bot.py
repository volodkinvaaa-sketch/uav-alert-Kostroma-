import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")

DEMO_STATUS = True


def menu():
    keyboard = [
        [InlineKeyboardButton("🚨 Статус Костромской области", callback_data="status")],
        [InlineKeyboardButton("📰 Последние сообщения", callback_data="news")],
        [InlineKeyboardButton("ℹ️ О сервисе", callback_data="about")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🚨 UAV ALERT\n\n"
        "Гражданский информационный сервис\n"
        "Костромская область\n\n"
        "Выберите раздел:"
    )

    await update.message.reply_text(
        text,
        reply_markup=menu()
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "status":
        if DEMO_STATUS:
            text = (
                "🚨 СТАТУС\n\n"
                "Костромская область\n"
                "🟢 Опасность не объявлена\n\n"
                "⚠️ Данные демонстрационные.\n"
                "Официальный источник ещё не подключён."
            )
        else:
            text = "Статус временно недоступен."

    elif query.data == "news":
        text = (
            "📰 ПОСЛЕДНИЕ СООБЩЕНИЯ\n\n"
            "Пока сообщений нет.\n\n"
            "⚠️ Данные будут публиковаться только "
            "после подключения проверенного источника."
        )

    elif query.data == "about":
        text = (
            "ℹ️ UAV ALERT\n\n"
            "Гражданский информационный сервис "
            "для Костромской области.\n\n"
            "Сервис предназначен для отображения "
            "официальных предупреждений и сообщений.\n\n"
            "Непроверенная информация не должна "
            "публиковаться как официальная."
        )

    else:
        text = "Неизвестная команда."

    await query.edit_message_text(
        text,
        reply_markup=menu()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Используйте /start, чтобы открыть меню UAV ALERT."
    )


def main():
    if not TOKEN:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Добавьте токен в переменную окружения."
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button))

    print("UAV ALERT запущен!")
    app.run_polling()


if __name__ == "__main__":
    main()
