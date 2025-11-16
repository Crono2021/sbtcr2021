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

# -----------------------------------------------
# CONFIG
# -----------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # Ya lo recibes de Railway

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# -----------------------------------------------
# FUNCIONES PARA GUARDAR / LEER TEMAS
# -----------------------------------------------
def load_topics():
    if TOPICS_FILE.exists():
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# -----------------------------------------------
# START
# -----------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ¡Hola! Este bot reenviará mensajes de los temas del grupo.\n"
        "El bot ya está configurado con GROUP_ID proporcionado por Railway.\n\n"
        "✓ Crea un tema nuevo en el grupo.\n"
        "✓ Todo mensaje dentro del tema será reenviado por privado."
    )


# -----------------------------------------------
# DETECTAR NUEVO TEMA
# -----------------------------------------------
async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    # Asegurar que no es privado
    if msg.chat_id != GROUP_ID:
        return

    # Solo mensajes que pertenecen a un topic:
    if not msg.is_topic_message:
        return

    topic_id = msg.message_thread_id

    topics = load_topics()
    gid = str(GROUP_ID)

    if gid not in topics:
        topics[gid] = {}

    # Si el topic es nuevo, lo guardamos
    if str(topic_id) not in topics[gid]:
        topics[gid][str(topic_id)] = {
            "name": f"Tema {topic_id}"
        }
        save_topics(topics)

        await msg.reply_text(f"🗂 Nuevo tema detectado y guardado: {topic_id}")
        print(f"[LOG] Tema guardado: {topic_id}")


# -----------------------------------------------
# REENVIAR MENSAJES DEL TEMA AL PRIVADO
# -----------------------------------------------
async def forward_from_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if msg.chat_id != GROUP_ID:
        return

    if not msg.is_topic_message:
        return

    # Reenviar sin mostrar quien lo envió
    try:
        await context.bot.forward_message(
            chat_id=update.effective_user.id,
            from_chat_id=GROUP_ID,
            message_id=msg.message_id
        )
    except Exception as e:
        print(f"[ERROR] No pude reenviar: {e}")


# -----------------------------------------------
# LISTAR TEMAS
# -----------------------------------------------
async def list_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()
    gid = str(GROUP_ID)

    if gid not in topics or len(topics[gid]) == 0:
        await update.message.reply_text("❌ No hay temas guardados aún.")
        return

    keyboard = []
    for tid, data in topics[gid].items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"topic:{tid}")])

    await update.message.reply_text(
        "📚 *Temas detectados:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# -----------------------------------------------
# ENVIAR CONTENIDO DE UN TEMA AL PRIVADO
# -----------------------------------------------
async def send_topic_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.split(":")[1])

    await query.message.reply_text("📨 Enviando contenido del tema...")

    # Obtener el historial del topic
    try:
        messages = await context.bot.get_chat_history(
            chat_id=GROUP_ID,
            message_thread_id=topic_id,
            limit=200  # máximos mensajes que permite Telegram
        )
    except:
        await query.message.reply_text("❌ Error al obtener mensajes del tema.")
        return

    count = 0

    for msg in reversed(messages):
        try:
            await context.bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=msg.message_id
            )
            count += 1
        except:
            pass

    await query.message.reply_text(f"✅ Envío completado ({count} mensajes reenviados).")


# -----------------------------------------------
# MAIN
# -----------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", list_topics))

    # Mensajes dentro de temas → reenviar
    app.add_handler(MessageHandler(filters.ALL, detect_topic))
    app.add_handler(MessageHandler(filters.ALL, forward_from_topic))

    # Botones de menú
    app.add_handler(CallbackQueryHandler(send_topic_content))

    print("🤖 Bot listo y funcionando en Railway…")
    app.run_polling()


if __name__ == "__main__":
    main()
