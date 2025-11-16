import os
import json
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, CallbackContext, filters
)

# ---------------------------------------
# VARIABLES DESDE RAILWAY
# ---------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

# ---------------------------------------
# RUTAS PERSISTENTES (VOLUMEN /data)
# ---------------------------------------
BASE_DIR = "/data"   # <--- ESTE ES EL VOLUMEN REAL
TOPICS_FILE = os.path.join(BASE_DIR, "topics.json")
MSG_DIR = os.path.join(BASE_DIR, "messages")

# Crear rutas del volumen con permisos
try:
    os.makedirs(BASE_DIR, exist_ok=True)
    os.makedirs(MSG_DIR, exist_ok=True)
except Exception as e:
    print("❌ Error creando directorios del volumen:", e)

# Crear topics.json si no existe
if not os.path.isfile(TOPICS_FILE):
    try:
        with open(TOPICS_FILE, "w") as f:
            json.dump({}, f)
        print("✔ topics.json creado correctamente en /data")
    except Exception as e:
        print("❌ No se pudo crear topics.json en /data:", e)


# ---------------------------------------
# FUNCIONES DE PERSISTENCIA
# ---------------------------------------
def load_topics():
    try:
        with open(TOPICS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}


def save_topics(data):
    with open(TOPICS_FILE, "w") as f:
        json.dump(data, f, indent=4)


def load_messages(topic_id):
    file = os.path.join(MSG_DIR, f"{topic_id}.json")
    if not os.path.exists(file):
        return []
    with open(file, "r") as f:
        return json.load(f)


def save_message(topic_id, message_id):
    file = os.path.join(MSG_DIR, f"{topic_id}.json")
    msgs = load_messages(topic_id)
    msgs.append(message_id)
    with open(file, "w") as f:
        json.dump(msgs, f)


# ---------------------------------------
# /start EN PRIVADO
# ---------------------------------------
async def start(update: Update, context: CallbackContext):
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            "🤖 ¡Hola!\n"
            "Este bot reenviará mensajes desde temas del grupo configurado.\n\n"
            "• Crea un tema nuevo en el grupo\n"
            "• El bot guardará su nombre real\n"
            "• Luego podrás pedir sus mensajes con /temas"
        )


# ---------------------------------------
# /temas EN PRIVADO
# ---------------------------------------
async def temas(update: Update, context: CallbackContext):
    if update.effective_chat.type != "private":
        return

    topics = load_topics().get(str(GROUP_ID), {})

    if not topics:
        await update.message.reply_text("📭 No hay temas detectados todavía.")
        return

    keyboard = [
        [InlineKeyboardButton(f"📌 {name}", callback_data=f"topic_{tid}")]
        for tid, name in topics.items()
    ]

    await update.message.reply_text(
        "📚 *Temas detectados:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ---------------------------------------
# BOTÓN → ENVIAR CONTENIDO DEL TEMA
# ---------------------------------------
async def on_topic_button(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    topic_id = int(query.data.replace("topic_", ""))

    await query.message.reply_text("📨 Enviando contenido del tema...")

    messages = load_messages(topic_id)

    for msg_id in messages:
        try:
            await context.bot.forward_message(
                chat_id=query.from_user.id,
                from_chat_id=GROUP_ID,
                message_id=msg_id
            )
        except:
            pass


# ---------------------------------------
# DETECTAR NUEVO TEMA (CON NOMBRE REAL)
# ---------------------------------------
async def on_topic_created(update: Update, context: CallbackContext):
    msg = update.message
    if not msg or not msg.forum_topic_created:
        return

    topic_id = msg.message_thread_id
    topic_name = msg.forum_topic_created.name  # NOMBRE REAL

    topics = load_topics()
    if str(GROUP_ID) not in topics:
        topics[str(GROUP_ID)] = {}

    topics[str(GROUP_ID)][str(topic_id)] = topic_name
    save_topics(topics)

    await context.bot.send_message(
        GROUP_ID,
        f"🗂 Tema detectado y guardado: *{topic_name}*",
        parse_mode="Markdown"
    )


# ---------------------------------------
# GUARDAR MENSAJES DE LOS TEMAS
# ---------------------------------------
async def store_messages(update: Update, context: CallbackContext):
    msg = update.message
    if not msg or msg.chat_id != GROUP_ID:
        return

    if not msg.message_thread_id:
        return  # No es un tema

    topic_id = msg.message_thread_id
    save_message(topic_id, msg.message_id)


# ---------------------------------------
# MAIN
# ---------------------------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Detectar nuevo tema
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CREATED, on_topic_created))

    # Guardar mensajes de temas
    app.add_handler(MessageHandler(filters.ALL, store_messages))

    # Comandos en privado
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Botones
    app.add_handler(CallbackQueryHandler(on_topic_button))

    print("🤖 Bot corriendo (con persistencia en /data)…")
    app.run_polling()


if __name__ == "__main__":
    main()
