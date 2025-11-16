import os
import sqlite3
from contextlib import closing

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# -----------------------------
# CONFIG
# -----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Falta la variable de entorno BOT_TOKEN.")

DB_PATH = "/data/topics.sqlite3"


# -----------------------------
# DB HELPERS
# -----------------------------
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                topic_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
            """
        )
        conn.commit()
    print("✔ Base de datos inicializada:", DB_PATH)


def set_group_id(group_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO config (key, value) VALUES ('group_id', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(group_id),),
        )
        conn.commit()


def get_group_id():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key = 'group_id'")
        r = cur.fetchone()
        return int(r[0]) if r else None


def save_topic(topic_id, name):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO topics (topic_id, name) VALUES (?, ?) "
            "ON CONFLICT(topic_id) DO UPDATE SET name = excluded.name",
            (topic_id, name),
        )
        conn.commit()
    print(f"✔ Tema guardado: {name} (ID {topic_id})")


def get_topics():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT topic_id, name FROM topics ORDER BY name")
        return cur.fetchall()


def save_message(topic_id, message_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO messages (topic_id, message_id) VALUES (?, ?)",
            (topic_id, message_id),
        )
        conn.commit()


def get_message_ids(topic_id):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT message_id FROM messages WHERE topic_id = ? ORDER BY id",
            (topic_id,),
        )
        return [r[0] for r in cur.fetchall()]


# -----------------------------
# HANDLERS
# -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hola!\nUsa /setgroup en el grupo y /temas en privado."
    )


async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Este comando solo funciona dentro del grupo.")
        return

    set_group_id(chat.id)
    await update.message.reply_text(
        f"✔ Grupo configurado.\nID guardado: `{chat.id}`",
        parse_mode="Markdown",
    )
    print("Grupo configurado:", chat.id)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = get_topics()
    if not topics:
        await update.message.reply_text("No hay temas guardados.")
        return

    kb = [
        [InlineKeyboardButton(name, callback_data=f"topic:{tid}")]
        for tid, name in topics
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def topic_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    data = q.data
    if not data.startswith("topic:"):
        return

    topic_id = int(data.split(":")[1])
    group_id = get_group_id()

    if not group_id:
        await q.edit_message_text("❌ No hay un grupo configurado.")
        return

    msg_ids = get_message_ids(topic_id)
    if not msg_ids:
        await q.edit_message_text("Este tema no tiene mensajes.")
        return

    await q.edit_message_text("Reenviando contenido...")

    for mid in msg_ids:
        try:
            await context.bot.forward_message(
                chat_id=q.message.chat_id,
                from_chat_id=group_id,
                message_id=mid,
            )
        except Exception as e:
            print("⚠ Error reenviando:", e)

    await context.bot.send_message(
        chat_id=q.message.chat_id,
        text="✔ Contenido reenviado."
    )


async def group_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Nuevo tema
    if msg.forum_topic_created:
        save_topic(msg.message_thread_id, msg.forum_topic_created.name)
        return

    # Mensajes dentro de un tema
    if msg.is_topic_message and msg.message_thread_id:
        save_message(msg.message_thread_id, msg.message_id)


# -----------------------------
# MAIN
# -----------------------------
def main():
    print("=== BOT TOPIC FORWARD v1 ===")
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", temas))

    app.add_handler(CallbackQueryHandler(topic_button))

    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.ALL, group_activity))

    print("🤖 Bot corriendo en Railway...")
    app.run_polling()


if __name__ == "__main__":
    main()
