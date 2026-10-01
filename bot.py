"""Bot de Telegram para gestionar entrenamientos de gimnasio, carrera, natación y bici."""
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import db
import gym
import notion_sync
import plan
from config import (ATHLETE, RACE_DATE, RACE_NAME, REMINDER_HOUR,
                    REMINDER_MINUTE, TELEGRAM_TOKEN, TIMEZONE)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    level=logging.INFO)
log = logging.getLogger(__name__)
TZ = ZoneInfo(TIMEZONE)

MD = ParseMode.MARKDOWN

AYUDA = """🤖 *Tu entrenador personal*

*Entrenamiento*
/hoy — sesión de hoy
/manana — sesión de mañana
/semana `[nº]` — resumen semanal (por defecto, la actual)
/gym — detalle del gimnasio de hoy
/ritmos — tabla de ritmos y zonas
/fases — estructura del plan completo

*Seguimiento*
/hecho `[nota]` — marca el entreno de hoy como completado
/peso `78.4` — registra tu peso
/grasa `12.5` — registra tu % de grasa
/stats — progreso y adherencia

*Otros*
/faltan — cuenta atrás para la carrera
/notion — estado de la sincronización con Notion
/recordatorio `on|off` — aviso diario
/perfil — tus datos
/ayuda — esta lista"""


def _hoy() -> date:
    return datetime.now(TZ).date()


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db.alta_usuario(update.effective_chat.id, u.first_name or "atleta")
    texto = (
        f"¡Hola {u.first_name}! 💪\n\n"
        f"Soy tu entrenador. Tienes un plan de *{plan.total_semanas()} semanas* que combina:\n"
        "• 4 días de gimnasio (pierna / pecho-tríceps / espalda-bíceps / hombro-abs)\n"
        "• 3 días de carrera (series, rodaje suave y tirada larga)\n"
        "• 1 día de natación y 1 de bici\n\n"
        f"Objetivo: *{RACE_NAME}* el {RACE_DATE.strftime('%d/%m/%Y')} bajando de 4:38 /km.\n\n"
        "Te escribiré cada mañana con el entreno del día.\n\n" + AYUDA
    )
    await update.message.reply_text(texto, parse_mode=MD)


async def ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(AYUDA, parse_mode=MD)


async def hoy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plan.formatear_dia(_hoy()), parse_mode=MD)


async def manana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plan.formatear_dia(_hoy() + timedelta(days=1)),
                                    parse_mode=MD)


async def semana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wn = plan.semana_indice(_hoy())
    if ctx.args:
        try:
            wn = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text("Usa `/semana 7` con el número de semana.", parse_mode=MD)
            return
    await update.message.reply_text(plan.resumen_semana(wn), parse_mode=MD)


async def gimnasio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _hoy()
    wn = plan.semana_indice(d)
    if plan.datos_semana(wn) is None:
        await update.message.reply_text("Hoy estás fuera del plan.")
        return
    grupos = {0: "pierna", 1: "pecho", 2: "espalda", 3: "hombro"}
    grupo = grupos.get(d.weekday())
    if not grupo:
        await update.message.reply_text(
            "Hoy no toca gimnasio. Usa /hoy para ver la sesión que te corresponde.")
        return
    await update.message.reply_text(gym.formatear(grupo, plan.variante(wn)), parse_mode=MD)


async def ritmos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plan.tabla_ritmos(), parse_mode=MD)


async def fases(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lineas = [f"🗺️ *Plan completo · {plan.total_semanas()} semanas*", ""]
    fase_actual = None
    for i in range(1, plan.total_semanas() + 1):
        w = plan.datos_semana(i)
        if w["fase"] != fase_actual:
            fase_actual = w["fase"]
            lineas += ["", f"*{plan.FASES[fase_actual]}*"]
        marca = " ⚠️" if w["descarga"] else ""
        larga = w["larga"].split(":")[0].split(" a ")[0].strip()
        lineas.append(f"  S{i:02d} ({plan.fecha_lunes(i).strftime('%d/%m')}) — larga: {larga}{marca}")
    lineas += ["", "⚠️ = semana de descarga"]
    await update.message.reply_text("\n".join(lineas), parse_mode=MD)


async def peso(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _metrica(update, ctx, "peso")


async def grasa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await _metrica(update, ctx, "grasa")


async def _metrica(update: Update, ctx: ContextTypes.DEFAULT_TYPE, campo: str):
    if not ctx.args:
        await update.message.reply_text(
            f"Indica el valor, por ejemplo `/{campo} {'78.4' if campo == 'peso' else '12.5'}`",
            parse_mode=MD)
        return
    try:
        valor = float(ctx.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Eso no es un número válido.")
        return
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    db.registrar_metrica(chat_id, **{campo: valor})
    unidad = "kg" if campo == "peso" else "%"
    extra = ""
    imc = None
    ult = db.historico_metricas(chat_id, 1)
    peso_ref = ult[0]["peso"] if ult and ult[0]["peso"] is not None else None
    if peso_ref is not None:
        imc = peso_ref / (ATHLETE["altura_cm"] / 100) ** 2
    if campo == "peso":
        extra = f"\nIMC: {imc:.1f}" if imc else ""

    fila = ult[0] if ult else None
    ok = await notion_sync.sync.guardar_metrica(
        _hoy(),
        fila["peso"] if fila else None,
        fila["grasa"] if fila else None,
        imc,
    )
    sufijo = "\n📓 Guardado también en Notion." if ok else ""
    await update.message.reply_text(
        f"✅ Registrado: {campo} = {valor} {unidad}{extra}{sufijo}")


async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    hist = db.historico_metricas(chat_id, 8)
    hoy_d = _hoy()
    lunes = hoy_d - timedelta(days=hoy_d.weekday())
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
            dif = pesos[0] - pesos[-1]
            out.append(f"\nVariación de peso en el periodo: {dif:+.1f} kg")
    else:
        out.append("Aún no has registrado peso ni grasa. Usa /peso y /grasa.")

    out += ["", f"*Esta semana*: {len(hechos)}/7 sesiones completadas"]
    for r in hechos:
        nota = f" — _{r['nota']}_" if r["nota"] else ""
        out.append(f"  ✅ {r['fecha']} {r['titulo']}{nota}")
    out += ["", f"*Total de entrenos registrados*: {db.total_entrenos(chat_id)}"]
    await update.message.reply_text("\n".join(out), parse_mode=MD)


async def hecho(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _hoy()
    s = plan.sesiones_dia(d)
    if s["fuera_de_plan"]:
        await update.message.reply_text(
            "Hoy no hay sesión planificada todavía. El plan arranca el "
            f"{plan.PLAN_START.strftime('%d/%m/%Y')}.")
        return
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    nota = " ".join(ctx.args)
    db.marcar_hecho(chat_id, d, s["titulo"], nota)
    ok = await notion_sync.sync.guardar_entreno(
        fecha=d,
        titulo=s["titulo"],
        nota=nota,
        semana=s["semana"],
        fase=plan.fase_nombre_corto(s["fase"]),
        dia=plan.DIAS[d.weekday()],
        tipos=plan.tipos_dia(d),
        descarga=bool(s.get("descarga")),
    )
    msg = f"✅ Anotado: *{s['titulo']}*"
    if nota:
        msg += f"\n📝 {nota}"
    if ok:
        msg += "\n📓 Guardado también en Notion."
    await update.message.reply_text(msg + "\n\n¡Buen trabajo! 💪", parse_mode=MD)


async def faltan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    dias = (RACE_DATE - _hoy()).days
    wn = plan.semana_indice(_hoy())
    if dias < 0:
        await update.message.reply_text(f"La carrera fue hace {-dias} días. ¿Qué tal fue? 😄")
        return
    if wn < 1:
        await update.message.reply_text(
            f"⏳ Faltan *{dias} días* ({dias // 7} semanas) para {RACE_NAME}.\n"
            f"El plan de {plan.total_semanas()} semanas arranca el "
            f"{plan.PLAN_START.strftime('%d/%m/%Y')}.", parse_mode=MD)
        return
    await update.message.reply_text(
        f"⏳ Faltan *{dias} días* ({dias // 7} semanas) para {RACE_NAME}.\n"
        f"Vas por la semana {wn} de {plan.total_semanas()}.", parse_mode=MD)


async def perfil(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    peso_act = ATHLETE["peso_inicial_kg"]
    grasa_act = None
    for r in db.historico_metricas(update.effective_chat.id, 60):
        if r["peso"] is not None:
            peso_act = r["peso"]
            break
    for r in db.historico_metricas(update.effective_chat.id, 60):
        if r["grasa"] is not None:
            grasa_act = r["grasa"]
            break
    imc = peso_act / (ATHLETE["altura_cm"] / 100) ** 2
    out = [
        "👤 *Tu perfil*", "",
        f"• Edad: {ATHLETE['edad']} años",
        f"• Altura: {ATHLETE['altura_cm']} cm",
        f"• Peso: {peso_act} kg (IMC {imc:.1f})",
        f"• Grasa corporal: {grasa_act if grasa_act is not None else '—'}%",
        f"• Mejor marca en media: {ATHLETE['marca_media']}",
        f"• Objetivo: {RACE_NAME} el {RACE_DATE.strftime('%d/%m/%Y')}",
    ]
    await update.message.reply_text("\n".join(out), parse_mode=MD)


async def notion(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not notion_sync.activo():
        await update.message.reply_text(
            "📓 *Notion no está configurado.*\n\n"
            "Para activarlo añade a tu `.env`:\n"
            "• `NOTION_TOKEN` — token de tu integración\n"
            "• `NOTION_PARENT_PAGE_ID` — id de la página donde crear las bases\n\n"
            "Tienes los pasos detallados en el README.", parse_mode=MD)
        return
    urls = await notion_sync.sync.url_bases()
    if not urls:
        await update.message.reply_text(
            "📓 Notion está configurado pero las bases aún no se han creado.\n"
            "Reinicia el bot y revisa los logs por si hay algún error de permisos.")
        return
    lineas = ["📓 *Notion conectado*", "", "Tus bases de datos:"]
    lineas += [f"• [{n}]({u})" for n, u in urls.items()]
    lineas += ["", "_Cada /hecho, /peso y /grasa se guarda automáticamente._"]
    await update.message.reply_text("\n".join(lineas), parse_mode=MD,
                                    disable_web_page_preview=True)


async def recordatorio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    if not ctx.args or ctx.args[0] not in ("on", "off"):
        await update.message.reply_text("Usa `/recordatorio on` o `/recordatorio off`.", parse_mode=MD)
        return
    activo = ctx.args[0] == "on"
    db.set_recordatorio(chat_id, activo)
    await update.message.reply_text(
        f"🔔 Recordatorio diario {'activado' if activo else 'desactivado'}"
        + (f" a las {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}." if activo else "."))


async def aviso_diario(ctx: ContextTypes.DEFAULT_TYPE):
    texto = plan.formatear_dia(_hoy())
    for chat_id in db.usuarios_con_recordatorio():
        try:
            await ctx.bot.send_message(chat_id, "☀️ *Entreno de hoy*\n\n" + texto, parse_mode=MD)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar a %s: %s", chat_id, e)


async def post_init(app: Application):
    if notion_sync.activo():
        if await notion_sync.sync.preparar():
            log.info("Notion conectado. Bases: %s", list(notion_sync.sync.db_ids))
        else:
            log.warning("Notion configurado pero no se pudo inicializar. "
                        "El bot seguirá guardando solo en local.")
    else:
        log.info("Notion no configurado: se guarda solo en SQLite.")

    await app.bot.set_my_commands([
        BotCommand("hoy", "Entreno de hoy"),
        BotCommand("manana", "Entreno de mañana"),
        BotCommand("semana", "Resumen de la semana"),
        BotCommand("gym", "Rutina de gimnasio de hoy"),
        BotCommand("ritmos", "Ritmos y zonas de entrenamiento"),
        BotCommand("fases", "Plan completo por fases"),
        BotCommand("hecho", "Marcar entreno como completado"),
        BotCommand("peso", "Registrar peso"),
        BotCommand("grasa", "Registrar % de grasa"),
        BotCommand("stats", "Progreso y adherencia"),
        BotCommand("faltan", "Cuenta atrás para la carrera"),
        BotCommand("notion", "Estado de la sincronización con Notion"),
        BotCommand("recordatorio", "Activar/desactivar aviso diario"),
        BotCommand("perfil", "Ver tu perfil"),
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
        "ritmos": ritmos, "fases": fases, "peso": peso, "grasa": grasa,
        "stats": stats, "hecho": hecho, "faltan": faltan, "perfil": perfil,
        "recordatorio": recordatorio, "notion": notion,
    }
    for nombre, fn in handlers.items():
        app.add_handler(CommandHandler(nombre, fn))

    if app.job_queue:
        app.job_queue.run_daily(
            aviso_diario,
            time=time(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=TZ),
            name="aviso_diario",
        )
    log.info("Bot en marcha. Plan de %s semanas.", plan.total_semanas())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
