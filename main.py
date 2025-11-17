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
#   CONFIGURACIÓN DEL BOT
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

# ID DEL OWNER — PERMISOS ESPECIALES
OWNER_ID = 5540195020

# Carpeta persistente de Railway
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# ======================================================
#   CARGA / GUARDA TEMAS
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
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (ARREGLADO)
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

    # Crear registro del tema si no existía
    if topic_id not in topics:

        # 1) Si viene desde el evento forum_topic_created
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name

        else:
            # 2) Obtener nombre real desde Telegram
            try:
                topic_info = await context.bot.get_forum_topic(
                    chat_id=GROUP_ID,
                    message_thread_id=int(topic_id)
                )
                topic_name = topic_info.name
            except:
                # 3) Último recurso
                topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        await msg.reply_text(
            f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
            parse_mode="HTML",
        )

    # Guardar cada mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
# ======================================================
def ordenar_temas(topics: dict):
    def clave(nombre):
        primer = nombre[0]

        if not primer.isalpha():
            return (0, nombre.lower())  # símbolos y números primero

        return (1, nombre.lower())  # luego letras

    return dict(sorted(topics.items(), key=lambda x: clave(x[1]["name"])))


# ======================================================
#   /TEMAS → LISTA ORDENADA
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

    topics = ordenar_temas(topics)

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")]
        )

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO PURO (sin copy)
# ======================================================
async def reenviar_bloque(bot, user_id, bloque, count, omitidos):
    for mid in bloque:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            count += 1

        except Exception as e:
            # Guardar mensajes realmente inexistentes
            omitidos.append(mid)

    return count


# ======================================================
#   CALLBACK → reenvío ordenado
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

    enviados = 0
    omitidos = []

    BLOQUE = 25

    for i in range(0, len(mensajes), BLOQUE):
        bloque = mensajes[i:i + BLOQUE]
        enviados = await reenviar_bloque(bot, user_id, bloque, enviados, omitidos)

    # Resumen
    txt = f"✔ Envío completado. {enviados} mensajes enviados 🎉"
    if omitidos:
        txt += f"\n⚠ {len(omitidos)} no existen y fueron omitidos."

    await bot.send_message(chat_id=user_id, text=txt)


# ======================================================
#   /UPDATE — SOLO OWNER — actualizar un tema
# ======================================================
async def update_topic_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = load_topics()
    if not topics:
        await update.message.reply_text("📭 No hay temas.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"🔄 {safe_name}", callback_data=f"upd:{tid}")]
        )

    await update.message.reply_text(
        "🔄 <b>Selecciona el tema a actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → actualizar tema individual
# ======================================================
async def update_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    await query.edit_message_text("🔄 Actualizando tema...")

    bot = context.bot
    mensajes = topics[topic_id]["messages"]

    nuevos = []
    eliminados = []

    for m in mensajes:
        mid = m["id"]
        try:
            await bot.forward_message(
                chat_id=update.effective_user.id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            nuevos.append({"id": mid})

        except:
            eliminados.append(mid)

    # Guardar actualización
    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await bot.send_message(
        chat_id=update.effective_user.id,
        text=f"✔ Tema actualizado.\n"
             f"Válidos: {len(nuevos)}\n"
             f"Omitidos (no existen): {len(eliminados)}"
    )


# ======================================================
#   /BORRARTEMA  — SOLO OWNER
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    chat = update.effective_chat
    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")]
        )

    await chat.send_message(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → eliminar tema
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
        f"🗑 Tema eliminado:\n<b>{escape(deleted_name)}</b>",
        parse_mode="HTML",
    )


# ======================================================
#   /REINICIAR_DB — SOLO OWNER
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


# ======================================================
#   /START → muestra catálogo
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

    # Owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))
    app.add_handler(CommandHandler("update", update_topic_menu))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^upd:"))

    # Guardar mensajes
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
