import os
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# ===== CONFIG =====
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_FILE = "/data/topics.db"   # ← Railway volume correcto


# ===== DB INIT =====
async def init_db():
    print("Inicializando base de datos...")

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                topic_name TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                message_id INTEGER
            )
        """)

        await db.commit()

    print("✔ Base de datos lista")


# ===== GUARDAR TOPIC =====
async def save_topic(topic_id, topic_name):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO topics (topic_id, topic_name) VALUES (?, ?)",
            (topic_id, topic_name)
        )
        await db.commit()

    print(f"✔ Tema guardado: {topic_name} ({topic_id})")


# ===== GUARDAR MENSAJE =====
async def save_message(topic_id, message_id):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO messages (topic_id, message_id) VALUES (?, ?)",
            (topic_id, message_id)
        )
        await db.commit()

    print(f"✔ Mensaje guardado en topic {topic_id}: {message_id}")


# ===== COMANDO /setgroup =====
async def setgroup(update: Update, context: CallbackContext):
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Este comando solo funciona en grupos.")
        return

    gid = chat.id

    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('group_id', ?)",
            (str(gid),)
        )
        await db.commit()

    await update.message.reply_text(f"✓ Grupo configurado.\nID guardado: {gid}")


# ===== COMANDO /start =====
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "Hola! Usa /temas para ver los temas guardados."
    )


# ===== LISTAR TEMAS =====
async def temas(update: Update, context: CallbackContext):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT topic_id, topic_name FROM topics")
        rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("No hay temas guardados.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic_{tid}")]
        for tid, name in rows
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ===== CALLBACK: USUARIO SELECCIONA TEMA =====
async def topic_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.replace("topic_", ""))

    await query.edit_message_text("Enviando contenido...")

    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT message_id FROM messages WHERE topic_id = ?", (topic_id,))
        msg_ids = await cur.fetchall()

    if not msg_ids:
        await query.edit_message_text("Este tema no tiene mensajes.")
        return

    # Reenviar SIN remitente
    for (mid,) in msg_ids:
        try:
            await context.bot.forward_message(
                chat_id=query.message.chat_id,
                from_chat_id=(await get_group_id()),
                message_id=mid
            )
        except Exception as e:
            print("Error reenviando:", e)

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="✓ Contenido enviado."
    )


# ===== OBTENER GROUP ID DE DB =====
async def get_group_id():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT value FROM config WHERE key='group_id'")
        row = await cur.fetchone()

    return int(row[0]) if row else None


# ===== DETECTAR TEMAS CREADOS =====
async def topic_created(update: Update, context: CallbackContext):
    msg = update.message

    if not msg or not msg.forum_topic_created:
        return

    topic_name = msg.forum_topic_created.name
    topic_id = msg.message_thread_id

    await save_topic(topic_id, topic_name)


# ===== DETECTAR MENSAJES DENTRO DE TEMAS =====
async def save_incoming_message(update: Update, context: CallbackContext):
    msg = update.message

    if not msg or not msg.message_thread_id:
        return

    topic_id = msg.message_thread_id
    await save_message(topic_id, msg.message_id)


# ===== MAIN =====
def main():
    print("=== BOT VERSION 5.0 ===")

    asyncio.run(init_db())

    app = Application.builder().token(BOT_TOKEN).build()

    # comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", temas))

    # topicos
    app.add_handler(MessageHandler(filters.FORUM_TOPIC_CREATED, topic_created))

    # mensajes dentro de topicos
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, save_incoming_message))

    # callback inline
    app.add_handler(CallbackQueryHandler(topic_callback))

    print("Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
