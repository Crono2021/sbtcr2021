import os
import json
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ForumTopicCreated
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ============================================================
# CONFIG
# ============================================================

GROUP_ID = int(os.getenv("GROUP_ID"))  # ← SE LEE DE RAILWAY
DATA_DIR = "/data"
DATA_FILE = os.path.join(DATA_DIR, "topics.json")

BOT_TOKEN = os.getenv("BOT_TOKEN")


# ============================================================
# FUNCIONES JSON
# ============================================================

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"topics": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"topics": {}}


def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ============================================================
# HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hola! Usa /temas para ver los temas guardados.")


async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if not msg or msg.chat_id != GROUP_ID:
        return

    if not msg.forum_topic_created:
        return

    topic: ForumTopicCreated = msg.forum_topic_created
    topic_id = msg.message_thread_id
    topic_name = topic.name

    data = load_data()
    data["topics"][str(topic_id)] = topic_name
    save_data(data)

    await msg.reply_text(f"📝 Tema guardado: *{topic_name}*", parse_mode="Markdown")


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    topics = data.get("topics", {})

    if not topics:
        await update.message.reply_text("No hay temas guardados aún.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{tid}")]
        for tid, name in topics.items()
    ]

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def enviar_tema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split(":")[1])

    await query.edit_message_text("Enviando contenido...")

    # Obtener mensajes del tema
    try:
        messages = await context.bot.get_forum_topic_messages(GROUP_ID, topic_id)
    except:
        await query.edit_message_text("❌ No se pudo obtener el contenido del tema.")
        return

    # Enviar cada mensaje como forward (oculta remitente automáticamente)
    for m in messages:
        try:
            await context.bot.forward_message(
                chat_id=query.message.chat_id,
                from_chat_id=GROUP_ID,
                message_id=m.message_id
            )
        except:
            pass


# ============================================================
# MAIN
# ============================================================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Detecta creación de temas
    app.add_handler(MessageHandler(filters.FORUM_TOPIC_CREATED, topic_created))

    # Para seleccionar tema en privado
    app.add_handler(CallbackQueryHandler(enviar_tema, pattern=r"topic:"))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
