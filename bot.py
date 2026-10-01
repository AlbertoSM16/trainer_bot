"""Bot de Telegram para gestionar entrenamientos de gimnasio, carrera, natación y bici."""
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

import alimentos
import compra
import db
import gym
import menu
import mercadona
import notion_sync
import nutricion
import plan
from config import (ATHLETE, COMPRA_HOUR, COMPRA_MINUTE, RACE_DATE, RACE_NAME,
                    REMINDER_HOUR, REMINDER_MINUTE, TELEGRAM_TOKEN, TIMEZONE)

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

*Cambios en tu semana*
/mover `jueves viernes` — intercambia dos días (o recupera uno saltado)
/saltar `[día]` — hoy (o ese día) descansas
/cambiar `corta|bici|natacion [día]` — versión corta o alternativa sin impacto
/deshacer `[día|semana]` — quita los cambios

*Seguimiento*
/hecho `[nota]` — marca el entreno de hoy como completado
/peso `78.4` — registra tu peso
/grasa `12.5` — registra tu % de grasa
/stats — progreso y adherencia

*Dieta*
/dieta `[mañana]` — kcal y macros del día según tu entreno
/menu `[mañana]` — qué comer y cuánto en cada comida (con whey y creatina)
/compra — lista de la compra de Mercadona para 7 días

*Otros*
/faltan — cuenta atrás para la carrera
/notion — estado de la sincronización con Notion
/recordatorio `on|off` — aviso diario
/perfil — tus datos
/ayuda — esta lista"""


def _hoy() -> date:
    return datetime.now(TZ).date()


def _cambios(chat_id: int, desde: date, hasta: date) -> dict[date, plan.Cambio]:
    return {d: plan.Cambio(o, m) for d, (o, m) in db.cambios_entre(chat_id, desde, hasta).items()}


def _cambio(chat_id: int, d: date) -> plan.Cambio | None:
    return _cambios(chat_id, d, d).get(d)


def _lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


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
    d = _hoy()
    await update.message.reply_text(
        plan.formatear_dia(d, _cambio(update.effective_chat.id, d)), parse_mode=MD)


async def manana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _hoy() + timedelta(days=1)
    await update.message.reply_text(
        plan.formatear_dia(d, _cambio(update.effective_chat.id, d)), parse_mode=MD)


async def semana(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    wn = plan.semana_indice(_hoy())
    if ctx.args:
        try:
            wn = int(ctx.args[0])
        except ValueError:
            await update.message.reply_text("Usa `/semana 7` con el número de semana.", parse_mode=MD)
            return
    lunes = plan.fecha_lunes(wn)
    cambios = _cambios(update.effective_chat.id, lunes, lunes + timedelta(days=6))
    await update.message.reply_text(plan.resumen_semana(wn, cambios), parse_mode=MD)


async def gimnasio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _hoy()
    wn = plan.semana_indice(d)
    if plan.datos_semana(wn) is None:
        await update.message.reply_text("Hoy estás fuera del plan.")
        return
    c = _cambio(update.effective_chat.id, d)
    grupos = {0: "pierna", 1: "pecho", 2: "espalda", 3: "hombro"}
    grupo = None
    if wn != plan.total_semanas() and (c is None or c.modo in (None, "corta")):
        grupo = grupos.get((c.origen if c else d).weekday())
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
    c = _cambio(update.effective_chat.id, d)
    s = plan.sesiones_dia(d, c)
    if s["fuera_de_plan"]:
        await update.message.reply_text(
            "Hoy no hay sesión planificada todavía. El plan arranca el "
            f"{plan.PLAN_START.strftime('%d/%m/%Y')}.")
        return
    if c and c.modo == "descanso":
        await update.message.reply_text(
            "Hoy tienes descanso 😴. Si al final entrenas, usa /deshacer y luego /hecho.")
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
        tipos=plan.tipos_dia(d, c),
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
    d = _hoy()
    for chat_id in db.usuarios_con_recordatorio():
        try:
            texto = plan.formatear_dia(d, _cambio(chat_id, d))
            await ctx.bot.send_message(chat_id, "☀️ *Entreno de hoy*\n\n" + texto, parse_mode=MD)
            await ctx.bot.send_message(chat_id, _menu(chat_id, _hoy()), parse_mode=MD)
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo enviar a %s: %s", chat_id, e)


# ---------- cambios en la semana ----------

_DIAS_NORM = {alimentos.normalizar(n): i for i, n in enumerate(plan.DIAS)}
_CLAVE = {"larga": "tirada larga", "calidad": "sesión de calidad"}


def _parse_dia(texto: str) -> date | None:
    t, h = alimentos.normalizar(texto), _hoy()
    if t == "hoy":
        return h
    if t == "manana":
        return h + timedelta(days=1)
    if t in _DIAS_NORM:
        return _lunes(h) + timedelta(days=_DIAS_NORM[t])
    return None


def _error_dia(d: date | None) -> str | None:
    if d is None:
        return "No entiendo el día. Usa `hoy`, `mañana` o un día de esta semana (`lunes`…`domingo`)."
    if d < _hoy():
        return "Ese día ya ha pasado: solo puedo cambiar de hoy en adelante."
    if plan.datos_semana(plan.semana_indice(d)) is None:
        return "Ese día está fuera del plan."
    if plan.es_dia_carrera(d):
        return "El día de la carrera no se toca 🏁"
    return None


def _nombre(d: date) -> str:
    return plan.DIAS[d.weekday()]


def _guardar(chat_id: int, nuevos: dict[date, plan.Cambio | None]):
    db.guardar_cambios(chat_id, {d: (c.origen, c.modo) if c else None for d, c in nuevos.items()})


def _cambios_semana(chat_id: int, d: date) -> dict[date, plan.Cambio]:
    return _cambios(chat_id, _lunes(d), _lunes(d) + timedelta(days=6))


def _resultado(chat_id: int, dias: list[date]) -> str:
    cambios = _cambios_semana(chat_id, dias[0])
    out = [f"• *{_nombre(d)}*: {plan.sesiones_dia(d, cambios.get(d))['titulo']}"
           for d in sorted(set(dias))]
    avisos = plan.avisos_semana(_lunes(dias[0]), cambios)
    if avisos:
        out += [""] + [f"⚠️ {a}" for a in avisos]
    return "\n".join(out)


async def _responder_error(update: Update, error: str):
    await update.message.reply_text(error, parse_mode=MD)


async def mover(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if len(ctx.args) != 2:
        await _responder_error(update, "Usa `/mover jueves viernes` (días de esta semana).")
        return
    a, b = (_parse_dia(x) for x in ctx.args)
    error = _error_dia(a) or _error_dia(b)
    if not error and a == b:
        error = "Son el mismo día."
    if not error and _lunes(a) != _lunes(b):
        error = "Solo puedo mover sesiones dentro de la misma semana (de lunes a domingo)."
    if error:
        await _responder_error(update, error)
        return
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    _guardar(chat_id, plan.mover(_cambios_semana(chat_id, a), a, b))
    await update.message.reply_text("🔀 *Semana cambiada*\n\n" + _resultado(chat_id, [a, b]),
                                    parse_mode=MD)


async def saltar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _parse_dia(ctx.args[0]) if ctx.args else _hoy()
    error = _error_dia(d)
    if error:
        await _responder_error(update, error)
        return
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    cambios = _cambios_semana(chat_id, d)
    if plan.efectivo(cambios, d).modo == "descanso":
        await update.message.reply_text(f"El {_nombre(d).lower()} ya es de descanso.")
        return
    clave = _CLAVE.get(plan.carga_dia(d, cambios))
    _guardar(chat_id, plan.cambiar(cambios, d, "descanso"))
    out = [f"⏭️ *{_nombre(d)}*: descanso."]
    if clave:
        hueco = plan.sugerir_recuperacion(_cambios_semana(chat_id, d), d, _hoy())
        out.append(f"\nEra tu *{clave}*, la sesión más importante de la semana.")
        if hueco:
            out.append(f"💡 Puedes recuperarla el {_nombre(hueco).lower()}: "
                       f"`/mover {_nombre(d).lower()} {_nombre(hueco).lower()}`")
        else:
            out.append("Esta semana no queda hueco sin sobrecargarte: mejor dejarla.")
    await update.message.reply_text("\n".join(out), parse_mode=MD)


async def cambiar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    modos = {"corta": "corta", "bici": "bici", "natacion": "natacion", "nadar": "natacion",
             "descanso": "descanso", "normal": None}
    modo = alimentos.normalizar(ctx.args[0]) if ctx.args else ""
    if modo not in modos:
        await _responder_error(update, "Usa `/cambiar corta`, `/cambiar bici` o "
                                       "`/cambiar natacion` (y opcionalmente el día). "
                                       "`/cambiar normal` vuelve a la sesión original.")
        return
    d = _parse_dia(ctx.args[1]) if len(ctx.args) > 1 else _hoy()
    error = _error_dia(d)
    if error:
        await _responder_error(update, error)
        return
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    _guardar(chat_id, plan.cambiar(_cambios_semana(chat_id, d), d, modos[modo]))
    if d == _hoy():
        await update.message.reply_text(plan.formatear_dia(d, _cambio(chat_id, d)), parse_mode=MD)
    else:
        await update.message.reply_text("✅ " + _resultado(chat_id, [d]), parse_mode=MD)


async def deshacer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    arg = alimentos.normalizar(ctx.args[0]) if ctx.args else "hoy"
    if arg == "semana":
        cambios = _cambios_semana(chat_id, _hoy())
        nuevos = {d: None for d in cambios if d >= _hoy()}
        dias = list(nuevos)
    else:
        d = _parse_dia(arg)
        error = _error_dia(d)
        if error:
            await _responder_error(update, error)
            return
        cambios = _cambios_semana(chat_id, d)
        nuevos = plan.deshacer(cambios, d)
        dias = list(nuevos)
    if not nuevos:
        await update.message.reply_text("No hay cambios que deshacer.")
        return
    _guardar(chat_id, nuevos)
    await update.message.reply_text("↩️ *Plan original*\n\n" + _resultado(chat_id, dias),
                                    parse_mode=MD)


# ---------- dieta ----------

def _peso_actual(chat_id: int) -> float:
    return db.ultimo_peso(chat_id) or ATHLETE["peso_inicial_kg"]


def _dia_pedido(ctx: ContextTypes.DEFAULT_TYPE) -> date:
    d = _hoy()
    if ctx.args and alimentos.normalizar(ctx.args[0]) == "manana":
        d += timedelta(days=1)
    return d


def _menu(chat_id: int, d: date) -> str:
    """Menú del día con la misma variante de proteína que la lista de la compra de esa
    semana (de sábado a viernes), calculada con los precios en caché."""
    ajuste, _ = db.ajuste_kcal(chat_id)
    peso = _peso_actual(chat_id)
    sabado = d - timedelta(days=(d.weekday() - 5) % 7)
    cambios = _cambios(chat_id, sabado, sabado + timedelta(days=6))
    opciones = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
    objetivos = nutricion.objetivos_semana(sabado, peso, ajuste, cambios)
    variante = compra.construir(objetivos, opciones).variante
    return menu.formatear(menu.menu_dia(objetivos[(d - sabado).days], variante))


async def menu_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    await update.message.reply_text(_menu(chat_id, _dia_pedido(ctx)), parse_mode=MD)


async def dieta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = _dia_pedido(ctx)
    chat_id = update.effective_chat.id
    ajuste, _ = db.ajuste_kcal(chat_id)
    o = nutricion.objetivo_dia(d, _peso_actual(chat_id), ajuste, _cambio(chat_id, d))
    await update.message.reply_text(nutricion.formatear_objetivo(o) + "\n\n🍽️ Qué comer: /menu",
                                    parse_mode=MD)


async def _lista_compra(chat_id: int) -> str:
    await mercadona.actualizar()
    ajuste, _ = db.ajuste_kcal(chat_id)
    cambios = _cambios(chat_id, _hoy(), _hoy() + timedelta(days=6))
    objetivos = nutricion.objetivos_semana(_hoy(), _peso_actual(chat_id), ajuste, cambios)
    opciones = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
    return compra.formatear(compra.construir(objetivos, opciones))


async def lista_compra(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    db.alta_usuario(chat_id, update.effective_user.first_name or "atleta")
    await update.message.reply_text("🛒 Consultando precios de Mercadona…")
    await update.message.reply_text(await _lista_compra(chat_id), parse_mode=MD,
                                    disable_web_page_preview=True)


def _ajuste_semanal(chat_id: int) -> str:
    """Recalcula el ajuste de kcal según la evolución del peso (como mucho una vez al día)."""
    hoy_d = _hoy()
    actual, fecha = db.ajuste_kcal(chat_id)
    if fecha == hoy_d.isoformat():
        return ""
    pesos = db.pesos_desde(chat_id, hoy_d - timedelta(days=14))
    nuevo, motivo = nutricion.nuevo_ajuste(pesos, hoy_d, actual)
    db.guardar_ajuste_kcal(chat_id, nuevo, hoy_d)
    return f"⚖️ {motivo}\n\n"


async def aviso_compra(ctx: ContextTypes.DEFAULT_TYPE):
    for chat_id in db.usuarios_con_recordatorio():
        try:
            texto = _ajuste_semanal(chat_id) + await _lista_compra(chat_id)
            await ctx.bot.send_message(chat_id, "🛍️ *¡Sábado de compra!*\n\n" + texto,
                                       parse_mode=MD, disable_web_page_preview=True)
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

    await app.bot.set_my_commands([
        BotCommand("hoy", "Entreno de hoy"),
        BotCommand("manana", "Entreno de mañana"),
        BotCommand("semana", "Resumen de la semana"),
        BotCommand("gym", "Rutina de gimnasio de hoy"),
        BotCommand("ritmos", "Ritmos y zonas de entrenamiento"),
        BotCommand("fases", "Plan completo por fases"),
        BotCommand("mover", "Intercambiar dos días de esta semana"),
        BotCommand("saltar", "Descansar hoy u otro día"),
        BotCommand("cambiar", "Versión corta, bici o natación"),
        BotCommand("deshacer", "Quitar cambios de la semana"),
        BotCommand("hecho", "Marcar entreno como completado"),
        BotCommand("peso", "Registrar peso"),
        BotCommand("grasa", "Registrar % de grasa"),
        BotCommand("stats", "Progreso y adherencia"),
        BotCommand("dieta", "Kcal y macros de hoy"),
        BotCommand("menu", "Qué comer hoy y cuánto"),
        BotCommand("compra", "Lista de la compra de Mercadona"),
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
        "mover": mover, "saltar": saltar, "cambiar": cambiar, "deshacer": deshacer,
        "dieta": dieta, "menu": menu_cmd, "compra": lista_compra,
    }
    for nombre, fn in handlers.items():
        app.add_handler(CommandHandler(nombre, fn))

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
    log.info("Bot en marcha. Plan de %s semanas.", plan.total_semanas())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
