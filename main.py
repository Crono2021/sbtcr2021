import os
import json
import asyncio
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
from telegram.error import RetryAfter, TelegramError

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
#   UTIL: ESCAPAR SOLO LO NECESARIO PARA HTML
#   (NO toca comillas, apóstrofes, acentos, etc.)
# ======================================================
def html_safe(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ======================================================
#   CARGA / GUARDA TEMAS
# ======================================================
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


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (NO TOCAR)
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
            # nombre REAL del tema
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {"name": topic_name, "messages": []}

        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{html_safe(topic_name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Guardar cada mensaje dentro del tema (id simple)
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
# ======================================================
def ordenar_temas(topics: dict):
    def clave(nombre: str):
        if not nombre:
            return (2, "")  # por si acaso

        primer = nombre[0]

        # símbolos / números primero
        if not primer.isalpha():
            return (0, nombre.lower())

        # luego letras
        return (1, nombre.lower())

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
        safe_name = html_safe(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")]
        )

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   HELPER → REENVIAR UN MENSAJE CON REINTENTOS
#   Siempre usa forward_message (nunca copy).
# ======================================================
async def safe_forward(bot, user_id: int, message_id: int) -> bool:
    while True:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=message_id,
            )
            return True

        except RetryAfter as e:
            # Flood control: esperamos lo que dice Telegram
            wait_for = int(getattr(e, "retry_after", 1)) + 1
            print(f"[safe_forward] Flood control, esperando {wait_for}s…")
            await asyncio.sleep(wait_for)
            continue

        except TelegramError as e:
            # Errores de Telegram (mensaje borrado, etc.)
            print(f"[safe_forward] TelegramError para {message_id}: {e}")
            return False

        except Exception as e:
            # Cualquier otro error -> lo registramos y seguimos
            print(f"[safe_forward] Error inesperado para {message_id}: {e}")
            return False


# ======================================================
#   CALLBACK → reenvío ordenado, estable y completo
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

    # IDs únicos y ordenados (por si acaso hubiera duplicados)
    mensajes = sorted({m["id"] for m in topics[topic_id]["messages"]})
    total = len(mensajes)

    if total == 0:
        await bot.send_message(
            chat_id=user_id,
            text="No hay mensajes guardados en este tema.",
        )
        return

    # Mensaje de progreso
    progreso = await bot.send_message(
        chat_id=user_id,
        text=f"⏳ Enviando… 0 / {total}",
    )

    enviados = 0
    BATCH = 70  # cada 70 mensajes: pausa + actualización
    PAUSA_SEGUNDOS = 2

    for idx, mid in enumerate(mensajes, start=1):
        ok = await safe_forward(bot, user_id, mid)
        if ok:
            enviados += 1

        # Cada 70 mensajes (o al final) actualizamos progreso y hacemos pausa
        if idx % BATCH == 0 or idx == total:
            try:
                await progreso.edit_text(f"⏳ Enviando… {enviados} / {total}")
            except Exception:
                pass
            # Pequeña pausa para no apisonar el rate limit
            await asyncio.sleep(PAUSA_SEGUNDOS)

    # Mensaje final
    await bot.send_message(
        chat_id=user_id,
        text=f"✅ Envío completado. {enviados} mensajes enviados 🎉",
    )


# ======================================================
#   /BORRARTEMA  — SOLO OWNER
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    chat = update.effective_chat
    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    topics = ordenar_temas(topics)

    keyboard = []
    for tid, data in topics.items():
        safe_name = html_safe(data["name"])
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
        f"🗑 Tema eliminado:\n<b>{html_safe(deleted_name)}</b>",
        parse_mode="HTML",
    )


# ======================================================
#   /REINICIAR_DB — SOLO OWNER
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
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

    # Comandos solo owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    # Guardar mensajes
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
