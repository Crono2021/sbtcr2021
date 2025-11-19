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

# Tamaño de página (temas por página en listados)
PAGE_SIZE = 30
# Cuántos temas se muestran en "Recientes"
RECENT_LIMIT = 20
# Límite de resultados en búsqueda de películas (por ahora sin paginación)
PELIS_RESULT_LIMIT = 70


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
#   CARGA / GUARDA ESTRUCTURA COMPLETA
#   Formato nuevo:
#   {
#       "topics": {...},
#       "silenced": ["12345", ...],
#       "users": {
#           "5540195020": {...}
#       }
#   }
#   También acepta formato antiguo (solo dict de topics) y migra.
# ======================================================
def load_all():
    if not TOPICS_FILE.exists():
        return {"topics": {}, "silenced": [], "users": {}}
    try:
        with open(TOPICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Si es el formato antiguo (dict de topics), migramos
        if not isinstance(data, dict) or "topics" not in data:
            data = {
                "topics": data if isinstance(data, dict) else {},
                "silenced": [],
                "users": {},
            }

        # Aseguramos claves básicas
        if "topics" not in data or not isinstance(data["topics"], dict):
            data["topics"] = {}
        if "silenced" not in data or not isinstance(data["silenced"], list):
            data["silenced"] = []
        if "users" not in data or not isinstance(data["users"], dict):
            data["users"] = {}

        # Saneamos structure de topics
        changed = False
        for tid, info in list(data["topics"].items()):
            if not isinstance(info, dict) or "name" not in info:
                # Tema corrupto
                del data["topics"][tid]
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
            save_all(data)

        return data
    except Exception as e:
        print("[load_all] ERROR cargando JSON:", e)
        return {"topics": {}, "silenced": [], "users": {}}


def save_all(data):
    try:
        # Normalizamos estructura mínima por si acaso
        data.setdefault("topics", {})
        data.setdefault("silenced", [])
        data.setdefault("users", {})

        with open(TOPICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print("[save_all] ERROR guardando JSON:", e)


def load_topics():
    return load_all().get("topics", {})


def save_topics(topics):
    data = load_all()
    data["topics"] = topics
    save_all(data)


def load_silenced():
    return load_all().get("silenced", [])


def save_silenced(silenced):
    data = load_all()
    data["silenced"] = list(silenced)
    save_all(data)


def load_users():
    return load_all().get("users", {})


def save_users(users):
    data = load_all()
    data["users"] = users
    save_all(data)


def get_pelis_topic_id(topics=None):
    """Busca el tema marcado como películas."""
    if topics is None:
        topics = load_topics()
    for tid, info in topics.items():
        if info.get("is_pelis"):
            return tid
    return None


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (RESPETA SILENCIO)
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

    data = load_all()
    topics = data["topics"]
    silenced = set(str(t) for t in data.get("silenced", []))

    # Si el tema está silenciado, no escuchamos nada
    if topic_id in silenced:
        return

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

    data["topics"] = topics
    save_all(data)


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
        if upper_first == "Ñ":
            base_key = "N"
            accent_rank = 2
        else:
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
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes, Películas o escribe el nombre de una serie para buscar.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


# ======================================================
#   /START y /TEMAS → MENÚ PRINCIPAL + registro de usuario
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user

    # Registrar usuario si está en privado
    if user is not None and chat.type == "private":
        data = load_all()
        users = data.get("users", {})
        users[str(user.id)] = {
            "id": user.id,
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
        }
        data["users"] = users
        save_all(data)

    if chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo para ver el catálogo 😊")
        return
    await show_main_menu(chat, context)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    await show_main_menu(chat, context)


# ======================================================
#   HANDLER: letra pulsada → lista paginada (para ver series)
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
        await query.edit_message_text(
            "🎬 <b>Catálogo de series</b>\n"
            "Elige una letra, pulsa Recientes, Películas o escribe el nombre de una serie para buscar.",
            parse_mode="HTML",
            reply_markup=build_main_keyboard(),
        )
        context.user_data.pop("search_mode", None)
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

        # Limitamos y ordenamos alfabéticamente por título
        matches.sort(key=lambda x: x[1].lower())
        matches = matches[:PELIS_RESULT_LIMIT]

        keyboard = []
        for mid, title in matches:
            safe_title = escape(title)
            # Callback incluye topic_id + message_id
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"🎬 {safe_title}",
                        callback_data=f"pelis_msg:{pelis_tid}:{mid}",
                    )
                ]
            )

        keyboard.append(
            [InlineKeyboardButton("🔙 Volver", callback_data="main_menu")]
        )

        await chat.send_message(
            f"🍿 Resultados para: <b>{escape(query_text)}</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
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
    # El orden ya es cronológico por cómo se van registrando

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

    # Solo owner
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    topics_all = load_all()
    topics = topics_all["topics"]

    # Si ya hay un tema de pelis, no dejamos cambiarlo (comando de un solo uso)
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

    # Aseguramos que el tema existe en la base de datos
    if topic_id not in topics:
        topic_name = msg.chat.title or f"Tema {topic_id}"
        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

    topics[topic_id]["is_pelis"] = True
    topics[topic_id].setdefault("movies", [])

    topics_all["topics"] = topics
    save_all(topics_all)

    await msg.reply_text(
        "🍿 Este tema ha sido configurado como <b>Películas</b>.\n"
        "A partir de ahora, cada mensaje con descripción se indexará para búsquedas.",
        parse_mode="HTML",
    )


# ======================================================
#   /SILENCIO y /ACTIVAR — SOLO OWNER
# ======================================================
async def silencio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    # Debe ejecutarse dentro del grupo y dentro de un tema
    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text(
            "🔇 Usa /silencio dentro del tema que quieras silenciar en el grupo.",
            parse_mode="HTML",
        )
        return

    topic_id = str(msg.message_thread_id)

    data = load_all()
    topics = data["topics"]
    silenced = set(str(t) for t in data.get("silenced", []))

    if topic_id in silenced:
        await msg.reply_text("🔇 Este tema ya estaba silenciado.")
        return

    silenced.add(topic_id)
    data["silenced"] = list(silenced)

    # Lo quitamos de la lista de temas para que no aparezca como serie
    if topic_id in topics:
        del topics[topic_id]
    data["topics"] = topics

    save_all(data)

    await msg.reply_text(
        "🔇 Este tema ha sido silenciado.\n"
        "El bot ya no guardará nada aquí ni lo mostrará en el catálogo.",
        parse_mode="HTML",
    )


async def activar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    if msg.chat.id != GROUP_ID or msg.message_thread_id is None:
        await msg.reply_text(
            "🔊 Usa /activar dentro del tema que quieras volver a activar.",
            parse_mode="HTML",
        )
        return

    topic_id = str(msg.message_thread_id)

    data = load_all()
    silenced = set(str(t) for t in data.get("silenced", []))

    if topic_id not in silenced:
        await msg.reply_text("ℹ️ Este tema no estaba silenciado.")
        return

    silenced.remove(topic_id)
    data["silenced"] = list(silenced)
    save_all(data)

    await msg.reply_text(
        "🔊 Este tema ha sido reactivado.\n"
        "El bot volverá a escuchar y registrar mensajes aquí.",
        parse_mode="HTML",
    )


# ======================================================
#   /BORRARTEMA — SOLO OWNER, con abecedario + paginación
# ======================================================
def build_borrartema_letter_keyboard():
    rows = []
    letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    for i in range(0, len(letters), 5):
        chunk = letters[i: i + 5]
        row = [
            InlineKeyboardButton(l, callback_data=f"del_letter:{l}")
            for l in chunk
        ]
        rows.append(row)

    # Fila '#'
    rows.append(
        [InlineKeyboardButton("#", callback_data="del_letter:#")]
    )

    # Fila volver al menú principal
    rows.append(
        [InlineKeyboardButton("🔙 Cancelar", callback_data="main_menu")]
    )

    return InlineKeyboardMarkup(rows)


async def borrartema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    chat = update.effective_chat
    topics = load_topics()

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    await chat.send_message(
        "🗑 <b>Borrar temas</b>\n"
        "Elige la letra por la que empieza el tema que quieres borrar.",
        parse_mode="HTML",
        reply_markup=build_borrartema_letter_keyboard(),
    )


def build_delete_page(letter, page, topics_dict):
    filtrados = filtrar_por_letra(topics_dict, letter)
    total = len(filtrados)
    if total == 0:
        return (
            f"📭 No hay temas que empiecen por <b>{escape(letter)}</b>.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Volver", callback_data="del_main_menu")]]
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
            [InlineKeyboardButton(f"❌ {safe_name}", callback_data=f"del_topic:{tid}")]
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
        [InlineKeyboardButton("🔙 Volver", callback_data="del_main_menu")]
    )

    if letter == "#":
        title = "🗑 <b>Temas por número o símbolo</b>"
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

    _, letter = query.data.split(":", 1)
    topics = load_topics()

    text, markup = build_delete_page(letter, 1, topics)
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
    text, markup = build_delete_page(letter, page, topics)

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as e:
        print("[on_del_page] Error editando mensaje:", e)


async def on_del_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Volver al selector de letras dentro del modo borrartema."""
    query = update.callback_query
    await query.answer()

    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    try:
        await query.edit_message_text(
            "🗑 <b>Borrar temas</b>\n"
            "Elige la letra por la que empieza el tema que quieres borrar.",
            parse_mode="HTML",
            reply_markup=build_borrartema_letter_keyboard(),
        )
    except Exception as e:
        print("[on_del_main_menu] Error editando mensaje:", e)


async def delete_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Seguridad extra: solo OWNER
    if query.from_user.id != OWNER_ID:
        await query.edit_message_text("⛔ No tienes permiso para esta acción.")
        return

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    data = load_all()
    topics = data["topics"]

    if topic_id not in topics:
        await query.edit_message_text("❌ Ese tema ya no existe.")
        return

    deleted_name = topics[topic_id]["name"]

    del topics[topic_id]
    data["topics"] = topics
    save_all(data)

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

    # Borra todo: topics, silenced, users
    save_all({"topics": {}, "silenced": [], "users": {}})
    await update.message.reply_text("🗑 Base de datos reiniciada.")


# ======================================================
#   /USUARIOS — SOLO OWNER, ver registro
# ======================================================
async def usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    users = load_users()
    if not users:
        await update.message.reply_text("📭 No hay usuarios registrados todavía.")
        return

    # Ordenamos por nombre, luego por id
    items = list(users.items())

    def user_key(item):
        uid, info = item
        name = (info.get("first_name") or "") + " " + (info.get("last_name") or "")
        return (name.strip().lower(), int(uid))

    items.sort(key=user_key)

    lines = []
    for uid, info in items:
        name = (info.get("first_name") or "") + " " + (info.get("last_name") or "")
        name = name.strip() or "(sin nombre)"
        username = info.get("username")
        if username:
            line = f"- {escape(name)} (@{escape(username)}) — <code>{uid}</code>"
        else:
            line = f"- {escape(name)} — <code>{uid}</code>"
        lines.append(line)

    text = "👥 <b>Usuarios registrados</b>\n" + "\n".join(lines)
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
    app.add_handler(CommandHandler("activar", activar))
    app.add_handler(CommandHandler("usuarios", usuarios))

    # Callbacks navegación general
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_pelis_btn, pattern=r"^pelis$"))

    # Callbacks borrartema (owner)
    app.add_handler(CallbackQueryHandler(on_del_letter, pattern=r"^del_letter:"))
    app.add_handler(CallbackQueryHandler(on_del_page, pattern=r"^del_page:"))
    app.add_handler(CallbackQueryHandler(on_del_main_menu, pattern=r"^del_main_menu$"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del_topic:"))

    # Callbacks de temas / películas
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(send_peli_message, pattern=r"^pelis_msg:"))

    # Búsqueda por texto en privado (series o pelis según modo)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Guardar mensajes de temas (en grupo)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
