"""Bot de Telegram: entrenador de gimnasio, carrera, natación y bici, con dieta y compra."""
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
import gym
import menu
import notion_sync
import plan
import servicios as sv
from config import (COMPRA_HOUR, COMPRA_MINUTE, REMINDER_HOUR, REMINDER_MINUTE,
                    TELEGRAM_TOKEN)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)
TZ = sv.TZ

MD = ParseMode.MARKDOWN
LIMITE_TELEGRAM = 4000

AYUDA = """🤖 *Tu entrenador personal*

💬 *Háblame normal*: «hoy estoy reventado», «me molesta el gemelo», «peso 79,4», «cámbiame la cena por fajitas»… y adapto el plan (necesita la API de Claude).

*Entrenamiento*
/hoy — sesión de hoy
/manana — sesión de mañana
/semana `[nº]` — resumen semanal (por defecto, la actual)
/gym — detalle del gimnasio de hoy
/ritmos — ritmos y zonas (salen de tu último test)
/bloque — bloque de 8 semanas y próximo test
/test `10k 44:30` — registra un test y recalcula ritmos

*Cambios en tu semana*
/sensaciones — dime cómo estás y adapto hoy (sin IA)
/mover `jueves viernes` — intercambia dos días (o recupera uno saltado)
/saltar `[día]` — hoy (o ese día) descansas
/cambiar `corta|suave|bici|natacion [día]` — versión corta, suave o alternativa sin impacto
/deshacer `[día|semana]` — quita los cambios

*Seguimiento*
/hecho `[nota]` — marca el entreno de hoy como completado
/peso `79.4` — registra tu peso
/grasa `12.5` — registra tu % de grasa
/stats — progreso y adherencia

*Dieta*
/dieta `[mañana]` — kcal y macros del día según tu entreno
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


# ---------- entrenamiento ----------

@comando
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    texto = (
        f"¡Hola {u.first_name}! 💪\n\n"
        "Soy tu entrenador. Tu semana tipo:\n"
        "• L pecho+tríceps · M espalda+bíceps · X pierna\n"
        "• J natación · V descanso flexible\n"
        "• S carrera + hombro y core · D bici\n\n"
        "El plan va en bloques de 8 semanas con un test de 5 o 10 km al final de cada uno "
        "para recalcular tus ritmos. La dieta es un volumen muy limpio hasta 81 kg.\n\n"
        "Te escribiré cada mañana con el entreno y el menú del día.\n\n" + AYUDA
    )
    await _enviar(update, texto)


@comando
async def ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, AYUDA)


@comando
async def hoy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.formatear_dia(update.effective_chat.id, sv.hoy()))


@comando
async def manana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.formatear_dia(update.effective_chat.id, sv.hoy() + timedelta(days=1)))


@comando
async def semana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wn = plan.semana_indice(sv.hoy())
    if ctx.args:
        try:
            wn = int(ctx.args[0])
        except ValueError:
            raise sv.ErrorUsuario("Usa `/semana 7` con el número de semana.") from None
    await _enviar(update, sv.resumen_semana(update.effective_chat.id, wn))


@comando
async def gimnasio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = sv.hoy()
    wn = plan.semana_indice(d)
    if plan.datos_semana(wn) is None:
        raise sv.ErrorUsuario("Hoy estás fuera del plan.")
    c = sv.cambio(update.effective_chat.id, d)
    grupos = {0: "pecho", 1: "espalda", 2: "pierna", 5: "hombro"}
    grupo = None
    if c is None or c.modo in (None, "corta", "suave"):
        grupo = grupos.get((c.origen if c else d).weekday())
    if not grupo:
        raise sv.ErrorUsuario("Hoy no toca gimnasio. Usa /hoy para ver la sesión que te corresponde.")
    await _enviar(update, gym.formatear(grupo, plan.variante(wn)))


@comando
async def ritmos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, plan.tabla_ritmos(*sv.ritmo(update.effective_chat.id)))


@comando
async def bloque(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.bloque_texto(update.effective_chat.id))


@comando
async def test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        raise sv.ErrorUsuario("Usa `/test 10k 44:30` (distancia y tiempo). También vale "
                              "`/test 5 21:40` o `/test media 1:41:00`.")
    km, seg = sv.parse_distancia(ctx.args[0]), sv.parse_tiempo(ctx.args[1])
    await _enviar(update, sv.registrar_test(update.effective_chat.id, km, seg))


# ---------- cambios en la semana ----------

@comando
async def mover(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        raise sv.ErrorUsuario("Usa `/mover jueves viernes` (días de esta semana).")
    await _enviar(update, sv.mover(update.effective_chat.id, ctx.args[0], ctx.args[1]))


@comando
async def saltar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.saltar(update.effective_chat.id, _arg(ctx, 0)))


@comando
async def cambiar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.cambiar(update.effective_chat.id, _arg(ctx, 0) or "", _arg(ctx, 1)))


@comando
async def deshacer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, sv.deshacer(update.effective_chat.id, _arg(ctx, 0)))


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
    texto = sv.aplicar_sensaciones(chat_id, energia, zona)
    try:
        await q.edit_message_text(texto, parse_mode=MD)
    except BadRequest:
        await q.edit_message_text(texto)


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
    chat_id = update.effective_chat.id
    hist = db.historico_metricas(chat_id, 8)
    lunes = sv.lunes(sv.hoy())
    hechos = db.entrenos_entre(chat_id, lunes, lunes + timedelta(days=6))

    out = ["📊 *Tu progreso*", ""]
    if hist:
        out.append("*Métricas recientes*")
        for r in hist:
            partes = [r["fecha"]]
            if r["peso"] is not None:
                partes.append(f"{r['peso']} kg")
            if r["grasa"] is not None:
                partes.append(f"{r['grasa']}% grasa")
            out.append("• " + " · ".join(partes))
        pesos = [r["peso"] for r in hist if r["peso"] is not None]
        if len(pesos) >= 2:
            out.append(f"\nVariación de peso en el periodo: {pesos[0] - pesos[-1]:+.1f} kg")
    else:
        out.append("Aún no has registrado peso ni grasa. Usa /peso y /grasa.")

    out += ["", f"*Esta semana*: {len(hechos)}/6 sesiones completadas"]
    for r in hechos:
        nota = f" — _{menu._md(r['nota'])}_" if r["nota"] else ""
        out.append(f"  ✅ {r['fecha']} {r['titulo']}{nota}")
    out += ["", f"*Total de entrenos registrados*: {db.total_entrenos(chat_id)}"]
    await _enviar(update, "\n".join(out))


@comando
async def hecho(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _enviar(update, await sv.marcar_hecho(update.effective_chat.id, " ".join(ctx.args)))


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
    lineas += ["", "_Cada /hecho, /peso y /grasa se guarda automáticamente._"]
    await _enviar(update, "\n".join(lineas), disable_web_page_preview=True)


@comando
async def recordatorio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or ctx.args[0] not in ("on", "off"):
        raise sv.ErrorUsuario("Usa `/recordatorio on` o `/recordatorio off`.")
    activo = ctx.args[0] == "on"
    db.set_recordatorio(update.effective_chat.id, activo)
    await _enviar(update, f"🔔 Recordatorio diario {'activado' if activo else 'desactivado'}"
                          + (f" a las {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}." if activo else "."))


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
                              "o `/plato jueves cena lentejas`. `normal` vuelve al plato del plan.\n\n"
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
    await _enviar(update, "🧹 He olvidado la conversación. Tus datos y el plan siguen igual.")


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
        try:
            await _mandar(ctx, chat_id, "☀️ *Entreno de hoy*\n\n" + sv.formatear_dia(chat_id, d))
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

    await app.bot.set_my_commands([
        BotCommand("hoy", "Entreno de hoy"),
        BotCommand("manana", "Entreno de mañana"),
        BotCommand("semana", "Resumen de la semana"),
        BotCommand("sensaciones", "Dime cómo estás y adapto hoy"),
        BotCommand("gym", "Rutina de gimnasio de hoy"),
        BotCommand("ritmos", "Ritmos y zonas de entrenamiento"),
        BotCommand("bloque", "Bloque actual y próximo test"),
        BotCommand("test", "Registrar un test (10k 44:30)"),
        BotCommand("mover", "Intercambiar dos días de esta semana"),
        BotCommand("saltar", "Descansar hoy u otro día"),
        BotCommand("cambiar", "Versión corta, suave, bici o natación"),
        BotCommand("deshacer", "Quitar cambios de la semana"),
        BotCommand("hecho", "Marcar entreno como completado"),
        BotCommand("peso", "Registrar peso"),
        BotCommand("grasa", "Registrar % de grasa"),
        BotCommand("stats", "Progreso y adherencia"),
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
        "hoy": hoy, "manana": manana, "semana": semana, "gym": gimnasio,
        "ritmos": ritmos, "bloque": bloque, "test": test,
        "peso": peso, "grasa": grasa, "stats": stats, "hecho": hecho, "perfil": perfil,
        "recordatorio": recordatorio, "notion": notion, "sensaciones": sensaciones,
        "mover": mover, "saltar": saltar, "cambiar": cambiar, "deshacer": deshacer,
        "dieta": dieta, "menu": menu_cmd, "plato": plato, "compra": lista_compra,
        "olvidar": olvidar,
    }
    for nombre, fn in handlers.items():
        app.add_handler(CommandHandler(nombre, fn))
    app.add_handler(CallbackQueryHandler(sensaciones_boton, pattern=r"^sens:"))
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
    log.info("Bot en marcha. Plan cíclico desde el %s.", plan.PLAN_START.isoformat())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
