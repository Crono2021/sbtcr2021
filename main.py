import os
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters,
)

# ==========================
#   CONFIGURACIÓN
# ==========================

DB_PATH = os.getenv("DB_PATH", "/data/topics.sqlite3")

# Crear carpeta (necesario en Railway)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


# ==========================
#   BASE DE DATOS
# ==========================

async def init_db():
    """Crea la tabla si no existe."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                topic_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
        """)
        await db.commit()


async def save_topic(topic_id: int, name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO topics (topic_id, name) VALUES (?, ?)",
            (topic_id, name)
        )
        await db.commit()


async def get_topics():
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT topic_id, name FROM topics ORDER BY name ASC"
        )
        return await cursor.fetchall()


# ==========================
#   EVENTO: NUEVO TEMA
# ==========================

async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guarda temas cuando se crean."""
    if not update.message or not update.message.forum_topic_created:
        return

    topic_id = update.message.message_thread_id
    topic_name = update.message.forum_topic_created.name

    await save_topic(topic_id, topic_name)
    await update.message.reply_text(f"Tema registrado correctamente: {topic_name}")


async def topic_created_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler genérico que detecta creación de temas."""
    if update.message and update.message.forum_topic_created:
        await topic_created(update, context)


# ==========================
#   COMANDO: /start
# ==========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = await get_topics()

    if not topics:
        await update.message.reply_text("Todavía no tengo temas registrados.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{topic_id}")]
        for topic_id, name in topics
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ==========================
#   COMANDO: /setgroup
# ==========================

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Devuelve el GROUP_ID sin usar Markdown para evitar errores."""
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "Este comando debe ejecutarse dentro de un grupo o supergrupo."
        )
        return

    group_id = chat.id

    await update.message.reply_text(
        f"El GROUP_ID de este grupo es:\n\n{group_id}"
    )


# ==========================
#   BOTONES: SELECCIONAR TEMA
# ==========================

async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("topic:"):
        return

    topic_id = int(data.split(":")[1])

    group_id_str = os.getenv("GROUP_ID")
    if not group_id_str:
        await query.message.reply_text("Error: Falta GROUP_ID en Railway.")
        return

    group_id = int(group_id_str)

    await query.message.reply_text("Enviando contenido...")

    bot = context.bot

    # Recorre el hilo en orden cronológico
    async for msg in bot.get_chat_history(
        chat_id=group_id,
        message_thread_id=topic_id,
        limit=2000,
        oldest_first=True
    ):
        try:
            await bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=group_id,
                message_id=msg.message_id
            )
        except:
            pass


# ==========================
#   MAIN — ARRANQUE ESTABLE PARA RAILWAY
# ==========================

async def main():
    """Arranque estable sin problemas de event loop."""
    await init_db()

    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Error: BOT_TOKEN no está configurado en Railway.")

    application = ApplicationBuilder().token(token).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setgroup", setgroup))
    application.add_handler(CallbackQueryHandler(select_topic, pattern="^topic:"))
    application.add_handler(MessageHandler(filters.ALL, topic_created_filter))

    print("Bot corriendo en Railway...")

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    # Mantener el bot vivo
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
