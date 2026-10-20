"""Acciones del usuario, compartidas por los comandos de Telegram (bot.py) y el coach (coach.py).

Escriben primero en SQLite, luego sincronizan con Notion (best-effort) y devuelven el texto de
respuesta en Markdown legacy de Telegram. Los errores de uso se lanzan como `ErrorUsuario`.
"""
import asyncio
import logging
import re
import shlex
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import alimentos
import compra
import db
import entrenador
import garmin
import garmin_ejercicios
import gym
import menu
import mercadona
import notion_sync
import nutricion
import plan
from config import (ATHLETE, GARMIN_AJUSTES, GARMIN_CHAT_ID, GARMIN_DIAS_ENVIO,
                    GARMIN_USAR_PREDICCION, PESO_OBJETIVO, TEST_VIGENCIA_DIAS, TIMEZONE)

TZ = ZoneInfo(TIMEZONE)
log = logging.getLogger(__name__)


class ErrorUsuario(Exception):
    """Petición que no se puede aplicar; el mensaje se muestra tal cual al usuario."""


def hoy() -> date:
    return datetime.now(TZ).date()


def ahora() -> datetime:
    return datetime.now(TZ)


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def nombre(d: date) -> str:
    return plan.DIAS[d.weekday()]


# ---------- plan y cambios ----------

def cambios(chat_id: int, desde: date, hasta: date) -> dict[date, plan.Cambio]:
    return {d: plan.Cambio(o, m) for d, (o, m) in db.cambios_entre(chat_id, desde, hasta).items()}


def cambio(chat_id: int, d: date) -> plan.Cambio | None:
    return cambios(chat_id, d, d).get(d)


def cambios_semana(chat_id: int, d: date) -> dict[date, plan.Cambio]:
    return cambios(chat_id, lunes(d), lunes(d) + timedelta(days=6))


def guardar(chat_id: int, nuevos: dict[date, plan.Cambio | None]):
    db.guardar_cambios(chat_id, {d: (c.origen, c.modo) if c else None for d, c in nuevos.items()})


def _linea_ejercicio(f) -> str:
    if f["reps_max"]:
        reps = (f"{f['reps_min']}" if f["reps_min"] == f["reps_max"] else
                f"{f['reps_min']}-{f['reps_max']}")
        txt = f"{f['series']}x{reps}"
    else:
        txt = f"{f['series']} series"
    if f["peso_kg"] is not None:
        txt += f" · {f['peso_kg']:g} kg"
    if f["nota"]:
        txt += f" ({menu._md(f['nota'])})"
    return f"`#{f['id']}` {menu._md(f['ejercicio'])} — {txt}"


def rutinas(chat_id: int) -> dict:
    """Rutina propia en el formato de `gym.formatear`: {grupo: {variante: {...}}}.
    Los ejercicios sin variante valen para A y B."""
    filas = db.rutina(chat_id)
    out = {}
    for grupo in dict.fromkeys(f["grupo"] for f in filas):
        fg = [f for f in filas if f["grupo"] == grupo]
        con_variante = any(f["variante"] for f in fg)
        out[grupo] = {}
        for v in ("A", "B"):
            fv = [f for f in fg if f["variante"] in (None, v)] or fg
            out[grupo][v] = {
                "titulo": gym.titulo(grupo).upper() + (f" {v}" if con_variante else ""),
                "ejercicios": [_linea_ejercicio(f) for f in fv],
                "filas": [dict(f) for f in fv],
            }
    return out


def estructura(chat_id: int) -> plan.Estructura:
    dias = db.semana_usuario(chat_id)
    base = (tuple(tuple(dias[i]) for i in range(7)) if dias else
            plan.ESTRUCTURA_DEFECTO.dias)
    return plan.Estructura(base, rutinas(chat_id))


_DIAS_NORM = {alimentos.normalizar(n): i for i, n in enumerate(plan.DIAS)}


def parse_dia(texto: str) -> date | None:
    """hoy | mañana | lunes…domingo (de esta semana) | AAAA-MM-DD | DD/MM."""
    t, h = alimentos.normalizar(texto or "hoy"), hoy()
    if t == "hoy":
        return h
    if t == "manana":
        return h + timedelta(days=1)
    if t in _DIAS_NORM:
        return lunes(h) + timedelta(days=_DIAS_NORM[t])
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        try:
            return date(h.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def error_dia(d: date | None) -> str | None:
    if d is None:
        return "No entiendo el día. Usa `hoy`, `mañana` o un día de esta semana (`lunes`…`domingo`)."
    if d < hoy():
        return "Ese día ya ha pasado: solo puedo cambiar de hoy en adelante."
    if plan.datos_semana(plan.semana_indice(d)) is None:
        return "Ese día está fuera del plan."
    return None


def dia_cambiable(texto: str | None) -> date:
    d = parse_dia(texto or "hoy")
    error = error_dia(d)
    if error:
        raise ErrorUsuario(error)
    return d


def ritmo(chat_id: int) -> tuple[int, str]:
    """Ritmo de referencia (media, s/km) del usuario y de dónde sale: un test reciente, si no
    la predicción de media de Garmin, si no un test antiguo y si no la marca de config."""
    t = db.ultimo_test(chat_id)
    pred = db.ultima_prediccion(chat_id) if GARMIN_USAR_PREDICCION else None
    if t and (pred is None or (hoy() - t[0]).days <= TEST_VIGENCIA_DIAS):
        f, km, seg = t
        return plan.ritmo_ref((km, seg)), (f"Según tu test de {km:g} km del {f.strftime('%d/%m')} "
                                           f"({plan.tiempo_txt(seg)})")
    if pred:
        f, seg = pred
        return round(seg / plan.MEDIA_KM), (f"Según la predicción de media de tu Garmin "
                                            f"({plan.tiempo_txt(seg)}, {f.strftime('%d/%m')})")
    return plan.ritmo_ref(), (f"Estimados de tu marca en media ({ATHLETE['marca_media']}) "
                              "con margen por el parón. Se ajustan con tu primer test.")


def sesion(chat_id: int, d: date) -> dict:
    """Sesión del día con los cambios, el ritmo y la semana tipo del usuario."""
    return plan.sesiones_dia(d, cambio(chat_id, d), ritmo(chat_id)[0], estructura(chat_id))


def formatear_dia(chat_id: int, d: date) -> str:
    return plan.formatear_dia(d, cambio(chat_id, d), ritmo(chat_id)[0], estructura(chat_id))


def resumen_semana(chat_id: int, week_num: int) -> str:
    l = plan.fecha_lunes(week_num)
    return plan.resumen_semana(week_num, cambios(chat_id, l, l + timedelta(days=6)),
                               estructura(chat_id))


def resultado(chat_id: int, dias: list[date]) -> str:
    cs = cambios_semana(chat_id, dias[0])
    est = estructura(chat_id)
    out = [f"• *{nombre(d)}*: {plan.sesiones_dia(d, cs.get(d), None, est)['titulo']}"
           for d in sorted(set(dias))]
    avisos = plan.avisos_semana(lunes(dias[0]), cs, est)
    if avisos:
        out += [""] + [f"⚠️ {a}" for a in avisos]
    return "\n".join(out)


CLAVE = {"carrera": "carrera de la semana", "pierna": "sesión de pierna"}
MODOS_TEXTO = {"corta": "corta", "suave": "suave", "bici": "bici", "natacion": "natacion",
               "nadar": "natacion", "descanso": "descanso", "normal": None}


def mover(chat_id: int, texto_a: str, texto_b: str) -> str:
    a, b = dia_cambiable(texto_a), dia_cambiable(texto_b)
    if a == b:
        raise ErrorUsuario("Son el mismo día.")
    if lunes(a) != lunes(b):
        raise ErrorUsuario("Solo puedo mover sesiones dentro de la misma semana (de lunes a domingo).")
    guardar(chat_id, plan.mover(cambios_semana(chat_id, a), a, b))
    return "🔀 *Semana cambiada*\n\n" + resultado(chat_id, [a, b])


def saltar(chat_id: int, texto: str | None = None) -> str:
    d = dia_cambiable(texto)
    cs = cambios_semana(chat_id, d)
    if plan.efectivo(cs, d).modo == "descanso":
        return f"El {nombre(d).lower()} ya es de descanso."
    est = estructura(chat_id)
    clave = CLAVE.get(plan.carga_dia(d, cs, est))
    guardar(chat_id, plan.cambiar(cs, d, "descanso"))
    out = [f"⏭️ *{nombre(d)}*: descanso."]
    if clave:
        hueco = plan.sugerir_recuperacion(cambios_semana(chat_id, d), d, hoy(), est)
        out.append(f"\nEra tu *{clave}*.")
        if hueco:
            out.append(f"💡 Puedes recuperarla el {nombre(hueco).lower()}: "
                       f"`/mover {nombre(d).lower()} {nombre(hueco).lower()}`")
        else:
            out.append("Esta semana no queda hueco sin sobrecargarte: mejor dejarla.")
    return "\n".join(out)


def cambiar(chat_id: int, modo_texto: str, texto_dia: str | None = None) -> str:
    modo_n = alimentos.normalizar(modo_texto or "")
    if modo_n not in MODOS_TEXTO:
        raise ErrorUsuario("Usa `/cambiar corta|suave|bici|natacion [día]`. "
                           "`/cambiar normal` vuelve a la sesión original.")
    d = dia_cambiable(texto_dia)
    guardar(chat_id, plan.cambiar(cambios_semana(chat_id, d), d, MODOS_TEXTO[modo_n]))
    if d == hoy():
        return formatear_dia(chat_id, d)
    return "✅ " + resultado(chat_id, [d])


def deshacer(chat_id: int, texto: str | None = None) -> str:
    arg = alimentos.normalizar(texto or "hoy")
    if arg == "semana":
        cs = cambios_semana(chat_id, hoy())
        nuevos = {d: None for d in cs if d >= hoy()}
    else:
        d = dia_cambiable(arg)
        nuevos = plan.deshacer(cambios_semana(chat_id, d), d)
    if not nuevos:
        return "No hay cambios que deshacer."
    guardar(chat_id, nuevos)
    return "↩️ *Plan original*\n\n" + resultado(chat_id, list(nuevos))


# ---------- sensaciones ----------

def registrar_sensaciones(chat_id: int, energia: int | None, molestias: str = "",
                          nota: str = "") -> str:
    if energia is not None and not 1 <= energia <= 5:
        raise ErrorUsuario("La energía va de 1 (fundido) a 5 (a tope).")
    db.guardar_sensaciones(chat_id, hoy(), energia, molestias, nota)
    return "📝 Sensaciones anotadas."


def aplicar_sensaciones(chat_id: int, energia: int, zona: str) -> str:
    """Reglas sin IA: anota las sensaciones y adapta la sesión de hoy si hace falta."""
    d = hoy()
    registrar_sensaciones(chat_id, energia, plan.ZONAS_MOLESTIA.get(zona, zona))
    if plan.datos_semana(plan.semana_indice(d)) is None:
        return "📝 Anotado. Hoy aún no hay plan."
    cs = cambios_semana(chat_id, d)
    est = estructura(chat_id)
    modo, motivo = plan.recomendar(d, cs, energia, zona, est)
    out = [f"📝 Energía {energia}/5 · {plan.ZONAS_MOLESTIA.get(zona, zona)}", "", f"💡 {motivo}"]
    if modo:
        guardar(chat_id, plan.cambiar(cs, d, modo))
        out += ["", formatear_dia(chat_id, d)]
        if modo == "descanso":
            clave = CLAVE.get(plan.carga_dia(d, cs, est))
            hueco = clave and plan.sugerir_recuperacion(cambios_semana(chat_id, d), d, d, est)
            if hueco:
                out += ["", f"💡 La {clave} se puede recuperar el {nombre(hueco).lower()}: "
                            f"`/mover {nombre(d).lower()} {nombre(hueco).lower()}`"]
        out += ["", "_Si mejoras, `/deshacer` vuelve a la sesión original._"]
    elif (c := cs.get(d)) and c.modo:
        out += ["", "_Hoy ya tenías la sesión cambiada; `/deshacer` vuelve a la original._"]
    if zona != "ninguna":
        out += ["", "_Si es un dolor agudo, punzante o dura más de una semana, consulta a "
                    "un fisio o médico._"]
    return "\n".join(out)


# ---------- métricas, entrenos y tests ----------

async def registrar_metrica(chat_id: int, campo: str, valor: float) -> str:
    if campo == "peso" and not 40 <= valor <= 150:
        raise ErrorUsuario("Ese peso no parece correcto.")
    if campo == "grasa" and not 3 <= valor <= 50:
        raise ErrorUsuario("Ese % de grasa no parece correcto.")
    db.registrar_metrica(chat_id, **{campo: valor})
    ult = db.historico_metricas(chat_id, 1)
    fila = ult[0] if ult else None
    imc = None
    if fila and fila["peso"] is not None:
        imc = fila["peso"] / (ATHLETE["altura_cm"] / 100) ** 2
    unidad = "kg" if campo == "peso" else "%"
    extra = ""
    if campo == "peso":
        extra = f"\nIMC: {imc:.1f}" if imc else ""
        falta = PESO_OBJETIVO - valor
        extra += (f"\n🎯 Te faltan {falta:.1f} kg para {PESO_OBJETIVO:g} kg." if falta > 0 else
                  f"\n🎯 ¡Objetivo de {PESO_OBJETIVO:g} kg alcanzado! La dieta pasa a mantenimiento.")
    ok = await notion_sync.sync.guardar_metrica(
        hoy(), fila["peso"] if fila else None, fila["grasa"] if fila else None, imc)
    sufijo = "\n📓 Guardado también en Notion." if ok else ""
    return f"✅ Registrado: {campo} = {valor:g} {unidad}{extra}{sufijo}"


async def guardar_entreno(chat_id: int, d: date, titulo: str, nota: str = "") -> bool:
    """Guarda un entreno hecho en SQLite y Notion. Devuelve si se guardó en Notion."""
    c = cambio(chat_id, d)
    est = estructura(chat_id)
    s = plan.sesiones_dia(d, c, None, est)
    db.marcar_hecho(chat_id, d, titulo, nota)
    return await notion_sync.sync.guardar_entreno(
        fecha=d, titulo=titulo, nota=nota, semana=s["semana"],
        fase=plan.fase_nombre_corto(s["fase"]), dia=nombre(d),
        tipos=plan.tipos_dia(d, c, est), descarga=bool(s.get("descarga")))


async def marcar_hecho(chat_id: int, nota: str = "") -> str:
    d = hoy()
    c = cambio(chat_id, d)
    est = estructura(chat_id)
    s = plan.sesiones_dia(d, c, None, est)
    if s["fuera_de_plan"]:
        raise ErrorUsuario("Hoy no hay sesión planificada todavía. El plan arranca el "
                           f"{plan.PLAN_START.strftime('%d/%m/%Y')}.")
    if (c and c.modo == "descanso") or (not c and est.descanso(d.weekday())):
        raise ErrorUsuario("Hoy tienes descanso 😴. Si al final entrenas, cambia el día "
                           "(`/mover` o `/deshacer`) y luego marca /hecho.")
    ok = await guardar_entreno(chat_id, d, s["titulo"], nota)
    msg = f"✅ Anotado: *{s['titulo']}*"
    if nota:
        msg += f"\n📝 {menu._md(nota)}"
    if ok:
        msg += "\n📓 Guardado también en Notion."
    return msg + "\n\n¡Buen trabajo! 💪"


def parse_distancia(texto: str) -> float:
    t = alimentos.normalizar(texto).replace(",", ".")
    t = {"media": "21.0975", "mm": "21.0975"}.get(t, t)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|km)?", t)
    if not m:
        raise ErrorUsuario("No entiendo la distancia. Usa por ejemplo `5k`, `10k` o `media`.")
    km = float(m.group(1))
    if not 1 <= km <= 42.2:
        raise ErrorUsuario("La distancia debe estar entre 1 y 42 km.")
    return km


def parse_tiempo(texto: str) -> int:
    partes = texto.strip().split(":")
    if not 2 <= len(partes) <= 3 or not all(p.isdigit() for p in partes):
        raise ErrorUsuario("No entiendo el tiempo. Usa `mm:ss` o `h:mm:ss`.")
    seg = 0
    for p in partes:
        seg = seg * 60 + int(p)
    return seg


def registrar_test(chat_id: int, km: float, segundos: int) -> str:
    ritmo_km = segundos / km
    if not 150 <= ritmo_km <= 600:
        raise ErrorUsuario("Ese tiempo da un ritmo poco realista. Revisa distancia y tiempo.")
    antes, _ = ritmo(chat_id)
    db.guardar_test(chat_id, hoy(), km, segundos)
    ref, fuente = ritmo(chat_id)
    dif = ref - antes
    cambio_txt = ("igual que antes" if dif == 0 else
                  f"{abs(dif)} s/km {'más rápido' if dif < 0 else 'más lento'} que antes")
    return "\n".join([
        f"⏱️ *Test registrado*: {km:g} km en {plan.tiempo_txt(segundos)} "
        f"({plan.pace(round(ritmo_km))} /km)",
        f"Ritmo de media equivalente: *{plan.pace(ref)} /km* ({cambio_txt}).",
        "", plan.tabla_ritmos(ref, fuente)])


# ---------- dieta ----------

def peso_actual(chat_id: int) -> float:
    return db.ultimo_peso(chat_id) or ATHLETE["peso_inicial_kg"]


def objetivo(chat_id: int, d: date) -> nutricion.Objetivo:
    ajuste, _ = db.ajuste_kcal(chat_id)
    return nutricion.objetivo_dia(d, peso_actual(chat_id), ajuste, cambio(chat_id, d),
                                  estructura(chat_id))


def dieta_texto(chat_id: int, d: date) -> str:
    return nutricion.formatear_objetivo(objetivo(chat_id, d)) + "\n\n🍽️ Qué comer: /menu"


def _menus_semana(chat_id: int, d: date) -> tuple[date, list[nutricion.Objetivo], str,
                                                   dict[date, dict[str, str]]]:
    """Objetivos y variante de proteína de la semana de compra (sábado a viernes) de `d`,
    con la misma variante que elegiría la lista de la compra (precios en caché)."""
    ajuste, _ = db.ajuste_kcal(chat_id)
    sabado = d - timedelta(days=(d.weekday() - 5) % 7)
    domingo = sabado + timedelta(days=6)
    cs = cambios(chat_id, sabado, domingo)
    platos = db.platos_entre(chat_id, sabado, domingo)
    opciones = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
    objetivos = nutricion.objetivos_semana(sabado, peso_actual(chat_id), ajuste, cs,
                                           estructura(chat_id))
    variante = compra.construir(objetivos, opciones, platos).variante
    return sabado, objetivos, variante, platos


def menu_texto(chat_id: int, d: date) -> str:
    sabado, objetivos, variante, platos = _menus_semana(chat_id, d)
    return menu.formatear(menu.menu_dia(objetivos[(d - sabado).days], variante, platos.get(d)))


def menu_semana_texto(chat_id: int, d: date) -> str:
    """Platos de los 7 días de la semana de compra de `d` (sábado a viernes)."""
    _, objetivos, variante, platos = _menus_semana(chat_id, d)
    return menu.formatear_semana([menu.menu_dia(o, variante, platos.get(o.fecha))
                                  for o in objetivos])


async def lista_compra(chat_id: int) -> str:
    await mercadona.actualizar()
    h = hoy()
    ajuste, _ = db.ajuste_kcal(chat_id)
    cs = cambios(chat_id, h, h + timedelta(days=6))
    objetivos = nutricion.objetivos_semana(h, peso_actual(chat_id), ajuste, cs,
                                           estructura(chat_id))
    opciones = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
    platos = db.platos_entre(chat_id, h, h + timedelta(days=6))
    return compra.formatear(compra.construir(objetivos, opciones, platos))


def ajuste_semanal(chat_id: int) -> str:
    """Recalcula el ajuste de kcal según la evolución del peso (como mucho una vez al día)."""
    h = hoy()
    actual, fecha = db.ajuste_kcal(chat_id)
    if fecha == h.isoformat():
        return ""
    pesos = db.pesos_desde(chat_id, h - timedelta(days=14))
    objetivo_kg = nutricion.kg_semana_objetivo(peso_actual(chat_id))
    nuevo, motivo = nutricion.nuevo_ajuste(pesos, h, actual, objetivo_kg)
    db.guardar_ajuste_kcal(chat_id, nuevo, h)
    return f"⚖️ {motivo}\n\n"


COMIDAS_NORM = {"desayuno": "desayuno", "comida": "comida", "almuerzo": "comida",
                "taper": "comida", "merienda": "merienda", "cena": "cena"}


def platos_disponibles(comida: str | None = None) -> str:
    comidas = [comida] if comida else list(menu.COMIDAS)
    out = []
    for c in comidas:
        out += ["", f"*{menu.COMIDAS[c]}*"]
        for k in menu.platos_validos(c):
            p = menu.PLATOS[k]
            nombre_p = p.nombre.format(p=menu.NOMBRE_CORTO.get(p.proteina, "proteína"))
            out.append(f"• `{k}` — {menu._md(nombre_p)}")
    return "\n".join(out).strip()


def cambiar_plato(chat_id: int, texto_dia: str | None, comida: str, plato: str | None) -> str:
    """Cambia el plato de una comida de un día (de hoy a 7 días vista). `None` o `normal`
    vuelve al plato del plan."""
    d = parse_dia(texto_dia or "hoy")
    if d is None or d < hoy() or d > hoy() + timedelta(days=7):
        raise ErrorUsuario("Solo puedo cambiar platos de hoy a 7 días vista.")
    c = COMIDAS_NORM.get(alimentos.normalizar(comida or ""))
    if not c:
        raise ErrorUsuario("La comida tiene que ser `desayuno`, `comida`, `merienda` o `cena`.")
    clave = alimentos.normalizar(plato or "normal").replace(" ", "_")
    if clave == "normal":
        db.guardar_plato(chat_id, d, c, None)
        return f"↩️ {nombre(d)}: {c} vuelve al plato del plan.\n\n" + menu_texto(chat_id, d)
    if clave not in menu.platos_validos(c):
        raise ErrorUsuario(f"`{clave}` no vale para {c}. Opciones:\n\n" + platos_disponibles(c))
    db.guardar_plato(chat_id, d, c, clave)
    return f"🔁 {nombre(d)}: plato de {c} cambiado.\n\n" + menu_texto(chat_id, d)


# ---------- perfil y bloque ----------

def perfil_texto(chat_id: int) -> str:
    p = peso_actual(chat_id)
    grasa = next((r["grasa"] for r in db.historico_metricas(chat_id, 60)
                  if r["grasa"] is not None), None)
    imc = p / (ATHLETE["altura_cm"] / 100) ** 2
    ref, fuente = ritmo(chat_id)
    estado = ("mantenimiento" if p >= PESO_OBJETIVO else
              f"volumen limpio (+{nutricion.superavit(p)} kcal/día)")
    return "\n".join([
        "👤 *Tu perfil*", "",
        f"• Edad: {ATHLETE['edad']} años · Altura: {ATHLETE['altura_cm']} cm",
        f"• Peso: {p:g} kg (IMC {imc:.1f}) → objetivo {PESO_OBJETIVO:g} kg",
        f"• Grasa corporal: {grasa if grasa is not None else '—'}%",
        f"• Dieta: {estado}",
        f"• Mejor marca en media: {ATHLETE['marca_media']}",
        f"• Ritmo de media actual: {plan.pace(ref)} /km",
        f"  _{fuente}_",
        "", f"🎯 _{ATHLETE['objetivo']}_",
    ])


def bloque_texto(chat_id: int) -> str:
    d = hoy()
    wn = plan.semana_indice(d)
    pt = plan.proximo_test(d, estructura(chat_id))
    out = []
    if wn >= 1:
        out.append(f"Vas por la *semana {plan.semana_en_bloque(wn)}/{plan.SEMANAS_BLOQUE}* "
                   f"del bloque {plan.bloque_de(wn)} (semana {wn} del plan).")
    if pt:
        test, km = pt
        dias = (test - d).days
        out.append(f"⏱️ Próximo test: *{km} km* el {nombre(test).lower()} "
                   f"{test.strftime('%d/%m')} "
                   + ("(¡hoy!)" if dias == 0 else f"(dentro de {dias} días)") + ".")
    else:
        out.append("⏱️ Sin día de carrera en tu semana tipo no hay tests (`/semana_config`).")
    tests = db.tests(chat_id, 3)
    if tests:
        out.append("Últimos tests: " + " · ".join(
            f"{t['km']:g} km en {plan.tiempo_txt(t['segundos'])} "
            f"({date.fromisoformat(t['fecha']).strftime('%d/%m')})"
            for t in tests))
    if not pt:
        return "\n".join(out)
    return "\n".join(out) + "\n\n" + plan.resumen_bloque(wn, ritmo(chat_id)[0])


# ---------- semana tipo ----------

TIPOS_TEXTO = {"gym": "gym", "gimnasio": "gym", "carrera": "carrera", "correr": "carrera",
               "natacion": "natacion", "nadar": "natacion", "piscina": "natacion",
               "bici": "bici", "ciclismo": "bici", "descanso": "descanso", "libre": "descanso"}


def _grupo_valido(g: str) -> str:
    g = alimentos.normalizar(g).replace(" ", "_")
    if not re.fullmatch(r"[a-z0-9_]{2,20}", g):
        raise ErrorUsuario(f"«{g}» no vale como grupo: usa una palabra (pecho, torso, pierna…).")
    return g


def parse_items(texto: str, chat_id: int) -> list[tuple[str, str | None]]:
    """«carrera + gym:hombro», «pecho», «descanso»… -> [(tipo, grupo)]."""
    conocidos = set(gym.GRUPOS) | {f["grupo"] for f in db.rutina(chat_id)}
    items = []
    for tok in re.split(r"[+,\s]+", alimentos.normalizar(texto or "")):
        if not tok:
            continue
        if tok.startswith(("gym:", "gimnasio:")):
            items.append(("gym", _grupo_valido(tok.split(":", 1)[1])))
        elif tok in TIPOS_TEXTO and TIPOS_TEXTO[tok] != "gym":
            items.append((TIPOS_TEXTO[tok], None))
        elif tok in conocidos:
            items.append(("gym", tok))
        else:
            raise ErrorUsuario(f"No entiendo «{tok}». Usa carrera, natacion, bici, descanso o "
                               "gym:grupo (por ejemplo `gym:pecho`).")
    if not items:
        raise ErrorUsuario("Dime qué lleva el día, por ejemplo `carrera+gym:hombro` o `descanso`.")
    if len(items) > 1:
        items = [i for i in items if i[0] != "descanso"] or [("descanso", None)]
    return items


def semana_config_texto(chat_id: int) -> str:
    est = estructura(chat_id)
    propia = db.semana_usuario(chat_id) is not None
    out = ["🗓️ *Tu semana tipo*" + ("" if propia else " (la de por defecto)"), ""]
    for i in range(7):
        partes = [f"Gimnasio · {gym.titulo(g)}" if t == "gym" else plan.TIPOS[t]
                  for t, g in est.items(i)]
        out.append(f"*{plan.DIAS[i]}* — {' + '.join(partes)}")
    sin_rutina = sorted({g for i in range(7) for g in est.grupos(i)}
                        - set(est.rutinas) - set(gym.GRUPOS))
    if sin_rutina:
        out += ["", "⚠️ Grupos sin rutina: " + ", ".join(sin_rutina) + " (`/rutina_add`)."]
    avisos = plan.avisos_estructura(est)
    if avisos:
        out += [""] + [f"⚠️ {a}" for a in avisos]
    out += ["", "Cambia un día con `/dia_config sabado carrera+gym:hombro` (tipos: carrera, "
                "natacion, bici, descanso, gym:grupo). `/semana_reset` vuelve a la de por "
                "defecto."]
    return "\n".join(out)


def configurar_dia(chat_id: int, texto_dia: str, texto_items: str) -> str:
    dow = _DIAS_NORM.get(alimentos.normalizar(texto_dia or ""))
    if dow is None:
        raise ErrorUsuario("Indica el día de la semana (`lunes`…`domingo`).")
    est = estructura(chat_id)
    dias = {i: list(est.dias[i]) for i in range(7)}
    dias[dow] = parse_items(texto_items, chat_id)
    db.guardar_semana(chat_id, dias)
    return "✅ Semana tipo actualizada.\n\n" + semana_config_texto(chat_id)


def reset_semana(chat_id: int) -> str:
    db.guardar_semana(chat_id, None)
    return "↩️ Vuelves a la semana por defecto.\n\n" + semana_config_texto(chat_id)


# ---------- rutina propia ----------

_SPEC = re.compile(r"(\d+)\s*x\s*(\d+)(?:\s*-\s*(\d+))?")
_KG = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg")
_DESCANSO = re.compile(r"(\d+)\s*(s|seg|\"|'|min)")


def _tokens(texto: str) -> list[str]:
    """Separa por espacios respetando comillas (también las tipográficas del móvil).
    `2'` y `90"` se leen como minutos y segundos de descanso."""
    texto = (texto or "").translate(str.maketrans("“”«»‘’", "\"\"\"\"''"))
    texto = re.sub(r"(\d)'", r"\1min", texto)
    texto = re.sub(r"(\d)\"(?=\s|$)", r"\1s", texto)
    try:
        return shlex.split(texto)
    except ValueError:
        return [t.strip("\"'") for t in texto.split()]


def _opcion(tok: str, campos: dict) -> bool:
    """Interpreta un token de serie/peso/variante/descanso. Devuelve si lo ha reconocido."""
    t = alimentos.normalizar(tok)
    if m := _SPEC.fullmatch(t):
        campos["series"], campos["reps_min"] = int(m.group(1)), int(m.group(2))
        campos["reps_max"] = int(m.group(3) or m.group(2))
        if campos["reps_max"] < campos["reps_min"]:
            raise ErrorUsuario("El rango de repeticiones va de menos a más (`4x6-8`).")
        return True
    if m := _KG.fullmatch(t):
        campos["peso_kg"] = float(m.group(1).replace(",", "."))
        return True
    if t in ("pc", "sinpeso", "sin_peso"):
        campos["peso_kg"] = None
        return True
    if t in ("a", "b", "ab"):
        campos["variante"] = None if t == "ab" else t.upper()
        return True
    if m := _DESCANSO.fullmatch(t):
        n = int(m.group(1))
        campos["descanso_s"] = n * 60 if m.group(2) in ("'", "min") else n
        return True
    return False


def rutina_texto(chat_id: int, grupo: str | None = None) -> str:
    filas = db.rutina(chat_id, _grupo_valido(grupo) if grupo else None)
    if not filas:
        if grupo and grupo in gym.GRUPOS:
            return (f"No tienes rutina propia de {grupo}: uso la de por defecto.\n\n"
                    + gym.formatear(grupo, "A") + "\n\n" + gym.formatear(grupo, "B")
                    + f"\n\nCópiala para editarla: `/rutina_importar {grupo}`.")
        return ("Aún no tienes rutina propia (uso la de por defecto).\n\n"
                "• `/rutina_importar` copia la de por defecto para que la edites\n"
                "• `/rutina_add pecho \"Press banca\" 4x6-8 80kg` añade un ejercicio\n"
                "Opciones: `A`/`B` (variante; sin nada vale para las dos), `90s` o `2'` "
                "de descanso, `pc` (peso corporal).")
    out = ["🏋️ *Tu rutina*"]
    for g in dict.fromkeys(f["grupo"] for f in filas):
        out += ["", f"*{gym.titulo(g)}* (`{g}`)"]
        for f in sorted((f for f in filas if f["grupo"] == g),
                        key=lambda f: (f["variante"] or "", f["orden"], f["id"])):
            var = f" [{f['variante']}]" if f["variante"] else ""
            out.append(f"• {_linea_ejercicio(f)}{var}")
    out += ["", "`/rutina_edit 12 85kg` · `/rutina_edit 12 4x8-10` · `/rutina_del 12` · "
                "`/series 12 80x8 80x8 80x7`"]
    return "\n".join(out)


def anadir_ejercicio(chat_id: int, texto: str) -> str:
    """`grupo "Nombre del ejercicio" 4x6-8 [80kg|pc] [A|B] [90s] [nota libre]`."""
    tok = _tokens(texto)
    if len(tok) < 3:
        raise ErrorUsuario("Usa `/rutina_add pecho \"Press banca\" 4x6-8 80kg` "
                           "(opcional: `A`/`B`, `90s` de descanso, `pc` para peso corporal).")
    grupo = _grupo_valido(tok[0])
    i = next((i for i, t in enumerate(tok[1:], 1) if _SPEC.fullmatch(alimentos.normalizar(t))),
             None)
    if i is None or i == 1:
        raise ErrorUsuario("Falta el nombre o las series: `/rutina_add pecho \"Press banca\" 4x8`.")
    nombre = " ".join(tok[1:i]).strip()
    campos = {"grupo": grupo, "ejercicio": nombre, "garmin_cat": garmin_ejercicios.categoria(nombre),
              "descanso_s": 90}
    nota = []
    for t in tok[i:]:
        if not _opcion(t, campos):
            nota.append(t)
    if nota:
        campos["nota"] = " ".join(nota)
    nuevo = db.anadir_ejercicio(chat_id, **campos)
    cat = campos["garmin_cat"] or "sin categoría (el reloj lo registrará como genérico)"
    return (f"➕ Añadido `#{nuevo}` a {gym.titulo(grupo)} · Garmin: {cat}\n\n"
            + rutina_texto(chat_id, grupo))


def _fila(chat_id: int, texto_id: str):
    try:
        rid = int(str(texto_id).lstrip("#"))
    except ValueError:
        raise ErrorUsuario("Indica el número del ejercicio (lo ves en /rutina).") from None
    f = db.ejercicio(chat_id, rid)
    if f is None:
        raise ErrorUsuario(f"No tienes ningún ejercicio #{rid}. Mira /rutina.")
    return f


def editar_ejercicio(chat_id: int, texto: str) -> str:
    """`id [4x8-10] [85kg|pc] [A|B|AB] [90s] [nombre "…"] [nota …] [grupo x] [orden n]`."""
    tok = _tokens(texto)
    if len(tok) < 2:
        raise ErrorUsuario("Usa `/rutina_edit 12 85kg` (o `4x8-10`, `A`, `90s`, "
                           "`nombre \"Press inclinado\"`, `nota …`, `grupo torso`, `orden 2`).")
    f = _fila(chat_id, tok[0])
    campos: dict = {}
    resto = tok[1:]
    while resto:
        t = resto.pop(0)
        clave = alimentos.normalizar(t)
        if clave in ("nombre", "nota"):
            valor = " ".join(resto).strip()
            resto = []
            if clave == "nombre":
                if not valor:
                    raise ErrorUsuario("Falta el nombre nuevo.")
                campos["ejercicio"] = valor
                campos["garmin_cat"] = garmin_ejercicios.categoria(valor)
            else:
                campos["nota"] = valor or None
        elif clave == "grupo" and resto:
            campos["grupo"] = _grupo_valido(resto.pop(0))
        elif clave == "orden" and resto and resto[0].isdigit():
            campos["orden"] = int(resto.pop(0))
        elif not _opcion(t, campos):
            raise ErrorUsuario(f"No entiendo «{t}».")
    if "peso_kg" in campos:
        campos["fallos"] = 0
    db.editar_ejercicio(chat_id, f["id"], **campos)
    return "✏️ Ejercicio actualizado.\n\n" + rutina_texto(chat_id, campos.get("grupo", f["grupo"]))


def borrar_ejercicio(chat_id: int, texto_id: str) -> str:
    f = _fila(chat_id, texto_id)
    db.borrar_ejercicio(chat_id, f["id"])
    return f"🗑️ Quitado: {menu._md(f['ejercicio'])}."


def borrar_rutina_grupo(chat_id: int, grupo: str) -> str:
    n = db.borrar_grupo(chat_id, _grupo_valido(grupo))
    if not n:
        raise ErrorUsuario(f"No tienes ejercicios en «{grupo}».")
    return (f"🗑️ Borrados {n} ejercicios de {grupo}."
            + (" Vuelve la rutina por defecto." if grupo in gym.GRUPOS else ""))


def parse_linea_gym(linea: str, primera: bool = False) -> dict:
    """Ejercicio de `gym.py` («Press banca — 4x6-8 (RPE 8)») como fila de rutina."""
    nombre, _, resto = linea.partition(" — ")
    m = _SPEC.search(resto) if "+" not in resto else None
    if m and not re.match(r"[\d\"s']", resto[m.end():m.end() + 1] or " "):
        series, rmin, rmax = int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))
        nota = (resto[:m.start()] + resto[m.end():]).strip(" ,") or None
        if nota and nota.startswith("(") and nota.endswith(")") and nota.count("(") == 1:
            nota = nota[1:-1]
    else:
        n = re.search(r"\d+", resto)
        series, rmin, rmax, nota = (int(n.group()) if n else 3), 0, 0, resto.strip() or None
    return {"ejercicio": nombre.strip(), "series": series, "reps_min": rmin, "reps_max": rmax,
            "peso_kg": None, "descanso_s": 150 if primera else 90, "nota": nota,
            "garmin_cat": garmin_ejercicios.categoria(nombre), "fallos": 0, "id": None}


def filas_por_defecto(grupo: str, var: str) -> list[dict]:
    b = gym.bloque(grupo, var)
    return [parse_linea_gym(e, i == 0) for i, e in enumerate(b["ejercicios"])] if b else []


def importar_rutina(chat_id: int, grupo: str | None = None) -> str:
    grupos = [_grupo_valido(grupo)] if grupo else list(gym.GRUPOS)
    hechos, saltados = [], []
    for g in grupos:
        if g not in gym.GRUPOS:
            raise ErrorUsuario(f"No hay rutina por defecto de «{g}». Grupos: {', '.join(gym.GRUPOS)}.")
        if db.rutina(chat_id, g):
            saltados.append(g)
            continue
        for var in ("A", "B"):
            for i, f in enumerate(filas_por_defecto(g, var), 1):
                campos = {k: v for k, v in f.items() if k not in ("id", "fallos")}
                db.anadir_ejercicio(chat_id, grupo=g, variante=var, orden=i, **campos)
        hechos.append(g)
    out = []
    if hechos:
        out.append(f"📥 Copiada la rutina de: {', '.join(hechos)}. Pon tus pesos con "
                   "`/rutina_edit <nº> 80kg` o registra una sesión con /series y los fijo yo.")
    if saltados:
        out.append(f"Ya tenías rutina de {', '.join(saltados)}: no la toco "
                   "(`/rutina_borrar grupo` para empezar de cero).")
    return "\n".join(out) + "\n\n" + rutina_texto(chat_id, grupo)


# ---------- series hechas y progresión ----------

def _aplicar_progresion(chat_id: int, fila, series: list[dict], aplicar: bool = True,
                        manual: bool = False) -> str:
    """Progresión del ejercicio. Las series apuntadas a mano siempre la aplican; las del reloj,
    solo con GARMIN_AJUSTES."""
    cambios_fila, msg = entrenador.progresion(dict(fila), series)
    if aplicar and (manual or GARMIN_AJUSTES) and cambios_fila:
        db.editar_ejercicio(chat_id, fila["id"], **cambios_fila)
    elif aplicar and cambios_fila and "peso_kg" in cambios_fila:
        msg += " (ajustes automáticos desactivados: cámbialo tú con `/rutina_edit`)"
    return msg


def registrar_series(chat_id: int, texto: str) -> str:
    """`id 80x8 80x8 77.5x7` o `id 8 8 7` (con el peso objetivo)."""
    tok = _tokens(texto)
    if len(tok) < 2:
        raise ErrorUsuario("Usa `/series 12 80x8 80x8 80x7` (peso x reps) o `/series 12 8 8 7` "
                           "si usaste el peso objetivo.")
    f = _fila(chat_id, tok[0])
    series = []
    for i, t in enumerate(tok[1:], 1):
        t = t.lower().replace(",", ".").replace("×", "x")
        if m := re.fullmatch(r"(\d+(?:\.\d+)?)(?:kg)?x(\d+)", t):
            peso, reps = float(m.group(1)), int(m.group(2))
        elif t.isdigit():
            peso, reps = f["peso_kg"], int(t)
        else:
            raise ErrorUsuario(f"No entiendo la serie «{t}»: usa `80x8` o `8`.")
        series.append({"rutina_id": f["id"], "ejercicio": f["ejercicio"], "serie": i,
                       "reps": reps, "peso_kg": peso})
    db.alta_usuario(chat_id, "atleta")
    db.guardar_series(chat_id, hoy(), series, "manual")
    msg = _aplicar_progresion(chat_id, f, series, manual=True)
    return (f"📝 {menu._md(f['ejercicio'])}: {entrenador.texto_series(series)}"
            + (f"\n{msg}" if msg else ""))


# ---------- Garmin ----------

def garmin_activo(chat_id: int) -> bool:
    return garmin.configurado() and chat_id == GARMIN_CHAT_ID


def _exigir_garmin(chat_id: int):
    if not garmin.configurado():
        raise ErrorUsuario("⌚ Garmin no está configurado. Pon `GARMIN_CHAT_ID` en `.env` y "
                           "ejecuta una vez `.venv/bin/python garmin_login.py` (ver README).")
    if chat_id != GARMIN_CHAT_ID:
        raise ErrorUsuario("⌚ La cuenta de Garmin está vinculada a otro chat.")


async def _g(fn, *args):
    try:
        r = await asyncio.to_thread(fn, *args)
        garmin.ultimo_error = ""
        return r
    except Exception as e:
        garmin.ultimo_error = f"{type(e).__name__}: {e}"
        if "auth" in type(e).__name__.lower() or "401" in str(e):
            garmin.reiniciar()
        raise


def _filas_gym(est: plan.Estructura, entreno: dict) -> list[dict]:
    propia = est.rutinas.get(entreno["grupo"], {}).get(entreno["variante"])
    return propia["filas"] if propia else filas_por_defecto(entreno["grupo"], entreno["variante"])


async def _procesar_fuerza(chat_id: int, d: date, a: dict, s: dict, est: plan.Estructura,
                           aplicar: bool) -> list[str]:
    filas = [f for e in s["entrenos"] if e["tipo"] == "gym" for f in _filas_gym(est, e)
             if f.get("id")]
    series = a.get("series") or []
    if not series:
        return ["El reloj no ha registrado series. Anótalas con `/series <nº> 80x8 80x8…`."]
    if not filas:
        por_cat: dict[str, list] = {}
        for x in series:
            por_cat.setdefault(x.get("categoria") or "?", []).append(x)
        return ([f"• {c.lower().replace('_', ' ')}: {entrenador.texto_series(v)}"
                 for c, v in por_cat.items()]
                + ["", "Sin rutina propia no puedo seguir tus pesos: `/rutina_importar`."])
    asignadas, sueltas = entrenador.emparejar(series, filas)
    out = []
    for f in filas:
        ss = asignadas.get(f["id"])
        if not ss:
            out.append(f"• {menu._md(f['ejercicio'])}: sin registrar "
                       f"(`/series {f['id']} 80x8 …` si lo hiciste)")
            continue
        db.guardar_series(chat_id, d, [{"rutina_id": f["id"], "ejercicio": f["ejercicio"],
                                        "serie": i, "reps": x["reps"], "peso_kg": x["peso_kg"]}
                                       for i, x in enumerate(ss, 1)], "garmin")
        msg = _aplicar_progresion(chat_id, db.ejercicio(chat_id, f["id"]), ss, aplicar)
        out.append(f"• {menu._md(f['ejercicio'])}: {entrenador.texto_series(ss)}"
                   + (f"\n  {msg}" if msg else ""))
    if sueltas:
        out.append(f"• Otras series: {entrenador.texto_series(sueltas)}")
    return out


async def procesar_actividad(chat_id: int, a: dict, aplicar: bool = True) -> str:
    """Compara una actividad del reloj con el plan, ajusta (tests, pesos) y la marca como hecha.
    Devuelve el análisis en Markdown."""
    d = date.fromisoformat(a["fecha"])
    est = estructura(chat_id)
    ref, _ = ritmo(chat_id)
    s = plan.sesiones_dia(d, cambio(chat_id, d), ref, est)
    previstos = {e["tipo"] for e in s.get("entrenos", [])}
    out = [entrenador.resumen_actividad(a)]
    if a["tipo"] == "carrera":
        sesion_c = next((e for e in s.get("entrenos", []) if e["tipo"] == "carrera"), None)
        out += entrenador.analizar_carrera(a, sesion_c, ref)
        if aplicar and sesion_c and sesion_c["nombre"].startswith("Test"):
            km = int(re.search(r"\d+", sesion_c["nombre"]).group())
            seg = entrenador.detectar_test(a, km)
            if seg and 150 <= seg / km <= 600:
                db.guardar_test(chat_id, d, km, seg)
                nuevo, _ = ritmo(chat_id)
                out.append(f"⏱️ *Test detectado*: {km} km en {plan.tiempo_txt(seg)}. "
                           f"Ritmo de media nuevo: {plan.pace(nuevo)} /km (antes {plan.pace(ref)}). "
                           "Mira /ritmos.")
            else:
                out.append(f"⏱️ Hoy era el test de {km} km pero no encuentro el tramo: "
                           f"regístralo con `/test {km}k mm:ss`.")
    elif a["tipo"] == "gym":
        out += await _procesar_fuerza(chat_id, d, a, s, est, aplicar)
    if a["tipo"] not in previstos:
        out.append("➕ No estaba en el plan de ese día: cuenta como extra.")
    elif aplicar and not s["fuera_de_plan"] and not db.entreno(chat_id, d):
        db.alta_usuario(chat_id, "atleta")
        await guardar_entreno(chat_id, d, s["titulo"],
                              f"Garmin: {menu._md(a.get('nombre') or a['tipo'])}")
        out.append(f"✅ Marcado como hecho: {s['titulo']}")
    texto = "\n".join(out)
    db.guardar_actividad(chat_id, a, texto)
    return texto


async def sync_garmin(chat_id: int) -> list[str]:
    """Baja las actividades nuevas del reloj. La primera vez importa 14 días sin avisar
    (solo analiza las de hoy). Devuelve un análisis por actividad nueva."""
    _exigir_garmin(chat_id)
    h = hoy()
    primera = not db.hay_actividades(chat_id)
    acts = await _g(garmin.actividades, h - timedelta(days=14 if primera else 3), h)
    msgs = []
    for a in sorted(acts, key=lambda x: (x["fecha"], x["hora"])):
        if db.actividad_existe(a["id"]):
            continue
        try:
            if a["tipo"] in ("carrera", "natacion", "bici"):
                a["vueltas"] = await _g(garmin.vueltas, a["id"])
            elif a["tipo"] == "gym":
                a["series"] = await _g(garmin.series_fuerza, a["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("Garmin: sin detalle de la actividad %s: %s", a["id"], e)
        silenciosa = primera and a["fecha"] < h.isoformat()
        texto = await procesar_actividad(chat_id, a, aplicar=not silenciosa)
        if not silenciosa:
            msgs.append(texto)
    return msgs


async def revisar_bienestar(chat_id: int, forzar: bool = False) -> str:
    """Lee el estado de forma de hoy y, una vez al día, adapta la sesión si hace falta.
    Devuelve texto solo si hay algo que contar (o si `forzar`)."""
    _exigir_garmin(chat_id)
    h = hoy()
    db.alta_usuario(chat_id, "atleta")
    db.guardar_bienestar(chat_id, h, await _g(garmin.bienestar, h))
    b = dict(db.bienestar(chat_id, h))
    hay_datos = b["readiness"] is not None or b["hrv_estado"] is not None
    if b["aplicado"] or not hay_datos:
        return entrenador.texto_bienestar(b) if forzar else ""
    db.marcar_bienestar_aplicado(chat_id, h)
    out = [entrenador.texto_bienestar(b)]
    if plan.datos_semana(plan.semana_indice(h)) is None:
        return "\n".join(out)
    cs = cambios_semana(chat_id, h)
    est = estructura(chat_id)
    modo, motivo = entrenador.regla_bienestar(b, plan.carga_dia(h, cs, est))
    if modo and GARMIN_AJUSTES and ahora().hour < 13 and h not in cs and not db.entreno(chat_id, h):
        guardar(chat_id, plan.cambiar(cs, h, modo))
        out += ["", f"🔁 {motivo}", "", formatear_dia(chat_id, h), "",
                "_Si te ves bien, `/deshacer` vuelve a la sesión original._"]
    elif motivo:
        out += ["", f"💡 {motivo}"]
    return "\n".join(out)


async def enviar_reloj(chat_id: int, dias: int = GARMIN_DIAS_ENVIO) -> list[str]:
    """Deja en el calendario de Garmin las sesiones de los próximos `dias` días (con los
    cambios). Solo sube lo que ha cambiado; borra lo que ya no toca."""
    _exigir_garmin(chat_id)
    h = hoy()
    est = estructura(chat_id)
    ref, _ = ritmo(chat_id)
    deseados: dict[tuple[str, str], dict] = {}
    for i in range(dias):
        d = h + timedelta(days=i)
        if d == h and db.entreno(chat_id, d):
            continue
        s = plan.sesiones_dia(d, cambio(chat_id, d), ref, est)
        for e in s.get("entrenos", []):
            clave = e["tipo"] + (f":{e['grupo']}" if e["tipo"] == "gym" else "")
            if e["tipo"] == "carrera":
                n = f"{garmin.PREFIJO} {d:%d/%m} {e['nombre']}"
                j = garmin.json_carrera(n, e, ref)
            elif e["tipo"] == "gym":
                n = f"{garmin.PREFIJO} {d:%d/%m} {gym.titulo(e['grupo'])} {e['variante']}"
                j = garmin.json_fuerza(n, _filas_gym(est, e), e)
            else:
                n = f"{garmin.PREFIJO} {d:%d/%m} {plan.TIPOS[e['tipo']]} {e['minutos']}'"
                j = garmin.json_cardio(n, e)
            deseados[(d.isoformat(), clave)] = j
    hecho_hoy = db.entreno(chat_id, h) is not None
    existentes = {(r["fecha"], r["clave"]): r
                  for r in db.programados(chat_id, h, h + timedelta(days=dias - 1))}
    out = []
    for k, r in existentes.items():
        if k[0] == h.isoformat() and hecho_hoy:
            continue
        if k not in deseados or r["hash"] != garmin.huella(deseados[k]):
            try:
                await _g(garmin.borrar, r["workout_id"])
            except Exception as e:  # noqa: BLE001
                log.info("Garmin: no se pudo borrar %s: %s", r["workout_id"], e)
            db.borrar_programado(chat_id, *k)
            if k not in deseados:
                out.append(f"🗑️ {date.fromisoformat(k[0]):%d/%m} {k[1]}: quitado")
    for k, j in deseados.items():
        r = existentes.get(k)
        if r and r["hash"] == garmin.huella(j):
            continue
        wid = await _g(garmin.subir, j)
        await _g(garmin.programar, wid, date.fromisoformat(k[0]))
        db.guardar_programado(chat_id, date.fromisoformat(k[0]), k[1], wid, garmin.huella(j))
        out.append(f"⌚ {j['workoutName'].removeprefix(garmin.PREFIJO).strip()}")
    for r in db.programados(chat_id, hasta=h - timedelta(days=15)):
        try:
            await _g(garmin.borrar, r["workout_id"])
        except Exception:  # noqa: BLE001
            pass
        db.borrar_programado(chat_id, r["fecha"], r["clave"])
    return out


def actividades_texto(chat_id: int, n: int = 5) -> str:
    acts = db.actividades(chat_id, limite=n)
    if not acts:
        return "⌚ Aún no tengo actividades de tu Garmin. Prueba /garmin para sincronizar."
    return "\n\n".join(a.get("analisis") or entrenador.resumen_actividad(a) for a in acts)


def bienestar_texto(chat_id: int, dias: int = 7) -> str:
    filas = db.bienestar_desde(chat_id, hoy() - timedelta(days=dias - 1))
    if not filas:
        return "⌚ Sin datos de estado de forma todavía. Prueba /garmin."
    out = ["⌚ *Estado de forma*", ""]
    for f in reversed(filas):
        out.append(f"*{nombre(date.fromisoformat(f['fecha']))[:3]} "
                   f"{date.fromisoformat(f['fecha']):%d/%m}* "
                   + entrenador.texto_bienestar(dict(f)).removeprefix("⌚ "))
    pred = db.ultima_prediccion(chat_id)
    if pred:
        out += ["", f"🏁 Predicción de media: {plan.tiempo_txt(pred[1])} "
                    f"({plan.pace(round(pred[1] / plan.MEDIA_KM))} /km)"]
    return "\n".join(out)
