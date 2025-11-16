import os
import json
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

# -------------------------------------------------
# CONFIG
# -------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

DATA_DIR = Path("/app/data/topics")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS_INDEX = DATA_DIR / "index.json"


# -------------------------------------------------
# INDEX (lista de temas)
# -------------------------------------------------
def load_index():
    if TOPICS_INDEX.exists():
        return json.load(open(TOPICS_INDEX, "r", encoding="utf-8"))
    return {}


def save_index(data):
    json.dump(data, open(TOPICS_INDEX, "w", encoding="utf-8"), indent=4, ensure_ascii=False)


# -------------------------------------------------
# START
# -------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ¡Hola! Este bot reenviará mensajes desde los *temas del grupo configurado*.\n\n"
        "El bot ya está configurado automáticamente porque GROUP_ID viene desde Railway.\n\n"
        "✔ Crea un tema nuevo en el grupo\n"
        "✔ Todo mensaje dentro del tema será guardado\n"
        "✔ Usa /temas en privado para que te reenvíe su contenido",
        parse_mode="Markdown"
    )


# -------------------------------------------------
# Capturar y guardar mensajes de un topic
# -------------------------------------------------
async def capture_topic_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Solo mensajes del grupo configurado
    if msg.chat_id != GROUP_ID:
        return

    # Solo mensajes dentro de temas
    if not msg.is_topic_message:
        return

    topic_id = msg.message_thread_id
    topic_file = DATA_DIR / f"{topic_id}.json"

    # Cargar o inicializar archivo del tema
    if topic_file.exists():
        data = json.load(open(topic_file, "r", encoding="utf-8"))
    else:
        data = {"topic_id": topic_id, "messages": []}

        # Añadir al índice si es nuevo
        index = load_index()
        index[str(topic_id)] = f"Tema {topic_id}"
        save_index(index)

    # Guardar mensaje
    data["messages"].append(msg.message_id)

    json.dump(data, open(topic_file, "w", encoding="utf-8"), indent=4, ensure_ascii=False)


# -------------------------------------------------
# Mostrar lista de temas
# -------------------------------------------------
async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    index = load_index()

    if not index:
        await update.message.reply_text("❌ No hay temas guardados todavía.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"topic:{tid}")]
        for tid, name in index.items()
    ]

    await update.message.reply_text(
        "📚 *Temas detectados:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# -------------------------------------------------
# Reenviar contenido del tema al usuario
# -------------------------------------------------
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tid = query.data.split(":")[1]
    topic_file = DATA_DIR / f"{tid}.json"

    if not topic_file.exists():
        await query.message.reply_text("❌ No existe archivo de ese tema.")
        return

    data = json.load(open(topic_file, "r", encoding="utf-8"))
    msgs = data.get("messages", [])

    await query.message.reply_text("📨 Enviando contenido del tema...")

    enviados = 0

    for mid in msgs:
        try:
            await context.bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            enviados += 1
        except Exception as e:
            print(f"No se pudo reenviar {mid}: {e}")

    await query.message.reply_text(f"✅ {enviados} mensajes reenviados.")


# -------------------------------------------------
# MAIN
# -------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", list_topics))

    # Capturar **todos** los mensajes del grupo y guardar si son de un topic
    app.add_handler(MessageHandler(filters.ALL, capture_topic_messages))

    # Selección de tema (botones)
    app.add_handler(CallbackQueryHandler(send_topic))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
