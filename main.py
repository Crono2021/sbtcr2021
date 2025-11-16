import os
import aiosqlite
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

DB_PATH = "topics.db"
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # O ponlo manualmente aquí


# ------------------ BASE DE DATOS ------------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                topic_id INTEGER,
                topic_name TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER,
                msg_id INTEGER
            )
        """)
        await db.commit()


async def save_topic(group_id, topic_id, name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO topics (group_id, topic_id, topic_name) VALUES (?, ?, ?)",
            (group_id, topic_id, name),
        )
        await db.commit()


async def save_message(topic_id, msg_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (topic_id, msg_id) VALUES (?, ?)",
            (topic_id, msg_id),
        )
        await db.commit()


async def get_topics(group_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT topic_id, topic_name FROM topics WHERE group_id = ?", (group_id,)
        )
        return await cur.fetchall()


async def get_messages(topic_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT msg_id FROM messages WHERE topic_id = ?", (topic_id,)
        )
        return await cur.fetchall()


# ------------------ COMANDOS ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola! Usa /setgroup para guardar este chat y /temas para ver los temas guardados."
    )


async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.message.chat_id

    await update.message.reply_text(
        f"✔ Grupo configurado.\nID guardado: `{gid}`",
        parse_mode="Markdown"
    )

    context.chat_data["group_id"] = str(gid)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = context.chat_data.get("group_id")

    if not gid:
        await update.message.reply_text("Primero usa /setgroup en el grupo.")
        return

    topics = await get_topics(gid)

    if not topics:
        await update.message.reply_text("No hay temas guardados aún.")
        return

    botones = [[KeyboardButton(f"{name} ({tid})")] for tid, name in topics]
    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=ReplyKeyboardMarkup(botones, one_time_keyboard=True, resize_keyboard=True)
    )


# ------------------ MANEJO DE TEMAS ------------------

async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if not m or not m.is_topic_message:
        return

    gid = str(m.chat_id)
    tid = m.message_thread_id
    name = m.reply_to_message.text if m.reply_to_message else "Sin nombre"

    await save_topic(gid, tid, name)
    print(f"[OK] Tema guardado: {name} ({tid})")


# ------------------ MENSAJES DENTRO DE TEMAS ------------------

async def topic_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    m = update.message
    if not m or not m.is_topic_message:
        return  # ignorar todo lo que no sea mensaje de tema

    tid = m.message_thread_id
    mid = m.message_id

    await save_message(tid, mid)

    print(f"[OK] Mensaje detectado en topic {tid}: msg_id={mid}")


# ------------------ REENVÍO DE CONTENIDO ------------------

async def mensaje_seleccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Si el usuario selecciona un tema del teclado, reenviamos su contenido."""
    texto = update.message.text

    # Buscar topic por nombre dentro de BD
    gid = context.chat_data.get("group_id")
    if not gid:
        return

    topics = await get_topics(gid)
    mapping = {f"{name} ({tid})": tid for tid, name in topics}

    if texto not in mapping:
        return  # no es un tema

    topic_id = mapping[texto]

    await update.message.reply_text("Enviando contenido...")

    msgs = await get_messages(topic_id)

    for (mid,) in msgs:
        try:
            await context.bot.forward_message(
                chat_id=update.message.chat_id,
                from_chat_id=gid,
                message_id=mid
            )
        except Exception as e:
            print("Error reenviando:", e)


# ------------------ MAIN ------------------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Inicializar BD antes de arrancar
    app.post_init = lambda _: init_db()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", temas))

    # Detección de creación de tema (primer mensaje del topic)
    app.add_handler(MessageHandler(filters.ALL, topic_created))

    # Capturar **TODOS** los mensajes dentro de un topic
    app.add_handler(MessageHandler(filters.ALL, topic_message))

    # Detección de selección de tema
    app.add_handler(MessageHandler(filters.TEXT, mensaje_seleccion))

    print("Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
