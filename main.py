import os
import json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

# ---------------------------
# 🔥 RUTA PERSISTENTE EN RAILWAY
# ---------------------------
BASE_DIR = "/data"
TOPIC_DIR = f"{BASE_DIR}/topics"
TOPICS_FILE = f"{TOPIC_DIR}/topics.json"

os.makedirs(TOPIC_DIR, exist_ok=True)

if not os.path.exists(TOPICS_FILE):
    with open(TOPICS_FILE, "w") as f:
        json.dump({}, f)

def load_topics():
    with open(TOPICS_FILE, "r") as f:
        return json.load(f)

def save_topics(data):
    with open(TOPICS_FILE, "w") as f:
        json.dump(data, f, indent=4)

# -----------------------------------

async def start(update: Update, context: CallbackContext):
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "🤖 ¡Hola!\nEste bot reenviará mensajes desde temas del grupo configurado.\n\n"
            "• Simplemente crea un tema en el grupo.\n"
            "• El bot detectará el tema automáticamente.\n"
            "• Luego podrás pedir el contenido con /temas."
        )

async def temas(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private":
        return

    topics = load_topics().get(str(GROUP_ID), {})

    if not topics:
        await update.message.reply_text("📭 No hay temas detectados todavía.")
        return

    keyboard = [
        [InlineKeyboardButton(f"📌 {name}", callback_data=f"topic_{tid}")]
        for tid, name in topics.items()
    ]

    await update.message.reply_text(
        "📚 *Temas detectados:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def on_topic_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.replace("topic_", ""))
    await query.message.reply_text("📨 Enviando contenido del tema...")

    offset = 0
    while True:
        msgs = await context.bot.get_chat_history(
            chat_id=GROUP_ID,
            limit=100,
            offset_id=offset
        )

        if not msgs:
            break

        msgs_in_topic = [m for m in msgs if m.message_thread_id == topic_id]

        for msg in msgs_in_topic:
            try:
                await msg.forward(update.effective_user.id)
            except:
                pass

        offset = msgs[-1].message_id
        if len(msgs) < 100:
            break

async def detect_topic(update: Update, context: CallbackContext):
    msg = update.message
    if not msg or msg.chat_id != GROUP_ID:
        return

    thread_id = msg.message_thread_id
    if not thread_id:
        return

    topics = load_topics()
    if str(GROUP_ID) not in topics:
        topics[str(GROUP_ID)] = {}

    # → GUARDAMOS EL NOMBRE REAL DEL TEMA
    topic_name = msg.reply_to_message.forum_topic_created.name if msg.reply_to_message and msg.reply_to_message.forum_topic_created else None
    if not topic_name:
        topic_name = f"Tema {thread_id}"

    if str(thread_id) not in topics[str(GROUP_ID)]:
        topics[str(GROUP_ID)][str(thread_id)] = topic_name
        save_topics(topics)

        try:
            await context.bot.send_message(
                GROUP_ID, f"🗂 Tema detectado y guardado: *{topic_name}*",
                parse_mode="Markdown"
            )
        except:
            pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CallbackQueryHandler(on_topic_button))

    app.add_handler(MessageHandler(filters.ALL, detect_topic))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()

if __name__ == "__main__":
    main()
