import os
import aiosqlite
import asyncio

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
DB_PATH = os.getenv("DB_PATH", "/data/topics.sqlite3")


# -------------------------------------
# BASE DE DATOS (RESETEA TABLA SI CAMBIA)
# -------------------------------------

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL
            )
        """)
        await db.commit()


async def save_topic(group_id, topic_id, topic_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO topics (group_id, topic_id, topic_name) VALUES (?, ?, ?)",
            (group_id, topic_id, topic_name),
        )
        await db.commit()


async def get_topics(group_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT topic_id, topic_name FROM topics WHERE group_id=? ORDER BY id ASC",
            (group_id,),
        )
        return await cur.fetchall()


# -------------------------------------
# HANDLERS
# -------------------------------------

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type not in ("group", "supergroup"):
        return await update.message.reply_text("Usa este comando dentro del grupo.")

    gid = update.effective_chat.id
    await update.message.reply_text(f"GROUP_ID: `{gid}`", parse_mode="Markdown")


async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.forum_topic_created:
        return

    topic_name = msg.forum_topic_created.name
    topic_id = msg.message_thread_id
    group_id = msg.chat_id

    await save_topic(group_id, topic_id, topic_name)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not GROUP_ID:
        return await update.message.reply_text("Primero usa /setgroup en el grupo.")

    topics = await get_topics(int(GROUP_ID))
    if not topics:
        return await update.message.reply_text("No hay temas guardados.")

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{tid}")]
        for tid, name in topics
    ]

    await update.message.reply_text("Selecciona un tema:", reply_markup=InlineKeyboardMarkup(keyboard))


async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tid = int(query.data.split(":")[1])
    gid = int(GROUP_ID)

    await context.bot.send_message(query.from_user.id, "Enviando contenido…")

    async for msg in context.bot.get_chat_history(
        chat_id=gid,
        message_thread_id=tid,
        oldest_first=True,
        limit=2000
    ):
        try:
            await context.bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=gid,
                message_id=msg.message_id,
                protect_content=True,
            )
            await asyncio.sleep(0.3)
        except:
            pass


# -------------------------------------
# MAIN
# -------------------------------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CallbackQueryHandler(select_topic))
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, topic_created))

    print("Bot corriendo en Railway…")

    # Crear DB antes de arrancar polling
    asyncio.get_event_loop().run_until_complete(init_db())

    app.run_polling()


if __name__ == "__main__":
    main()
