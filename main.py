import os
import json
import math
import time
import unicodedata
import asyncio
from pathlib import Path
from html import escape
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile
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
#   CONFIGURACIÓN
# ======================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
OWNER_ID = 5540195020

DATA_DIR = Path("/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOPICS_FILE = DATA_DIR / "topics.json"
USERS_FILE = DATA_DIR / "users.json"

PAGE_SIZE = 30
RECENT_LIMIT = 20
PELIS_PAGE_SIZE = 50
PELIS_MAX_RESULTS = 500


# ======================================================
#   UTILIDADES
# ======================================================
def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_first_and_base(name: str):
    if not name:
        return None, None
    s = name.strip()
    if not s:
        return None, None
    first = s[0]
    decomp = unicodedata.normalize("NFD", first)
    base = decomp[0].upper()
    return first.upper(), base.upper()


# ======================================================
#   TOPICS
# ======================================================
def load_topics():
    return load_json(TOPICS_FILE, {})


def save_topics(data):
    save_json(TOPICS_FILE, data)


def get_pelis_topic_id(topics=None):
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if info.get("is_pelis"):
            return tid
    return None


# ======================================================
#   REGISTRO DE USUARIOS
# ======================================================
def load_users():
    return load_json(USERS_FILE, {})


def save_users(data):
    save_json(USERS_FILE, data)


async def register_user(update: Update):
    if not update.effective_user:
        return

    u = update.effective_user
    users = load_users()

    if str(u.id) not in users:
        users[str(u.id)] = {
            "id": u.id,
            "username": u.username or "",
            "name": u.first_name or "",
        }
        save_users(users)


# ======================================================
#   DETECTAR MENSAJES EN GRUPO
# ======================================================
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    if msg.chat.id != GROUP_ID:
        return
    if msg.message_thread_id is None:
        return

    topics = load_topics()
    tid = str(msg.message_thread_id)

    # Crear tema si no existe
    if tid not in topics:
        name = None

        if msg.forum_topic_created:
            name = msg.forum_topic_created.name

        if not name:
            try:
                t = await context.bot.get_forum_topic(
                    chat_id=GROUP_ID,
                    message_thread_id=msg.message_thread_id
                )
                if t and t.name:
                    name = t.name
            except Exception:
                pass

        if not name:
            name = f"Tema {tid}"

        topics[tid] = {
            "name": name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0
        }

        try:
            await msg.reply_text(
                f"📄 Tema detectado:\n<b>{escape(name)}</b>",
                parse_mode="HTML"
            )
        except:
            pass

    # Guardar mensaje
    topics[tid]["messages"].append({"id": msg.message_id})

    # Si es tema de pelis
    if topics[tid].get("is_pelis"):
        title = msg.caption or msg.text or ""
        if title.strip():
            topics[tid].setdefault("movies", [])
            topics[tid]["movies"].append({"id": msg.message_id, "title": title})

    save_topics(topics)


# ======================================================
#   ORDENA TEMAS (acentos / ñ / símbolos)
# ======================================================
def ordenar_temas(items):
    def clave(item):
        _tid, info = item
        n = info["name"].strip()

        first, base = get_first_and_base(n)
        if not base:
            return (3, "", 0, n)

        if not ("A" <= base <= "Z"):
            return (0, base, 0, n.lower())

        accent_rank = 1
        base_key = base

        if first == "Ñ":
            base_key = "N"
            accent_rank = 2
        else:
            if first != base:
                accent_rank = 0

        return (1, base_key, accent_rank, n.lower())

    return sorted(items, key=clave)


# ======================================================
#   FILTRAR POR LETRA
# ======================================================
def filtrar_por_letra(topics, letter):
    res = []
    for tid, info in topics.items():
        n = info["name"].strip()
        first, base = get_first_and_base(n)
        if not base:
            continue

        if letter == "#":
            if not ("A" <= base <= "Z"):
                res.append((tid, info))
        else:
            if base == letter:
                res.append((tid, info))
    return ordenar_temas(res)


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
    await register_user(update)
    if update.effective_chat.type != "private":
        return
    await show_main_menu(update.effective_chat, context)


async def temas(update, context):
    if update.effective_chat.type != "private":
        return
    await show_main_menu(update.effective_chat, context)


# ======================================================
#   PAGINACIÓN POR LETRA
# ======================================================
def build_letter_page(letter, page, topics):
    items = filtrar_por_letra(topics, letter)
    total = len(items)

    if total == 0:
        return (
            f"📭 No hay temas para “{letter}”.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))
    subset = items[(page-1)*PAGE_SIZE : page*PAGE_SIZE]

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
        f"🎬 <b>Temas por “{letter}”</b>\nMostrando {len(subset)} de {total}.",
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
    p = int(p)
    text, markup = build_letter_page(letter, p, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ======================================================
#   REENVÍO CON REINTENTOS INFINITOS (ROBUSTO)
# ======================================================
async def send_topic(update, context):
    q = update.callback_query
    _, tid = q.data.split(":")
    topics = load_topics()

    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    messages = topics[tid]["messages"]
    total = len(messages)

    if total == 0:
        await q.edit_message_text("❌ Este tema está vacío.")
        return

    await q.edit_message_text("📨 Enviando mensajes…")

    bot = context.bot
    uid = q.from_user.id

    enviados = 0
    fallados = 0

    for entry in messages:
        mid = entry["id"]

        start_time = time.time()
        enviado = False

        while True:  # Reintentos infinitos
            try:
                await bot.forward_message(uid, GROUP_ID, mid)
                enviados += 1
                enviado = True
                break
            except Exception:
                # Si falla más de 30 segundos → mensaje imposible
                if time.time() - start_time > 30:
                    fallados += 1
                    break
                await asyncio.sleep(1)

        # Anti-flood
        if enviados % 70 == 0:
            await asyncio.sleep(2)

    await bot.send_message(
        uid,
        f"✔ Envío completado.\n\n"
        f"📨 Enviados: <b>{enviados}</b>\n"
        f"⚠ Fallados: <b>{fallados}</b>\n"
        f"📁 Total registrados: <b>{total}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
    )


# ======================================================
#   PELÍCULAS (idéntico a versión anterior)
# ======================================================
async def on_pelis_btn(update, context):
    q = update.callback_query
    context.user_data["search_mode"] = "pelis"
    await q.edit_message_text(
        "🍿 Escribe parte del título.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
    )


def build_pelis_page(matches, page, tid):
    total = len(matches)
    total_pages = max(1, math.ceil(total / PELIS_PAGE_SIZE))
    page = max(1, min(page, total_pages))
    subset = matches[(page-1)*PELIS_PAGE_SIZE : page*PELIS_PAGE_SIZE]

    kb = [
        [InlineKeyboardButton(f"🎬 {escape(title)}", callback_data=f"pelis_msg:{tid}:{mid}")]
        for mid, title in subset
    ]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pelis_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pelis_page:{page+1}"))
    kb.append(nav)

    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])
    return InlineKeyboardMarkup(kb)


async def on_pelis_page(update, context):
    q = update.callback_query
    _, p = q.data.split(":")
    page = int(p)

    matches = context.user_data["pelis_results"]
    tid = context.user_data["pelis_tid"]

    markup = build_pelis_page(matches, page, tid)
    await q.edit_message_reply_markup(markup)


async def send_peli_message(update, context):
    q = update.callback_query
    _, tid, mid = q.data.split(":")
    mid = int(mid)
    uid = q.from_user.id

    try:
        await context.bot.forward_message(uid, GROUP_ID, mid)
        await context.bot.send_message(uid, "🍿 Película enviada.")
    except:
        await q.answer("No se pudo reenviar.", show_alert=True)


# ======================================================
#   /SETPELIS
# ======================================================
async def setpelis(update, context):
    msg = update.message

    topics = load_topics()
    if get_pelis_topic_id(topics):
        await msg.reply_text("🍿 Ya existe un tema de películas.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("🍿 Usa este comando dentro del tema de películas.")
        return

    tid = str(msg.message_thread_id)

    topics.setdefault(tid, {
        "name": f"Tema {tid}",
        "messages": [],
        "created_at": msg.date.timestamp() if msg.date else 0
    })
    topics[tid]["is_pelis"] = True
    topics[tid]["movies"] = []

    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas.")


# ======================================================
#   /BORRARTEMA AVANZADO
# ======================================================
async def borrartema(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    kb = []
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#"
    for row in range(0, 27, 5):
        kb.append([InlineKeyboardButton(l, callback_data=f"b_del_letter:{l}") for l in letters[row:row+5]])

    await update.message.reply_text(
        "🗑 Elige una letra:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def borrartema_letter(update, context):
    q = update.callback_query
    _, letter = q.data.split(":")

    topics = load_topics()
    items = filtrar_por_letra(topics, letter)

    if not items:
        await q.edit_message_text("❌ No hay temas.")
        return

    kb = [
        [InlineKeyboardButton(f"❌ {escape(info['name'])}", callback_data=f"b_del_topic:{tid}")]
        for tid, info in items
    ]
    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await q.edit_message_text(
        f"🗑 Elige tema para borrar ({letter}):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def borrartema_delete(update, context):
    q = update.callback_query
    _, tid = q.data.split(":")

    topics = load_topics()
    if tid in topics:
        del topics[tid]
        save_topics(topics)
        await q.edit_message_text("✔ Tema borrado.")
    else:
        await q.edit_message_text("❌ No encontrado.")


# ======================================================
#   /BORRARPELI
# ======================================================
async def borrarpeli(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No permitido.")
        return

    query = update.message.text.replace("/borrarpeli", "").strip()
    if not query:
        await update.message.reply_text("Uso: /borrarpeli título")
        return

    topics = load_topics()
    tid = get_pelis_topic_id(topics)
    if not tid:
        await update.message.reply_text("🍿 No hay tema de películas.")
        return

    movies = topics[tid].get("movies", [])
    q = query.lower()

    matches = [(m["id"], m["title"]) for m in movies if q in m["title"].lower()]

    if not matches:
        await update.message.reply_text("❌ No encontrado.")
        return

    kb = [
        [InlineKeyboardButton(f"❌ {escape(title)}", callback_data=f"delpeli:{tid}:{mid}")]
        for mid, title in matches
    ]
    kb.append([InlineKeyboardButton("Cancelar", callback_data="main_menu")])

    await update.message.reply_text(
        f"Coincidencias:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML"
    )


async def delete_peli(update, context):
    q = update.callback_query
    _, tid, mid = q.data.split(":")
    mid = int(mid)

    topics = load_topics()
    if tid not in topics:
        await q.edit_message_text("❌ No encontrado.")
        return

    topics[tid]["movies"] = [m for m in topics[tid].get("movies", []) if m["id"] != mid]
    save_topics(topics)
    await q.edit_message_text("✔ Película borrada.")


# ======================================================
#   /IMPORTAR /EXPORTAR — SOLO OWNER
# ======================================================
async def exportar(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    if not TOPICS_FILE.exists():
        await update.message.reply_text("❌ No hay topics.json")
        return

    await update.message.reply_document(InputFile(TOPICS_FILE), filename="topics.json")


async def importar(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    if not update.message.document:
        await update.message.reply_text("Sube un archivo JSON.")
        return

    doc = update.message.document
    file = await doc.get_file()
    bytes_data = await file.download_as_bytearray()

    try:
        data = json.loads(bytes_data.decode("utf-8"))
        save_topics(data)
        await update.message.reply_text("✔ Importado correctamente.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error importando: {e}")


# ======================================================
#   /USUARIOS — SOLO OWNER
# ======================================================
async def usuarios(update, context):
    if update.effective_user.id != OWNER_ID:
        return

    users = load_users()
    if not users:
        await update.message.reply_text("No hay usuarios registrados.")
        return

    text = "📋 <b>Usuarios registrados</b>\n\n"
    for u in users.values():
        text += f"• <b>{escape(u['name'])}</b> @{u['username']} — <code>{u['id']}</code>\n"

    await update.message.reply_text(text, parse_mode="HTML")


# ======================================================
#   BÚSQUEDA
# ======================================================
async def search_text(update, context):
    msg = update.message
    chat = msg.chat

    if chat.type != "private":
        return

    text = msg.text.strip()
    if not text:
        return

    mode = context.user_data.get("search_mode", "series")
    topics = load_topics()

    if mode == "pelis":
        tid = get_pelis_topic_id(topics)
        if not tid:
            await chat.send_message("🍿 No hay tema de películas configurado.")
            return

        movies = topics[tid].get("movies", [])
        q = text.lower()

        matches = []
        seen = set()
        for m in movies:
            mid = m["id"]
            title = m["title"]
            if mid in seen:
                continue
            if q in title.lower():
                matches.append((mid, title))
                seen.add(mid)

        if not matches:
            await chat.send_message(
                f"🍿 No encontré resultados para <b>{escape(text)}</b>",
                parse_mode="HTML",
            )
            return

        matches.sort(key=lambda x: x[1].lower())
        matches = matches[:PELIS_MAX_RESULTS]

        markup = build_pelis_page(matches, 1, tid)
        context.user_data["pelis_results"] = matches
        context.user_data["pelis_tid"] = tid

        await chat.send_message(
            f"🍿 Resultados para <b>{escape(text)}</b> ({len(matches)}).",
            reply_markup=markup,
            parse_mode="HTML",
        )
        return

    q = text.lower()
    found = [(tid, info) for tid, info in topics.items() if q in info["name"].lower()]

    if not found:
        await chat.send_message(
            f"🔍 No encontré series con: <b>{escape(text)}</b>",
            parse_mode="HTML",
        )
        return

    found = ordenar_temas(found)[:30]

    kb = [
        [InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
        for tid, info in found
    ]
    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await chat.send_message(
        f"🔍 Resultados para <b>{escape(text)}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CommandHandler("setpelis", setpelis))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("borrarpeli", borrarpeli))
    app.add_handler(CommandHandler("exportar", exportar))
    app.add_handler(CommandHandler("importar", importar))
    app.add_handler(CommandHandler("usuarios", usuarios))

    # Callbacks
    app.add_handler(CallbackQueryHandler(borrartema_letter, pattern=r"^b_del_letter:"))
    app.add_handler(CallbackQueryHandler(borrartema_delete, pattern=r"^b_del_topic:"))
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))
    app.add_handler(CallbackQueryHandler(delete_peli, pattern=r"^delpeli:"))

    # Búsqueda y detección
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
