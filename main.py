import os
import json
import asyncio
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
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
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
#   REENVÍO POR BLOQUES CON PLAN B AUTOMÁTICO
# ======================================================
async def reenviar_bloque(bot, user_id, bloque, count, errores):
    for mid in bloque:
        try:
            # Intento 1 → reenviar (rápido)
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            count += 1

        except Exception:
            try:
                # Plan B → copiar (más lento pero seguro)
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=GROUP_ID,
                    message_id=mid
                )
                count += 1
            except Exception as e:
                print(f"[ERROR reenviando/copiando {mid}]: {e}")
                errores.append(mid)

    return count


# ======================================================
#   CALLBACK → reenvío ordenado Y SEGURO
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
        enviados = await reenviar_bloque(bot, user_id, bloque, enviados, errores)

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes enviados 🎉"
             + (f"\n⚠ {len(errores)} fallaron." if errores else "")
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
#   /REINICIAR_DB — SOLO OWNER
# ======================================================
async def reiniciar_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


# ======================================================
#   /UPDATE — SOLO OWNER — ACTUALIZAR UN TEMA
# ======================================================
async def update_tema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    chat = update.effective_chat
    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para actualizar.")
        return

    keyboard = []
    for tid, data in topics.items():
        safe_name = escape(data["name"])
        keyboard.append(
            [InlineKeyboardButton(f"🔄 {safe_name}", callback_data=f"upd:{tid}")]
        )

    await chat.send_message(
        "🔧 <b>Selecciona el tema que deseas actualizar:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   CALLBACK — ACTUALIZACIÓN REAL DEL TEMA
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
    user = update.effective_user
    nombre = topics[topic_id]["name"]
    mensajes_actuales = topics[topic_id]["messages"]

    mensajes_validos = []
    eliminados = 0

    await query.edit_message_text(
        f"🔄 Actualizando <b>{escape(nombre)}</b>…",
        parse_mode="HTML"
    )

    for msg_info in mensajes_actuales:
        mid = msg_info["id"]
        ok = False

        # 1️⃣ Intento con forward
        try:
            temp = await bot.forward_message(
                chat_id=user.id,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            ok = True
        except:
            # 2️⃣ Segundo intento → copy
            try:
                temp = await bot.copy_message(
                    chat_id=user.id,
                    from_chat_id=GROUP_ID,
                    message_id=mid
                )
                ok = True
            except:
                ok = False

        # Borrar mensaje temporal VALIDADO
        if ok:
            try:
                await bot.delete_message(chat_id=user.id, message_id=temp.message_id)
            except:
                pass

        if ok:
            mensajes_validos.append(msg_info)
        else:
            eliminados += 1

        await asyncio.sleep(0.08)

    # Guardar resultado
    topics[topic_id]["messages"] = mensajes_validos
    save_topics(topics)

    await bot.send_message(
        chat_id=user.id,
        text=(
            f"✔ Tema <b>{escape(nombre)}</b> actualizado.\n"
            f"• Mensajes válidos: <b>{len(mensajes_validos)}</b>\n"
            f"• Eliminados: <b>{eliminados}</b>"
        ),
        parse_mode="HTML"
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

    # Comandos solo owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))
    app.add_handler(CommandHandler("update", update_tema))

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
