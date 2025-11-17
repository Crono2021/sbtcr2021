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

OWNER_ID = 5540195020  # SOLO TÚ TIENES PERMISOS


# Carpeta persistente en Railway
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
#   DETECTAR TEMAS Y GUARDAR MENSAJES (NO TOCAR)
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
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        await msg.reply_text(
            f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
            parse_mode="HTML",
        )

    # Guardar mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
# ======================================================
def ordenar_temas(topics: dict):
    def clave(nombre):
        primer = nombre[0]

        if not primer.isalpha():   # símbolos y números primero
            return (0, nombre.lower())

        return (1, nombre.lower())

    return dict(sorted(topics.items(), key=lambda x: clave(x[1]["name"])))


# ======================================================
#   /TEMAS — LISTA ORDENADA
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
#   REENVÍO SEGURO (SOLO FORWARD)
# ======================================================
async def forward_bloque(bot, user_id, bloque, count, errores):
    for mid in bloque:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            count += 1

        except Exception as e:
            print(f"[FORWARD ERROR {mid}] {e}")
            errores.append(mid)

    return count


# ======================================================
#   CALLBACK — REENVÍO ORDENADO
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
    errores = []

    BLOQUE = 25
    for i in range(0, len(mensajes), BLOQUE):
        bloque = mensajes[i:i + BLOQUE]
        enviados = await forward_bloque(bot, user_id, bloque, enviados, errores)

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes enviados 🎉"
             + (f"\n⚠ {len(errores)} no existen y fueron omitidos." if errores else "")
    )


# ======================================================
#   /UPDATE — SOLO OWNER → muestra botones
# ======================================================
async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    topics = load_topics()
    if not topics:
        await update.message.reply_text("📭 No hay temas para actualizar.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append([
            InlineKeyboardButton(f"🔄 {safe_name}", callback_data=f"upd:{tid}")
        ])

    await update.message.reply_text(
        "🔧 <b>Selecciona un tema para actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ======================================================
#   CALLBACK — UPDATE DE UN SOLO TEMA
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

    bot = context.bot
    mensajes = [m["id"] for m in topics[topic_id]["messages"]]

    nuevos = []
    eliminados = 0

    for mid in mensajes:
        try:
            await bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            nuevos.append({"id": mid})

        except:
            eliminados += 1

    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await query.edit_message_text(
        f"🔄 Tema actualizado.\n"
        f"✔ Mensajes válidos: {len(nuevos)}\n"
        f"🗑 Eliminados: {eliminados}"
    )


# ======================================================
#   /BORRARTEMA — SOLO OWNER
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = load_topics()

    if not topics:
        await update.message.reply_text("📭 No hay temas para borrar.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")]
        )

    await update.message.reply_text(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK — ELIMINAR TEMA
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
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^upd:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    # Guardar mensajes
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
