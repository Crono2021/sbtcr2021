import os
import json
from pathlib import Path
from html import escape
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
#   CONFIGURACIÓN
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

OWNER_ID = 5540195020  # Único admin autorizado

# Carpeta persistente
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# ======================================================
#   CARGAR / GUARDAR TEMAS
# ======================================================
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
#   DETECTAR TEMAS Y MENSAJES
# ======================================================
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

    # Crear si no existe
    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        await msg.reply_text(
            f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
            parse_mode="HTML",
        )

    # Guardar mensaje
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDEN DE TEMAS
# ======================================================
def ordenar_temas(topics: dict):

    def clave(nombre):
        primer = nombre[0].lower()

        # símbolos y números primero
        if not primer.isalpha():
            return (0, nombre.lower())

        return (1, nombre.lower())

    return dict(sorted(topics.items(), key=lambda x: clave(x[1]["name"])))


# ======================================================
#   /TEMAS - LISTA CORRECTA
# ======================================================
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return

    topics = ordenar_temas(load_topics())

    if not topics:
        await chat.send_message("📭 No hay series aún.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append([
            InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")
        ])

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO SEGURO (NUNCA COPY)
# ======================================================
async def reenviar_bloque(bot, user_id, bloque, count):
    for mid in bloque:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            count += 1
        except Exception:
            # Si no existe → lo ignoramos
            pass

    return count


# ======================================================
#   CALLBACK → Enviar contenido
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
    mensajes = sorted([m["id"] for m in topics[topic_id]["messages"]])

    enviados = 0
    BLOQUE = 25

    for i in range(0, len(mensajes), BLOQUE):
        bloque = mensajes[i:i + BLOQUE]
        enviados = await reenviar_bloque(bot, user_id, bloque, enviados)

    # Resumen
    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes enviados 🎉"
    )


# ======================================================
#   /BORRARTEMA (Owner)
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = ordenar_temas(load_topics())

    if not topics:
        await update.message.reply_text("📭 No hay temas para borrar.")
        return

    keyboard = [
        [InlineKeyboardButton(f"❌ {escape(data['name'])}", callback_data=f"del:{tid}")]
        for tid, data in topics.items()
    ]

    await update.message.reply_text(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    nombre = topics[topic_id]["name"]
    del topics[topic_id]
    save_topics(topics)

    await query.edit_message_text(
        f"🗑 Tema eliminado:\n<b>{escape(nombre)}</b>",
        parse_mode="HTML",
    )


# ======================================================
#   /UPDATE (Owner) → Actualizar un tema
# ======================================================
async def update_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = ordenar_temas(load_topics())

    if not topics:
        await update.message.reply_text("📭 No hay temas para actualizar.")
        return

    keyboard = [
        [InlineKeyboardButton(f"🔄 {escape(data['name'])}", callback_data=f"upd:{tid}")]
        for tid, data in topics.items()
    ]

    await update.message.reply_text(
        "♻ <b>Selecciona el tema que deseas actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def update_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema no existe.")
        return

    # Filtrar solo los mensajes que realmente existen
    nuevos = []
    bot = context.bot

    for item in topics[topic_id]["messages"]:
        msg_id = item["id"]
        try:
            await bot.get_chat(GROUP_ID).get_message(msg_id)
            nuevos.append({"id": msg_id})
        except:
            pass

    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await query.edit_message_text(
        "♻ Tema actualizado correctamente.",
        parse_mode="HTML",
    )


# ======================================================
#   /REINICIAR_DB (Owner)
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


# ======================================================
#   /START
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Selecciona una serie:",
        parse_mode="HTML",
    )
    return await temas(update, context)


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Solo owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))
    app.add_handler(CommandHandler("update", update_topics))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^upd:"))

    # Detect
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
