import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# ===========================
#   CONFIGURACIÓN
# ===========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # YA VIENE FIJO DESDE RAILWAY

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

TOPICS_FILE = os.path.join(DATA_DIR, "topics.json")

# ===========================
#   FUNCIONES DE ARCHIVO
# ===========================

def load_topics():
    if not os.path.exists(TOPICS_FILE):
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# ===========================
#   COMANDOS
# ===========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ¡Hola! Este bot reenvía mensajes desde los temas del grupo configurado.\n"
        "El bot ya está configurado automáticamente mediante Railway (GROUP_ID).\n\n"
        "✔ Crea un tema nuevo en el grupo.\n"
        "✔ Todo mensaje dentro del tema será reenviado a este chat privado."
    )

async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()

    if str(GROUP_ID) not in topics or not topics[str(GROUP_ID)]:
        return await update.message.reply_text("📭 No hay temas registrados todavía.")

    keyboard = []
    for topic_id, title in topics[str(GROUP_ID)].items():
        keyboard.append([InlineKeyboardButton(title, callback_data=f"tema:{topic_id}")])

    await update.message.reply_text(
        "📚 Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ===========================
#   CALLBACK DE TEMAS
# ===========================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("tema:"):
        topic_id = int(data.split(":")[1])

        await query.edit_message_text("📨 Enviando contenido del tema...")

        # Forward messages from topic
        async for msg in context.bot.get_chat_history(
            chat_id=GROUP_ID,
            message_thread_id=topic_id,
            limit=500
        ):
            try:
                # REENVIAR OCULTANDO REMITENTE
                await msg.forward(
                    chat_id=query.message.chat_id,
                    protect_content=True
                )
            except:
                pass

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="✔ Contenido enviado."
        )

# ===========================
#   DETECTAR TEMAS NUEVOS
# ===========================

async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    if update.message.chat_id != GROUP_ID:
        return
    if not update.message.is_topic_message:
        return
    if update.message.message_thread_id is None:
        return

    topic_id = update.message.message_thread_id
    thread_name = update.message.thread_name or "Sin título"

    topics = load_topics()
    if str(GROUP_ID) not in topics:
        topics[str(GROUP_ID)] = {}

    # Si es nuevo tema
    if str(topic_id) not in topics[str(GROUP_ID]]:
        topics[str(GROUP_ID]][str(topic_id)] = thread_name
        save_topics(topics)

        await update.message.reply_text(f"📝 Tema detectado y guardado: {thread_name}")

# ===========================
#   MAIN
# ===========================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos privados
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Callback botones
    app.add_handler(MessageHandler(filters.StatusUpdate.ALL, detect_topic))
    app.add_handler(MessageHandler(filters.ALL & filters.Chat(GROUP_ID), detect_topic))
    app.add_handler(MessageHandler(filters.ALL, detect_topic))

    # Botones inline
    app.add_handler(
        MessageHandler(filters.ALL, detect_topic)
    )
    app.add_handler(
        CommandHandler("start", start)
    )
    app.add_handler(
        CommandHandler("temas", temas)
    )
    app.add_handler(
        MessageHandler(filters.ALL, detect_topic)
    )
    app.add_handler(
        MessageHandler(filters.ALL, detect_topic)
    )

    # CallbackQueryHandler
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
