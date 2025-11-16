import os
import asyncio
import aiosqlite

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
    filters,
)

print("=== BOT VERSION 4.1 ===")

# -------------------------------------
# VARIABLES DE ENTORNO
# -------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/topics.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN no configurado.")


# -------------------------------------
# BASE DE DATOS
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
            (group_id, topic_id, topic_name)
        )
        await db.commit()


async def get_topics(group_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT topic_id, topic_name FROM topics WHERE group_id = ? ORDER BY id ASC",
            (group_id,)
        )
        return await cursor.fetchall()


# -------------------------------------
# /setgroup
# -------------------------------------

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    if msg.chat.type not in ("group", "supergroup"):
        await msg.reply_text("⚠ Usa /setgroup dentro del grupo donde están los temas.")
        return

    group_id = msg.chat_id

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM topics WHERE group_id = ?", (group_id,))
        count = (await cur.fetchone())[0]

    await msg.reply_text(
        f"✔ Grupo registrado.\n\n"
        f"Group ID: `{group_id}`\n"
        f"Temas guardados: **{count}**\n"
        "Ahora usa /temas por privado.",
        parse_mode="Markdown"
    )


# -------------------------------------
# NUEVO TEMA CREADO
# -------------------------------------

async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.forum_topic_created:
        return

    topic = msg.forum_topic_created
    topic_id = msg.message_thread_id
    topic_name = topic.name
    group_id = msg.chat_id

    await save_topic(group_id, topic_id, topic_name)
    await msg.reply_text(f"Tema guardado: {topic_name}")


# -------------------------------------
# /temas
# -------------------------------------

async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Envíame /temas por privado 😉")
        return

    group_id = os.getenv("GROUP_ID")
    if not group_id:
        await update.message.reply_text("⚠ Primero usa /setgroup dentro del grupo.")
        return

    group_id = int(group_id)
    rows = await get_topics(group_id)

    if not rows:
        await update.message.reply_text("No hay temas guardados todavía.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{topic_id}")]
        for topic_id, name in rows
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# -------------------------------------
# Selección de un tema → reenviar mensajes
# -------------------------------------

async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("topic:"):
        return

    topic_id = int(data.split(":")[1])
    group_id = int(os.getenv("GROUP_ID"))
    bot = context.bot

    await bot.send_message(query.from_user.id, "Enviando contenido...")

    try:
        async for msg in bot.get_chat_history(
            chat_id=group_id,
            message_thread_id=topic_id,
            limit=2000,
            oldest_first=True
        ):
            try:
                await bot.forward_message(
                    chat_id=query.from_user.id,
                    from_chat_id=group_id,
                    message_id=msg.message_id,
                    protect_content=True,
                )
                await asyncio.sleep(0.5)
            except:
                pass

        await bot.send_message(query.from_user.id, "✔ Finalizado.")
    except Exception as e:
        await bot.send_message(query.from_user.id, f"❌ Error: {e}")


# -------------------------------------
# post_init ASYNC
# -------------------------------------

async def on_start(app: Application):
    print("Inicializando base de datos…")
    await init_db()
    print("BD lista.")


# -------------------------------------
# MAIN
# -------------------------------------

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(on_start).build()

    # Handlers
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CallbackQueryHandler(select_topic))

    # Tema creado (COMPATIBLE)
    app.add_handler(
        MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, topic_created)
    )

    print("Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
