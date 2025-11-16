import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

DB_FILE = "/app/data/topics.db"
GROUP_ID = None


# ---------------------------
#  BASE DE DATOS
# ---------------------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        # tabla de temas
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER,
                topic_id INTEGER,
                title TEXT
            )
        """)

        # tabla para guardar permanentemente el group_id
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.commit()


async def save_group_id(group_id):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            INSERT INTO config (key, value) 
            VALUES ('group_id', ?) 
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (str(group_id),))
        await db.commit()


async def load_group_id():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT value FROM config WHERE key = 'group_id'")
        row = await cur.fetchone()
        return int(row[0]) if row else None


async def save_topic(group_id, topic_id, title):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO topics (group_id, topic_id, title) VALUES (?, ?, ?)",
            (group_id, topic_id, title)
        )
        await db.commit()


async def get_topics(group_id):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT topic_id, title FROM topics WHERE group_id = ?", (group_id,))
        return await cur.fetchall()


# ---------------------------
#  COMANDOS PRIVADOS
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_ID
    if GROUP_ID is None:
        await update.message.reply_text(
            "Hola! Antes debes hacer /setgroup en tu grupo para configurarlo."
        )
    else:
        await update.message.reply_text(
            "Todo listo. Usa /temas para ver los temas guardados."
        )


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_ID
    if GROUP_ID is None:
        await update.message.reply_text("❌ Aún no hay un grupo configurado. Usa /setgroup en tu grupo.")
        return

    topics = await get_topics(GROUP_ID)
    if not topics:
        await update.message.reply_text("No hay temas guardados todavía.")
        return

    botones = [
        [InlineKeyboardButton(t[1], callback_data=f"topic:{t[0]}")]
        for t in topics
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(botones)
    )


async def boton_seleccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_ID
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    topic_id = int(data[1])

    await query.edit_message_text("Enviando contenido...")

    # reenviar mensaje del tema
    await context.bot.forward_messages(
        chat_id=update.effective_user.id,
        from_chat_id=GROUP_ID,
        message_ids=[topic_id]
    )


# ---------------------------
#  COMANDOS EN GRUPO
# ---------------------------
async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_ID
    GROUP_ID = update.message.chat_id

    await save_group_id(GROUP_ID)

    await update.message.reply_text(
        f"✔ Grupo configurado.\nID guardado: `{GROUP_ID}`",
        parse_mode="Markdown"
    )


async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global GROUP_ID
    if GROUP_ID is None:
        GROUP_ID = update.message.chat_id
        await save_group_id(GROUP_ID)

    topic = update.message.forum_topic_created
    if topic is None:
        return

    topic_id = update.message.message_thread_id
    topic_name = topic.name

    await save_topic(GROUP_ID, topic_id, topic_name)

    await update.message.reply_text(f"✔ Tema guardado: {topic_name} (ID {topic_id})")


# ---------------------------
#  INICIO
# ---------------------------
async def main():
    global GROUP_ID

    await init_db()

    # CARGAR EL GROUP_ID DESDE EL ARCHIVO
    GROUP_ID = await load_group_id()

    print("GROUP_ID cargado:", GROUP_ID)

    app = ApplicationBuilder().token("AQUI_TU_TOKEN").build()

    # privados
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CallbackQueryHandler(boton_seleccion))

    # grupo
    app.add_handler(CommandHandler("setgroup", setgroup, filters.ChatType.GROUP))
    app.add_handler(MessageHandler(filters.FORUM_TOPIC_CREATED, topic_created))

    print("Bot corriendo…")
    await app.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
