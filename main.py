import os
import json
import html
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

# Carpeta persistente
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


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

    if msg.chat.id != GROUP_ID:
        return

    if msg.message_thread_id is None:
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{html.escape(topic_name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ---------------------------------------------------------
#   /TEMAS -> LISTA CON BOTONES
# ---------------------------------------------------------
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return

    try:
        topics = load_topics()

        if not topics:
            await chat.send_message("📭 No hay temas detectados aún.")
            return

        keyboard = []
        for tid, data in topics.items():
            safe_name = html.escape(data["name"])
            keyboard.append(
                [InlineKeyboardButton(f"📌 {safe_name}", callback_data=f"t:{tid}")]
            )

        await chat.send_message(
            "📚 <b>Temas detectados:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        await chat.send_message(f"❌ Error en /temas: {e}")
        print("[/temas] ERROR:", e)


# ---------------------------------------------------------
#   CALLBACK → ENVIAR CONTENIDO
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
#   /BORRARTEMA -> LISTA DE TEMAS PARA ELIMINAR
# ---------------------------------------------------------
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("Usa /borrartema en privado.")
        return

    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = html.escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")]
        )

    await chat.send_message(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------
#   CALLBACK → ELIMINAR TEMA
# ---------------------------------------------------------
async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    deleted_name = topics[topic_id]["name"]

    del topics[topic_id]
    save_topics(topics)

    await query.edit_message_text(
        f"🗑 Tema eliminado:\n<b>{html.escape(deleted_name)}</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------
#   /START
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot activo.\n"
        f"• Grupo configurado: <code>{GROUP_ID}</code>\n"
        "• Detecta temas automáticamente.\n"
        "• Usa /temas para verlos.\n"
        "• Usa /borrartema para eliminarlos.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------
#   MAIN
# ---------------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CommandHandler("borrartema", borrartema))

    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
