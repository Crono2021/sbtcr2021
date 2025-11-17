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


# ======================================================
#   CARGA / GUARDA TEMAS
#   ESTRUCTURA:
#   {
#       "12345": {
#           "name": "Nombre exacto del tema",
#           "messages": [{"id": 111}, {"id": 112}, ...],
#           "created_at": 1700000000.0   # timestamp (float)
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
            # Normalizamos por si hay temas antiguos sin created_at
            changed = False
            for tid, info in list(data.items()):
                if "name" not in info:
                    # Tema corrupto, lo saltamos
                    del data[tid]
                    changed = True
                    continue
                if "messages" not in info:
                    info["messages"] = []
                    changed = True
                if "created_at" not in info:
                    # Si no hay fecha, ponemos 0 para que quede al final en "recientes"
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
#   DETECTAR TEMAS Y GUARDAR MENSAJES  (NO TOCAR LÓGICA)
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
        # Si ya existía pero no tiene created_at (casos antiguos), lo ponemos ahora
        if "created_at" not in topics[topic_id]:
            topics[topic_id]["created_at"] = msg.date.timestamp() if msg.date else 0

    # Guardar cada mensaje dentro del tema
    topics[topic_id]["messages"].append({"id": msg.message_id})
    save_topics(topics)


# ======================================================
#   ORDENAR TEMAS (símbolos/números primero)
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

        primer = nombre[0]
        # Si NO es letra (número, símbolo, etc) => grupo 0 (primero)
        if not primer.isalpha():
            return (0, nombre.lower())
        # Letras => grupo 1
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
#   TECLADO PRINCIPAL (ABECEDARIO + Buscar + Recientes)
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

    return InlineKeyboardMarkup(rows)


async def show_main_menu(chat):
    await chat.send_message(
        "🎬 <b>Catálogo de series</b>\n"
        "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.",
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
    await show_main_menu(chat)


async def temas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type != "private":
        await update.message.reply_text("Usa /temas en privado.")
        return
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
#   HANDLER: botón "Volver" y "Buscar" y "Recientes"
# ======================================================
async def on_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat = query.message.chat
    try:
        await query.edit_message_text(
            "🎬 <b>Catálogo de series</b>\n"
            "Elige una letra, pulsa Recientes o escribe el nombre de una serie para buscar.",
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
    items = list(topics.items())
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
#   BÚSQUEDA POR TEXTO (solo en privado)
# ======================================================
async def search_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None:
        return

    chat = msg.chat
    if chat.type != "private":
        return  # Ignoramos texto en grupo para búsqueda

    query = msg.text.strip()
    if not query:
        await chat.send_message("Escribe parte del nombre de la serie para buscar.")
        return

    topics = load_topics()
    if not topics:
        await chat.send_message("📭 No hay series aún.")
        return

    # Filtrado por coincidencia parcial (case-insensitive)
    query_lower = query.lower()
    matches = [
        (tid, info)
        for tid, info in topics.items()
        if query_lower in info.get("name", "").lower()
    ]

    if not matches:
        await chat.send_message(
            f"🔍 No encontré ninguna serie que contenga: <b>{escape(query)}</b>",
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
        InlineKeyboardButton("🔙 Volver", callback_data="main_menu")
    ])

    await chat.send_message(
        f"🔍 Resultados para: <b>{escape(query)}</b>",
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
    if topic_id not in topics:
        await query.edit_message_text("❌ Tema no encontrado.")
        return

    await query.edit_message_text("📨 Enviando contenido del tema...")

    bot = context.bot
    user_id = query.from_user.id

    # Mensajes en el orden en que se guardaron (cronológico)
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
            # No hacemos copy, solo anotamos el error por consola
            print(f"[send_topic] ERROR reenviando {mid}: {e}")

    await bot.send_message(
        chat_id=user_id,
        text=f"✔ Envío completado. {enviados} mensajes reenviados 🎉",
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

    # Callbacks navegación
    app.add_handler(CallbackQueryHandler(on_letter, pattern=r"^letter:"))
    app.add_handler(CallbackQueryHandler(on_page, pattern=r"^page:"))
    app.add_handler(CallbackQueryHandler(on_main_menu, pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(on_search_btn, pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(on_recent_btn, pattern=r"^recent$"))

    # Callbacks de temas
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
