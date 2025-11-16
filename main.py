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

# ---------------------------------------------------------
#   SISTEMA DE PATH SEGURO QUE NO ROMPE NADA
# ---------------------------------------------------------

if Path("/data").exists():
    BASE_DIR = Path("/data/topics")
else:
    BASE_DIR = Path("/app/storage/topics")

BASE_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = BASE_DIR / "topics.json"
print(f"📁 Usando directorio de almacenamiento: {BASE_DIR}")


# ---------------------------------------------------------
#   CARGA / GUARDA TEMAS
# ---------------------------------------------------------
def load_topics():
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------
#   DETECTAR TEMAS Y GUARDAR MENSAJES
# ---------------------------------------------------------
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    # Solo el grupo configurado
    if msg.chat.id != GROUP_ID:
        return

    # Solo mensajes dentro de un tema
    if msg.message_thread_id is None:
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    # --- NOMBRE REAL DEL TEMA ---
    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{topic_name}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # --- GUARDAR ESTE MENSAJE ---
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ---------------------------------------------------------
#   /TEMAS -> LISTA CON BOTONES
# ---------------------------------------------------------
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()

    if not topics:
        await update.message.reply_text("📭 No hay temas detectados aún.")
        return

    keyboard = [
        [InlineKeyboardButton(data["name"], callback_data=f"t:{tid}")]
        for tid, data in topics.items()
    ]

    await update.message.reply_text(
        "📚 <b>Temas detectados:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------
#   CALLBACK -> REENVIAR CONTENIDO DEL TEMA
# ---------------------------------------------------------
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id_raw = query.data.split(":")
    topic_id = topic_id_raw.strip()

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot
    count = 0

    for msg_info in topics[topic_id]["messages"]:
        try:
            await bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=msg_info["id"],
                protect_content=True,
            )
            count += 1
        except Exception as e:
            print(f"[ERROR] copiando mensaje {msg_info['id']}: {e}")

    await bot.send_message(
        chat_id=query.from_user.id,
        text=f"✔ Fin del contenido del tema. ({count} mensajes)",
    )


# ---------------------------------------------------------
#   /START
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot activo.\n"
        f"• Grupo configurado: <code>{GROUP_ID}</code>\n"
        "• Detecta nuevos temas automáticamente.\n"
        "• Guarda todos los mensajes de cada tema.\n"
        "• Usa /temas en privado para recibirlos.",
        parse_mode="HTML",
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
    print(f"🗂 Archivo de temas: {TOPICS_FILE}")
    app.run_polling()


if __name__ == "__main__":
    main()
