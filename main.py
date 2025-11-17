import os
import json
import unicodedata
import re
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

# ID DEL OWNER — ÚNICO ADMIN
OWNER_ID = 5540195020

# Carpeta persistente de Railway
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"


# ======================================================
#   FUNCIONES DE BASE DE DATOS
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
#   LIMPIAR NOMBRE DEL TEMA (UTF-8 SEGURO)
# ======================================================
def limpiar_nombre(nombre):
    nombre = unicodedata.normalize("NFKC", nombre)                # normalizar UTF
    nombre = "".join(c for c in nombre if c.isprintable())        # quitar raros
    nombre = re.sub(r"[\u0000-\u001F\u007F]", "", nombre)         # quitar control
    return nombre.strip()


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES
# ======================================================
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if msg.chat.id != GROUP_ID:
        return

    if msg.message_thread_id is None:
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    # Crear registro del tema si no existe
    if topic_id not in topics:
        if msg.forum_topic_created:
            raw_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            raw_name = f"Tema {topic_id}"

        topic_name = limpiar_nombre(raw_name)

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
        if not primer.isalpha():   # símbolo o número
            return (0, nombre.lower())
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
        safe_name = escape(data["name"])
        keyboard.append([InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")])

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO SEGURO EN ORDEN
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

    # Enviar uno por uno para respetar orden y evitar fallos
    for mid in mensajes:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            enviados += 1
        except Exception as e:
            print(f"[ERROR reenviando {mid}]: {e}")
            errores.append(mid)

    # RESUMEN SIN MOSTRAR OMITIDOS
    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes enviados 🎉"
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

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append([InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")])

    await chat.send_message(
        "🗑 <b>Selecciona el tema que deseas borrar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → BORRAR TEMA
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
#   /UPDATE — ACTUALIZAR UN TEMA (solo OWNER)
# ======================================================
async def update_topics(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        keyboard.append([InlineKeyboardButton(f"🔄 {safe_name}", callback_data=f"upd:{tid}")])

    await update.message.reply_text(
        "🔧 <b>Selecciona el tema que deseas actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → ACTUALIZAR TEMA
# ======================================================
async def update_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":")
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    # VALIDAR MENSAJES EXISTENTES
    nuevos = []
    for m in topics[topic_id]["messages"]:
        try:
            await context.bot.forward_message(
                chat_id=OWNER_ID,  # Solo comprobación
                from_chat_id=GROUP_ID,
                message_id=m["id"]
            )
            nuevos.append(m)
        except:
            pass  # mensaje no existe → eliminado

    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await query.edit_message_text(
        "✔ Tema actualizado correctamente.",
        parse_mode="HTML",
    )


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

    # owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", lambda u, c: save_topics({})))
    app.add_handler(CommandHandler("update", update_topics))

    # callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^upd:"))

    # detección de temas/mensajes
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
