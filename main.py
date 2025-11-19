import os
import json
import math
import unicodedata
from datetime import datetime
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
SILENCED_FILE = DATA_DIR / "silenced_topics.json"
USERS_FILE = DATA_DIR / "users.json"

# Tamaño de página (temas por página en listados)
PAGE_SIZE = 30
# Cuántos temas se muestran en "Recientes"
RECENT_LIMIT = 20
# Tamaño de página en resultados de películas
PELIS_PAGE_SIZE = 50


# ======================================================
#   HELPERS PARA ACENTOS / PRIMERA LETRA
# ======================================================
def get_first_and_base(name: str):
    """
    Devuelve (primer_caracter_original, letra_base_normalizada)
    Ej: 'Ángela' -> ('Á', 'A'), 'ñandú' -> ('ñ','N'), '1Caso' -> ('1','1')
    """
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
#   ESTRUCTURA:
#   {
#       "12345": {
#           "name": "Nombre exacto del tema",
#           "messages": [{"id": 111}, {"id": 112}, ...],
#           "created_at": 1700000000.0,
#           "is_pelis": True/False,
#           "movies": [
#               {"id": 111, "title": "Título en descripción"},
#               ...
#           ]
#       },
#       ...
#   }
# ======================================================
def load_topics():
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        changed = False
        for tid, info in list(data.items()):
            # Saneamos entradas raras
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
        print("[load_topics] ERROR cargando JSON:", e)
        return {}


def save_topics(data):
    try:
        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[save_topics] ERROR guardando JSON:", e)


# ======================================================
#   TEMAS SILENCIADOS
# ======================================================
def load_silenced():
    if not SILENCED_FILE.exists():
        return set()
    try:
        with open(SILENCED_FILE, "r", encoding="utf-8") as f:
            lst = json.load(f)
            return set(str(x) for x in lst)
    except Exception as e:
        print("[load_silenced] ERROR cargando JSON:", e)
        return set()


def save_silenced(silenced_set):
    try:
        with open(SILENCED_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(silenced_set)), f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[save_silenced] ERROR guardando JSON:", e)


def get_pelis_topic_id(topics=None):
    """Busca el tema marcado como películas."""
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if info.get("is_pelis"):
            return tid
    return None


# ======================================================
#   REGISTRO DE USUARIOS (/usuarios)
# ======================================================
def load_users():
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[load_users] ERROR cargando JSON:", e)
        return {}


def save_users(data):
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[save_users] ERROR guardando JSON:", e)


def register_user(update: Update):
    """Registra o actualiza un usuario cuando usa /start."""
    msg = update.message
    user = update.effective_user
    if user is None:
        return

    users = load_users()
    uid = str(user.id)

    if msg and msg.date:
        now_iso = msg.date.isoformat()
    else:
        now_iso = datetime.utcnow().isoformat()

    entry = users.get(uid, {})
    if "first_seen" not in entry:
        entry["first_seen"] = now_iso

    entry["id"] = user.id
    entry["username"] = user.username or ""
    entry["first_name"] = user.first_name or ""
    entry["last_name"] = user.last_name or ""
    entry["last_seen"] = now_iso

    users[uid] = entry
    save_users(users)


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (RESPETA SILENCIADOS)
# ======================================================
async def detect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    # Solo el grupo configurado
    if msg.chat.id != GROUP_ID:
        return

    # Solo mensajes dentro de un tema
    if msg.message_thread_id is None:
        return

    topic_id = str(msg.message_thread_id)

    # Si el tema está silenciado, no hacemos nada
    silenced = load_silenced()
    if topic_id in silenced:
        return

    topics = load_topics()

    # Crear registro del tema si no existía
    if topic_id not in topics:
        if msg.forum_topic_created:
            # Nombre EXACTO del tema en Telegram
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
        except Exception as e:
            print("[detect] Error al avisar tema nuevo:", e)
    else:
        # Si ya existía pero no tiene created_at (casos antiguos), lo ponemos ahora
        if "created_at" not in topics[topic_id]:
            topics[topic_id]["created_at"] = msg.date.timestamp() if msg.date else 0
        # Aseguramos estructura peli si procede
        if topics[topic_id].get("is_pelis") and "movies" not in topics[topic_id]:
            topics[topic_id]["movies"] = []

    # Guardar cada mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})

    # Si es el tema de películas, indexamos por descripción/título
    if topics[topic_id].get("is_pelis"):
        title = msg.caption or msg.text or ""
        title = title.strip()
        if title:
            topics[topic_id].setdefault("movies", [])
            topics[topic_id]["movies"].append(
                {"id": msg.message_id, "title": title}
            )

    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números → letras con acento → letras normales)
# ======================================================
def ordenar_temas(items):
    """
    items: iterable de (topic_id, info_dict)
    Orden:
      0) nombres vacíos al final
      1) símbolos / números / otros primero (grupo 0)
      2) letras A-Z (grupo 1)
      Dentro de cada grupo de letra:
          - primero acentuadas (Á...) (accent_rank 0)
          - luego normales (A...) (accent_rank 1)
          - 'Ñ' se trata como N pero con accent_rank 2 (después de N)
    """

    def clave(item):
        _tid, info = item
        nombre = info.get("name", "").strip()
        if not nombre:
            return (2, "", 0, "")  # vacíos al final

        first, base = get_first_and_base(nombre)
        if base is None:
            return (2, "", 0, nombre.lower())

        base_key = base
        upper_first = first.upper()

        # Símbolos/números: base no es A-Z
        if not ("A" <= base <= "Z"):
            return (0, base_key, 0, nombre.lower())

        # Letras A-Z
        # Caso especial Ñ: la tratamos como N pero detrás
        if upper_first == "Ñ":
            base_key = "N"
            accent_rank = 2
        else:
            # Acentuadas si difiere de la base (ej: Á vs A)
            accent_rank = 0 if upper_first != base_key else 1

        return (1, base_key, accent_rank, nombre.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    """
    Devuelve lista [(tid, info), ...] filtrada por primera letra.
    letter: 'A'..'Z' o '#'
    Usa la letra base normalizada (Á -> A, É -> E, etc).
    """
    letter = letter.upper()
    filtrados = []

    for tid, info in topics.items():
        nombre = info.get("name", "")
        nombre_strip = nombre.strip()
        if not nombre_strip:
            continue

        first, base = get_first_and_base(nombre_strip)
        if base is None:
            continue

        if letter == "#":
            # Todo lo que NO empiece por A-Z
            if not ("A" <= base <= "Z"):
                filtrados.append((tid, info))
        else:
            if base == letter:
                filtrados.append((tid, info))

    return ordenar_temas(filtrados)


# ======================================================
#   TECLADO PRINCIPAL (ABECEDARIO + Buscar + Recientes + Películas)
# ======================================================
def build_main_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    # Filas de 5 letras
    for i in range(0, len(letters), 5):
        chunk = letters[i: i + 5]
        row = [
            InlineKeyboardButton(l, callback_data=f"letter:{l}")
            for l in chunk
        ]
        rows.append(row)

    # Fila para '#'
    rows.append(
        [InlineKeyboardButton("#", callback_data="letter:#")]
    )

    # Fila Buscar + Recientes
    rows.append(
        [
            InlineKeyboardButton("🔍 Buscar series", callback_data="search"),
            InlineKeyboardButton("🕒 Recientes", callback_data="recent"),
        ]
    )

    # Fila Películas (especial)
    rows.append(
        [InlineKeyboardButton("🍿 Películas", callback_data="pelis")]
    )

    return InlineKeyboardMarkup(rows)


async def show_main_menu(chat, context: ContextTypes.DEFAULT_TYPE):
    # Reset modo de búsqueda
    context.user_data.pop("search_mode", None)
    context.user_data.pop("pelis_search", None)
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes, Películas o escribe el nombre de una serie para buscar.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


# ======================================================
#   /START y /TEMAS → MENÚ PRINCIPAL
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)

    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text(
            "Entra en privado conmigo para ver el catálogo 😊"
        )
        return
    await show_main_menu(chat, context)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(chat, context)


# ======================================================
#   HANDLER: letra pulsada → lista paginada
# ======================================================
def build_letter_page(letter, page, topics_dict):
    filtrados = filtrar_por_letra(topics_dict, letter)

    total = len(filtrados)
    if total == 0:
        return (
            f"📭 No hay series que empiecen por <b>{escape(letter)}</b>.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
            ),
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    slice_items = filtrados[start_idx:end_idx]

    keyboard = []
    for tid, info in slice_items:
        name = info.get("name", "")
        safe_name = escape(name)
        keyboard.append(
            [InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")]
        )

    # Fila navegación
    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Anterior", callback_data=f"page:{letter}:{page-1}"
                )
            )
        nav_row.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    "Siguiente ➡️", callback_data=f"page:{letter}:{page+1}"
                )
            )
    if nav_row:
        keyboard.append(nav_row)

    # Fila volver
    keyboard.append(
        [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
    )

    if letter == "#":
        title = "🎬 <b>Series que empiezan por número o símbolo</b>"
    else:
        title = f"🎬 <b>Series que empiezan por “{escape(letter)}”</b>"

    text = f"{title}\nMostrando {len(slice_items)} de {total}."

    return text, InlineKeyboardMarkup(keyboard)


async def on_letter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, letter = query.data.split(":", 1)
    topics = load_topics()

    text, markup = build_letter_page(letter, 1, topics)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_letter] Error editando mensaje:", e)


async def on_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, letter, page_str = query.data.split(":", 2)
    page = int(page_str)

    topics = load_topics()
    text, markup = build_letter_page(letter, page, topics)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_page] Error editando mensaje:", e)


# ======================================================
#   HANDLER: MAIN MENU / BUSCAR / RECIENTES / PELÍCULAS
# ======================================================
async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    try:
        context.user_data.pop("search_mode", None)
        context.user_data.pop("pelis_search", None)
        await query.edit_message_text(
            "🎬 <b>Catálogo de series</b>\n"
            "Elige una letra, pulsa Recientes, Películas o escribe el nombre de una serie para buscar.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
    except Exception as e:
        print("[on_main_menu] Error editando mensaje:", e)


async def on_search_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat

    if chat.type != "private":
        await query.edit_message_text("🔍 Usa la búsqueda en privado conmigo.")
        return

    context.user_data["search_mode"] = "series"
    context.user_data.pop("pelis_search", None)

    try:
        await query.edit_message_text(
            "🔍 <b>Buscar serie</b>\n"
            "Escribe el nombre o parte del nombre de la serie en el chat.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
            ),
        )
    except Exception as e:
        print("[on_search_btn] Error editando mensaje:", e)


async def on_recent_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    if chat.type != "private":
        await query.edit_message_text("🕒 Usa Recientes en privado conmigo.")
        return

    topics = load_topics()
    if not topics:
        await query.edit_message_text(
            "📭 No hay series aún.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
            ),
        )
        return

    # Ordenamos por created_at descendente
    items = list(topics.items())
    items.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)
    items = items[:RECENT_LIMIT]

    keyboard = []
    for tid, info in items:
        safe_name = escape(info.get("name", ""))
        keyboard.append(
            [InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")]
        )

    keyboard.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    try:
        await query.edit_message_text(
            "🕒 <b>Series recientes</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        print("[on_recent_btn] Error editando mensaje:", e)


async def on_pelis_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entrada al modo búsqueda de películas."""
    query = update.callback_query
    await query.answer()
    chat = query.message.chat

    if chat.type != "private":
        await query.edit_message_text("🍿 Usa Películas en privado conmigo.")
        return

    context.user_data["search_mode"] = "pelis"
    context.user_data.pop("pelis_search", None)

    try:
        await query.edit_message_text(
            "🍿 <b>Búsqueda de películas</b>\n"
            "Escribe el título o parte del título de la película que buscas.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
            ),
        )
    except Exception as e:
        print("[on_pelis_btn] Error editando mensaje:", e)


# ======================================================
#   PÁGINAS DE RESULTADOS DE PELÍCULAS
# ======================================================
def build_pelis_page(matches, pelis_tid, page, query_text):
    total = len(matches)
    total_pages = max(1, math.ceil(total / PELIS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PELIS_PAGE_SIZE
    end_idx = start_idx + PELIS_PAGE_SIZE
    slice_items = matches[start_idx:end_idx]

    keyboard = []
    for mid, title in slice_items:
        safe_title = escape(title)
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🎬 {safe_title}",
                    callback_data=f"pelis_msg:{pelis_tid}:{mid}",
                )
            ]
        )

    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Anterior", callback_data=f"pelis_page:{page-1}"
                )
            )
        nav_row.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    "Siguiente ➡️", callback_data=f"pelis_page:{page+1}"
                )
            )
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append(
        [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
    )

    text = (
        f"🍿 Resultados para: <b>{escape(query_text)}</b>\n"
        f"Mostrando {len(slice_items)} de {total}."
    )

    return text, InlineKeyboardMarkup(keyboard)


async def on_pelis_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("pelis_search")
    if not data:
        await query.edit_message_text(
            "🍿 La búsqueda ha expirado. Vuelve a buscar.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]
            ),
        )
        return

    _, page_str = query.data.split(":", 1)
    page = int(page_str)

    matches = data.get("matches", [])
    pelis_tid = data.get("tid")
    query_text = data.get("query", "")

    text, markup = build_pelis_page(matches, pelis_tid, page, query_text)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_pelis_page] Error editando mensaje:", e)


# ======================================================
#   BÚSQUEDA POR TEXTO (privado) — series o pelis según modo
# ======================================================
async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    chat = msg.chat
    if chat.type != "private":
        return  # Ignoramos texto en grupo para búsqueda

    query_text = msg.text.strip()
    if not query_text:
        await chat.send_message("Escribe parte del nombre para buscar.")
        return

    mode = context.user_data.get("search_mode", "series")

    topics = load_topics()
    if not topics:
        await chat.send_message("📭 No hay series aún.")
        return

    if mode == "pelis":
        # --- BÚSQUEDA EN TEMA PELÍCULAS ---
        pelis_tid = get_pelis_topic_id(topics)
        if not pelis_tid or pelis_tid not in topics:
            await chat.send_message(
                "🍿 No hay un tema de <b>Películas</b> configurado todavía.",
                parse_mode="HTML",
            )
            return

        info = topics[pelis_tid]
        movies = info.get("movies", [])
        if not movies:
            await chat.send_message(
                "🍿 Aún no hay películas indexadas.\n"
                "Sube películas con descripción al tema configurado.",
                parse_mode="HTML",
            )
            return

        q = query_text.lower()
        matches = []
        seen_ids = set()

        for m in movies:
            mid = m.get("id")
            title = m.get("title", "")
            if not mid or not title:
                continue
            if mid in seen_ids:
                continue
            if q in title.lower():
                matches.append((mid, title))
                seen_ids.add(mid)

        if not matches:
            await chat.send_message(
                f"🍿 No encontré ninguna película que contenga: "
                f"<b>{escape(query_text)}</b>",
                parse_mode="HTML",
            )
            return

        # Ordenamos por título (sin límite, paginamos luego)
        matches.sort(key=lambda x: x[1].lower())

        # Guardamos en user_data para paginación
        context.user_data["pelis_search"] = {
            "tid": pelis_tid,
            "matches": matches,
            "query": query_text,
        }

        text, markup = build_pelis_page(matches, pelis_tid, 1, query_text)

        await chat.send_message(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )

    else:
        # --- BÚSQUEDA NORMAL DE SERIES (por nombre de tema) ---
        query_lower = query_text.lower()
        matches = [
            (tid, info)
            for tid, info in topics.items()
            if query_lower in info.get("name", "").lower()
        ]

        if not matches:
            await chat.send_message(
                f"🔍 No encontré ninguna serie que contenga: <b>{escape(query_text)}</b>",
                parse_mode="HTML",
            )
            return

        # Orden y límite a 30 resultados
        matches = ordenar_temas(matches)
        matches = matches[:30]

        keyboard = []
        for tid, info in matches:
            safe_name = escape(info.get("name", ""))
            keyboard.append(
                [InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")]
            )

        keyboard.append(
            [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
        )

        await chat.send_message(
            f"🔍 Resultados para: <b>{escape(query_text)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ======================================================
#   REENVÍO ORDENADO (SOLO FORWARD, SIN COPY)
#   + Botón volver al catálogo
# ======================================================
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot
    user_id = query.from_user.id

    mensajes = [m["id"] for m in topics[topic_id]["messages"]]

    enviados = 0

    for mid in mensajes:
        try:
            await bot.forward_message(
                chat_id=user_id,
                from_chat_id=GROUP_ID,
                message_id=mid,
            )
            enviados += 1
        except Exception as e:
            print(f"[send_topic] ERROR reenviando {mid}: {e}")

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes reenviados 🎉",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]]
        ),
    )


# ======================================================
#   CALLBACK → enviar UNA película concreta
# ======================================================
async def send_peli_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, topic_id, mid_str = query.data.split(":", 2)
    topic_id = str(topic_id)
    try:
        mid = int(mid_str)
    except ValueError:
        await query.edit_message_text("❌ Película no encontrada.")
        return

    bot = context.bot
    user_id = query.from_user.id

    try:
        await bot.forward_message(
            chat_id=user_id,
            from_chat_id=GROUP_ID,
            message_id=mid,
        )
        await bot.send_message(
            chat_id=user_id,
            text="🍿 Película enviada.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]]
            ),
        )
    except Exception as e:
        print(f"[send_peli_message] ERROR reenviando peli {mid}: {e}")
        await query.edit_message_text("❌ No se pudo reenviar esa película.")


# ======================================================
#   /SETPELIS — marcar tema actual como Películas (one-shot)
# ======================================================
async def setpelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    topics = load_topics()
    existing_pelis_tid = get_pelis_topic_id(topics)
    if existing_pelis_tid:
        await msg.reply_text(
            "🍿 Ya hay un tema configurado como <b>Películas</b>.\n"
            "No se puede volver a cambiar.",
            parse_mode="HTML",
        )
        return

    # Debe ejecutarse dentro del grupo y dentro de un tema
    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text(
            "🍿 Usa /setpelis dentro del tema de <b>Películas</b> en el grupo.",
            parse_mode="HTML",
        )
        return

    topic_id = str(msg.message_thread_id)

    if topic_id not in topics:
        topic_name = msg.chat.title or f"Tema {topic_id}"
        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

    topics[topic_id]["is_pelis"] = True
    topics[topic_id].setdefault("movies", [])

    save_topics(topics)

    await msg.reply_text(
        "🍿 Este tema ha sido configurado como <b>Películas</b>.\n"
        "A partir de ahora, cada mensaje con descripción se indexará para búsquedas.",
        parse_mode="HTML",
    )


# ======================================================
#   /SILENCIO — marcar un tema para que el bot lo ignore (solo OWNER)
# ======================================================
async def silencio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    # Debe ejecutarse dentro del grupo y dentro de un tema (#general incluido)
    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text(
            "🔇 Usa /silencio dentro del tema que quieras silenciar en el grupo.",
            parse_mode="HTML",
        )
        return

    topic_id = str(msg.message_thread_id)

    silenced = load_silenced()
    if topic_id in silenced:
        await msg.reply_text("🔇 Este tema ya estaba silenciado.")
        return

    silenced.add(topic_id)
    save_silenced(silenced)

    topics = load_topics()
    nombre = topics.get(topic_id, {}).get("name", f"Tema {topic_id}")

    await msg.reply_text(
        f"🔇 Tema silenciado:\n<b>{escape(nombre)}</b>\n"
        "El bot dejará de registrar mensajes en este tema.",
        parse_mode="HTML",
    )


# ======================================================
#   /BORRARTEMA  — SOLO OWNER (con letras)
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

    # Menú de letras para borrado
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    rows = []
    for i in range(0, len(letters), 5):
        chunk = letters[i: i + 5]
        row = [
            InlineKeyboardButton(l, callback_data=f"del_letter:{l}:1")
            for l in chunk
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("#", callback_data="del_letter:#:1")])
    rows.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    await chat.send_message(
        "🗑 <b>Borrado de temas</b>\n"
        "Elige la letra de la serie que quieres borrar.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows),
    )


def build_del_letter_page(letter, page, topics_dict):
    filtrados = filtrar_por_letra(topics_dict, letter)
    total = len(filtrados)
    if total == 0:
        return (
            f"📭 No hay temas que empiecen por <b>{escape(letter)}</b>.",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Volver a letras", callback_data="del_menu"
                        )
                    ]
                ]
            ),
        )

    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    slice_items = filtrados[start_idx:end_idx]

    keyboard = []
    for tid, info in slice_items:
        safe_name = escape(info.get("name", ""))
        keyboard.append(
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del:{tid}")]
        )

    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Anterior", callback_data=f"del_page:{letter}:{page-1}"
                )
            )
        nav_row.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    "Siguiente ➡️", callback_data=f"del_page:{letter}:{page+1}"
                )
            )
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append(
        [InlineKeyboardButton("🔙 Volver a letras", callback_data="del_menu")]
    )

    if letter == "#":
        title = "🗑 <b>Temas que empiezan por número o símbolo</b>"
    else:
        title = f"🗑 <b>Temas que empiezan por “{escape(letter)}”</b>"

    text = f"{title}\nMostrando {len(slice_items)} de {total}."

    return text, InlineKeyboardMarkup(keyboard)


async def on_del_letter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    _, letter, page_str = query.data.split(":", 2)
    page = int(page_str)

    topics = load_topics()
    text, markup = build_del_letter_page(letter, page, topics)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_del_letter] Error editando mensaje:", e)


async def on_del_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    _, letter, page_str = query.data.split(":", 2)
    page = int(page_str)

    topics = load_topics()
    text, markup = build_del_letter_page(letter, page, topics)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_del_page] Error editando mensaje:", e)


async def on_del_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    rows = []
    for i in range(0, len(letters), 5):
        chunk = letters[i: i + 5]
        row = [
            InlineKeyboardButton(l, callback_data=f"del_letter:{l}:1")
            for l in chunk
        ]
        rows.append(row)
    rows.append([InlineKeyboardButton("#", callback_data="del_letter:#:1")])
    rows.append([InlineKeyboardButton("🔙 Volver", callback_data="main_menu")])

    try:
        await query.edit_message_text(
            "🗑 <b>Borrado de temas</b>\n"
            "Elige la letra de la serie que quieres borrar.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(rows),
        )
    except Exception as e:
        print("[on_del_menu] Error editando mensaje:", e)


# ======================================================
#   CALLBACK → eliminar tema
# ======================================================
async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Seguridad extra: solo OWNER
    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    _, topic_id = query.data.split(":", 1)
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
#   /USUARIOS — SOLO OWNER
# ======================================================
async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    users = load_users()
    if not users:
        await update.message.reply_text("📭 No hay usuarios registrados aún.")
        return

    items = sorted(users.values(), key=lambda u: u.get("first_seen", ""))

    lines = [f"👥 Usuarios registrados: {len(items)}", ""]
    for u in items:
        uid = u.get("id", "")
        username = u.get("username", "")
        first_name = u.get("first_name", "")
        last_name = u.get("last_name", "")
        name = " ".join(p for p in [first_name, last_name] if p).strip()
        if not name:
            name = "(sin nombre)"
        if username:
            username_display = "@" + username
        else:
            username_display = "(sin usuario)"
        first_seen = u.get("first_seen", "")[:10]
        lines.append(
            f"• <code>{uid}</code> — {username_display} — {escape(name)} — desde {first_seen}"
        )

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ======================================================
#   MAIN
# ======================================================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Comandos usuario
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("temas", temas))

    # Comandos solo owner
    app.add_handler(CommandHandler("borrartema", borrartema))
    app.add_handler(CommandHandler("reiniciar_db", reiniciar_db))
    app.add_handler(CommandHandler("setpelis", setpelis))
    app.add_handler(CommandHandler("silencio", silencio))
    app.add_handler(CommandHandler("usuarios", usuarios))

    # Callbacks navegación general
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))

    # Callbacks de temas / películas
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(on_del_letter, pattern=r"^del_letter:"))
    app.add_handler(CallbackQueryHandler(on_del_page, pattern=r"^del_page:"))
    app.add_handler(CallbackQueryHandler(on_del_menu, pattern=r"^del_menu$"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))

    # Búsqueda por texto en privado (series o pelis según modo)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Guardar mensajes de temas (en grupo)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
