import os
import json
from pathlib import Path
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

DATA_DIR = Path("/app/storage/topics")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS_FILE = DATA_DIR / "topics.json"


# ---------------------------------------------------------
#   CARGA Y GUARDA ARCHIVO DE TEMAS
# ---------------------------------------------------------
def load_topics():
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------
#   DETECTAR TEMAS NUEVOS Y GUARDAR MENSAJES
# ---------------------------------------------------------
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    if msg.chat.id != GROUP_ID:
        return

    if msg.message_thread_id is None:
        return  # mensaje fuera de topic

    topic_id = str(msg.message_thread_id)

    topics = load_topics()

    if topic_id not in topics:
        # Obtener nombre real del tema
        if msg.reply_to_message and msg.reply_to_message.forum_topic_created:
            topic_name = msg.reply_to_message.forum_topic_created.name
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {
            "name": topic_name,
            "messages": []
        }

    # Guardar el mensaje nuevo del tema
    topics[topic_id]["messages"].append({
        "id": msg.message_id
    })

    save_topics(topics)


# ---------------------------------------------------------
#   COMANDO /TEMAS
# ---------------------------------------------------------
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()

    if not topics:
        await update.message.reply_text("📭 No hay temas detectados aún.")
        return

    keyboard = []
    for tid, data in topics.items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"t:{tid}")])

    await update.message.reply_text(
        "📚 <b>Temas detectados:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------
#   REENVIAR TODOS LOS MENSAJES GUARDADOS DE UN TEMA
# ---------------------------------------------------------
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot

    for msg_info in topics[topic_id]["messages"]:
        try:
            await bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=msg_info["id"],
                protect_content=True   # elimina remitente
            )
        except:
            pass

    await bot.send_message(
        chat_id=query.from_user.id,
        text="✔ Fin del contenido del tema."
    )


# ---------------------------------------------------------
#   /start
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot activo.\n"
        "• Detecta nuevos temas automáticamente.\n"
        "• Reenvía todos los mensajes del tema a tu privado.\n"
        "• Usa /temas para ver la lista completa.",
        parse_mode="HTML"
    )


# ---------------------------------------------------------
#   MAIN
# ---------------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CallbackQueryHandler(send_topic))
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
