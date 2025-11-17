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
def limpiar_nombre(nombre: str) -> str:
    # Normalizar y eliminar caracteres de control / no imprimibles,
    # pero manteniendo emojis, acentos, etc.
    nombre = unicodedata.normalize("NFKC", nombre)
    nombre = "".join(c for c in nombre if c.isprintable())
    nombre = re.sub(r"[\u0000-\u001F\u007F]", "", nombre)
    return nombre.strip()


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES
# ======================================================
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    # Solo el grupo configurado
    if msg.chat.id != GROUP_ID:
        return

    # Solo mensajes dentro de temas
    if msg.message_thread_id is None:
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    # Crear registro del tema si no existe
    if topic_id not in topics:
        # 1) Si el mensaje ES el de creación del tema
        if msg.forum_topic_created:
            raw_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            # 2) El bot no vio la creación → pedimos el tema real a Telegram
            try:
                forum_topic = await context.bot.get_forum_topic(
                    chat_id=GROUP_ID,
                    message_thread_id=msg.message_thread_id
                )
                raw_name = forum_topic.name or f"Tema {topic_id}"
            except Exception:
                # 3) Si también falla, usamos un nombre genérico
                raw_name = f"Tema {topic_id}"

        topic_name = limpiar_nombre(raw_name)

        topics[topic_id] = {"name": topic_name, "messages": []}

        # Aviso una sola vez por tema
        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Guardar mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
# ======================================================
def ordenar_temas(topics: dict):
    def clave(nombre: str):
        if not nombre:
            return (1, "")  # por si acaso
        primer = nombre[0]
        if not primer.isalpha():
            # símbolos y números primero
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
#   REENVÍO SEGURO EN ORDEN (SOLO forward_message)
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
#   /UPDATE — LISTA DE TEMAS A ACTUALIZAR (solo OWNER)
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
        keyboard.append(
            [InlineKeyboardButton(f"🔄 {safe_name}", callback_data=f"upd:{tid}")]
        )

    await update.message.reply_text(
        "🔧 <b>Selecciona el tema que deseas actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK → ACTUALIZAR UN TEMA (solo OWNER)
#   (limpia mensajes que ya no existen)
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

    # OJO: esto intentará reenviar a OWNER_ID solo para comprobar
    # que el mensaje existe. Si te molesta el "spam", podemos
    # cambiarlo a otra estrategia más silenciosa.
    nuevos = []
    for m in topics[topic_id]["messages"]:
        mid = m["id"]
        try:
            await context.bot.forward_message(
                chat_id=OWNER_ID,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            nuevos.append(m)
        except Exception as e:
            print(f"[UPDATE] Mensaje {mid} no existe, eliminado de la DB: {e}")

    topics[topic_id]["messages"] = nuevos
    save_topics(topics)

    await query.edit_message_text(
        "✔ Tema actualizado correctamente.",
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
    app.add_handler(CommandHandler("update", update_topics))

    # Callbacks
    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))
    app.add_handler(CallbackQueryHandler(update_topic, pattern="^upd:"))

    # Guardar mensajes / detectar temas
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
