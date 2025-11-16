import os
import json
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================
# CONFIGURACIÓN
# ==============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))  # ← Railway lo inyecta
DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# ==============================
# FUNCIONES PARA GUARDAR TEMAS
# ==============================

def load_topics():
    """Carga archivo JSON donde se guardan los temas detectados."""
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_topics(data):
    """Guarda los temas detectados en formato JSON."""
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ==============================
# COMANDOS
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensaje de inicio en privado."""
    if update.message.chat.type != "private":
        return

    await update.message.reply_text(
        "🤖 ¡Hola! Este bot reenvía mensajes desde los temas del grupo configurado.\n"
        "El bot ya está configurado mediante Railway (GROUP_ID).\n\n"
        "✔ Crea un tema nuevo en el grupo.\n"
        "✔ Todo mensaje dentro de ese tema será reenviado por privado."
    )


async def listar_temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista los temas detectados."""
    if update.message.chat.type != "private":
        return

    topics = load_topics()
    gid = str(GROUP_ID)

    if gid not in topics or len(topics[gid]) == 0:
        await update.message.reply_text("❌ No hay temas registrados todavía.")
        return

    texto = "📚 *Temas detectados:*\n\n"
    for tid, name in topics[gid].items():
        texto += f"• `{tid}` → {name}\n"

    await update.message.reply_text(texto, parse_mode="Markdown")


# ==============================
# DETECCIÓN DE TEMAS
# ==============================

async def detect_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return
    if msg.chat_id != GROUP_ID:
        return
    if not msg.is_topic_message:
        return

    topic_id = msg.message_thread_id
    topics = load_topics()
    gid = str(GROUP_ID)

    if gid not in topics:
        topics[gid] = {}

    # SI EL TEMA SE CREA EN ESTE MENSAJE
    if msg.forum_topic_created:
        topic_name = msg.forum_topic_created.name or "Sin título"

        if str(topic_id) not in topics[gid]:
            topics[gid][str(topic_id)] = topic_name
            save_topics(topics)

            await msg.reply_text(
                f"📝 Tema detectado y guardado: *{topic_name}*",
                parse_mode="Markdown"
            )

    # SI ES UN MENSAJE DENTRO DEL TEMA → reenviar
    else:
        await forward_message(update, context)


# ==============================
# REENVÍO DEL MENSAJE
# ==============================

async def forward_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return
    if msg.chat_id != GROUP_ID:
        return
    if not msg.is_topic_message:
        return

    try:
        await msg.forward(update.effective_user.id)
    except Exception as e:
        print("Error reenviando:", e)


# ==============================
# MAIN
# ==============================

def main():
    print("=== BOT INICIADO ===")
    print(f"GROUP_ID cargado: {GROUP_ID}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos privados
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", listar_temas))

    # Handler para detectar temas y reenviar
    app.add_handler(MessageHandler(filters.Chat(GROUP_ID), detect_topic))

    app.run_polling()


if __name__ == "__main__":
    main()
