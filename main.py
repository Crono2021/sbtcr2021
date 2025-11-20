import os
import json
import math
import unicodedata
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

# OWNER
OWNER_ID = 5540195020

# Ruta persistente Railway
DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
TOPICS_FILE = DATA_DIR / "topics.json"
USERS_FILE = DATA_DIR / "users.json"

PAGE_SIZE = 30
RECENT_LIMIT = 20
PELIS_RESULT_LIMIT = 70
USERS_PAGE_SIZE = 30


# ======================================================
#   HELPERS
# ======================================================
def get_first_and_base(name: str):
    if not name:
        return None, None
    s = name.strip()
    if not s:
        return None, None
    first = s[0]
    decomp = unicodedata.normalize("NFD", first)
    base = decomp[0].upper()
    return first, base


# ======================================================
#   CARGA/GUARDA TEMAS
# ======================================================
def load_topics():
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        changed = False
        for tid, info in list(data.items()):
            if "name" not in info:
                del data[tid]
                changed = True
                continue
            if "messages" not in info:
                info["messages"] = []
                changed = True
            if "created_at" not in info:
                info["created_at"] = 0
                changed = True
            if info.get("is_pelis") and "movies" not in info:
                info["movies"] = []
                changed = True

        if changed:
            save_topics(data)

        return data

    except Exception as e:
        print("[load_topics] ERROR:", e)
        return {}


def save_topics(data):
    try:
        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[save_topics] ERROR:", e)


def get_pelis_topic_id(topics=None):
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if info.get("is_pelis"):
            return tid
    return None


# ======================================================
#   CARGA/GUARDA USUARIOS
# ======================================================
def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_users(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def register_user_from_update(update: Update):
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    users = load_users()
    uid = str(user.id)

    if uid not in users:
        users[uid] = {
            "id": user.id,
            "name": user.full_name or user.username or f"ID {user.id}",
            "username": f"@{user.username}" if user.username else "",
            "first_seen": msg.date.timestamp() if msg.date else 0
        }
        save_users(users)


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES
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

    if topic_id in topics and topics[topic_id].get("muted"):
        return

    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0
        }

        try:
            await msg.reply_text(
                f"📄 Tema detectado:\n<b>{escape(topic_name)}</b>",
                parse_mode="HTML"
            )
        except:
            pass

    else:
        if "created_at" not in topics[topic_id]:
            topics[topic_id]["created_at"] = msg.date.timestamp() if msg.date else 0
        if topics[topic_id].get("is_pelis") and "movies" not in topics[topic_id]:
            topics[topic_id]["movies"] = []

    topics[topic_id]["messages"].append({"id": msg.message_id})

    if topics[topic_id].get("is_pelis"):
        title = (msg.caption or msg.text or "").strip()
        if title:
            topics[topic_id].setdefault("movies", [])
            topics[topic_id]["movies"].append(
                {"id": msg.message_id, "title": title}
            )

    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS
# ======================================================
def ordenar_temas(items):
    def clave(item):
        _, info = item
        name = info["name"].strip()
        if not name:
            return (3, "", 0, "")

        first, base = get_first_and_base(name)
        if not base:
            return (3, "", 0, name.lower())

        if not ("A" <= base <= "Z"):
            return (0, base, 0, name.lower())

        if first.upper() == "Ñ":
            return (1, "N", 2, name.lower())

        accent_rank = 0 if first.upper() != base else 1
        return (1, base, accent_rank, name.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    out = []
    for tid, info in topics.items():
        name = info["name"].strip()
        if not name:
            continue
        first, base = get_first_and_base(name)
        if not base:
            continue
        if letter == "#":
            if not ("A" <= base <= "Z"):
                out.append((tid, info))
        else:
            if base == letter:
                out.append((tid, info))
    return ordenar_temas(out)


# ======================================================
#   MENÚ PRINCIPAL
# ======================================================
def build_main_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    for i in range(0, 26, 5):
        rows.append([
            InlineKeyboardButton(l, callback_data=f"letter:{l}")
            for l in letters[i:i+5]
        ])

    rows.append([InlineKeyboardButton("#", callback_data="letter:#")])

    rows.append([
        InlineKeyboardButton("🔍 Buscar series", callback_data="search"),
        InlineKeyboardButton("🕒 Recientes", callback_data="recent")
    ])

    rows.append([InlineKeyboardButton("🍿 Películas", callback_data="pelis")])

    return InlineKeyboardMarkup(rows)


async def show_main_menu(chat, context):
    context.user_data.pop("search_mode", None)
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=build_main_keyboard()
    )


async def start(update, context):
    if update.effective_chat.type == "private":
        register_user_from_update(update)
        await show_main_menu(update.effective_chat, context)
    else:
        await update.message.reply_text("Entra en privado conmigo 😊")


async def temas(update, context):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(update.effective_chat, context)


# ======================================================
#   PÁGINAS POR LETRA
# ======================================================
def build_letter_page(letter, page, topics):
    filtrados = filtrar_por_letra(topics, letter)
    total = len(filtrados)

    if total == 0:
        return (
            f"📭 No hay series por <b>{escape(letter)}</b>",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    subset = filtrados[(page-1)*PAGE_SIZE : page*PAGE_SIZE]

    kb = [
        [InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
        for tid, info in subset
    ]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{letter}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{letter}:{page+1}"))
    kb.append(nav)

    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    return (
        f"🎬 <b>Series por {letter}</b>\nMostrando {len(subset)} de {total}.",
        InlineKeyboardMarkup(kb)
    )


async def on_letter(update, context):
    q = update.callback_query
    _, letter = q.data.split(":")
    text, markup = build_letter_page(letter, 1, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def on_page(update, context):
    q = update.callback_query
    _, letter, p = q.data.split(":")
    text, markup = build_letter_page(letter, int(p), load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ======================================================
#   🔥 REENVÍO FIABLE DEL TEMA (REINTENTOS INFINITOS)
# ======================================================
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":", 1)
    topics = load_topics()

    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...\nEsto puede tardar.")

    bot = context.bot
    user_id = query.from_user.id
    mensajes = [m["id"] for m in topics[topic_id]["messages"]]

    enviados = 0

    for i, mid in enumerate(mensajes, start=1):

        intento = 0

        while True:     # reintentos infinitos
            intento += 1
            try:
                await bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=GROUP_ID,
                    message_id=mid
                )
                enviados += 1
                break  # mensaje enviado → salir del loop
            except Exception as e:
                print(f"[send_topic] Error reenviando {mid}: {e} (intento {intento})")
                await asyncio.sleep(min(30, intento))  # backoff creciente

        # Anti-flood
        if enviados % 70 == 0:
            await asyncio.sleep(2)

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado.\n📨 {enviados} mensajes reenviados.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
        )
    )


# ======================================================
#   CALLBACK ENVIAR UNA PELÍCULA
# ======================================================
async def send_peli_message(update, context):
    q = update.callback_query
    await q.answer()
    _, tid, mid = q.data.split(":")
    try:
        await context.bot.forward_message(q.from_user.id, GROUP_ID, int(mid))
        await context.bot.send_message(q.from_user.id, "🍿 Película enviada.")
    except:
        await q.edit_message_text("❌ No se pudo reenviar.")


# ======================================================
#   SETPELIS / SILENCIO / ACTIVAR / BORRADOS / USUARIOS
# ======================================================
# (NO MODIFICO NADAAAA DE AQUÍ EN ADELANTE)
# ------------------------------------------------------
async def setpelis(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No permitido.")
        return

    msg = update.message
    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("🍿 Usa /setpelis en el tema correcto.")
        return

    topics = load_topics()
    if get_pelis_topic_id(topics):
        await msg.reply_text("🍿 Ya existe un tema de Películas.")
        return

    tid = str(msg.message_thread_id)
    if tid not in topics:
        topics[tid] = {
            "name": msg.chat.title or f"Tema {tid}",
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0
        }

    topics[tid]["is_pelis"] = True
    topics[tid]["movies"] = []
    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas.")


async def silencio(update, context):
    if update.effective_user.id != OWNER_ID:
        return

    msg = update.message
    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("Usa /silencio dentro del tema.")
        return

    tid = str(msg.message_thread_id)
    topics = load_topics()

    if tid not in topics:
        topics[tid] = {"name": f"Tema {tid}", "messages": [], "created_at": msg.date.timestamp()}

    topics[tid]["muted"] = True
    save_topics(topics)
    await msg.reply_text("🔇 Tema silenciado.")


async def activar(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    msg = update.message
    tid = str(msg.message_thread_id)

    topics = load_topics()
    if tid in topics and topics[tid].get("muted"):
        topics[tid]["muted"] = False
        save_topics(topics)
        await msg.reply_text("🔊 Tema activado.")
    else:
        await msg.reply_text("ℹ️ Este tema no estaba silenciado.")


async def borrartema(update, context):
    if update.effective_user.id != OWNER_ID:
        return

    rows = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#"
    for i in range(0, 27, 5):
        rows.append([
            InlineKeyboardButton(l, callback_data=f"del_letter:{l}")
            for l in letters[i:i+5]
        ])

    await update.message.reply_text(
        "🗑 Elige una letra.",
        reply_markup=InlineKeyboardMarkup(rows)
    )


def build_borrartema_letter_page(letter, page, topics):
    items = filtrar_por_letra(topics, letter)
    total = len(items)

    if total == 0:
        return ("📭 No hay temas.", InlineKeyboardMarkup([[InlineKeyboardButton("Volver", callback_data="main_menu")]]))

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    subset = items[(page-1)*PAGE_SIZE : page*PAGE_SIZE]

    kb = [
        [InlineKeyboardButton(f"❌ {escape(info['name'])}", callback_data=f"del:{tid}")]
        for tid, info in subset
    ]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"del_page:{letter}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"del_page:{letter}:{page+1}"))
    kb.append(nav)

    kb.append([InlineKeyboardButton("Volver", callback_data="main_menu")])

    return ("🗑 Elige tema:", InlineKeyboardMarkup(kb))


async def on_del_letter(update, context):
    q = update.callback_query
    _, letter = q.data.split(":")
    txt, markup = build_borrartema_letter_page(letter, 1, load_topics())
    await q.edit_message_text(txt, reply_markup=markup, parse_mode="HTML")


async def on_del_page(update, context):
    q = update.callback_query
    _, letter, page = q.data.split(":")
    txt, markup = build_borrartema_letter_page(letter, int(page), load_topics())
    await q.edit_message_text(txt, reply_markup=markup, parse_mode="HTML")


async def delete_topic(update, context):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.edit_message_text("⛔ No permitido.")
        return

    _, tid = q.data.split(":")
    topics = load_topics()

    if tid in topics:
        name = topics[tid]["name"]
        del topics[tid]
        save_topics(topics)
        await q.edit_message_text(f"🗑 Tema eliminado:\n<b>{escape(name)}</b>", parse_mode="HTML")
    else:
        await q.edit_message_text("❌ No encontrado.")


async def reiniciar_db(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


async def usuarios(update, context):
    if update.effective_user.id != OWNER_ID:
        return
    users = load_users()
    text = "👥 <b>Usuarios registrados</b>\n\n"
    for u in users.values():
        text += f"• <b>{escape(u['name'])}</b> {escape(u['username'])} — <code>{u['id']}</code>\n"
    await update.message.reply_text(text, parse_mode="HTML")


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    app.add_handler(CommandHandler("setpelis", setpelis))
    app.add_handler(CommandHandler("silencio", silencio))
    app.add_handler(CommandHandler("activar", activar))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))
    app.add_handler(CommandHandler("usuarios", usuarios))

    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(on_del_letter, pattern=r"^del_letter:"))
    app.add_handler(CallbackQueryHandler(on_del_page, pattern=r"^del_page:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del:"))

    app.add_handler(CallbackQueryHandler(lambda u, c: show_main_menu(u.callback_query.message.chat, c),
                                         pattern=r"^main_menu$"))

    app.add_handler(CallbackQueryHandler(lambda u, c: c.user_data.update({"search_mode": "series"}) or u.callback_query.edit_message_text(
        "🔍 Buscar serie", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Volver", callback_data="main_menu")]])),
        pattern=r"^search$"))

    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
