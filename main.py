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

# ======================================================
#   VARIABLES DE ENTORNO
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

# ID del único admin permitido
OWNER_ID = 5540195020

# ======================================================
#   BASE DE DATOS PERSISTENTE EN RAILWAY
# ======================================================
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


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


# ======================================================
#   DETECTAR NUEVOS TEMAS Y GUARDAR MENSAJES
# ======================================================
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if msg.chat.id != GROUP_ID:
        return

    if msg.message_thread_id is None:
        return  # no pertenece a un tema

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    # --- DETECTAR NUEVO TEMA ---
    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        await msg.reply_text(
            f"📄 Tema detectado y guardado:\n<b>{topic_name}</b>",
            parse_mode="HTML",
        )

    # Guardar mensaje
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   /TEMAS → LISTA ALFABÉTICA
# ======================================================
def ordenar_nombres(topics):
    """Temas especiales primero, luego alfabéticos."""
    def clave(t):
        name = t[1]["name"]
        first = name[0]

        # Si comienza con número o símbolo → va antes
        if not first.isalpha():
            return (0, name.lower())
        return (1, name.lower())

    return dict(sorted(topics.items(), key=clave))


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return

    try:
        topics = load_topics()
        if not topics:
            await chat.send_message("📭 No hay series disponibles todavía.")
            return

        topics = ordenar_nombres(topics)

        keyboard = [
            [InlineKeyboardButton(f"🎬 {data['name']}", callback_data=f"t:{tid}")]
            for tid, data in topics.items()
        ]

        await chat.send_message(
            "🎬 <b>Catálogo de series</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        await chat.send_message(f"❌ Error en /temas: {e}")
        print("ERROR /temas:", e)


# ======================================================
#   CALLBACK: REENVIAR CONTENIDO EN BLOQUES DE 100
# ======================================================
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
    user_id = query.from_user.id

    msg_ids = [m["id"] for m in topics[topic_id]["messages"]]

    # Bloques de 100 mensajes
    def chunk(lst, size):
        for i in range(0, len(lst), size):
            yield lst[i:i + size]

    total_sent = 0

    for block in chunk(msg_ids, 100):
        try:
            await bot.forward_messages(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_ids=block
            )
            total_sent += len(block)
        except Exception as e:
            print("Error reenviando bloque:", e)

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {total_sent} mensajes enviados 🎉"
    )


# ======================================================
#   ADMIN: /borrartema
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    topics = load_topics()
    if not topics:
        await update.message.reply_text("📭 No hay temas para borrar.")
        return

    keyboard = [
        [InlineKeyboardButton(f"❌ {data['name']}", callback_data=f"del:{tid}")]
        for tid, data in topics.items()
    ]

    await update.message.reply_text(
        "🗑 <b>Selecciona el tema a borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → ELIMINAR TEMA
# ======================================================
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
        f"🗑 Tema eliminado:\n<b>{deleted_name}</b>",
        parse_mode="HTML",
    )


# ======================================================
#   /reiniciar_db (solo owner)
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


# ======================================================
#   /START
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ¡Hola! Selecciona una serie para ver:")
    return await temas(update, context)


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    # Detectar mensajes dentro de temas
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
