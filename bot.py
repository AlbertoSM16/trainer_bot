"""Bot de Telegram: entrenador de carrera y bici con tu Garmin, historial de gym, dieta y compra."""
import logging
from datetime import time, timedelta
from functools import wraps

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import alimentos
import coach
import db
import garmin
import menu
import notion_sync
import plan
import servicios as sv
from config import (COMPRA_HOUR, COMPRA_MINUTE, GARMIN_CHAT_ID, GARMIN_SYNC_MIN, REMINDER_HOUR,
                    REMINDER_MINUTE, TELEGRAM_TOKEN)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
TZ = sv.TZ

MD = ParseMode.MARKDOWN
LIMITE_TELEGRAM = 4000

AYUDA = """🤖 *Tu entrenador personal*

💬 *Háblame normal*: «hoy estoy reventado», «me molesta el gemelo», «¿qué corro hoy?», «peso 79,4», «cámbiame la cena por fajitas»… (necesita la API de Claude).

*Qué hago hoy*
/carrera — qué entreno de carrera te conviene hoy (y lo mando al reloj)
/bici — qué entreno de bici te conviene hoy (por pulso)
/sensaciones — dime cómo estás y ajusto el consejo (sin IA)
/ritmos — ritmos de carrera y zonas de pulso de bici
/test `10k 44:30` — registra un test y recalcula ritmos

*Lo que has hecho*
/historial — tus entrenos de las 2 últimas semanas (gym con pesos y progreso)
/actividades `[n]` — tus últimas actividades analizadas
/stats — peso y volumen de las últimas semanas

*Garmin*
/garmin — estado y sincroniza ahora
/bienestar — readiness, HRV, sueño y body battery

*Seguimiento*
/peso `79.4` — registra tu peso
/grasa `12.5` — registra tu % de grasa

*Dieta*
/dieta `[mañana]` — kcal y macros (el ejercicio sale de la media de tu reloj)
/menu `[mañana|semana]` — qué comer y cuánto (con whey y creatina)
/plato `[día] cena fajitas` — cambia un plato (`normal` lo devuelve)
/compra — lista de la compra de Mercadona para 7 días

*Otros*
/notion — estado de la sincronización con Notion
/recordatorio `on|off` — aviso diario
/perfil — tus datos
/olvidar — borra la memoria de la conversación
/ayuda — esta lista"""

def _trozos(texto: str) -> list[str]:
    """Parte un texto largo por párrafos para no pasar del límite de Telegram."""
    if len(texto) <= LIMITE_TELEGRAM:
        return [texto]
    out, actual = [], ""
    for parrafo in texto.split("\n\n"):
        if actual and len(actual) + len(parrafo) + 2 > LIMITE_TELEGRAM:
            out.append(actual)
            actual = ""
        actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    out.append(actual)
    return [t[i:i + LIMITE_TELEGRAM] for t in out for i in range(0, len(t), LIMITE_TELEGRAM)]


async def _enviar(update: Update, texto: str, **kw):
    """Responde en Markdown y, si Telegram lo rechaza (Markdown roto), en texto plano."""
    for trozo in _trozos(texto):
        try:
            await update.effective_message.reply_text(trozo, parse_mode=MD, **kw)
        except BadRequest as e:
            if "parse" not in str(e).lower() and "entit" not in str(e).lower():
                raise
            await update.effective_message.reply_text(trozo, **kw)


def comando(fn):
    """Da de alta al usuario y convierte `ErrorUsuario` en una respuesta."""
    @wraps(fn)
    async def envoltura(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        db.alta_usuario(update.effective_chat.id, update.effective_user.first_name or "atleta")
        try:
            await fn(update, ctx)
        except sv.ErrorUsuario as e:
            await _enviar(update, str(e))
    return envoltura


def _arg(ctx: ContextTypes.DEFAULT_TYPE, i: int) -> str | None:
    return ctx.args[i] if len(ctx.args) > i else None


def _resto(update: Update) -> str:
    """Texto del mensaje tras el comando (respeta comillas y espacios)."""
    partes = (update.effective_message.text or "").split(maxsplit=1)
    return partes[1].strip() if len(partes) > 1 else ""


# ---------- entrenamiento ----------

@comando
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    texto = (
        f"¡Hola {u.first_name}! 💪\n\n"
        "Soy tu entrenador. Tu rutina de gimnasio la escribes en Garmin Connect y yo leo lo "
        "que haces con el reloj. Cada día te digo qué te conviene correr (/carrera) y rodar "
        "(/bici) según lo que llevas, tu descanso y tus sensaciones, y te lo mando al reloj. "
        "La dieta es un volumen muy limpio.\n\n" + AYUDA
    )
    await _enviar(update, texto)


@comando
async def ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, AYUDA)


def _boton_reloj(deportes: list[str]) -> InlineKeyboardMarkup | None:
    if not deportes:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        f"⌚ Mandar {'la carrera' if d == 'carrera' else 'la bici'} al reloj",
        callback_data=f"reloj:{d}")] for d in deportes])


async def _consejo(update: Update, ctx: ContextTypes.DEFAULT_TYPE, deporte: str):
    chat_id = update.effective_chat.id
    texto, para_reloj = sv.consejo_texto(chat_id, deporte)
    await _enviar(update, texto, reply_markup=_boton_reloj([deporte] if para_reloj else []))
    comentario = await coach.comentar_consejo(chat_id, texto)
    if comentario:
        await _enviar(update, comentario)


@comando
async def carrera(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _consejo(update, ctx, "carrera")


@comando
async def bici(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _consejo(update, ctx, "bici")


async def reloj_boton(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Enviando al reloj…")
    chat_id = update.effective_chat.id
    deporte = q.data.split(":", 1)[1]
    try:
        texto = await sv.mandar_reloj(chat_id, deporte)
    except sv.ErrorUsuario as e:
        texto = str(e)
    except Exception as e:  # noqa: BLE001
        log.warning("Garmin: no se pudo mandar %s al reloj: %s", deporte, e)
        texto = f"⚠️ No he podido mandarlo al reloj: {menu._md(str(e))[:200]}"
    await _enviar(update, texto)


@comando
async def historial(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.historial_texto(update.effective_chat.id))


@comando
async def ritmos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.ritmos_texto(update.effective_chat.id))


@comando
async def test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        raise sv.ErrorUsuario("Usa `/test 10k 44:30` (distancia y tiempo). También vale "
                              "`/test 5 21:40` o `/test media 1:41:00`.")
    km, seg = sv.parse_distancia(ctx.args[0]), sv.parse_tiempo(ctx.args[1])
    await _enviar(update, sv.registrar_test(update.effective_chat.id, km, seg))


@comando
async def sensaciones(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    botones = [[InlineKeyboardButton(f"{n} {e}", callback_data=f"sens:e:{n}")
                for n, e in ((1, "😵"), (2, "😩"), (3, "😐"), (4, "🙂"), (5, "💪"))]]
    await update.effective_message.reply_text(
        "¿Cómo tienes hoy la energía? (1 = fundido, 5 = a tope)",
        reply_markup=InlineKeyboardMarkup(botones))


async def sensaciones_boton(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    partes = q.data.split(":")
    if partes[1] == "e":
        energia = partes[2]
        botones = [[InlineKeyboardButton(t, callback_data=f"sens:m:{energia}:{z}")]
                   for z, t in plan.ZONAS_MOLESTIA.items()]
        await q.edit_message_text("¿Alguna molestia?", reply_markup=InlineKeyboardMarkup(botones))
        return
    energia, zona = int(partes[2]), partes[3]
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    texto, reloj = sv.aplicar_sensaciones(chat_id, energia, zona)
    try:
        await q.edit_message_text(texto, parse_mode=MD, reply_markup=_boton_reloj(reloj))
    except BadRequest:
        await q.edit_message_text(texto, reply_markup=_boton_reloj(reloj))


# ---------- seguimiento ----------

async def _metrica(update: Update, ctx: ContextTypes.DEFAULT_TYPE, campo: str):
    if not ctx.args:
        raise sv.ErrorUsuario(
            f"Indica el valor, por ejemplo `/{campo} {'79.4' if campo == 'peso' else '12.5'}`")
    try:
        valor = float(ctx.args[0].replace(",", "."))
    except ValueError:
        raise sv.ErrorUsuario("Eso no es un número válido.") from None
    await _enviar(update, await sv.registrar_metrica(update.effective_chat.id, campo, valor))


@comando
async def peso(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _metrica(update, ctx, "peso")


@comando
async def grasa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _metrica(update, ctx, "grasa")


@comando
async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.stats_texto(update.effective_chat.id))


@comando
async def perfil(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.perfil_texto(update.effective_chat.id))


@comando
async def notion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not notion_sync.activo():
        await _enviar(update,
                      "📓 *Notion no está configurado.*\n\n"
                      "Para activarlo añade a tu `.env`:\n"
                      "• `NOTION_TOKEN` — token de tu integración\n"
                      "• `NOTION_PARENT_PAGE_ID` — id de la página donde crear las bases\n\n"
                      "Tienes los pasos detallados en el README.")
        return
    urls = await notion_sync.sync.url_bases()
    if not urls:
        await _enviar(update, "📓 Notion está configurado pero las bases aún no se han creado.\n"
                              "Reinicia el bot y revisa los logs por si hay algún error de permisos.")
        return
    lineas = ["📓 *Notion conectado*", "", "Tus bases de datos:"]
    lineas += [f"• [{n}]({u})" for n, u in urls.items()]
    lineas += ["", "_Tus actividades del reloj, /peso y /grasa se guardan automáticamente._"]
    await _enviar(update, "\n".join(lineas), disable_web_page_preview=True)


@comando
async def recordatorio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or ctx.args[0] not in ("on", "off"):
        raise sv.ErrorUsuario("Usa `/recordatorio on` o `/recordatorio off`.")
    activo = ctx.args[0] == "on"
    db.set_recordatorio(update.effective_chat.id, activo)
    await _enviar(update, f"🔔 Recordatorio diario {'activado' if activo else 'desactivado'}"
                          + (f" a las {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}." if activo else "."))


# ---------- Garmin ----------

async def _garmin_seguro(coro, que: str) -> str:
    try:
        return await coro
    except sv.ErrorUsuario:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("Garmin (%s): %s", que, e)
        return f"⚠️ No he podido {que}: {menu._md(str(e))[:200]}"


@comando
async def garmin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not garmin.configurado():
        raise sv.ErrorUsuario(
            "⌚ *Garmin no está configurado.*\n\n"
            "1. Ejecuta una vez `.venv/bin/python garmin_login.py` (correo, contraseña y MFA).\n"
            f"2. Pon `GARMIN_CHAT_ID={chat_id}` en tu `.env`.\n"
            "3. Reinicia el bot.\n\nTienes los detalles en el README.")
    await update.effective_message.reply_text("⌚ Sincronizando con Garmin…")
    await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)

    async def _sync():
        msgs = await sv.sync_garmin(chat_id)
        return ("\n\n".join(msgs) if msgs else "Sin actividades nuevas.")

    partes = [await _garmin_seguro(_sync(), "leer tus actividades"),
              await _garmin_seguro(sv.revisar_bienestar(chat_id, forzar=True),
                                   "leer tu estado de forma")]
    await _enviar(update, "\n\n".join(p for p in partes if p))


@comando
async def actividades(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 5
    await _enviar(update, sv.actividades_texto(update.effective_chat.id, min(n, 15)))


@comando
async def bienestar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.bienestar_texto(update.effective_chat.id))


# ---------- dieta ----------

def _dia_pedido(ctx: ContextTypes.DEFAULT_TYPE):
    d = sv.hoy()
    if ctx.args and alimentos.normalizar(ctx.args[0]) == "manana":
        d += timedelta(days=1)
    return d


@comando
async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if ctx.args and alimentos.normalizar(ctx.args[0]) == "semana":
        await _enviar(update, sv.menu_semana_texto(chat_id, sv.hoy()))
        return
    await _enviar(update, sv.menu_texto(chat_id, _dia_pedido(ctx)))


@comando
async def dieta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.dieta_texto(update.effective_chat.id, _dia_pedido(ctx)))


@comando
async def plato(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = list(ctx.args)
    if len(args) < 2:
        await _enviar(update, "Usa `/plato [día] comida plato`, por ejemplo `/plato cena fajitas` "
                              "o `/plato jueves cena lentejas`. `normal` vuelve al plato de siempre.\n\n"
                      + sv.platos_disponibles())
        return
    dia = args.pop(0) if len(args) >= 3 else None
    await _enviar(update, sv.cambiar_plato(update.effective_chat.id, dia, args[0],
                                           "_".join(args[1:])))


@comando
async def lista_compra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🛒 Consultando precios de Mercadona…")
    await _enviar(update, await sv.lista_compra(update.effective_chat.id),
                  disable_web_page_preview=True)


# ---------- coach ----------

@comando
async def olvidar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db.borrar_historial(update.effective_chat.id)
    await _enviar(update, "🧹 He olvidado la conversación. Tus datos siguen igual.")


@comando
async def texto_libre(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    await _enviar(update, await coach.responder(update.effective_chat.id,
                                                update.effective_message.text))


# ---------- avisos programados ----------

async def _mandar(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, texto: str, **kw):
    for trozo in _trozos(texto):
        try:
            await ctx.bot.send_message(chat_id, trozo, parse_mode=MD, **kw)
        except BadRequest:
            await ctx.bot.send_message(chat_id, trozo, **kw)


async def aviso_diario(ctx: ContextTypes.DEFAULT_TYPE):
    d = sv.hoy()
    for chat_id in db.usuarios_con_recordatorio():
        if sv.garmin_activo(chat_id):
            try:
                texto = await sv.revisar_bienestar(chat_id)
                if texto:
                    await _mandar(ctx, chat_id, texto)
            except Exception as e:  # noqa: BLE001
                log.warning("Garmin: sin estado de forma: %s", e)
        for deporte in sv.DEPORTES:
            try:
                texto, para_reloj = sv.consejo_texto(chat_id, deporte)
                await _mandar(ctx, chat_id, texto,
                              reply_markup=_boton_reloj([deporte] if para_reloj else []))
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo enviar el consejo de %s a %s: %s", deporte, chat_id, e)
        try:
            await _mandar(ctx, chat_id, sv.menu_texto(chat_id, d))
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar a %s: %s", chat_id, e)


async def aviso_compra(ctx: ContextTypes.DEFAULT_TYPE):
    for chat_id in db.usuarios_con_recordatorio():
        try:
            texto = sv.ajuste_semanal(chat_id) + await sv.lista_compra(chat_id)
            await _mandar(ctx, chat_id, "🛍️ *¡Sábado de compra!*\n\n" + texto,
                          disable_web_page_preview=True)
            await _mandar(ctx, chat_id, sv.menu_semana_texto(chat_id, sv.hoy()))
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar la compra a %s: %s", chat_id, e)


async def sync_garmin(ctx: ContextTypes.DEFAULT_TYPE):
    """Cada GARMIN_SYNC_MIN minutos: actividades nuevas (análisis + comentario del coach) y
    estado de forma por la mañana."""
    chat_id = GARMIN_CHAT_ID
    try:
        for texto in await sv.sync_garmin(chat_id):
            await _mandar(ctx, chat_id, texto)
            comentario = await coach.comentar_actividad(chat_id, texto)
            if comentario:
                await _mandar(ctx, chat_id, comentario)
    except Exception as e:  # noqa: BLE001
        log.warning("Garmin: fallo al sincronizar actividades: %s", e)
    if 6 <= sv.ahora().hour < 13:
        try:
            texto = await sv.revisar_bienestar(chat_id)
            if texto:
                await _mandar(ctx, chat_id, texto + "\n\nQué te toca hoy: /carrera · /bici")
        except Exception as e:  # noqa: BLE001
            log.warning("Garmin: fallo al leer el estado de forma: %s", e)


async def post_init(app: Application):
    if notion_sync.activo():
        if await notion_sync.sync.preparar():
            log.info("Notion conectado. Bases: %s", list(notion_sync.sync.db_ids))
        else:
            log.warning("Notion configurado pero no se pudo inicializar. "
                        "El bot seguirá guardando solo en local.")
    else:
        log.info("Notion no configurado: se guarda solo en SQLite.")
    log.info("Coach con Claude: %s", "activo" if coach.activo() else "sin ANTHROPIC_API_KEY")
    log.info("Garmin: %s", f"activo para el chat {GARMIN_CHAT_ID}" if garmin.configurado()
             else "no configurado")

    await app.bot.set_my_commands([
        BotCommand("carrera", "Qué entreno de carrera hacer hoy"),
        BotCommand("bici", "Qué entreno de bici hacer hoy"),
        BotCommand("historial", "Tus entrenos de las 2 últimas semanas"),
        BotCommand("sensaciones", "Dime cómo estás y ajusto el consejo"),
        BotCommand("ritmos", "Ritmos de carrera y zonas de bici"),
        BotCommand("test", "Registrar un test (10k 44:30)"),
        BotCommand("garmin", "Estado de Garmin y sincronizar ahora"),
        BotCommand("actividades", "Últimas actividades del reloj"),
        BotCommand("bienestar", "Readiness, HRV, sueño y body battery"),
        BotCommand("peso", "Registrar peso"),
        BotCommand("grasa", "Registrar % de grasa"),
        BotCommand("stats", "Peso y volumen de las últimas semanas"),
        BotCommand("dieta", "Kcal y macros de hoy"),
        BotCommand("menu", "Qué comer hoy (o la semana)"),
        BotCommand("plato", "Cambiar un plato del menú"),
        BotCommand("compra", "Lista de la compra de Mercadona"),
        BotCommand("notion", "Estado de la sincronización con Notion"),
        BotCommand("recordatorio", "Activar/desactivar aviso diario"),
        BotCommand("perfil", "Ver tu perfil"),
        BotCommand("olvidar", "Borrar la memoria de la conversación"),
        BotCommand("ayuda", "Lista de comandos"),
    ])

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit(
            "Falta TELEGRAM_TOKEN. Crea un archivo .env con tu token de @BotFather.")
    db.init()
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()

    handlers = {
        "start": start, "ayuda": ayuda, "help": ayuda,
        "carrera": carrera, "bici": bici, "historial": historial,
        "ritmos": ritmos, "test": test, "sensaciones": sensaciones,
        "peso": peso, "grasa": grasa, "stats": stats, "perfil": perfil,
        "recordatorio": recordatorio, "notion": notion,
        "dieta": dieta, "menu": menu_cmd, "plato": plato, "compra": lista_compra,
        "olvidar": olvidar,
        "garmin": garmin_cmd, "actividades": actividades, "bienestar": bienestar,
    }
    for nombre, fn in handlers.items():
        app.add_handler(CommandHandler(nombre, fn))
    app.add_handler(CallbackQueryHandler(sensaciones_boton, pattern=r"^sens:"))
    app.add_handler(CallbackQueryHandler(reloj_boton, pattern=r"^reloj:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto_libre))

    if app.job_queue:
        app.job_queue.run_daily(
            aviso_diario,
            time=time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=TZ),
            name="aviso_diario",
        )
        # En PTB los días van de 0 = domingo a 6 = sábado
        app.job_queue.run_daily(
            aviso_compra,
            time=time(hour=COMPRA_HOUR, minute=COMPRA_MINUTE, tzinfo=TZ),
            days=(6,),
            name="aviso_compra",
        )
        if garmin.configurado():
            app.job_queue.run_repeating(sync_garmin, interval=GARMIN_SYNC_MIN * 60, first=30,
                                        name="sync_garmin")
    log.info("Bot en marcha.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
