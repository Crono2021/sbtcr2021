import os
import json
import math
import time
import asyncio
import unicodedata
from pathlib import Path
from html import escape

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
    filters,
)
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden, BadRequest

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
#   UTILIDADES JSON
# ======================================================
def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


# ======================================================
#   UTILIDADES TEXTO / ORDEN
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


def ordenar_temas(items):
    """
    Orden alfabético correcto con acentos:
    - grupo 0 → símbolos y números
    - grupo 1 → letras A-Z
      - Á antes que A
      - Ñ después de N
    """

    def clave(item):
        _tid, info = item
        n = (info.get("name") or "").strip()

        if not n:
            return (3, "", 0, "")

        first, base = get_first_and_base(n)
        if not base:
            return (3, "", 0, n)

        # Símbolos / números
        if not ("A" <= base <= "Z"):
            return (0, base, 0, n.lower())

        # Letras
        accent_rank = 1
        base_key = base

        # Ñ
        if first == "Ñ":
            base_key = "N"
            accent_rank = 2
        else:
            # Á, É, etc. (primer char != base)
            if first != base:
                accent_rank = 0

        return (1, base_key, accent_rank, n.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    letter = letter.upper()
    res = []

    for tid, info in topics.items():
        if not isinstance(info, dict):
            continue
        n = (info.get("name") or "").strip()
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
#   TOPICS
# ======================================================
def load_topics():
    data = load_json(TOPICS_FILE, {})
    changed = False

    # Limpiar entradas raras (ej: _group_id, errores, etc.)
    for tid in list(data.keys()):
        info = data[tid]
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
    save_json(TOPICS_FILE, data)


def get_pelis_topic_id(topics=None):
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if isinstance(info, dict) and info.get("is_pelis"):
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
#   DETECTAR MENSAJES EN EL GRUPO
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

        # 1) Mensaje de creación de topic
        if msg.forum_topic_created:
            name = msg.forum_topic_created.name

        # 2) Intentar preguntarle a Telegram el nombre exacto
        if not name:
            try:
                t = await context.bot.get_forum_topic(
                    chat_id=GROUP_ID,
                    message_thread_id=msg.message_thread_id
                )
                if t and getattr(t, "name", None):
                    name = t.name
            except Exception as e:
                print("[detect] Error get_forum_topic:", e)

        # 3) Fallback
        if not name:
            name = f"Tema {tid}"

        topics[tid] = {
            "name": name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

        try:
            await msg.reply_text(
                f"📄 Tema detectado y guardado:\n<b>{escape(name)}</b>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Asegurar estructura
    topic = topics[tid]
    topic.setdefault("messages", [])
    if topic.get("is_pelis"):
        topic.setdefault("movies", [])

    # Guardar el mensaje
    topic["messages"].append({"id": msg.message_id})

    # Si es tema de pelis, indexar título
    if topic.get("is_pelis"):
        title = msg.caption or msg.text or ""
        title = title.strip()
        if title:
            topic["movies"].append({"id": msg.message_id, "title": title})

    save_topics(topics)


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


async def show_main_menu(chat, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("search_mode", None)
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes, Películas o escribe para buscar.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)

    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "Entra en privado conmigo para usar el catálogo 😊"
        )
        return

    await show_main_menu(update.effective_chat, context)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(update.effective_chat, context)


async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    context.user_data.pop("search_mode", None)
    await q.edit_message_text(
        "🎬 <b>Catálogo de series</b>",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


# ======================================================
#   LISTADO POR LETRA (CON PAGINACIÓN)
# ======================================================
def build_letter_page(letter, page, topics):
    items = filtrar_por_letra(topics, letter)
    total = len(items)

    if total == 0:
        return (
            f"📭 No hay series que empiecen por <b>{letter}</b>.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    subset = items[start:end]

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

    text = f"🎬 <b>Series que empiezan por “{letter}”</b>\nMostrando {len(subset)} de {total}."
    return text, InlineKeyboardMarkup(kb)


async def on_letter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, letter = q.data.split(":")
    text, markup = build_letter_page(letter, 1, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


async def on_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, letter, p = q.data.split(":")
    p = int(p)
    text, markup = build_letter_page(letter, p, load_topics())
    await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)


# ======================================================
#   RECIENTES / BUSCAR / PELIS (BOTONES)
# ======================================================
async def on_recent_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    topics = load_topics()
    items = list(topics.items())
    items = [it for it in items if isinstance(it[1], dict)]
    items.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)
    items = items[:RECENT_LIMIT]

    kb = [
        [InlineKeyboardButton(f"🎬 {escape(info['name'])}", callback_data=f"t:{tid}")]
        for tid, info in items
    ]
    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await q.edit_message_text(
        "🕒 <b>Series recientes</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def on_search_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    context.user_data["search_mode"] = "series"
    await q.edit_message_text(
        "🔍 Escribe parte del nombre de la serie.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
    )


async def on_pelis_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    context.user_data["search_mode"] = "pelis"
    await q.edit_message_text(
        "🍿 Escribe parte del título de la película.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
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
            InlineKeyboardButton(
                f"🎬 {escape(title)}",
                callback_data=f"pelis_msg:{pelis_tid}:{mid}",
            )
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


async def on_pelis_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, p = q.data.split(":")
    page = int(p)

    matches = context.user_data.get("pelis_results", [])
    tid = context.user_data.get("pelis_tid")

    markup = build_pelis_page(matches, page, tid)
    await q.edit_message_reply_markup(markup)


async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    chat = msg.chat
    if chat.type != "private":
        return

    await register_user(update)

    text = (msg.text or "").strip()
    if not text:
        return

    mode = context.user_data.get("search_mode", "series")
    topics = load_topics()

    # =======================
    #   PELÍCULAS
    # =======================
    if mode == "pelis":
        pelis_tid = get_pelis_topic_id(topics)
        if not pelis_tid or pelis_tid not in topics:
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
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # =======================
    #   SERIES (TEMAS)
    # =======================
    q = text.lower()
    found = [
        (tid, info)
        for tid, info in topics.items()
        if isinstance(info, dict) and q in (info.get("name", "").lower())
    ]

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
#   REENVIAR TEMA (REENVÍOS CON REINTENTOS)
# ======================================================
async def send_peli_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, tid, mid = q.data.split(":")
    mid = int(mid)
    uid = q.from_user.id

    try:
        await context.bot.forward_message(chat_id=uid, from_chat_id=GROUP_ID, message_id=mid)
        await context.bot.send_message(uid, "🍿 Película enviada.")
    except Exception:
        await q.answer("No se pudo reenviar.", show_alert=True)


async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, tid = q.data.split(":")
    tid = str(tid)

    topics = load_topics()
    if tid not in topics or not isinstance(topics[tid], dict):
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    msgs = topics[tid].get("messages", [])
    total = len(msgs)

    if total == 0:
        await q.edit_message_text("❌ Tema vacío.")
        return

    await q.edit_message_text("📨 Enviando mensajes…")

    bot = context.bot
    uid = q.from_user.id
    sent = 0

    for entry in msgs:
        mid = entry["id"]

        # Reintentos "infinitos" para errores temporales
        while True:
            try:
                await bot.forward_message(chat_id=uid, from_chat_id=GROUP_ID, message_id=mid)
                break  # OK → siguiente mensaje
            except RetryAfter as e:
                # Telegram nos dice cuánto tiempo esperar
                await asyncio.sleep(e.retry_after)
            except (TimedOut, NetworkError):
                # Problema de red → reintentar tras pequeña pausa
                await asyncio.sleep(1)
            except Forbidden:
                # No podemos enviarle nada a este usuario → abortamos
                await bot.send_message(
                    uid,
                    "⛔ No puedo enviarte mensajes (quizás me bloqueaste).",
                )
                return
            except BadRequest:
                # Mensaje inexistente u otro error permanente → lo saltamos
                break
            except Exception:
                # Cualquier otra cosa rara → esperamos un poco y seguimos
                await asyncio.sleep(1)
                break

        sent += 1

        # Pausa anti-flood
        if sent % 70 == 0:
            await asyncio.sleep(2)

    await bot.send_message(
        uid,
        f"✔ Enviados {sent} mensajes.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
        ),
    )


# ======================================================
#   /SETPELIS
# ======================================================
async def setpelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    topics = load_topics()
    if get_pelis_topic_id(topics):
        await msg.reply_text("🍿 Ya existe un tema de películas.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("🍿 Usa este comando dentro del tema de películas.")
        return

    tid = str(msg.message_thread_id)

    topics.setdefault(
        tid,
        {
            "name": f"Tema {tid}",
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        },
    )
    topics[tid]["is_pelis"] = True
    topics[tid].setdefault("movies", [])

    save_topics(topics)

    await msg.reply_text("🍿 Tema configurado como Películas correctamente.")


# ======================================================
#   /BORRARTEMA AVANZADO (OWNER)
# ======================================================
async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#"
    kb = []
    for i in range(0, len(letters), 5):
        kb.append([
            InlineKeyboardButton(l, callback_data=f"b_letter:{l}")
            for l in letters[i:i+5]
        ])

    await update.message.reply_text(
        "🗑 Elige una letra para borrar temas:",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def borrartema_letter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("⛔ Sin permiso.", show_alert=True)
        return

    _, letter = q.data.split(":")

    topics = load_topics()
    items = filtrar_por_letra(topics, letter)

    if not items:
        await q.edit_message_text("❌ No hay temas con esa letra.")
        return

    kb = [
        [InlineKeyboardButton(f"❌ {escape(info['name'])}", callback_data=f"b_del:{tid}")]
        for tid, info in items
    ]
    kb.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await q.edit_message_text(
        f"🗑 Elige el tema a borrar (letra {letter}):",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def borrartema_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("⛔ Sin permiso.", show_alert=True)
        return

    _, tid = q.data.split(":")
    topics = load_topics()

    if tid in topics:
        name = topics[tid].get("name", "")
        del topics[tid]
        save_topics(topics)
        await q.edit_message_text(f"✔ Tema borrado: <b>{escape(name)}</b>", parse_mode="HTML")
    else:
        await q.edit_message_text("❌ Tema no encontrado.")


# ======================================================
#   /BORRARPELI — SOLO OWNER
# ======================================================
async def borrarpeli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso.")
        return

    query = msg.text.replace("/borrarpeli", "", 1).strip()
    if not query:
        await msg.reply_text("Uso: /borrarpeli título")
        return

    topics = load_topics()
    pelis_tid = get_pelis_topic_id(topics)
    if not pelis_tid or pelis_tid not in topics:
        await msg.reply_text("🍿 No hay tema de películas.")
        return

    movies = topics[pelis_tid].get("movies", [])
    q = query.lower()

    matches = [(m["id"], m["title"]) for m in movies if q in m["title"].lower()]

    if not matches:
        await msg.reply_text("❌ No encontré coincidencias.")
        return

    kb = []
    for mid, title in matches:
        kb.append([
            InlineKeyboardButton(
                f"❌ {escape(title)}",
                callback_data=f"delpeli:{pelis_tid}:{mid}",
            )
        ])

    kb.append([InlineKeyboardButton("🔙 Cancelar", callback_data="main_menu")])

    await msg.reply_text(
        f"🍿 Coincidencias para <b>{escape(query)}</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def delete_peli(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != OWNER_ID:
        await q.answer("⛔ Sin permiso.", show_alert=True)
        return

    _, tid, mid = q.data.split(":")
    mid = int(mid)

    topics = load_topics()
    if tid not in topics:
        await q.edit_message_text("❌ Tema no encontrado.")
        return

    movies = topics[tid].get("movies", [])
    newlist = [m for m in movies if m["id"] != mid]
    topics[tid]["movies"] = newlist
    save_topics(topics)

    await q.edit_message_text("🗑 Película eliminada.")


# ======================================================
#   /EXPORTAR /IMPORTAR — SOLO OWNER
# ======================================================
async def exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    if not TOPICS_FILE.exists():
        await update.message.reply_text("❌ No hay topics.json todavía.")
        return

    await update.message.reply_document(
        document=InputFile(TOPICS_FILE),
        filename="topics.json",
        caption="Backup de topics.json",
    )


async def importar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ Sin permiso.")
        return

    if not update.message.document:
        await update.message.reply_text(
            "📥 Envía /importar adjuntando el archivo topics.json como documento."
        )
        return

    doc = update.message.document
    file = await doc.get_file()
    bytes_data = await file.download_as_bytearray()

    try:
        data = json.loads(bytes_data.decode("utf-8"))
        save_topics(data)
        await update.message.reply_text("✔ topics.json importado correctamente.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error importando JSON: {e}")


# ======================================================
#   /USUARIOS — SOLO OWNER
# ======================================================
async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return

    users = load_users()
    if not users:
        await update.message.reply_text("No hay usuarios registrados.")
        return

    text = "📋 <b>Usuarios registrados</b>\n\n"
    for u in users.values():
        name = escape(u.get("name", ""))
        username = u.get("username") or ""
        uid = u.get("id")
        if username:
            text += f"• <b>{name}</b> @{username} — <code>{uid}</code>\n"
        else:
            text += f"• <b>{name}</b> — <code>{uid}</code>\n"

    await update.message.reply_text(text, parse_mode="HTML")


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

    # Callbacks de navegación
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))

    # Callbacks de temas / pelis
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))
    app.add_handler(CallbackQueryHandler(borrartema_letter, pattern=r"^b_letter:"))
    app.add_handler(CallbackQueryHandler(borrartema_delete, pattern=r"^b_del:"))
    app.add_handler(CallbackQueryHandler(delete_peli, pattern=r"^delpeli:"))

    # Búsqueda por texto (privado)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Detección de mensajes en el grupo (temas / pelis)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
