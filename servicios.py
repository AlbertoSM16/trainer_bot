"""Acciones del usuario, compartidas por los comandos de Telegram (bot.py) y el coach (coach.py).

Escriben primero en SQLite, luego sincronizan con Notion (best-effort) y devuelven el texto de
respuesta en Markdown legacy de Telegram. Los errores de uso se lanzan como `ErrorUsuario`.
"""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import alimentos
import compra
import db
import menu
import mercadona
import notion_sync
import nutricion
import plan
from config import ATHLETE, PESO_OBJETIVO, TIMEZONE

TZ = ZoneInfo(TIMEZONE)


class ErrorUsuario(Exception):
    """Petición que no se puede aplicar; el mensaje se muestra tal cual al usuario."""


def hoy() -> date:
    return datetime.now(TZ).date()


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
    """Ritmo de referencia (media, s/km) del usuario y de dónde sale."""
    t = db.ultimo_test(chat_id)
    if t:
        f, km, seg = t
        return plan.ritmo_ref((km, seg)), (f"Según tu test de {km:g} km del {f.strftime('%d/%m')} "
                                           f"({plan.tiempo_txt(seg)})")
    return plan.ritmo_ref(), (f"Estimados de tu marca en media ({ATHLETE['marca_media']}) "
                              "con margen por el parón. Se ajustan con tu primer test.")


def formatear_dia(chat_id: int, d: date) -> str:
    return plan.formatear_dia(d, cambio(chat_id, d), ritmo(chat_id)[0])


def resumen_semana(chat_id: int, week_num: int) -> str:
    l = plan.fecha_lunes(week_num)
    return plan.resumen_semana(week_num, cambios(chat_id, l, l + timedelta(days=6)))


def resultado(chat_id: int, dias: list[date]) -> str:
    cs = cambios_semana(chat_id, dias[0])
    out = [f"• *{nombre(d)}*: {plan.sesiones_dia(d, cs.get(d))['titulo']}"
           for d in sorted(set(dias))]
    avisos = plan.avisos_semana(lunes(dias[0]), cs)
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
    clave = CLAVE.get(plan.carga_dia(d, cs))
    guardar(chat_id, plan.cambiar(cs, d, "descanso"))
    out = [f"⏭️ *{nombre(d)}*: descanso."]
    if clave:
        hueco = plan.sugerir_recuperacion(cambios_semana(chat_id, d), d, hoy())
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
    modo, motivo = plan.recomendar(d, cs, energia, zona)
    out = [f"📝 Energía {energia}/5 · {plan.ZONAS_MOLESTIA.get(zona, zona)}", "", f"💡 {motivo}"]
    if modo:
        guardar(chat_id, plan.cambiar(cs, d, modo))
        out += ["", formatear_dia(chat_id, d)]
        if modo == "descanso":
            clave = CLAVE.get(plan.carga_dia(d, cs))
            hueco = clave and plan.sugerir_recuperacion(cambios_semana(chat_id, d), d, d)
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


async def marcar_hecho(chat_id: int, nota: str = "") -> str:
    d = hoy()
    c = cambio(chat_id, d)
    s = plan.sesiones_dia(d, c)
    if s["fuera_de_plan"]:
        raise ErrorUsuario("Hoy no hay sesión planificada todavía. El plan arranca el "
                           f"{plan.PLAN_START.strftime('%d/%m/%Y')}.")
    if (c and c.modo == "descanso") or (not c and d.weekday() == 4):
        raise ErrorUsuario("Hoy tienes descanso 😴. Si al final entrenas, cambia el día "
                           "(`/mover` o `/deshacer`) y luego marca /hecho.")
    db.marcar_hecho(chat_id, d, s["titulo"], nota)
    ok = await notion_sync.sync.guardar_entreno(
        fecha=d, titulo=s["titulo"], nota=nota, semana=s["semana"],
        fase=plan.fase_nombre_corto(s["fase"]), dia=nombre(d),
        tipos=plan.tipos_dia(d, c), descarga=bool(s.get("descarga")))
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
    return nutricion.objetivo_dia(d, peso_actual(chat_id), ajuste, cambio(chat_id, d))


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
    objetivos = nutricion.objetivos_semana(sabado, peso_actual(chat_id), ajuste, cs)
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
    objetivos = nutricion.objetivos_semana(h, peso_actual(chat_id), ajuste, cs)
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
    test, km = plan.proximo_test(d)
    out = []
    if wn >= 1:
        out.append(f"Vas por la *semana {plan.semana_en_bloque(wn)}/{plan.SEMANAS_BLOQUE}* "
                   f"del bloque {plan.bloque_de(wn)} (semana {wn} del plan).")
    dias = (test - d).days
    out.append(f"⏱️ Próximo test: *{km} km* el sábado {test.strftime('%d/%m')} "
               + ("(¡hoy!)" if dias == 0 else f"(dentro de {dias} días)") + ".")
    tests = db.tests(chat_id, 3)
    if tests:
        out.append("Últimos tests: " + " · ".join(
            f"{t['km']:g} km en {plan.tiempo_txt(t['segundos'])} "
            f"({date.fromisoformat(t['fecha']).strftime('%d/%m')})"
            for t in tests))
    return "\n".join(out) + "\n\n" + plan.resumen_bloque(wn, ritmo(chat_id)[0])
