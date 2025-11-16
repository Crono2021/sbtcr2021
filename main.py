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

# Carpeta PERSISTENTE de Railway: tu volumen está montado en /data
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

        # Solo avisamos una vez cuando detectamos el tema
        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{html.escape(topic_name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # --- GUARDAR ESTE MENSAJE ---
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ---------------------------------------------------------
#   /TEMAS -> LISTA CON BOTONES
#   (ÚNICA FUNCIÓN QUE HE TOCADO)
# ---------------------------------------------------------
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    message = update.effective_message

    # Lo normal: usar /temas en privado
    if chat.type != "private":
        await message.reply_text("Usa /temas en el chat privado con el bot.")
        return

    try:
        topics = load_topics()

        if not topics:
            await chat.send_message("📭 No hay temas detectados aún.")
            return

        keyboard = []
        for tid, data in topics.items():
            name = data.get("name", f"Tema {tid}")
            # Evitamos que caracteres raros rompan el HTML
            safe_name = html.escape(name)
            keyboard.append(
                [InlineKeyboardButton(safe_name, callback_data=f"t:{tid}")]
            )

        await chat.send_message(
            "📚 <b>Temas detectados:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        # Para que NO se quede callado si algo falla
        await chat.send_message(f"❌ Error en /temas: {e}")
        print("[/temas] ERROR:", e)


# ---------------------------------------------------------
#   CALLBACK -> REENVIAR CONTENIDO DEL TEMA
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
            # copy_message => SIN remitente (“enviado por el bot”)
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
        "• Guarda todos los mensajes que se envían dentro del tema.\n"
        "• Usa /temas en privado para recibir su contenido.",
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
    # Capturamos TODO lo que no sea comando para guardar mensajes de temas
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
