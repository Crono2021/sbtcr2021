import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# =============================
# Configuración
# =============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # Configuración desde Railway

DATA_DIR = "/app/data"
os.makedirs(DATA_DIR, exist_ok=True)

TOPICS_FILE = os.path.join(DATA_DIR, "topics.json")


# =============================
# Manejo de archivos
# =============================

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


# =============================
# Comandos
# =============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Bot listo*\n"
        "Reenviaré por privado los mensajes enviados dentro de cada tema del grupo.\n\n"
        "✔ El bot ya está configurado con el GROUP_ID desde Railway.\n"
        "✔ Crea un tema en el grupo y envía mensajes.\n"
        "✔ Usa /temas para ver la lista de temas guardados.",
        parse_mode="Markdown"
    )


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()
    group_id_str = str(GROUP_ID)

    if group_id_str not in topics or not topics[group_id_str]:
        return await update.message.reply_text("📭 No hay temas almacenados todavía.")

    keyboard = [
        [InlineKeyboardButton(title, callback_data=f"topic:{tid}")]
        for tid, title in topics[group_id_str].items()
    ]

    await update.message.reply_text(
        "📚 Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =============================
# Callback: Enviar contenido del tema
# =============================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("topic:"):
        return

    topic_id = int(data.split(":")[1])

    await query.edit_message_text("📨 Enviando mensajes del tema...")

    async for msg in context.bot.get_chat_history(
        chat_id=GROUP_ID,
        message_thread_id=topic_id,
        limit=500
    ):
        try:
            await msg.forward(
                chat_id=query.message.chat_id,
                protect_content=True  # Oculta remitente
            )
        except:
            pass

    await context.bot.send_message(query.message.chat_id, "✔ Contenido enviado.")


# =============================
# Detectar temas nuevos
# =============================

async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return
    if msg.chat_id != GROUP_ID:
        return
    if not msg.is_topic_message:
        return

    topic_id = msg.message_thread_id
    topic_name = msg.thread_name or "Sin título"

    topics = load_topics()
    group_id_str = str(GROUP_ID)

    if group_id_str not in topics:
        topics[group_id_str] = {}

    if str(topic_id) not in topics[group_id_str]:
        topics[group_id_str][str(topic_id)] = topic_name
        save_topics(topics)

        await msg.reply_text(f"📝 Tema detectado y guardado: *{topic_name}*", parse_mode="Markdown")


# =============================
# Main
# =============================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Recoger nuevos temas
    app.add_handler(MessageHandler(filters.ALL, detect_topic))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    print("🤖 Bot corriendo en Railway...")
    app.run_polling()


if __name__ == "__main__":
    main()
