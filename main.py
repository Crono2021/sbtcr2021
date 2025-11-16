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
OWNER_ID = int(os.getenv("OWNER_ID", "0"))  # ID del admin autorizado

# ---------------------------------------------------------
#   RUTA PERSISTENTE
# ---------------------------------------------------------
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

    # Crear entrada del tema si no existe
    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        # Avisar solo una vez al crear/registrar el tema
        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{html.escape(topic_name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Guardar el ID del mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ---------------------------------------------------------
#   /TEMAS -> LISTA CON BOTONES (TODOS PUEDEN USARLO)
# ---------------------------------------------------------
async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return

    try:
        topics = load_topics()

        if not topics:
            await chat.send_message("📭 No hay temas detectados aún.")
            return

        keyboard = []
        for tid, data in topics.items():
            # defensivo por si hay datos antiguos
            if isinstance(data, dict) and "name" in data:
                name = data["name"]
            else:
                name = str(data)

            safe_name = html.escape(name)
            keyboard.append(
                [InlineKeyboardButton(f"📌 {safe_name}", callback_data=f"t:{tid}")]
            )

        await chat.send_message(
            "📚 <b>Temas detectados:</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        await chat.send_message(f"❌ Error en /temas: {e}")
        print("[/temas] ERROR:", e)


# ---------------------------------------------------------
#   CALLBACK → ENVIAR CONTENIDO DEL TEMA (TODOS)
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
            await bot.copy_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=msg_info["id"],
                protect_content=True,  # oculta info de origen
            )
            count += 1
        except Exception as e:
            print(f"[ERROR] copiando mensaje {msg_info['id']}: {e}")

    await bot.send_message(
        chat_id=query.from_user.id,
        text=f"✔ Fin del contenido del tema. ({count} mensajes)",
    )


# ---------------------------------------------------------
#   /BORRARTEMA -> SOLO ADMIN (OWNER_ID)
# ---------------------------------------------------------
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if user.id != OWNER_ID:
        await chat.send_message("⛔ No tienes permiso para usar este comando.")
        return

    if chat.type != "private":
        await update.message.reply_text("Usa /borrartema en privado.")
        return

    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    keyboard = []
    for tid, data in topics.items():
        name = data["name"] if isinstance(data, dict) and "name" in data else str(data)
        safe_name = html.escape(name)
        keyboard.append(
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")]
        )

    await chat.send_message(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------------------------
#   CALLBACK → ELIMINAR TEMA (SOLO ADMIN)
# ---------------------------------------------------------
async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    if user.id != OWNER_ID:
        await query.answer("⛔ No tienes permiso.", show_alert=True)
        return

    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    deleted_name = topics[topic_id]["name"] if "name" in topics[topic_id] else topic_id

    del topics[topic_id]
    save_topics(topics)

    await query.edit_message_text(
        f"🗑 Tema eliminado:\n<b>{html.escape(deleted_name)}</b>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------
#   /REINICIAR_DB -> SOLO ADMIN (OWNER_ID)
# ---------------------------------------------------------
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    if user.id != OWNER_ID:
        await chat.send_message("⛔ No tienes permiso para usar este comando.")
        return

    # Vaciar temas
    save_topics({})
    await chat.send_message("✅ Base de datos de temas reiniciada.")


# ---------------------------------------------------------
#   /START (TODOS) → MENSAJE CORTO + EJECUTAR /TEMAS
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    # Mensaje simple para cualquier usuario
    await chat.send_message("¡Hola! Selecciona una serie para ver.")

    # Mostrar directamente la lista de temas (si está en privado)
    if chat.type == "private":
        await temas(update, context)


# ---------------------------------------------------------
#   MAIN
# ---------------------------------------------------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos para todos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Comandos solo admin
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    # Guardar mensajes de temas
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
