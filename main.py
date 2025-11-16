import os
import json
import asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))   # Grupo configurado en Railway

# Carpeta persistente de Railway
DATA_DIR = Path("/app/storage/topics")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS_INDEX = DATA_DIR / "index.json"


# ----------------------------------------------------------------------
#   Cargar y guardar JSON
# ----------------------------------------------------------------------

def load_topics():
    if not TOPICS_INDEX.exists():
        return {}
    try:
        with open(TOPICS_INDEX, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_topics(data):
    with open(TOPICS_INDEX, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ----------------------------------------------------------------------
#   Detectar nuevos temas creados en el grupo
# ----------------------------------------------------------------------

async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # Sólo detectar mensajes dentro de topics
    if msg.chat.id != GROUP_ID:
        return

    # thread_id = ID del tema
    if msg.message_thread_id is None:
        return

    topic_id = msg.message_thread_id

    # NO es necesario detectar nombre desde thread_name (ya no existe)
    # La primera vez que llega un mensaje del tema, guardamos su nombre como:
    topic_name = f"Tema {topic_id}"

    topics = load_topics()
    group_key = str(GROUP_ID)

    if group_key not in topics:
        topics[group_key] = {}

    # Registrar tema si no existe aún
    if str(topic_id) not in topics[group_key]:
        topics[group_key][str(topic_id)] = {
            "name": topic_name
        }
        save_topics(topics)

        await msg.reply_text(f"📄 Tema detectado y guardado:\n<b>{topic_name}</b>", parse_mode="HTML")


# ----------------------------------------------------------------------
#   Mostrar lista de temas
# ----------------------------------------------------------------------

async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topics = load_topics()
    group_key = str(GROUP_ID)

    if group_key not in topics or not topics[group_key]:
        await update.message.reply_text("❌ No hay temas guardados todavía.")
        return

    keyboard = []
    for tid, data in topics[group_key].items():
        keyboard.append([InlineKeyboardButton(data["name"], callback_data=f"tema:{tid}")])

    await update.message.reply_text(
        "📚 <b>Temas detectados:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ----------------------------------------------------------------------
#   Enviar contenido del tema seleccionado
# ----------------------------------------------------------------------

async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = int(topic_id)

    await query.edit_message_text("📨 Enviando contenido del tema...")

    application = context.application
    bot = application.bot

    try:
        # get_forum_topic_messages obtiene mensajes del tema (Telegram recientes)
        messages = await bot.get_forum_topic_messages(
            chat_id=GROUP_ID,
            message_thread_id=topic_id
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Error al obtener mensajes del tema.\n{e}")
        return

    if not messages:
        await query.edit_message_text("❌ El tema está vacío.")
        return

    for m in messages:
        try:
            # Reenviar sin remitente
            await bot.forward_message(
                chat_id=update.effective_user.id,
                from_chat_id=GROUP_ID,
                message_id=m.message_id,
                message_thread_id=None
            )
        except:
            pass

    await query.edit_message_text("✔ Contenido enviado.")


# ----------------------------------------------------------------------
#   /start
# ----------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "🤖 ¡Hola! Este bot reenvía mensajes desde los temas del grupo configurado.\n"
        f"El bot ya está configurado vía Railway (GROUP_ID = <code>{GROUP_ID}</code>).\n\n"
        "✔ Crea un tema nuevo en el grupo.\n"
        "✔ Todo mensaje dentro del tema será reenviado por privado.\n"
        "✔ Usa /temas para ver los temas detectados."
    )
    await update.message.reply_text(txt, parse_mode="HTML")


# ----------------------------------------------------------------------
#   MAIN
# ----------------------------------------------------------------------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Registrar handlers
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), detect_topic))
    app.add_handler(CallbackQueryHandler(send_topic))
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex("^/temas$"), temas))
    app.add_handler(MessageHandler(filters.COMMAND, start))

    print("BOT INICIADO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
