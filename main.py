# ======================================================
#   REENVÍO FIABLE (REINTENTOS INFINITOS, NO PIERDE NADA)
# ======================================================
import asyncio
from telegram.error import RetryAfter, TimedOut, NetworkError

async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema (esto puede tardar)...")

    bot = context.bot
    user_id = query.from_user.id

    mensajes = [m["id"] for m in topics[topic_id]["messages"]]
    total = len(mensajes)

    enviados = 0

    for mid in mensajes:
        while True:  # reintentos infinitos
            try:
                await bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=GROUP_ID,
                    message_id=mid,
                )
                enviados += 1
                break  # mensaje reenviado → pasamos al siguiente

            except RetryAfter as e:
                # FloodWait: Telegram te dice cuánto tiempo esperar
                wait_time = int(e.retry_after) + 1
                print(f"[send_topic] FloodWait {wait_time}s")
                await asyncio.sleep(wait_time)

            except TimedOut:
                # Timeout de red → intentamos de nuevo
                print("[send_topic] TimedOut, reintentando...")
                await asyncio.sleep(2)

            except NetworkError:
                # Problema temporal de conexión
                print("[send_topic] NetworkError, reintentando...")
                await asyncio.sleep(2)

            except Exception as e:
                # Cualquier otro error raro → esperamos y reintentamos
                print(f"[send_topic] Error inesperado reenviando {mid}: {e}")
                await asyncio.sleep(2)

        # Anti-flood suave adicional
        if enviados % 70 == 0:
            await asyncio.sleep(2)

    # Final
    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} / {total} mensajes reenviados 🎉",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]]
        ),
    )
