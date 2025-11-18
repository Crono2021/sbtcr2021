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

# Tamaño de página (series por página en listados por letra)
PAGE_SIZE = 30
# Cuántas series se muestran en "Recientes"
RECENT_LIMIT = 20
# Tamaño de página para resultados de películas
PELIS_PAGE_SIZE = 50

# Clave especial de configuración dentro del JSON
CONFIG_KEY = "_config"


# ======================================================
#   NORMALIZACIÓN DE LETRAS (Á→A, Ñ→N, Ó→O... para agrupar)
# ======================================================
def base_letter(c: str) -> str:
    """
    Devuelve la letra base en mayúscula.
    Á → A, É → E, Ñ → N, etc.
    Si no es letra, se devuelve tal cual en mayúscula.
    """
    if not c:
        return ""
    # NFD: descompone acentos. 'Á' -> 'A' + '́'
    decomp = unicodedata.normalize("NFD", c)
    base = decomp[0].upper()
    return base


# ======================================================
#   CARGA / GUARDA TEMAS
#   ESTRUCTURA:
#   {
#       "12345": {
#           "name": "Nombre exacto del tema",
#           "messages": [{"id": 111}, {"id": 112}, ...],
#           "created_at": 1700000000.0   # timestamp (float)
#       },
#       "_config": {
#           "pelis_topic_id": "67890"
#       }
#   }
# ======================================================
def load_topics():
    if not TOPICS_FILE.exists():
        return {}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Normalizamos por si hay temas antiguos sin created_at / mensajes
            changed = False
            for tid, info in list(data.items()):
                if tid == CONFIG_KEY:
                    # Config especial, no tocamos aquí
                    continue

                if "name" not in info:
                    # Tema corrupto, lo saltamos
                    del data[tid]
                    changed = True
                    continue

                if "messages" not in info:
                    info["messages"] = []
                    changed = True

                if "created_at" not in info:
                    info["created_at"] = 0
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


def get_pelis_topic_id(topics: dict) -> str | None:
    cfg = topics.get(CONFIG_KEY, {})
    return cfg.get("pelis_topic_id")


def set_pelis_topic_id(topic_id: str):
    topics = load_topics()
    cfg = topics.get(CONFIG_KEY, {})
    cfg["pelis_topic_id"] = topic_id
    topics[CONFIG_KEY] = cfg
    save_topics(topics)


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES
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
    topics = load_topics()

    pelis_topic_id = get_pelis_topic_id(topics)

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

    # Guardar cada mensaje dentro del tema
    entry = {"id": msg.message_id}

    # Si este tema es el de PELÍCULAS, guardamos también la descripción/título
    if pelis_topic_id and topic_id == pelis_topic_id:
        desc = msg.caption or msg.text or ""
        entry["text"] = desc

    topics[topic_id]["messages"].append(entry)
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero, luego letras)
# ======================================================
def ordenar_temas(items):
    """
    items: iterable de (topic_id, info_dict)
    Devuelve lista ordenada por:
      1) símbolos / números / otros primero
      2) luego letras A-Z (normalizando acentos Á→A, Ñ→N, etc)
      3) dentro de cada grupo, por nombre (case-insensitive)
    """
    def clave(item):
        tid, info = item
        if tid == CONFIG_KEY:
            # Config, al final del todo
            return (3, "")

        nombre = info.get("name", "").strip()
        if not nombre:
            return (2, "")

        first_char = nombre[0]
        first = base_letter(first_char)

        # Si NO es letra latina A-Z => grupo símbolos/números
        if not ("A" <= first <= "Z"):
            return (0, nombre.lower())

        # Letras
        return (1, nombre.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    """
    Devuelve lista [(tid, info), ...] filtrada por primera letra.
    letter: 'A'..'Z' o '#'
    """
    letter = letter.upper()
    filtrados = []

    for tid, info in topics.items():
        if tid == CONFIG_KEY:
            continue  # no es un tema real

        nombre = info.get("name", "")
        nombre_strip = nombre.strip()
        if not nombre_strip:
            continue

        first_char = nombre_strip[0]
        base = base_letter(first_char)

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
def build_main_keyboard(show_pelis_button: bool):
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    # Filas de 5 letras
    for i in range(0, len(letters), 5):
        chunk = letters[i:i + 5]
        row = [
            InlineKeyboardButton(l, callback_data=f"letter:{l}")
            for l in chunk
        ]
        rows.append(row)

    # Fila para '#'
    rows.append([
        InlineKeyboardButton("#", callback_data="letter:#")
    ])

    # Fila Buscar + Recientes
    rows.append([
        InlineKeyboardButton("🔍 Buscar", callback_data="search"),
        InlineKeyboardButton("🕒 Recientes", callback_data="recent"),
    ])

    # Fila Películas (ancho completo) si está configurado
    if show_pelis_button:
        rows.append([
            InlineKeyboardButton("🎬 Películas", callback_data="pelis_menu")
        ])

    return InlineKeyboardMarkup(rows)


async def show_main_menu(chat, topics: dict, context: ContextTypes.DEFAULT_TYPE):
    # Por defecto, modo búsqueda de series
    user_data = context.user_data
    user_data["search_mode"] = "series"

    pelis_topic_id = get_pelis_topic_id(topics)
    show_pelis = bool(pelis_topic_id)

    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.\n"
        "Si ves el botón <b>Películas</b>, úsalo para buscar películas por título.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(show_pelis),
    )


# ======================================================
#   /START y /TEMAS → muestran MENÚ PRINCIPAL
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo para ver el catálogo 😊")
        return
    topics = load_topics()
    await show_main_menu(chat, topics, context)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    topics = load_topics()
    await show_main_menu(chat, topics, context)


# ======================================================
#   HANDLER: letra pulsada → lista paginada de series
# ======================================================
def build_letter_page(letter, page, topics_dict):
    filtrados = filtrar_por_letra(topics_dict, letter)

    total = len(filtrados)
    if total == 0:
        return (
            f"📭 No hay series que empiecen por <b>{escape(letter)}</b>.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]]),
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
        keyboard.append([
            InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")
        ])

    # Fila navegación
    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(
                InlineKeyboardButton("⬅️ Anterior", callback_data=f"page:{letter}:{page-1}")
            )
        nav_row.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton("Siguiente ➡️", callback_data=f"page:{letter}:{page+1}")
            )
    if nav_row:
        keyboard.append(nav_row)

    # Fila volver
    keyboard.append([
        InlineKeyboardButton("🔙 Volver", callback_data="main_menu")
    ])

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
#   HANDLER: botón "Volver", "Buscar", "Recientes", "Películas"
# ======================================================
async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    topics = load_topics()
    try:
        await query.edit_message_text(
            "🎬 <b>Catálogo de series</b>\n"
            "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.\n"
            "Si ves el botón <b>Películas</b>, úsalo para buscar películas por título.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(bool(get_pelis_topic_id(topics))),
        )
        context.user_data["search_mode"] = "series"
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

    try:
        await query.edit_message_text(
            "🔍 <b>Buscar serie</b>\n"
            "Escribe el nombre o parte del nombre de la serie en el chat.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
            ]),
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
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
            ]),
        )
        return

    # Ordenamos por created_at descendente
    items = [
        (tid, info)
        for tid, info in topics.items()
        if tid != CONFIG_KEY
    ]
    items.sort(key=lambda x: x[1].get("created_at", 0), reverse=True)
    items = items[:RECENT_LIMIT]

    keyboard = []
    for tid, info in items:
        safe_name = escape(info.get("name", ""))
        keyboard.append([
            InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver", callback_data="main_menu")
    ])

    try:
        await query.edit_message_text(
            "🕒 <b>Series recientes</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception as e:
        print("[on_recent_btn] Error editando mensaje:", e)


async def on_pelis_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat

    if chat.type != "private":
        await query.edit_message_text("🎬 Usa el modo Películas en privado conmigo.")
        return

    topics = load_topics()
    pelis_topic_id = get_pelis_topic_id(topics)
    if not pelis_topic_id or pelis_topic_id not in topics:
        await query.edit_message_text(
            "🎬 No hay un tema configurado como <b>Películas</b> aún.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
            ]),
        )
        return

    context.user_data["search_mode"] = "pelis"
    context.user_data.pop("pelis_search", None)

    try:
        await query.edit_message_text(
            "🎬 <b>Películas</b>\n"
            "Escribe el título o parte del título de la película que estás buscando.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
            ]),
        )
    except Exception as e:
        print("[on_pelis_menu] Error editando mensaje:", e)


# ======================================================
#   BÚSQUEDA POR TEXTO (series / películas) solo en privado
# ======================================================
def build_pelis_page(user_data, page: int):
    data = user_data.get("pelis_search")
    if not data:
        text = (
            "🎬 <b>Películas</b>\n"
            "No hay resultados cargados. Vuelve a escribir el título para buscar."
        )
        return text, InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ])

    results = data["results"]
    query_str = data["query"]

    total = len(results)
    if total == 0:
        text = (
            f"🎬 <b>Películas</b>\n"
            f"🔍 No se encontraron películas para: <b>{escape(query_str)}</b>"
        )
        return text, InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ])

    total_pages = max(1, math.ceil(total / PELIS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * PELIS_PAGE_SIZE
    end_idx = start_idx + PELIS_PAGE_SIZE
    slice_items = results[start_idx:end_idx]

    keyboard = []
    for idx, item in enumerate(slice_items, start=start_idx):
        text = item.get("text", "") or "(sin descripción)"
        short = text.strip()
        if len(short) > 40:
            short = short[:37] + "..."
        keyboard.append([
            InlineKeyboardButton(f"🎬 {short}", callback_data=f"pelis_msg:{idx}")
        ])

    # Navegación
    nav_row = []
    if total_pages > 1:
        if page > 1:
            nav_row.append(
                InlineKeyboardButton("⬅️ Anterior", callback_data=f"pelis_page:{page-1}")
            )
        nav_row.append(
            InlineKeyboardButton(f"{page}/{total_pages}", callback_data="noop")
        )
        if page < total_pages:
            nav_row.append(
                InlineKeyboardButton("Siguiente ➡️", callback_data=f"pelis_page:{page+1}")
            )
    if nav_row:
        keyboard.append(nav_row)

    # Volver
    keyboard.append([
        InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")
    ])

    header = (
        f"🎬 <b>Películas</b>\n"
        f"🔍 Resultados para: <b>{escape(query_str)}</b>\n"
        f"Mostrando {len(slice_items)} de {total}."
    )

    return header, InlineKeyboardMarkup(keyboard)


async def on_pelis_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, page_str = query.data.split(":", 1)
    page = int(page_str)

    text, markup = build_pelis_page(context.user_data, page)
    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_pelis_page] Error editando mensaje:", e)


async def on_pelis_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = context.user_data.get("pelis_search")
    if not data:
        await query.message.reply_text("⚠ No hay resultados cargados. Vuelve a buscar.")
        return

    _, idx_str = query.data.split(":", 1)
    idx = int(idx_str)

    results = data["results"]
    if idx < 0 or idx >= len(results):
        await query.message.reply_text("⚠ Esa película ya no está en la lista.")
        return

    msg_id = results[idx]["id"]

    try:
        await context.bot.forward_message(
            chat_id=query.from_user.id,
            from_chat_id=GROUP_ID,
            message_id=msg_id,
        )
    except Exception as e:
        print("[on_pelis_msg] Error reenviando película:", e)
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="❌ No se pudo reenviar esa película.",
        )
        return

    # Botón volver al catálogo
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="🔙 Volver al catálogo",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ]),
    )


async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    chat = msg.chat
    if chat.type != "private":
        return  # Ignoramos texto en grupo para búsqueda

    query_str = msg.text.strip()
    if not query_str:
        await chat.send_message("Escribe parte del nombre para buscar.")
        return

    mode = context.user_data.get("search_mode", "series")

    topics = load_topics()
    if not topics:
        await chat.send_message("📭 No hay series ni películas aún.")
        return

    # ----------------- BÚSQUEDA DE PELÍCULAS -----------------
    if mode == "pelis":
        pelis_topic_id = get_pelis_topic_id(topics)
        if not pelis_topic_id or pelis_topic_id not in topics:
            await chat.send_message(
                "🎬 No hay un tema configurado como <b>Películas</b> aún.",
                parse_mode="HTML",
            )
            return

        mensajes = topics[pelis_topic_id].get("messages", [])
        qlower = query_str.lower()

        results = []
        for entry in mensajes:
            text = entry.get("text", "")
            if text and qlower in text.lower():
                results.append({"id": entry["id"], "text": text})

        # Guardamos en user_data para paginación
        context.user_data["pelis_search"] = {
            "query": query_str,
            "results": results,
        }

        text, markup = build_pelis_page(context.user_data, 1)
        await chat.send_message(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
        return

    # ----------------- BÚSQUEDA DE SERIES (por nombre de tema) -----------------
    query_lower = query_str.lower()
    matches = [
        (tid, info)
        for tid, info in topics.items()
        if tid != CONFIG_KEY and query_lower in info.get("name", "").lower()
    ]

    if not matches:
        await chat.send_message(
            f"🔍 No encontré ninguna serie que contenga: <b>{escape(query_str)}</b>",
            parse_mode="HTML",
        )
        return

    matches = ordenar_temas(matches)
    matches = matches[:30]

    keyboard = []
    for tid, info in matches:
        safe_name = escape(info.get("name", ""))
        keyboard.append([
            InlineKeyboardButton(f"🎬 {safe_name}", callback_data=f"t:{tid}")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")
    ])

    await chat.send_message(
        f"🔍 Resultados para: <b>{escape(query_str)}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO ORDENADO (SOLO FORWARD, SIN COPY)
# ======================================================
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics or topic_id == CONFIG_KEY:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot
    user_id = query.from_user.id

    mensajes = [m["id"] for m in topics[topic_id].get("messages", [])]

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
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ]),
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

    # Excluimos CONFIG_KEY
    temas_puros = {
        tid: info for tid, info in topics.items() if tid != CONFIG_KEY
    }

    if not temas_puros:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    items = ordenar_temas(list(temas_puros.items()))

    keyboard = []
    for tid, info in items:
        safe_name = escape(info.get("name", ""))
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

    # Seguridad extra: solo OWNER
    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    topics = load_topics()
    if topic_id not in topics or topic_id == CONFIG_KEY:
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
#   /SETPERLIS — marcar el tema actual como "Películas"
# ======================================================
async def setpelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text("Usa /setpelis dentro del tema de *Películas* en el grupo.", parse_mode="Markdown")
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    if topic_id not in topics:
        # Si por lo que sea no existía, lo creamos rápido
        topic_name = msg.forum_topic_created.name if msg.forum_topic_created else f"Tema {topic_id}"
        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

    cfg = topics.get(CONFIG_KEY, {})
    cfg["pelis_topic_id"] = topic_id
    topics[CONFIG_KEY] = cfg
    save_topics(topics)

    await msg.reply_text("🎬 Este tema ha sido marcado como el tema especial de *Películas*.", parse_mode="Markdown")


# ======================================================
#   NOOP CALLBACK (para los botones informativos de página)
# ======================================================
async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Solo cerramos el "loading..." del botón sin hacer nada
    query = update.callback_query
    await query.answer()


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

    # Callbacks navegación
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_pelis_menu, pattern=r"^pelis_menu$"))
    app.add_handler(CallbackQueryHandler(on_pelis_page, pattern=r"^pelis_page:"))
    app.add_handler(CallbackQueryHandler(on_pelis_msg, pattern=r"^pelis_msg:"))
    app.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop$"))

    # Callbacks de temas (series)
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del:"))

    # Búsqueda por texto en privado
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Guardar mensajes de temas (en grupo)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
