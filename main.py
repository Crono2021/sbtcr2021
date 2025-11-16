import os
import json
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ============================
# CONFIG
# ============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # ← Railway env var
DATA_FILE = "/app/data/topics.json"   # ← guardado en volumen Railway

# ============================
# BASE DE DATOS (JSON SIMPLE)
# ============================

def ensure_storage():
    os.makedirs("/app/data", exist_ok=True)
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w") as f:
            f.write(json.dumps({"topics": {}}))

def load_data():
    ensure_storage()
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ============================
# HANDLERS
# ============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 ¡Hola! Este bot reenvía mensajes desde temas del grupo configurado.\n"
        "El bot ya está configurado vía Railway (GROUP_ID).\n\n"
        "✓ Crear un tema nuevo en el grupo.\n"
        "✓ Todo mensaje dentro del tema será reenviado por privado."
    )

# --- detectar creación de temas ---
async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Solo actuar en el grupo correcto
    if msg.chat_id != GROUP_ID:
        return

    # Solo si REALMENTE es creación de un tema
    if not msg.forum_topic_created:
        return

    topic_id = msg.message_thread_id
    topic_name = msg.forum_topic_created.name

    data = load_data()
    data["topics"][str(topic_id)] = topic_name
    save_data(data)

    await msg.reply_text(f"📝 Tema detectado y guardado:\n*{topic_name}*", parse_mode="Markdown")

# --- reenviar mensajes de temas ---
async def forward_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Solo reenviar mensajes del grupo correcto
    if msg.chat_id != GROUP_ID:
        return

    # Solo reenviar mensajes que estén dentro de un tema
    if msg.message_thread_id is None:
        return

    # Comprobar que el tema está registrado
    data = load_data()
    topic_id = str(msg.message_thread_id)
    if topic_id not in data["topics"]:
        return

    # Reenviar el mensaje al usuario
    try:
        await context.bot.forward_message(
            chat_id=msg.from_user.id,
            from_chat_id=GROUP_ID,
            message_id=msg.message_id
        )
    except:
        pass

# ============================
# MAIN
# ============================

def main():
    print("=== BOT VERSION FINAL ===")
    print("Montando base de datos/volumen…")
    ensure_storage()
    print("✔ Base de datos lista")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))

    # Detectar creación de temas (PTB v21+)
    app.add_handler(MessageHandler(filters.ALL, topic_created))

    # Reenviar mensajes de temas
    app.add_handler(MessageHandler(filters.ALL, forward_messages))

    print("🤖 Bot corriendo en Railway…")
    app.run_polling()

if __name__ == "__main__":
    main()
