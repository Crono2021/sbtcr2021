import os
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

print("=== VERSION BOT: 1.0 ===")


# ------------------------------
#  VARIABLES Railway
# ------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/topics.sqlite3")

if not BOT_TOKEN:
    raise RuntimeError("Error: BOT_TOKEN no está configurado en Railway.")


# ------------------------------
#  BASE DE DATOS
# ------------------------------

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                topic_name TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def save_topic(group_id: int, topic_id: int, topic_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO topics (group_id, topic_id, topic_name) VALUES (?, ?, ?)",
            (group_id, topic_id, topic_name),
        )
        await db.commit()


async def get_topics(group_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT topic_id, topic_name FROM topics WHERE group_id = ? ORDER BY id ASC",
            (group_id,),
        )
        rows = await cursor.fetchall()
        return rows


# ------------------------------
# COMANDO /setgroup
# ------------------------------

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if message.chat.type not in ["group", "supergroup"]:
        await message.reply_text("⚠ Usa este comando dentro del grupo donde están los temas.")
        return

    group_id = message.chat_id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM topics WHERE group_id = ?", (group_id,)
        )
        count = (await cursor.fetchone())[0]

    await message.reply_text(
        f"✔ Grupo registrado.\n\n"
        f"Group ID: `{group_id}`\n"
        f"Temas guardados: **{count}**\n\n"
        "Ya puedes usar el bot por privado.",
        parse_mode="Markdown"
    )


# ------------------------------
# EVENTO: Nuevo tema creado
# ------------------------------

async def topic_created(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    if not msg or not msg.forum_topic_created:
        return

    topic = msg.forum_topic_created
    topic_id = msg.message_thread_id
    topic_name = topic.name
    group_id = msg.chat_id

    await save_topic(group_id, topic_id, topic_name)
    await msg.reply_text(f"Nuevo tema guardado: {topic_name}")


# ------------------------------
# /temas → lista los temas
# ------------------------------

async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    private = update.effective_chat.type == "private"
    if not private:
        await update.message.reply_text("Envíame /temas por privado 😉")
        return

    group_id = os.getenv("GROUP_ID")
    if not group_id:
        await update.message.reply_text("⚠ Primero configura /setgroup en el grupo.")
        return

    group_id = int(group_id)

    rows = await get_topics(group_id)
    if not rows:
        await update.message.reply_text("No hay temas guardados todavía.")
        return

    keyboard = []
    for topic_id, topic_name in rows:
        keyboard.append([InlineKeyboardButton(topic_name, callback_data=f"topic:{topic_id}")])

    await update.message.reply_text(
        "Selecciona un tema:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ------------------------------
# Seleccionar un tema y reenviar mensajes
# ------------------------------

async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("topic:"):
        return

    topic_id = int(data.split(":")[1])
    group_id = int(os.getenv("GROUP_ID"))

    bot = context.bot

    await query.message.reply_text("Enviando contenido...")

    # Recorrer mensajes del hilo
    try:
        async for msg in bot.get_chat_history(
            chat_id=group_id,
            message_thread_id=topic_id,
            limit=2000,
            oldest_first=True
        ):
            try:
                await bot.forward_message(
                    chat_id=query.from_user.id,
                    from_chat_id=group_id,
                    message_id=msg.message_id,
                    protect_content=True
                )
                await asyncio.sleep(0.8)
            except Exception:
                pass

        await bot.send_message(query.from_user.id, "✔ Finalizado.")
    except Exception as e:
        await bot.send_message(query.from_user.id, f"❌ Error leyendo el tema: {e}")


# ------------------------------
#  INICIO DEL BOT
# ------------------------------

async def main():
    await init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    # handlers
    application.add_handler(CommandHandler("setgroup", setgroup))
    application.add_handler(CommandHandler("temas", temas))
    application.add_handler(CallbackQueryHandler(select_topic))
    application.add_handler(
        # Handler de temas creados
        CallbackQueryHandler(select_topic)
    )
    application.add_handler(
        # Handler evento topic creado
        CommandHandler("topic_created_dummy", lambda *_: None)
    )

    # Este sí captura el evento real
    application.add_handler(
        CallbackQueryHandler(select_topic)
    )

    application.add_handler(
        CommandHandler("start", lambda u,c: u.message.reply_text("Usa /temas"))
    )

    # Evento real de topic
    application.add_handler(
        CallbackQueryHandler(select_topic)
    )

    application.add_handler(
        CommandHandler("help", lambda u,c: u.message.reply_text("Usa /temas"))
    )

    # Evento FORUM TOPIC creado correctamente:
    application.add_handler(
        CommandHandler("dummy", lambda *_: None)
    )

    application.add_handler(
        CallbackQueryHandler(select_topic)
    )

    # Evento real
    application.add_handler(
        CommandHandler("topic", lambda *_: None)
    )

    # IMPORTANTE
    application.add_handler(
        CallbackQueryHandler(select_topic)
    )

    # Handler real para topic_created:
    from telegram.ext import MessageHandler, filters
    application.add_handler(
        MessageHandler(filters.ALL & filters.ChatType.GROUPS, topic_created)
    )

    print("Bot corriendo en Railway...")
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())
