import os
import json
import math
import unicodedata
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

# Tamaño de página (temas por página)
PAGE_SIZE = 30
RECENT_LIMIT = 20
PELIS_RESULT_LIMIT = 70  # límite de películas mostradas

# ======================================================
#   AYUDAS PARA ACENTOS
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
#   CARGA / GUARDA TEMAS
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
#   DETECTAR TEMAS
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

    if topic_id not in topics:
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or f"Tema {topic_id}"
        else:
            topic_name = f"Tema {topic_id}"

        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
                parse_mode="HTML",
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
        title = msg.caption or msg.text or ""
        title = title.strip()
        if title:
            topics[topic_id]["movies"].append({"id": msg.message_id, "title": title})

    save_topics(topics)

# ======================================================
#   ORDENAR TEMAS
# ======================================================
def ordenar_temas(items):
    def clave(item):
        _tid, info = item
        nombre = info.get("name", "").strip()
        if not nombre:
            return (2, "", 0, "")

        first, base = get_first_and_base(nombre)
        if base is None:
            return (2, "", 0, nombre.lower())

        base_key = base
        upper_first = first.upper()

        if not ("A" <= base <= "Z"):
            return (0, base_key, 0, nombre.lower())

        if upper_first == "Ñ":
            base_key = "N"
            accent_rank = 2
        else:
            accent_rank = 0 if upper_first != base_key else 1

        return (1, base_key, accent_rank, nombre.lower())

    return sorted(items, key=clave)

def filtrar_por_letra(topics, letter):
    letter = letter.upper()
    filtrados = []

    for tid, info in topics.items():
        nombre = info.get("name", "").strip()
        if not nombre:
            continue
        first, base = get_first_and_base(nombre)
        if base is None:
            continue

        if letter == "#":
            if not ("A" <= base <= "Z"):
                filtrados.append((tid, info))
        else:
            if base == letter:
                filtrados.append((tid, info))

    return ordenar_temas(filtrados)

# ======================================================
#   TECLADO PRINCIPAL
# ======================================================
def build_main_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    for i in range(0, len(letters), 5):
        chunk = letters[i:i+5]
        rows.append([
            InlineKeyboardButton(l, callback_data=f"letter:{l}")
            for l in chunk
        ])

    rows.append([InlineKeyboardButton("#", callback_data="letter:#")])

    rows.append([
        InlineKeyboardButton("🔍 Buscar series", callback_data="search"),
        InlineKeyboardButton("🕒 Recientes", callback_data="recent"),
    ])

    rows.append([InlineKeyboardButton("🍿 Películas", callback_data="pelis")])

    return InlineKeyboardMarkup(rows)

async def show_main_menu(chat, context):
    context.user_data.pop("search_mode", None)
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes, Películas o escribe para buscar.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )

async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_main_menu(query.message.chat, context)

# ======================================================
#   /START y /TEMAS
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo 😊")
        return
    await show_main_menu(chat, context)

async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(chat, context)

# ======================================================
#   LISTADO POR LETRA Y PÁGINAS
# ======================================================
def build_letter_page(letter, page, topics_dict):
    items = filtrar_por_letra(topics_dict, letter)
    total = len(items)

    if total == 0:
        return ("📭 No hay series.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]))

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_items = items[start:end]

    keyboard = []
    for tid, info in slice_items:
        keyboard.append([InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")])

    # Navegación
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{letter}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{letter}:{page+1}"))

    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    title = f"🎬 <b>Series por '{letter}'</b>"
    return (title, InlineKeyboardMarkup(keyboard))

async def on_letter(update: Update, context):
    q = update.callback_query
    await q.answer()
    _, letter = q.data.split(":", 1)
    items = load_topics()
    text, markup = build_letter_page(letter, 1, items)
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

async def on_page(update: Update, context):
    q = update.callback_query
    await q.answer()
    _, letter, page = q.data.split(":")
    page = int(page)
    text, markup = build_letter_page(letter, page, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)

# ======================================================
#   BUSCAR (SERIES / PELIS)
# ======================================================
async def on_search_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["search_mode"] = "series"
    await q.edit_message_text(
        "🔍 <b>Buscar serie</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
    )

async def on_pelis_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    context.user_data["search_mode"] = "pelis"
    await q.edit_message_text(
        "🍿 <b>Búsqueda de películas</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
    )

async def on_recent_btn(update: Update, context):
    q = update.callback_query
    await q.answer()
    topics = load_topics()
    items = list(topics.items())
    items.sort(key=lambda x: x[1]["created_at"], reverse=True)
    items = items[:RECENT_LIMIT]

    keyboard = [[InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
                for tid, info in items]
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await q.edit_message_text(
        "🕒 <b>Series recientes</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ======================================================
#   BÚSQUEDA POR TEXTO
# ======================================================
async def search_text(update: Update, context):
    msg = update.message
    if msg.chat.type != "private":
        return

    q = msg.text.strip()
    mode = context.user_data.get("search_mode", "series")
    topics = load_topics()

    if mode == "series":
        results = [(tid, info) for tid, info in topics.items() if q.lower() in info["name"].lower()]
        results = ordenar_temas(results)[:30]

        kb = [[InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
              for tid, info in results]
        kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

        await msg.chat.send_message(
            f"🔍 Resultados para <b>{escape(q)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb),
        )

    else:
        tid = get_pelis_topic_id(topics)
        if not tid:
            await msg.chat.send_message("🍿 Aún no hay tema Películas configurado.")
            return

        movies = topics[tid].get("movies", [])
        ql = q.lower()
        results = [(m["id"], m["title"]) for m in movies if ql in m["title"].lower()]

        results.sort(key=lambda x: x[1].lower())
        results = results[:PELIS_RESULT_LIMIT]

        kb = [[InlineKeyboardButton(f"🎬 {escape(t)}", callback_data=f"pelis_msg:{tid}:{mid}")]
              for mid, t in results]
        kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

        await msg.chat.send_message(
            f"🍿 Resultados para <b>{escape(q)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb),
        )

# ======================================================
#   ENVÍO DE SERIES
# ======================================================
async def send_topic(update: Update, context):
    q = update.callback_query
    await q.answer()

    _, tid = q.data.split(":")
    topics = load_topics()

    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    await q.edit_message_text("📨 Enviando...")

    bot = context.bot
    uid = q.from_user.id

    for m in topics[tid]["messages"]:
        try:
            await bot.forward_message(uid, GROUP_ID, m["id"])
        except:
            pass

    await bot.send_message(
        uid,
        "✔ Envío completado",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]]),
    )

# ======================================================
#   ENVÍO DE PELÍCULA
# ======================================================
async def send_peli_message(update: Update, context):
    q = update.callback_query
    await q.answer()

    _, tid, mid = q.data.split(":")
    mid = int(mid)

    bot = context.bot
    uid = q.from_user.id

    try:
        await bot.forward_message(uid, GROUP_ID, mid)
        await bot.send_message(
            uid,
            "🍿 Película enviada.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]]),
        )
    except:
        await q.edit_message_text("❌ No se pudo reenviar.")

# ======================================================
#   /SETPELIS — ONE SHOT
# ======================================================
async def setpelis(update: Update, context):
    msg = update.message
    topics = load_topics()

    if get_pelis_topic_id(topics):
        await msg.reply_text("🍿 Ya existe un tema Películas.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("🍿 Usa /setpelis dentro del tema de Películas.")
        return

    tid = str(msg.message_thread_id)

    if tid not in topics:
        topics[tid] = {
            "name": msg.chat.title or f"Tema {tid}",
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

    topics[tid]["is_pelis"] = True
    topics[tid].setdefault("movies", [])

    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas. ¡Listo!")

# ======================================================
#   ADMINISTRACIÓN
# ======================================================
async def borrartema(update: Update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    topics = load_topics()
    items = ordenar_temas(list(topics.items()))

    kb = [[InlineKeyboardButton(f"❌ {escape(info['name'])}", callback_data=f"del:{tid}")]
          for tid, info in items]

    await update.message.reply_text(
        "🗑 Selecciona tema a eliminar:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def delete_topic(update: Update, context):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        await q.edit_message_text("⛔ No tienes permiso.")
        return

    _, tid = q.data.split(":")
    topics = load_topics()

    if tid not in topics:
        await q.edit_message_text("❌ Ese tema ya no existe.")
        return

    name = topics[tid]["name"]
    del topics[tid]
    save_topics(topics)

    await q.edit_message_text(f"🗑 Tema eliminado:\n<b>{escape(name)}</b>", parse_mode="HTML")

async def reiniciar_db(update: Update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso.")
        return

    save_topics({})
    await update.message.reply_text("🗑 Base de datos reiniciada.")

# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CommandHandler("setpelis", setpelis))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))

    app.add_handler(CallbackQueryHandler(on_letter, pattern="^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern="^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern="^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern="^recent$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern="^pelis$"))

    app.add_handler(CallbackQueryHandler(send_topic, pattern="^t:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern="^pelis_msg:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern="^del:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()

if __name__ == "__main__":
    main()
