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

# Tamaño de página (temas por página en series)
PAGE_SIZE = 30
# Cuántos temas se muestran en "Recientes"
RECENT_LIMIT = 20
# Películas: 50 resultados por página
PELIS_PAGE_SIZE = 50


# ======================================================
#   HELPERS PARA ACENTOS / PRIMERA LETRA
# ======================================================
def get_first_and_base(name: str):
    """Devuelve (primer_caracter_original, letra_base_normalizada)."""
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

    except:
        return {}


def save_topics(data):
    with open(TOPICS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_pelis_topic_id(topics=None):
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if info.get("is_pelis"):
            return tid
    return None


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

    # Crear tema nuevo si no existía
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
                f"📄 Tema detectado y guardado:\n<b>{escape(topic_name)}</b>",
                parse_mode="HTML"
            )
        except:
            pass

    # Asegurar estructura películas si procede
    if topics[topic_id].get("is_pelis") and "movies" not in topics[topic_id]:
        topics[topic_id]["movies"] = []

    # Registrar el mensaje
    topics[topic_id]["messages"].append({"id": msg.message_id})

    # Registrar película si es el tema de pelis
    if topics[topic_id].get("is_pelis"):
        title = msg.caption or msg.text or ""
        title = title.strip()
        if title:
            topics[topic_id]["movies"].append(
                {"id": msg.message_id, "title": title}
            )

    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS — ACENTOS Y Ñ ORDEN CORRECTO
# ======================================================
def ordenar_temas(items):
    def clave(item):
        tid, info = item
        nombre = info.get("name", "").strip()
        if not nombre:
            return (2, "", 0, "")

        first, base = get_first_and_base(nombre)
        if base is None:
            return (2, "", 0, "")

        upper_first = first.upper()
        base_key = base

        # Símbolos y números primero
        if not ("A" <= base <= "Z"):
            return (0, base_key, 0, nombre.lower())

        # Ñ → detrás de N
        if upper_first == "Ñ":
            return (1, "N", 2, nombre.lower())

        # Vocal acentuada → antes del mismo carácter sin acento
        accent_rank = 0 if upper_first != base_key else 1

        return (1, base_key, accent_rank, nombre.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    letter = letter.upper()
    filtrados = []
    for tid, info in topics.items():
        name = info.get("name", "")
        if not name.strip():
            continue
        first, base = get_first_and_base(name)
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
#   MENU PRINCIPAL — ABECEDARIO + RECIENTES + PELÍCULAS
# ======================================================
def build_main_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    # Filas de 5
    for i in range(0, len(letters), 5):
        chunk = letters[i:i+5]
        rows.append([
            InlineKeyboardButton(l, callback_data=f"letter:{l}")
            for l in chunk
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


# ======================================================
#   START / TEMAS
# ======================================================
async def start(update, context):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo para ver el catálogo 😊")
        return
    await show_main_menu(update.effective_chat, context)


async def temas(update, context):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(update.effective_chat, context)


# ======================================================
#   LETRAS → PAGINACIÓN DE SERIES
# ======================================================
def build_letter_page(letter, page, topics):
    filtrados = filtrar_por_letra(topics, letter)
    total = len(filtrados)
    if total == 0:
        return (
            f"📭 No hay series que empiecen por <b>{escape(letter)}</b>",
            InlineKeyboardMarkup([[InlineKeyboardButton("Volver", callback_data="main_menu")]])
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page-1)*PAGE_SIZE
    end = start + PAGE_SIZE
    subset = filtrados[start:end]

    keyboard = []
    for tid, info in subset:
        nm = escape(info["name"])
        keyboard.append([InlineKeyboardButton(f"🎬 {nm}", callback_data=f"t:{tid}")])

    # navegación
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{letter}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{letter}:{page+1}"))
    keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("Volver", callback_data="main_menu")])

    title = f"🎬 <b>Series que empiezan por {letter}</b>"
    return (title, InlineKeyboardMarkup(keyboard))


async def on_letter(update, context):
    query = update.callback_query
    await query.answer()
    letter = query.data.split(":")[1]
    topics = load_topics()
    text, markup = build_letter_page(letter, 1, topics)
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")


async def on_page(update, context):
    query = update.callback_query
    await query.answer()
    _, letter, page_str = query.data.split(":")
    page = int(page_str)
    topics = load_topics()
    text, markup = build_letter_page(letter, page, topics)
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")


# ======================================================
#   BOTÓN PELÍCULAS → ENTRAR MODO BÚSQUEDA
# ======================================================
async def on_pelis_btn(update, context):
    q = update.callback_query
    await q.answer()
    if q.message.chat.type != "private":
        await q.edit_message_text("🍿 Solo en privado")
        return

    context.user_data["search_mode"] = "pelis"

    await q.edit_message_text(
        "🍿 <b>Búsqueda de películas</b>\nEscribe el título.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Volver", callback_data="main_menu")]
        ])
    )


# ======================================================
#   SISTEMA COMPLETO DE PAGINACIÓN PARA PELÍCULAS
# ======================================================
def build_pelis_page(matches, page, query_text):
    total = len(matches)
    total_pages = max(1, math.ceil(total / PELIS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page-1)*PELIS_PAGE_SIZE
    end = start + PELIS_PAGE_SIZE
    subset = matches[start:end]

    keyboard = []
    for mid, title, tid in subset:
        short = title
        if len(short) > 40:
            short = short[:37] + "…"
        keyboard.append([
            InlineKeyboardButton(
                f"🎬 {escape(short)}",
                callback_data=f"pelis_msg:{tid}:{mid}"
            )
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pelis_page:{page-1}:{query_text}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pelis_page:{page+1}:{query_text}"))
    keyboard.append(nav)

    keyboard.append([InlineKeyboardButton("Volver", callback_data="main_menu")])

    text = (
        f"🍿 <b>Películas</b>\n"
        f"Resultados para: <b>{escape(query_text)}</b>\n"
        f"Mostrando {len(subset)} de {total}."
    )

    return text, InlineKeyboardMarkup(keyboard)


async def on_pelis_page(update, context):
    q = update.callback_query
    await q.answer()
    _, page_str, query_text = q.data.split(":", 2)
    page = int(page_str)

    matches = context.user_data.get("pelis_cache", [])
    qtext = context.user_data.get("pelis_query", query_text)

    text, markup = build_pelis_page(matches, page, qtext)
    await q.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")


# ======================================================
#   BÚSQUEDA (SERIES Y PELÍCULAS)
# ======================================================
async def search_text(update, context):
    msg = update.message
    if msg.chat.type != "private":
        return

    text = msg.text.strip()
    if not text:
        return

    mode = context.user_data.get("search_mode", "series")
    topics = load_topics()

    # ====== PELÍCULAS ======
    if mode == "pelis":
        tid = get_pelis_topic_id(topics)
        if not tid:
            await msg.reply_text("🍿 No hay tema de películas configurado.")
            return

        movies = topics[tid].get("movies", [])
        q = text.lower()

        results = []
        seen = set()
        for m in movies:
            mid = m["id"]
            title = m["title"]
            if q in title.lower() and mid not in seen:
                results.append((mid, title, tid))
                seen.add(mid)

        if not results:
            await msg.reply_text(
                f"🍿 No encontré películas que contengan <b>{escape(text)}</b>",
                parse_mode="HTML"
            )
            return

        results.sort(key=lambda x: x[1].lower())
        context.user_data["pelis_cache"] = results
        context.user_data["pelis_query"] = text

        page_text, markup = build_pelis_page(results, 1, text)
        await msg.reply_text(page_text, parse_mode="HTML", reply_markup=markup)
        return

    # ====== SERIES ======
    q = text.lower()

    matches = [
        (tid, info)
        for tid, info in topics.items()
        if q in info["name"].lower()
    ]

    if not matches:
        await msg.reply_text(
            f"🔍 No encontré series que contengan <b>{escape(text)}</b>",
            parse_mode="HTML"
        )
        return

    matches = ordenar_temas(matches)[:30]

    keyboard = []
    for tid, info in matches:
        nm = escape(info["name"])
        keyboard.append([InlineKeyboardButton(f"🎬 {nm}", callback_data=f"t:{tid}")])

    keyboard.append([InlineKeyboardButton("Volver", callback_data="main_menu")])

    await msg.reply_text(
        f"🔍 Resultados para <b>{escape(text)}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ======================================================
#   ENVÍO DE SERIES (FORWARD PURO)
# ======================================================
async def send_topic(update, context):
    q = update.callback_query
    await q.answer()

    tid = q.data.split(":")[1]
    topics = load_topics()

    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    await q.edit_message_text("📨 Enviando contenido...")

    bot = context.bot
    user = q.from_user.id

    enviados = 0
    for m in topics[tid]["messages"]:
        mid = m["id"]
        try:
            await bot.forward_message(
                chat_id=user,
                from_chat_id=GROUP_ID,
                message_id=mid
            )
            enviados += 1
        except:
            pass

    await bot.send_message(
        user,
        f"✔ Envío completado ({enviados} mensajes)",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Volver", callback_data="main_menu")]
        ])
    )


# ======================================================
#   ENVÍO DE PELÍCULA INDIVIDUAL
# ======================================================
async def send_peli_message(update, context):
    q = update.callback_query
    await q.answer()

    _, tid, mid_str = q.data.split(":")
    mid = int(mid_str)

    try:
        await context.bot.forward_message(
            q.from_user.id,
            GROUP_ID,
            mid
        )
        await context.bot.send_message(
            q.from_user.id,
            "🍿 Película enviada.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Volver", callback_data="main_menu")]
            ])
        )
    except:
        await q.edit_message_text("❌ No se pudo reenviar la película.")


# ======================================================
#   SET PELÍCULAS — UNA VEZ
# ======================================================
async def setpelis(update, context):
    msg = update.message
    topics = load_topics()

    if get_pelis_topic_id(topics):
        await msg.reply_text("🍿 Ya hay un tema de películas configurado.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("🍿 Usa /setpelis dentro del tema de películas.")
        return

    tid = str(msg.message_thread_id)

    if tid not in topics:
        topics[tid] = {
            "name": msg.chat.title,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0
        }

    topics[tid]["is_pelis"] = True
    topics[tid].setdefault("movies", [])

    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas.")


# ======================================================
#   BORRAR TEMA
# ======================================================
async def borrartema(update, context):
    if update.effective_user.id != OWNER_ID:
        return

    topics = load_topics()
    items = ordenar_temas(list(topics.items()))
    kb = [
        [InlineKeyboardButton(f"❌ {escape(v['name'])}", callback_data=f"del:{k}")]
        for k, v in items
    ]

    await update.message.reply_text(
        "Selecciona un tema a borrar:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def delete_topic(update, context):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != OWNER_ID:
        return

    tid = q.data.split(":")[1]
    topics = load_topics()

    if tid in topics:
        del topics[tid]
        save_topics(topics)

    await q.edit_message_text("🗑 Tema eliminado.")


# ======================================================
#   REINICIAR DB
# ======================================================
async def reiniciar_db(update, context):
    if update.effective_user.id != OWNER_ID:
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

    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
