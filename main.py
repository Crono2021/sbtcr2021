import os
import asyncio
import aiosqlite
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# =====================
#  CONFIGURACIÓN
# =====================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ ERROR: Falta la variable BOT_TOKEN en Railway.")

DB_PATH = "/data/bot.db"  # Volumen persistente en Railway

# =====================
#  INICIALIZACIÓN DB
# =====================
async def init_db():
    os.makedirs("/data", exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS topics (
                topic_id INTEGER PRIMARY KEY,
                topic_name TEXT NOT NULL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL
            )
        """)

        await db.commit()
        print("✔ Base de datos lista")


# =====================
#  GUARDAR NUEVO TEMA
# =====================
async def save_topic(topic_id: int, topic_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO topics (topic_id, topic_name) VALUES (?, ?)",
            (topic_id, topic_name)
        )
        await db.commit()
        print(f"✔ Tema guardado: {topic_name} ({topic_id})")


# =====================
#  GUARDAR MENSAJES
# =====================
async def save_message(topic_id: int, message_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (topic_id, message_id) VALUES (?, ?)",
            (topic_id, message_id)
        )
        await db.commit()
        print(f"💾 Mensaje {message_id} guardado en topic {topic_id}")


# =====================
#  HANDLER: COMANDO /setgroup
# =====================
async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    gid = update.message.chat_id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM topics")
        await db.execute("DELETE FROM messages")
        await db.commit()

    await update.message.reply_text("✔ Grupo configurado correctamente.")
    print(f"GRUPO REGISTRADO: {gid}")


# =====================
#  HANDLER: COMANDO /temas
# =====================
async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT topic_id, topic_name FROM topics ORDER BY topic_name")
        rows = await cursor.fetchall()

    if not rows:
        await update.message.reply_text("No hay temas registrados todavía.")
        return

    text = "📚 *Temas disponibles:*\n\n"
    for t_id, t_name in rows:
        text += f"• `{t_id}` — {t_name}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# =====================
#  HANDLER: COMANDO /ver <topic_id>
# =====================
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) == 0:
        await update.message.reply_text("Uso correcto:\n/ver <topic_id>")
        return

    try:
        topic_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("El topic_id debe ser número.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT message_id FROM messages WHERE topic_id = ? ORDER BY id",
            (topic_id,)
        )
        msgs = await cursor.fetchall()

    if not msgs:
        await update.message.reply_text("Ese tema no tiene mensajes guardados.")
        return

    await update.message.reply_text("📨 Enviando mensajes del tema...")

    # reenviar mensajes ocultando remitente
    for (msg_id,) in msgs:
        try:
            await context.bot.forward_message(
                chat_id=update.effective_chat.id,
                from_chat_id=update.effective_chat.id,  # no importa origen, se oculta
                message_id=msg_id
            )
        except:
            pass


# =====================
#  DETECCIÓN DE TEMAS CREADOS (SIN FILTROS ESPECIALES)
# =====================
async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if msg.forum_topic_created:
        topic_id = msg.message_thread_id
        topic_name = msg.forum_topic_created.name
        await save_topic(topic_id, topic_name)


# =====================
#  DETECTAR MENSAJES DENTRO DE UN TEMA
# =====================
async def detect_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.message_thread_id:
        return  # No pertenece a un tema

    await save_message(msg.message_thread_id, msg.message_id)


# =====================
#  MAIN
# =====================
def main():
    print("=== BOT VERSION 6.0 ===")

    asyncio.run(init_db())

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # comandos
    app.add_handler(CommandHandler("setgroup", setgroup))
    app.add_handler(CommandHandler("temas", list_topics))
    app.add_handler(CommandHandler("ver", send_topic))

    # detecta creación de temas
    app.add_handler(MessageHandler(filters.ALL, detect_topic))

    # detecta mensajes dentro de temas
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, detect_message))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
