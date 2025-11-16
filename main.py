import os
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

DB_PATH = os.getenv("DB_PATH", "/data/topics.sqlite3")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                topic_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)
        await db.commit()

async def save_topic(topic_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO topics (topic_id, name) VALUES (?, ?)",
            (topic_id, name)
        )
        await db.commit()

async def get_topics():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT topic_id, name FROM topics")
        return await cursor.fetchall()

async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic_id = update.message.message_thread_id
    topic_name = update.message.forum_topic_created.name
    await save_topic(topic_id, topic_name)
    await update.message.reply_text(f"Tema registrado: {topic_name}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = await get_topics()
    if not topics:
        await update.message.reply_text("Todavía no tengo temas registrados.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{tid}")]
        for tid, name in topics
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = int(topic_id)
    group_id = int(os.getenv("GROUP_ID"))

    await query.message.reply_text("Enviando contenido...")

    async for msg in context.bot.get_chat_history(
        chat_id=group_id,
        message_thread_id=topic_id,
        limit=2000
    ):
        try:
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=group_id,
                message_id=msg.message_id
            )
        except:
            pass

async def run_bot():
    await init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN no está configurado")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(select_topic, pattern="^topic:"))
    app.add_handler(MessageHandler(filters.FORUM_TOPIC_CREATED, topic_created))

    print("Bot corriendo en Railway...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(run_bot())
