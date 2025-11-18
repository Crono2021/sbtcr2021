import os
import json
import math
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
# Límite de resultados de búsqueda de películas
MOVIES_LIMIT = 70


# ======================================================
#   CARGA / GUARDA TEMAS
#   ESTRUCTURA:
#   {
#       "12345": {
#           "name": "Nombre exacto del tema",
#           "messages": [
#               {"id": 111},                              # series normales
#               {"id": 222, "desc": "Pelicula X (2024)"}  # películas
#           ],
#           "created_at": 1700000000.0,
#           "is_movies": true/false (solo para Películas)
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
                if not isinstance(info, dict):
                    del data[tid]
                    changed = True
                    continue

                if "name" not in info:
                    # Tema corrupto, lo saltamos
                    del data[tid]
                    changed = True
                    continue

                if "messages" not in info or not isinstance(info["messages"], list):
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


# ======================================================
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (NO TOCAR LÓGICA BASE)
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
        # Rellenar created_at si faltara (casos antiguos)
        if "created_at" not in topics[topic_id]:
            topics[topic_id]["created_at"] = msg.date.timestamp() if msg.date else 0

    # Guardar mensaje dentro del tema
    info = topics[topic_id]

    # Si es el tema especial de películas, guardamos también descripción
    if info.get("is_movies"):
        desc = msg.caption or msg.text or ""
        info["messages"].append({
            "id": msg.message_id,
            "desc": desc,
        })
    else:
        # Tema normal (serie)
        info["messages"].append({"id": msg.message_id})

    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
#   *Ignora el tema marcado como is_movies en los listados de series*
# ======================================================
def ordenar_temas(items):
    """
    items: iterable de (topic_id, info_dict)
    Devuelve lista ordenada por:
      1) símbolos / números / otros primero
      2) luego letras A-Z
      3) dentro de cada grupo, por nombre (case-insensitive)
    """
    def clave(item):
        _tid, info = item
        nombre = info.get("name", "").strip()
        if not nombre:
            return (2, "")  # vacíos al final

        first = nombre[0].upper()
        # Símbolos/números primero
        if not first.isalpha():
            return (0, nombre.lower())
        # Letras después
        return (1, nombre.lower())

    return sorted(items, key=clave)


def filtrar_por_letra(topics, letter):
    """
    Devuelve lista [(tid, info), ...] filtrada por primera letra.
    letter: 'A'..'Z' o '#'
    NO incluye el tema especial de películas (is_movies=True).
    """
    letter = letter.upper()
    filtrados = []

    for tid, info in topics.items():
        # Saltamos el tema de películas en los listados normales
        if info.get("is_movies"):
            continue

        nombre = info.get("name", "")
        nombre_strip = nombre.strip()
        if not nombre_strip:
            continue
        first = nombre_strip[0].upper()

        if letter == "#":
            # Todo lo que NO empiece por A-Z
            if not ("A" <= first <= "Z"):
                filtrados.append((tid, info))
        else:
            if first == letter:
                filtrados.append((tid, info))

    # Ordenamos usando la misma lógica
    return ordenar_temas(filtrados)


# ======================================================
#   TECLADO PRINCIPAL (ABECEDARIO + Buscar + Recientes + Películas)
# ======================================================
def build_main_keyboard():
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

    # Fila especial Películas (ancho completo)
    rows.append([
        InlineKeyboardButton("🎬 Películas", callback_data="movies")
    ])

    return InlineKeyboardMarkup(rows)


async def show_main_menu(chat):
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.\n"
        "También puedes usar el modo especial de <b>Películas</b>.",
        parse_mode="HTML",
        reply_markup=build_main_keyboard(),
    )


# ======================================================
#   /START y /TEMAS → muestran MENÚ PRINCIPAL
# ======================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Entra en privado conmigo para ver el catálogo 😊")
        return
    # reset modo
    context.user_data.pop("mode", None)
    await show_main_menu(chat)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
    # reset modo
    context.user_data.pop("mode", None)
    await show_main_menu(chat)


# ======================================================
#   HANDLER: letra pulsada → lista paginada
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
#   HANDLER: botón "Volver" / "Buscar" / "Recientes"
# ======================================================
async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    context.user_data.pop("mode", None)
    try:
        await query.edit_message_text(
            "🎬 <b>Catálogo de series</b>\n"
            "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.\n"
            "También puedes usar el modo especial de <b>Películas</b>.",
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

    # modo búsqueda de series
    context.user_data["mode"] = "series"

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

    # Ignoramos el tema de películas en "Recientes"
    items = [
        (tid, info) for tid, info in topics.items()
        if not info.get("is_movies")
    ]
    # Ordenamos por created_at descendente
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


# ======================================================
#   MODO PELÍCULAS
# ======================================================
async def on_movies_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botón 🎬 Películas en el menú principal."""
    query = update.callback_query
    await query.answer()
    chat = query.message.chat

    if chat.type != "private":
        await query.edit_message_text("🎬 Usa el modo Películas en privado conmigo.")
        return

    topics = load_topics()
    movies_ids = [
        tid for tid, info in topics.items()
        if info.get("is_movies")
    ]

    if not movies_ids:
        await query.edit_message_text(
            "🎬 Aún no has configurado el tema especial de <b>Películas</b>.\n\n"
            "Entra en el tema de Películas en el grupo y escribe /setpelis.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
            ]),
        )
        return

    movies_tid = movies_ids[0]
    context.user_data["mode"] = "movies"
    context.user_data["movies_topic_id"] = movies_tid

    await query.edit_message_text(
        "🎬 <b>Películas</b>\n\n"
        "Escribe el título de la película que estás buscando.\n"
        "La búsqueda funciona por el <b>inicio</b> del título.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ]),
    )


async def search_movies(chat, query_text, context: ContextTypes.DEFAULT_TYPE):
    """Búsqueda dentro del tema especial Películas (por descripción/caption)."""
    topics = load_topics()
    movies_tid = context.user_data.get("movies_topic_id")

    if not movies_tid or movies_tid not in topics:
        await chat.send_message(
            "🎬 No tengo un tema de <b>Películas</b> configurado.\n"
            "En el grupo, entra en el tema de Películas y usa /setpelis.",
            parse_mode="HTML",
        )
        return

    info = topics[movies_tid]
    mensajes = info.get("messages", [])

    if not mensajes:
        await chat.send_message("📭 Aún no hay películas registradas en ese tema.")
        return

    q = query_text.strip().lower()
    if not q:
        await chat.send_message("Escribe el inicio del título de la película.")
        return

    # Coincidencia SOLO por el inicio del título (Opción A)
    matches = []
    for m in mensajes:
        desc = (m.get("desc") or "").strip()
        if not desc:
            continue
        if desc.lower().startswith(q):
            matches.append((m["id"], desc))

    if not matches:
        await chat.send_message(
            f"🔍 No he encontrado ninguna película cuyo título empiece por:\n<b>{escape(query_text)}</b>",
            parse_mode="HTML",
        )
        return

    # Limitamos a MOVIES_LIMIT
    matches = matches[:MOVIES_LIMIT]

    keyboard = []
    for mid, desc in matches:
        texto = desc.strip() or f"ID {mid}"
        if len(texto) > 60:
            texto = texto[:57] + "..."
        safe_text = escape(texto)
        keyboard.append([
            InlineKeyboardButton(f"🎬 {safe_text}", callback_data=f"movie:{movies_tid}:{mid}")
        ])

    keyboard.append([
        InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")
    ])

    await chat.send_message(
        f"🔍 Resultados en <b>Películas</b> para:\n<b>{escape(query_text)}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def send_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reenvía una película concreta desde el tema especial."""
    query = update.callback_query
    await query.answer()

    _, topic_id, mid_str = query.data.split(":", 2)
    topic_id = str(topic_id)
    try:
        mid = int(mid_str)
    except ValueError:
        await query.edit_message_text("❌ Parámetros inválidos.")
        return

    bot = context.bot
    user_id = query.from_user.id

    try:
        await bot.forward_message(
            chat_id=user_id,
            from_chat_id=GROUP_ID,
            message_id=mid,
        )
    except Exception as e:
        print(f"[send_movie] ERROR reenviando {mid}: {e}")
        await query.edit_message_text("❌ No se pudo reenviar esa película.")
        return

    # Mensaje con opciones después de enviar
    await bot.send_message(
        chat_id=user_id,
        text="🎬 Película enviada.\n\n¿Quieres hacer algo más?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")],
            [InlineKeyboardButton("🎬 Buscar otra película", callback_data="movies")],
        ]),
    )


# ======================================================
#   BÚSQUEDA POR TEXTO (series / películas según modo)
# ======================================================
async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    chat = msg.chat
    if chat.type != "private":
        return  # Ignoramos texto en grupo para búsquedas

    query_text = msg.text.strip()
    if not query_text:
        await chat.send_message("Escribe algo para buscar.")
        return

    mode = context.user_data.get("mode")

    if mode == "movies":
        # Búsqueda dentro de Películas
        await search_movies(chat, query_text, context)
        return

    # ===========================
    #  BÚSQUEDA GENERAL DE SERIES
    # ===========================
    topics = load_topics()
    if not topics:
        await chat.send_message("📭 No hay series aún.")
        return

    # Filtrado por coincidencia parcial (case-insensitive), solo series normales
    query_lower = query_text.lower()
    matches = [
        (tid, info)
        for tid, info in topics.items()
        if not info.get("is_movies")
        and query_lower in info.get("name", "").lower()
    ]

    if not matches:
        await chat.send_message(
            f"🔍 No encontré ninguna serie que contenga:\n<b>{escape(query_text)}</b>",
            parse_mode="HTML",
        )
        return

    # Orden y límite a 30 resultados
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
        f"🔍 Resultados de series para:\n<b>{escape(query_text)}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ======================================================
#   REENVÍO ORDENADO (SOLO FORWARD, SIN COPY) — SERIES
# ======================================================
async def send_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, topic_id = query.data.split(":", 1)
    topic_id = str(topic_id)

    topics = load_topics()
    info = topics.get(topic_id)

    # No usamos esto para el tema de Películas
    if not info or info.get("is_movies"):
        await query.edit_message_text("❌ Tema no encontrado o no disponible.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot
    user_id = query.from_user.id

    # Mensajes en el orden en que se guardaron (cronológico)
    mensajes = [m["id"] for m in info.get("messages", [])]

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

    # Mensaje final + botón VOLVER
    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes reenviados 🎉",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Volver al catálogo", callback_data="main_menu")]
        ]),
    )


# ======================================================
#   /SETPELIS — MARCAR TEMA ACTUAL COMO PELÍCULAS (SOLO OWNER)
# ======================================================
async def setpelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.message

    if user.id != OWNER_ID:
        await msg.reply_text("⛔ No tienes permiso para usar este comando.")
        return

    if msg.chat.id != GROUP_ID:
        await msg.reply_text("Usa /setpelis dentro del grupo, en el tema de Películas.")
        return

    if msg.message_thread_id is None:
        await msg.reply_text("Debes usar /setpelis dentro del tema de Películas.")
        return

    topic_id = str(msg.message_thread_id)
    topics = load_topics()

    # Si por lo que sea no existiera aún el tema en la DB, lo creamos
    if topic_id not in topics:
        topic_name = f"Tema {topic_id}"
        topics[topic_id] = {
            "name": topic_name,
            "messages": [],
            "created_at": msg.date.timestamp() if msg.date else 0,
        }

    # Quitamos el flag is_movies de cualquier otro tema
    for tid, info in topics.items():
        if isinstance(info, dict) and info.get("is_movies"):
            info["is_movies"] = False

    topics[topic_id]["is_movies"] = True
    save_topics(topics)

    await msg.reply_text(
        f"🎬 El tema <b>{escape(topics[topic_id]['name'])}</b> se ha establecido como tema especial de <b>Películas</b>.",
        parse_mode="HTML",
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

    if not topics:
        await chat.send_message("📭 No hay temas para borrar.")
        return

    # Incluimos también el tema de películas aquí, por si quieres borrarlo
    items = ordenar_temas(list(topics.items()))

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

    # Callbacks navegación catálogo
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))
    app.add_handler(CallbackQueryHandler(on_movies_btn, pattern=r"^movies$"))

    # Callbacks de temas y películas
    app.add_handler(CallbackQueryHandler(send_topic, pattern=r"^t:"))
    app.add_handler(CallbackQueryHandler(delete_topic, pattern=r"^del:"))
    app.add_handler(CallbackQueryHandler(send_movie, pattern=r"^movie:"))

    # Búsqueda por texto en privado (series / películas según modo)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_text))

    # Guardar mensajes de temas (en grupo)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, detect))

    print("BOT LISTO ✔")
    app.run_polling()


if __name__ == "__main__":
    main()
