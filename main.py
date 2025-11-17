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
#   CONFIGURACIÓN DEL BOT
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
OWNER_ID = 5540195020  # tu ID

# Carpeta persistente Railway
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# ======================================================
#   CARGA / GUARDA TEMAS (con limpieza automática)
# ======================================================
def load_topics():
    if not TOPICS_FILE.exists():
        return {}

    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        return {}

    limpio = {}

    for tid, info in data.items():
        if not isinstance(info, dict):
            continue

        name = info.get("name")
        msgs = info.get("messages")

        if not isinstance(name, str) or name.strip() == "":
            name = f"Tema {tid}"

        if not isinstance(msgs, list):
            msgs = []

        limpio[tid] = {"name": name, "messages": msgs}

    return limpio


def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES (OK)
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

    # Crear tema si no existía
    if topic_id not in topics:

        nombre = None
        if msg.forum_topic_created:
            nombre = msg.forum_topic_created.name

        if not nombre:
            nombre = f"Tema {topic_id}"

        topics[topic_id] = {"name": nombre, "messages": []}

        await msg.reply_text(
            f"📄 Tema detectado y guardado:\n<b>{nombre}</b>",
            parse_mode="HTML",
        )

    # Registrar el mensaje
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS — CORREGIDO
# ======================================================
def ordenar_temas(topics: dict):
    return sorted(
        topics.items(),
        key=lambda x: x[1]["name"].lower()
    )


# ======================================================
#   /TEMAS → mostrar listado correcto SIEMPRE
# ======================================================
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return

    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay series aún.")
        return

    ordenados = ordenar_temas(topics)

    keyboard = []
    for tid, data in ordenados:
        nombre = data["name"]
        keyboard.append(
            [InlineKeyboardButton(f"🎬 {nombre}", callback_data=f"t:{tid}")]
        )

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO SEGURO DE MENSAJES (solo forward)
# ======================================================
async def reenviar_bloque(bot, user_id, bloque, enviados):
    for mid in bloque:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            enviados += 1
        except:
            pass
    return enviados


# ======================================================
#   CALLBACK → enviar contenido del tema
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

    mensajes = [m["id"] for m in topics[topic_id]["messages"]]
    mensajes.sort()

    BLOQUE = 25
    enviados = 0

    for i in range(0, len(mensajes), BLOQUE):
        bloque = mensajes[i:i + BLOQUE]
        enviados = await reenviar_bloque(bot, user_id, bloque, enviados)

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes enviados 🎉"
    )


# ======================================================
#   /UPDATE → actualizar un tema (solo owner)
# ======================================================
async def update_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = load_topics()

    if not topics:
        await update.message.reply_text("📭 No hay temas aún.")
        return

    ordenados = ordenar_temas(topics)

    keyboard = [
        [InlineKeyboardButton(f"🔄 {data['name']}", callback_data=f"up:{tid}")]
        for tid, data in ordenados
    ]

    await update.message.reply_text(
        "🔄 <b>Selecciona un tema para actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → actualizar un tema específico
# ======================================================
async def update_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema no existe.")
        return

    # mensajes existentes
    mensajes_actuales = topics[topic_id]["messages"]
    nuevos = []
    count_ok = 0

    # revisar existencia de los mensajes
    for m in mensajes_actuales:
        try:
            await context.bot.forward_message(
                chat_id=OWNER_ID,
                from_chat_id=GROUP_ID,
                message_id=m["id"]
            )
            nuevos.append({"id": m["id"]})
            count_ok += 1
        except:
            pass

    # actualizar base de datos
    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"🔄 Tema actualizado: {topics[topic_id]['name']}\n✔ {count_ok} mensajes confirmados."
    )


# ======================================================
#   /BORRARTEMA
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = load_topics()

    if not topics:
        await update.message.reply_text("📭 No hay temas.")
        return

    ordenados = ordenar_temas(topics)

    keyboard = [
        [InlineKeyboardButton(f"❌ {data['name']}", callback_data=f"del:{tid}")]
        for tid, data in ordenados
    ]

    await update.message.reply_text(
        "🗑 <b>Selecciona el tema a borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK BORRAR
# ======================================================
async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, tid = query.data.split(":")
    tid = str(tid)

    topics = load_topics()

    if tid not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    nombre = topics[tid]["name"]
    del topics[tid]
    save_topics(topics)

    await query.edit_message_text(
        f"🗑 Tema eliminado:\n<b>{nombre}</b>",
        parse_mode="HTML",
    )


# ======================================================
#   /REINICIAR_DB
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypesDEFAULT_TYPE):
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

    # Comandos principales
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("update", update_menu))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^up:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    # Detección de temas/mensajes
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
