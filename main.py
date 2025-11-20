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
USERS_FILE = DATA_DIR / "users.json"

# Tamaño de página (temas por página en listados)
PAGE_SIZE = 30
# Cuántos temas se muestran en "Recientes"
RECENT_LIMIT = 20

# Películas: paginación y búsquedas
PELIS_PAGE_SIZE = 50
PELIS_MAX_RESULTS = 500


# ======================================================
#   HELPERS (acentos / primera letra)
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
    return first.upper(), base.upper()


# ======================================================
#   LOAD / SAVE TOPICS
# ======================================================
def load_topics():
    if not TOPICS_FILE.exists():
        return {}

    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        return {}

    changed = False
    for tid, info in list(data.items()):
        if not isinstance(info, dict) or "name" not in info:
            del data[tid]
            changed = True
            continue

        info.setdefault("messages", [])
        info.setdefault("created_at", 0)
        if info.get("is_pelis"):
            info.setdefault("movies", [])

    if changed:
        save_topics(data)

    return data


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
#   LOAD / SAVE USERS
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


def register_user(user):
    if not user:
        return
    users = load_users()
    uid = str(user.id)
    entry = users.get(uid, {})
    entry["id"] = user.id
    entry["first_name"] = user.first_name
    entry["last_name"] = user.last_name
    entry["username"] = user.username
    entry["is_bot"] = bool(user.is_bot)
    users[uid] = entry
    save_users(users)


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

    # Crear nuevo tema
    if topic_id not in topics:
        topic_name = None

        # 1) Si el mensaje es el de creación del tema, usamos ese nombre
        if msg.forum_topic_created:
            topic_name = msg.forum_topic_created.name or None

        # 2) Si no, intentamos pedirle a Telegram el nombre real del tema
        if not topic_name:
            try:
                forum_topic = await context.bot.get_forum_topic(
                    chat_id=msg.chat.id,
                    message_thread_id=msg.message_thread_id,
                )
                if forum_topic and getattr(forum_topic, "name", None):
                    topic_name = forum_topic.name
            except Exception as e:
                print("[detect] Error get_forum_topic:", e)

        # 3) Fallback por si todo falla
        if not topic_name:
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

    # Asegurar estructura del tema
    topic = topics[topic_id]
    topic.setdefault("messages", [])
    if topic.get("is_pelis"):
        topic.setdefault("movies", [])

    # Guardar mensaje
    topic["messages"].append({"id": msg.message_id})

    # Si es de pelis → indexar título por caption/text
    if topic.get("is_pelis"):
        title = msg.caption or msg.text or ""
        title = title.strip()
        if title:
            topic["movies"].append({"id": msg.message_id, "title": title})

    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS
# ======================================================
def ordenar_temas(items):
    """
    Orden alfabético correcto con acentos:
    - grupo 0 → símbolos y números
    - grupo 1 → letras A-Z
      - dentro: Á antes que A
      - Ñ después de N
    """

    def clave(item):
        _tid, info = item
        n = info["name"].strip()
        if not n:
            return (3, "", 0, n)

        first, base = get_first_and_base(n)
        if not first:
            return (3, "", 0, n)

        # símbolos / números
        if not ("A" <= base <= "Z"):
            return (0, base, 0, n.lower())

        # letras
        accent_rank = 1
        base_key = base

        # caso especial Ñ
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
    letter = letter.upper()
    res = []

    for tid, info in topics.items():
        n = info["name"].strip()
        if not n:
            continue

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


# ======================================================
#   /START + /TEMAS + /USUARIOS
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Registrar usuario SIEMPRE que use /start
    register_user(update.effective_user)

    if update.effective_chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo para usar el menú 😊")
        return
    await show_main_menu(update.effective_chat, context)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(update.effective_chat, context)


async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    users = load_users()
    if not users:
        await update.message.reply_text("No hay usuarios registrados todavía.")
        return

    # Ordenar por ID numérico
    items = sorted(users.items(), key=lambda x: int(x[0]))
    lines = []
    for uid, info in items:
        first = info.get("first_name") or ""
        last = info.get("last_name") or ""
        name = (first + " " + last).strip() or "Sin nombre"
        username = info.get("username")
        line = f"• {name} — ID: {uid}"
        if username:
            line += f" — @{username}"
        lines.append(line)

    text = "👥 Usuarios registrados:\n\n" + "\n".join(lines)
    await update.message.reply_text(text)


# ======================================================
#   PÁGINAS POR LETRA
# ======================================================
def build_letter_page(letter, page, topics):
    items = filtrar_por_letra(topics, letter)
    total = len(items)

    if total == 0:
        return (
            f"📭 No hay series que empiecen por <b>{letter}</b>.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
        )

    total_pages = math.ceil(total / PAGE_SIZE)
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    subset = items[start:end]

    keyboard = [
        [InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
        for tid, info in subset
    ]

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"page:{letter}:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"page:{letter}:{page+1}"))

    keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    title = f"🎬 <b>Series que empiezan por “{letter}”</b>"
    return f"{title}\nMostrando {len(subset)} de {total}.", InlineKeyboardMarkup(keyboard)


async def on_letter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, l = q.data.split(":")
    text, markup = build_letter_page(l, 1, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def on_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, letter, p = q.data.split(":")
    p = int(p)
    text, markup = build_letter_page(letter, p, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ======================================================
#   RECENTES / BUSCAR / PELIS
# ======================================================
async def on_main_menu(update, context):
    q = update.callback_query
    await q.edit_message_text(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )
    context.user_data.pop("search_mode", None)


async def on_recent_btn(update, context):
    q = update.callback_query
    items = list(load_topics().items())
    items.sort(key=lambda x: x[1]["created_at"], reverse=True)
    items = items[:RECENT_LIMIT]

    keys = [
        [InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
        for tid, info in items
    ]
    keys.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await q.edit_message_text("🕒 <b>Series recientes</b>", parse_mode="HTML",
                              reply_markup=InlineKeyboardMarkup(keys))


async def on_search_btn(update, context):
    q = update.callback_query
    context.user_data["search_mode"] = "series"
    await q.edit_message_text(
        "🔍 Escribe parte del nombre de la serie.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
    )


async def on_pelis_btn(update, context):
    q = update.callback_query
    context.user_data["search_mode"] = "pelis"
    await q.edit_message_text(
        "🍿 Escribe parte del título de la película.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
    )


# ======================================================
#   SEARCH (SERIES / PELIS)
# ======================================================
def build_pelis_page(matches, page, pelis_tid):
    total = len(matches)
    total_pages = max(1, math.ceil(total / PELIS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * PELIS_PAGE_SIZE
    end = start + PELIS_PAGE_SIZE
    subset = matches[start:end]

    kb = []
    for mid, title in subset:
        kb.append([
            InlineKeyboardButton(f"🎬 {escape(title)}", callback_data=f"pelis_msg:{pelis_tid}:{mid}")
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"pelis_page:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"pelis_page:{page+1}"))

    kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    return InlineKeyboardMarkup(kb)


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

    # =======================
    #     BUSCAR PELIS
    # =======================
    if mode == "pelis":
        pelis_tid = get_pelis_topic_id(topics)
        if not pelis_tid:
            await chat.send_message("🍿 No hay tema de películas configurado.")
            return

        movies = topics[pelis_tid].get("movies", [])
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

        markup = build_pelis_page(matches, 1, pelis_tid)
        context.user_data["pelis_results"] = matches
        context.user_data["pelis_tid"] = pelis_tid

        await chat.send_message(
            f"🍿 Resultados para <b>{escape(text)}</b> ({len(matches)}).",
            reply_markup=markup,
            parse_mode="HTML",
        )
        return

    # =======================
    #     BUSCAR SERIES
    # =======================
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
#   PAGINACIÓN DE PELÍCULAS
# ======================================================
async def on_pelis_page(update, context):
    q = update.callback_query
    _, p = q.data.split(":")
    page = int(p)

    matches = context.user_data.get("pelis_results", [])
    tid = context.user_data.get("pelis_tid")

    markup = build_pelis_page(matches, page, tid)
    await q.edit_message_reply_markup(markup)


# ======================================================
#   SEND TOPIC
# ======================================================
async def send_topic(update, context):
    q = update.callback_query
    _, tid = q.data.split(":")
    tid = str(tid)

    topics = load_topics()
    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    await q.edit_message_text("📨 Enviando...")

    bot = context.bot
    uid = q.from_user.id

    for m in topics[tid]["messages"]:
        try:
            await bot.forward_message(chat_id=uid, from_chat_id=GROUP_ID, message_id=m["id"])
        except:
            pass

    await bot.send_message(
        uid,
        "✔ Terminado.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
    )


# ======================================================
#   ENVIAR UNA PELÍCULA
# ======================================================
async def send_peli_message(update, context):
    q = update.callback_query
    _, tid, mid = q.data.split(":")
    mid = int(mid)

    bot = context.bot
    uid = q.from_user.id

    try:
        await bot.forward_message(chat_id=uid, from_chat_id=GROUP_ID, message_id=mid)
        await bot.send_message(
            uid, "🍿 Película enviada.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]])
        )
    except:
        await q.answer("No se pudo reenviar.", show_alert=True)


# ======================================================
#   /SETPELIS
# ======================================================
async def setpelis(update, context):
    msg = update.message

    # Solo puede usarse *una vez*
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
        "created_at": msg.date.timestamp() if msg.date else 0,
    })

    topics[tid]["is_pelis"] = True
    topics[tid].setdefault("movies", [])

    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas correctamente.")


# ======================================================
#   /BORRARTEMA (solo owner, AVANZADO POR LETRA)
# ======================================================
def build_borrartema_letters_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    for i in range(0, 26, 5):
        rows.append([
            InlineKeyboardButton(l, callback_data=f"del_letter:{l}")
            for l in letters[i:i+5]
        ])

    rows.append([
        InlineKeyboardButton("#", callback_data="del_letter:#")
    ])
    return InlineKeyboardMarkup(rows)


async def borrartema(update, context):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    await update.message.reply_text(
        "🗑 Elige la letra de los temas que quieres gestionar:",
        reply_markup=build_borrartema_letters_keyboard()
    )


async def on_borrartema_letter(update, context):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("⛔ No permitido.", show_alert=True)
        return

    _, letter = q.data.split(":")
    topics = load_topics()
    items = filtrar_por_letra(topics, letter)

    if not items:
        await q.edit_message_text(
            f"❌ No hay temas que empiecen por {letter}.",
            reply_markup=build_borrartema_letters_keyboard()
        )
        return

    kb = []
    for tid, info in items:
        kb.append([
            InlineKeyboardButton(f"❌ {escape(info['name'])}", callback_data=f"del_topic:{tid}")
        ])

    kb.append([InlineKeyboardButton("⬅️ Otras letras", callback_data="del_menu")])

    await q.edit_message_text(
        f"🗑 Temas que empiezan por {letter}:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML",
    )


async def on_borrartema_menu(update, context):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("⛔ No permitido.", show_alert=True)
        return

    await q.edit_message_text(
        "🗑 Elige la letra de los temas que quieres gestionar:",
        reply_markup=build_borrartema_letters_keyboard()
    )


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
        await q.edit_message_text(f"🗑 Tema borrado:\n<b>{escape(name)}</b>", parse_mode="HTML")
    else:
        await q.edit_message_text("❌ No existe.")


# ======================================================
#   /BORRARPELI — SOLO OWNER
# ======================================================
async def borrarpeli(update, context):
    msg = update.message
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso.")
        return

    query = msg.text.replace("/borrarpeli", "").strip()
    if not query:
        await msg.reply_text("Uso: /borrarpeli título")
        return

    topics = load_topics()
    pelis_tid = get_pelis_topic_id(topics)
    if not pelis_tid:
        await msg.reply_text("🍿 No hay tema de películas.")
        return

    movies = topics[pelis_tid]["movies"]
    q = query.lower()

    matches = [(m["id"], m["title"]) for m in movies if q in m["title"].lower()]

    if not matches:
        await msg.reply_text("❌ No encontré coincidencias.")
        return

    kb = []
    for mid, title in matches:
        kb.append([InlineKeyboardButton(f"❌ {title}", callback_data=f"delpeli:{pelis_tid}:{mid}")])

    kb.append([InlineKeyboardButton("🔙 Cancelar", callback_data="main_menu")])

    await msg.reply_text(
        f"🍿 Coincidencias para <b>{escape(query)}</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def delete_peli(update, context):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.edit_message_text("⛔ No permitido.")
        return

    _, tid, mid = q.data.split(":")
    mid = int(mid)

    topics = load_topics()

    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    movies = topics[tid]["movies"]
    newlist = [m for m in movies if m["id"] != mid]
    topics[tid]["movies"] = newlist
    save_topics(topics)

    await q.edit_message_text("🗑 Película eliminada.")


# ======================================================
#   /EXPORTAR y /IMPORTAR — SOLO OWNER
# ======================================================
async def exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    if not TOPICS_FILE.exists():
        await update.message.reply_text("Aún no hay archivo topics.json para exportar.")
        return

    try:
        await update.message.reply_document(
            document=TOPICS_FILE.open("rb"),
            filename="topics_export.json",
            caption="📦 Backup del catálogo (topics.json)."
        )
    except Exception as e:
        print("Error en /exportar:", e)
        await update.message.reply_text("❌ Error al enviar el archivo.")


async def importar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    msg = update.message
    if not msg.document:
        await msg.reply_text(
            "Adjunta el archivo JSON en el mismo mensaje que /importar.\n\n"
            "Ejemplo: envía un archivo llamado topics.json y en el pie del archivo escribe /importar."
        )
        return

    doc = msg.document

    try:
        file = await doc.get_file()
        ba = await file.download_as_bytearray()
        text = ba.decode("utf-8")
        data = json.loads(text)
    except Exception as e:
        print("Error leyendo JSON en /importar:", e)
        await msg.reply_text("❌ No pude leer el JSON. Asegúrate de que el archivo es válido.")
        return

    if not isinstance(data, dict):
        await msg.reply_text("❌ El JSON debe ser un objeto (diccionario) en la raíz.")
        return

    # Sobrescribir toda la base de datos de topics
    save_topics(data)
    await msg.reply_text(f"✔ Importación completada. Temas cargados: {len(data)}")


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))
    app.add_handler(CommandHandler("usuarios", usuarios))
    app.add_handler(CommandHandler("setpelis", setpelis))
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("borrarpeli", borrarpeli))
    app.add_handler(CommandHandler("exportar", exportar))
    app.add_handler(CommandHandler("importar", importar))

    # Callbacks
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))
    app.add_handler(CallbackQueryHandler(on_borrartema_letter, pattern=r"^del_letter:"))
    app.add_handler(CallbackQueryHandler(on_borrartema_menu, pattern=r"^del_menu$"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del_topic:"))
    app.add_handler(CallbackQueryHandler(delete_peli, pattern=r"^delpeli:"))

    # Text search
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Detect messages in group
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
