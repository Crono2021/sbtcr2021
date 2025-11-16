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
    filters,
)

# -----------------------------------------
# CONFIGURACIÓN
# -----------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

# Carpeta en el volumen persistente
DATA_DIR = Path("/app/data/topics")
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Índice con la lista de temas (topic_id -> nombre)
TOPICS_INDEX = DATA_DIR / "index.json"


# -----------------------------------------
# UTILIDADES DE ÍNDICE
# -----------------------------------------
def load_index():
    if TOPICS_INDEX.exists():
        with open(TOPICS_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_index(data):
    with open(TOPICS_INDEX, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# -----------------------------------------
# /start
# -----------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ¡Hola! Este bot guarda los mensajes de los *temas* del grupo configurado "
        "y te los reenvía por privado.\n\n"
        "✔ El GROUP_ID viene de Railway.\n"
        "✔ Crea un tema nuevo en el grupo.\n"
        "✔ Todo lo que escribas dentro se guardará.\n"
        "✔ Usa /temas en privado para recibir su contenido.",
        parse_mode="Markdown",
    )


# -----------------------------------------
# CAPTURAR Y GUARDAR MENSAJES DE LOS TEMAS
# -----------------------------------------
async def capture_topic_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Solo nos interesa el grupo configurado
    if msg.chat_id != GROUP_ID:
        return

    # Solo mensajes que pertenezcan a un topic
    if not msg.is_topic_message:
        return

    topic_id = msg.message_thread_id
    topic_file = DATA_DIR / f"{topic_id}.json"

    # --- 1) Actualizar / crear índice con el NOMBRE REAL del tema ---
    index = load_index()

    # Si es el mensaje de creación del tema, tendrá forum_topic_created
    if msg.forum_topic_created is not None:
        topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        index[str(topic_id)] = topic_name
        save_index(index)
    else:
        # Si no lo tenemos aún en el índice, ponemos un nombre genérico
        if str(topic_id) not in index:
            index[str(topic_id)] = f"Tema {topic_id}"
            save_index(index)

    # --- 2) Guardar la lista de mensajes del topic ---
    if topic_file.exists():
        with open(topic_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"topic_id": topic_id, "messages": []}

    data["messages"].append(msg.message_id)

    with open(topic_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# -----------------------------------------
# /temas → lista de temas con botones
# -----------------------------------------
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
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# -----------------------------------------
# Callback de botón → reenviar contenido
# -----------------------------------------
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tid = query.data.split(":")[1]
    topic_file = DATA_DIR / f"{tid}.json"

    if not topic_file.exists():
        await query.message.reply_text("❌ No se encontró el archivo de ese tema.")
        return

    with open(topic_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    msgs = data.get("messages", [])

    await query.message.reply_text("📨 Enviando contenido del tema...")

    enviados = 0
    for mid in msgs:
        try:
            # IMPORTANTE: copy_message para OCULTAR remitente
            await context.bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=mid,
            )
            enviados += 1
        except Exception as e:
            print(f"[ERROR] No se pudo copiar el mensaje {mid}: {e}")

    await query.message.reply_text(f"✅ {enviados} mensajes enviados.")


# -----------------------------------------
# MAIN
# -----------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", list_topics))

    # Capturar TODOS los mensajes para guardar los de los topics
    app.add_handler(MessageHandler(filters.ALL, capture_topic_messages))

    # Botones de selección de tema
    app.add_handler(CallbackQueryHandler(send_topic))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
