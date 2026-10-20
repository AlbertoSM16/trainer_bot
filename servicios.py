"""Acciones del usuario, compartidas por los comandos de Telegram (bot.py) y el coach (coach.py).

Escriben primero en SQLite, luego sincronizan con Notion (best-effort) y devuelven el texto de
respuesta en Markdown legacy de Telegram. Los errores de uso se lanzan como `ErrorUsuario`.

No hay plan semanal fijo: lo entrenado sale del reloj (Garmin) y cada día `consejo.py` propone
qué hacer en carrera y en bici.
"""
import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import alimentos
import compra
import consejo
import db
import entrenador
import garmin
import menu
import mercadona
import notion_sync
import nutricion
import plan
from config import (ATHLETE, FC_UMBRAL, GARMIN_CHAT_ID, GARMIN_USAR_PREDICCION, PESO_OBJETIVO,
                    TEST_VIGENCIA_DIAS, TIMEZONE)

TZ = ZoneInfo(TIMEZONE)
log = logging.getLogger(__name__)
DEPORTES = ("carrera", "bici")


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


# ---------- ritmos y pulso ----------

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


def umbral(chat_id: int) -> tuple[int | None, str]:
    """FC de umbral de lactato corriendo y de dónde sale (`.env`, reloj o estimada)."""
    if FC_UMBRAL:
        return FC_UMBRAL, "puesta en `.env`"
    u = db.ultimo_umbral(chat_id)
    if u:
        return u, "medida por tu reloj"
    maximos = [a["fc_max"] for a in db.actividades(chat_id, hoy() - timedelta(days=60), 300)
               if a.get("tipo") in ("carrera", "bici") and a.get("fc_max")]
    if maximos:
        return round(0.9 * max(maximos)), "estimada (90 % de tu FC máxima de los últimos 2 meses)"
    return None, ""


def umbral_fiable(chat_id: int) -> int | None:
    """Umbral para clasificar sesiones duras por pulso: solo el medido o puesto a mano (el
    estimado puede quedarse corto si solo hay rodajes y lo marcaría todo como duro)."""
    u, fuente = umbral(chat_id)
    return None if fuente.startswith("estimada") else u


# ---------- consejo de carrera y bici ----------

def estado(chat_id: int) -> consejo.Estado:
    h = hoy()
    b = db.bienestar(chat_id, h)
    sens = db.sensaciones_desde(chat_id, h - timedelta(days=1))
    de_hoy = [s for s in sens if s["fecha"] == h.isoformat()]
    energia = next((s["energia"] for s in reversed(de_hoy) if s["energia"] is not None), None)
    # La última molestia anotada hoy manda; si hoy no hay nada, vale la de ayer.
    ultima = de_hoy[-1] if de_hoy else (sens[-1] if sens else None)
    molestia = consejo.zona_molestia(ultima["molestias"]) if ultima else "ninguna"
    return consejo.Estado(
        hoy=h, actividades=db.actividades(chat_id, h - timedelta(days=60), 300),
        bienestar=dict(b) if b else {}, energia=energia, molestia=molestia,
        umbral=umbral_fiable(chat_id), umbral_zonas=umbral(chat_id)[0], consejos=db.consejos_desde(chat_id, h - timedelta(days=60)))


def calcular_consejo(chat_id: int, deporte: str) -> consejo.Consejo:
    """Consejo de hoy para `deporte`; lo guarda (salvo «ya hecho») para el reloj y el análisis."""
    if deporte not in DEPORTES:
        raise ErrorUsuario("El deporte tiene que ser `carrera` o `bici`.")
    e = estado(chat_id)
    c = consejo.carrera(e, ritmo(chat_id)[0]) if deporte == "carrera" else consejo.bici(e)
    if c.clave != "hecho":
        db.alta_usuario(chat_id, "atleta")
        db.guardar_consejo(chat_id, e.hoy, c.datos())
    return c


def consejo_texto(chat_id: int, deporte: str) -> tuple[str, bool]:
    """Texto del consejo de hoy y si se puede mandar al reloj."""
    c = calcular_consejo(chat_id, deporte)
    texto = consejo.formatear(c)
    if not garmin_activo(chat_id):
        texto += "\n\n_Sin Garmin solo tengo en cuenta tus /sensaciones._"
    elif deporte == "bici" and c.pasos and not umbral(chat_id)[0]:
        texto += ("\n\n_Aún no conozco tu FC de umbral: te marco las zonas por sensaciones. "
                  "Ponla con `FC_UMBRAL` en `.env` o deja que el reloj la mida._")
    return texto, c.para_reloj and garmin_activo(chat_id)


async def mandar_reloj(chat_id: int, deporte: str) -> str:
    """Sube el consejo de hoy al calendario de Garmin (sustituye al anterior de ese deporte)."""
    _exigir_garmin(chat_id)
    h = hoy()
    datos = db.consejo(chat_id, h, deporte)
    if not datos or not datos.get("pasos"):
        raise ErrorUsuario(f"Hoy no tengo un entreno de {deporte} para el reloj. Pide antes "
                           f"/{deporte}.")
    n = f"{garmin.PREFIJO} {h:%d/%m} {datos['titulo']}"
    if deporte == "carrera":
        j = garmin.json_carrera(n, datos, ritmo(chat_id)[0])
    else:
        u = umbral(chat_id)[0]
        j = garmin.json_bici(n, datos, consejo.zonas_bici(u) if u else None)
    for r in db.programados(chat_id, h, h):
        if r["clave"] == deporte:
            try:
                await _g(garmin.borrar, r["workout_id"])
            except Exception as e:  # noqa: BLE001
                log.info("Garmin: no se pudo borrar %s: %s", r["workout_id"], e)
            db.borrar_programado(chat_id, r["fecha"], r["clave"])
    wid = await _g(garmin.subir, j)
    await _g(garmin.programar, wid, h)
    db.guardar_programado(chat_id, h, deporte, wid, garmin.huella(j))
    return (f"⌚ *{menu._md(datos['titulo'])}* está en el calendario de hoy.\n"
            "_Sincroniza el reloj con Garmin Connect y búscalo en Entrenamiento > Calendario._")


async def limpiar_reloj(chat_id: int) -> int:
    """Quita del calendario los entrenos que puso el bot otros días (incluidos los del plan
    semanal antiguo). Devuelve cuántos quita."""
    h = hoy()
    n = 0
    for r in db.programados(chat_id):
        if r["fecha"] == h.isoformat() and r["clave"] in DEPORTES:
            continue
        try:
            await _g(garmin.borrar, r["workout_id"])
        except Exception as e:  # noqa: BLE001
            log.info("Garmin: no se pudo borrar %s: %s", r["workout_id"], e)
        db.borrar_programado(chat_id, r["fecha"], r["clave"])
        n += 1
    return n


# ---------- historial ----------

ICONO = {"carrera": "🏃", "gym": "🏋️", "natacion": "🏊", "bici": "🚴"}


def _duracion(minutos: float) -> str:
    h, m = divmod(round(minutos), 60)
    return f"{h}h{m:02d}'" if h else f"{m}'"


def _linea(a: dict, u: int | None) -> str:
    partes = []
    if a.get("distancia_m") and a["tipo"] != "gym":
        partes.append(f"{a['distancia_m'] / 1000:.1f} km")
    if a.get("duracion_s"):
        partes.append(_duracion(a["duracion_s"] / 60))
    if a["tipo"] == "carrera" and a.get("distancia_m") and a.get("duracion_s"):
        partes.append(f"{plan.pace(round(a['duracion_s'] / (a['distancia_m'] / 1000)))} /km")
    if a.get("fc_media"):
        partes.append(f"FC {round(a['fc_media'])}")
    if a.get("te_aerobico"):
        partes.append(f"TE {a['te_aerobico']:.1f}/{a.get('te_anaerobico') or 0:.1f}")
    etiqueta = (" 🛣️ larga" if consejo.larga(a) else " 🔥 dura" if consejo.dura(a, u) else "")
    return (f"{ICONO.get(a['tipo'], '⌚')} {menu._md(a.get('nombre') or a['tipo'])}{etiqueta}: "
            + " · ".join(partes))


def _previas(gyms: list[dict]) -> dict[int, dict[str, list[dict]]]:
    """Para cada sesión de fuerza, las series de la última vez que se hizo cada ejercicio."""
    out, visto = {}, {}
    for a in sorted(gyms, key=lambda x: (x["fecha"], x.get("hora") or "")):
        out[a["id"]] = dict(visto)
        visto.update(entrenador.por_ejercicio(a.get("series") or []))
    return out


def historial_texto(chat_id: int, dias: int = 14) -> str:
    h = hoy()
    desde = h - timedelta(days=dias - 1)
    todas = db.actividades(chat_id, h - timedelta(days=120), 500)
    acts = sorted((a for a in todas if a["fecha"] >= desde.isoformat()),
                  key=lambda a: (a["fecha"], a.get("hora") or ""))
    if not acts:
        return ("📅 No tengo actividades de los últimos 14 días. "
                + ("Prueba /garmin para sincronizar." if garmin_activo(chat_id) else
                   "Conecta tu Garmin (ver README)."))
    u = umbral_fiable(chat_id)
    previas = _previas([a for a in todas if a["tipo"] == "gym"])
    out = [f"📅 *Historial · últimos {dias} días*"]
    inicio = lunes(desde)
    while inicio <= h:
        fin = inicio + timedelta(days=6)
        semana = [a for a in acts if inicio.isoformat() <= a["fecha"] <= fin.isoformat()]
        out += ["", _totales(inicio, semana, u) if semana else
                f"*Semana del {inicio:%d/%m}* — sin actividades"]
        for i in range(7):
            d = inicio + timedelta(days=i)
            if d < desde or d > h:
                continue
            del_dia = [a for a in semana if a["fecha"] == d.isoformat()]
            cab = f"*{nombre(d)[:3]} {d:%d/%m}*"
            if not del_dia:
                out.append(f"{cab} · descanso")
                continue
            out.append(cab)
            for a in del_dia:
                out.append(_linea(a, u))
                if a["tipo"] == "gym":
                    if a.get("series"):
                        out += ["  " + x for x in
                                entrenador.resumen_fuerza(a["series"], previas.get(a["id"], {}))]
                    else:
                        out.append("  _(sin series registradas)_")
        inicio += timedelta(days=7)
    return "\n".join(out)


def _totales(lun: date, acts: list[dict], u: int | None) -> str:
    def suma(tipo, campo):
        return sum((a.get(campo) or 0) for a in acts if a["tipo"] == tipo)
    partes = []
    carreras = [a for a in acts if a["tipo"] == "carrera"]
    if carreras:
        partes.append(f"🏃 {len(carreras)} · {suma('carrera', 'distancia_m') / 1000:.1f} km")
    bicis = [a for a in acts if a["tipo"] == "bici"]
    if bicis:
        partes.append(f"🚴 {len(bicis)} · {_duracion(suma('bici', 'duracion_s') / 60)}")
    gyms = [a for a in acts if a["tipo"] == "gym"]
    if gyms:
        partes.append(f"🏋️ {len(gyms)}")
    otras = len(acts) - len(carreras) - len(bicis) - len(gyms)
    if otras:
        partes.append(f"⌚ {otras} más")
    duras = sum(1 for a in acts if consejo.dura(a, u))
    total = sum((a.get("duracion_s") or 0) for a in acts) / 60
    return (f"*Semana del {lun:%d/%m}* — " + " · ".join(partes)
            + f" · {_duracion(total)} en total · {duras} dura{'s' if duras != 1 else ''}")


def stats_texto(chat_id: int) -> str:
    hist = db.historico_metricas(chat_id, 8)
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
    h = hoy()
    acts = db.actividades(chat_id, h - timedelta(days=27), 300)
    if acts:
        u = umbral_fiable(chat_id)
        out += ["", "*Últimas 4 semanas*"]
        for i in range(3, -1, -1):
            lun = lunes(h) - timedelta(days=7 * i)
            semana = [a for a in acts if lun.isoformat() <= a["fecha"]
                      <= (lun + timedelta(days=6)).isoformat()]
            if semana:
                out.append("• " + _totales(lun, semana, u).replace("*", ""))
        out.append("\nDetalle día a día: /historial")
    return "\n".join(out)


# ---------- sensaciones ----------

def registrar_sensaciones(chat_id: int, energia: int | None, molestias: str = "",
                          nota: str = "") -> str:
    if energia is not None and not 1 <= energia <= 5:
        raise ErrorUsuario("La energía va de 1 (fundido) a 5 (a tope).")
    db.alta_usuario(chat_id, "atleta")
    db.guardar_sensaciones(chat_id, hoy(), energia, molestias, nota)
    return "📝 Sensaciones anotadas."


def aplicar_sensaciones(chat_id: int, energia: int, zona: str) -> tuple[str, list[str]]:
    """Anota las sensaciones y devuelve el consejo de hoy de carrera y bici ya con ellas
    (y los deportes que se pueden mandar al reloj)."""
    registrar_sensaciones(chat_id, energia, plan.ZONAS_MOLESTIA.get(zona, zona))
    out = [f"📝 Energía {energia}/5 · {plan.ZONAS_MOLESTIA.get(zona, zona)}"]
    reloj = []
    for deporte in DEPORTES:
        texto, para_reloj = consejo_texto(chat_id, deporte)
        out += ["", texto]
        if para_reloj:
            reloj.append(deporte)
    if zona != "ninguna":
        out += ["", "_Si es un dolor agudo, punzante o dura más de una semana, consulta a "
                    "un fisio o médico._"]
    return "\n".join(out), reloj


# ---------- métricas y tests ----------

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
    db.alta_usuario(chat_id, "atleta")
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


def ritmos_texto(chat_id: int) -> str:
    out = plan.tabla_ritmos(*ritmo(chat_id))
    u, fuente = umbral(chat_id)
    if u:
        z = consejo.zonas_bici(u)
        out += ("\n\n🚴 *Zonas de bici* (FC de umbral corriendo " f"{u} ppm, {fuente})\n"
                + "\n".join(f"• {consejo.ZONAS_BICI[k][2]}: {a}-{b} ppm" for k, (a, b) in z.items()))
    return out


# ---------- dieta ----------

def peso_actual(chat_id: int) -> float:
    return db.ultimo_peso(chat_id) or ATHLETE["peso_inicial_kg"]


def kcal_ejercicio(chat_id: int) -> int | None:
    """Media diaria de kcal de ejercicio del reloj (14 días previos a hoy). None sin Garmin o
    sin actividades: la dieta usa entonces la media estimada de una semana tipo."""
    if not garmin_activo(chat_id) or not db.hay_actividades(chat_id):
        return None
    h = hoy()
    acts = [a for a in db.actividades(chat_id, h - timedelta(days=14), 300)
            if a["fecha"] < h.isoformat()]
    return nutricion.kcal_ejercicio_reloj(acts, peso_actual(chat_id))


def entreno_de_hoy(chat_id: int) -> str | None:
    """`deporte:clave` del entreno aconsejado hoy (para el consejo de comida)."""
    h = hoy()
    for deporte in DEPORTES:
        c = db.consejo(chat_id, h, deporte)
        if c and c["clave"] not in ("descanso", "hecho"):
            return f"{deporte}:{c['clave']}"
    return None


def _objetivos(chat_id: int, desde: date) -> list[nutricion.Objetivo]:
    ajuste, _ = db.ajuste_kcal(chat_id)
    h, peso = hoy(), peso_actual(chat_id)
    kcal = kcal_ejercicio(chat_id)
    return nutricion.objetivos_semana(
        desde, peso, ajuste,
        ejercicio_kcal=kcal if kcal is not None else nutricion.kcal_ejercicio_estimada(peso, h),
        entrenos={h: entreno_de_hoy(chat_id)}, reloj=kcal is not None)


def objetivo(chat_id: int, d: date) -> nutricion.Objetivo:
    return _objetivos(chat_id, d)[0]


def dieta_texto(chat_id: int, d: date) -> str:
    return nutricion.formatear_objetivo(objetivo(chat_id, d)) + "\n\n🍽️ Qué comer: /menu"


def _menus_semana(chat_id: int, d: date) -> tuple[date, list[nutricion.Objetivo], str,
                                                   dict[date, dict[str, str]]]:
    """Objetivos y variante de proteína de la semana de compra (sábado a viernes) de `d`,
    con la misma variante que elegiría la lista de la compra (precios en caché)."""
    sabado = d - timedelta(days=(d.weekday() - 5) % 7)
    platos = db.platos_entre(chat_id, sabado, sabado + timedelta(days=6))
    opciones = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
    objetivos = _objetivos(chat_id, sabado)
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
    objetivos = _objetivos(chat_id, h)
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
    db.alta_usuario(chat_id, "atleta")
    if clave == "normal":
        db.guardar_plato(chat_id, d, c, None)
        return f"↩️ {nombre(d)}: {c} vuelve al plato del plan.\n\n" + menu_texto(chat_id, d)
    if clave not in menu.platos_validos(c):
        raise ErrorUsuario(f"`{clave}` no vale para {c}. Opciones:\n\n" + platos_disponibles(c))
    db.guardar_plato(chat_id, d, c, clave)
    return f"🔁 {nombre(d)}: plato de {c} cambiado.\n\n" + menu_texto(chat_id, d)


# ---------- perfil ----------

def perfil_texto(chat_id: int) -> str:
    p = peso_actual(chat_id)
    grasa = next((r["grasa"] for r in db.historico_metricas(chat_id, 60)
                  if r["grasa"] is not None), None)
    imc = p / (ATHLETE["altura_cm"] / 100) ** 2
    ref, fuente = ritmo(chat_id)
    u, fuente_u = umbral(chat_id)
    estado_dieta = ("mantenimiento" if p >= PESO_OBJETIVO else
                    f"volumen limpio (+{nutricion.superavit(p)} kcal/día)")
    kcal = kcal_ejercicio(chat_id)
    return "\n".join([
        "👤 *Tu perfil*", "",
        f"• Edad: {ATHLETE['edad']} años · Altura: {ATHLETE['altura_cm']} cm",
        f"• Peso: {p:g} kg (IMC {imc:.1f}) → objetivo {PESO_OBJETIVO:g} kg",
        f"• Grasa corporal: {grasa if grasa is not None else '—'}%",
        f"• Dieta: {estado_dieta}"
        + (f" · ejercicio {kcal} kcal/día (media del reloj)" if kcal is not None else ""),
        f"• Mejor marca en media: {ATHLETE['marca_media']}",
        f"• Ritmo de media actual: {plan.pace(ref)} /km",
        f"  _{fuente}_",
        f"• FC de umbral: {f'{u} ppm ({fuente_u})' if u else '—'}",
        "", f"🎯 _{ATHLETE['objetivo']}_",
    ])


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


def _analizar_bici(a: dict, c: dict | None, u: int | None) -> list[str]:
    if not c or not c.get("pasos") or not u or not a.get("fc_media"):
        return []
    zonas = consejo.zonas_bici(u)
    if c["clave"] in ("recuperacion", "fondo"):
        zona = "b1" if c["clave"] == "recuperacion" else "b2"
        alto = zonas[zona][1]
        if a["fc_media"] > alto + 5:
            return [f"🐇 FC media {round(a['fc_media'])}, por encima de "
                    f"{consejo.ZONAS_BICI[zona][2]} (hasta {alto} ppm): hoy tocaba suave."]
        return [f"✅ Pulso en zona ({consejo.ZONAS_BICI[zona][2]}, hasta {alto} ppm)."]
    return [f"✅ Hecha la sesión aconsejada: {menu._md(c['titulo'])}."]


async def procesar_actividad(chat_id: int, a: dict) -> str:
    """Analiza una actividad del reloj (frente al consejo del día si lo hubo) y la guarda.
    Devuelve el análisis en Markdown."""
    d = date.fromisoformat(a["fecha"])
    out = [entrenador.resumen_actividad(a)]
    if a["tipo"] == "carrera":
        ref, _ = ritmo(chat_id)
        c = db.consejo(chat_id, d, "carrera")
        sesion = c if c and c.get("pasos") else None
        if sesion:
            out.append(f"🎯 Tocaba: {menu._md(sesion['titulo'])}")
        out += entrenador.analizar_carrera(a, sesion, ref)
    elif a["tipo"] == "bici":
        c = db.consejo(chat_id, d, "bici")
        if c and c.get("pasos"):
            out.append(f"🎯 Tocaba: {menu._md(c['titulo'])}")
        out += _analizar_bici(a, c, umbral(chat_id)[0])
    elif a["tipo"] == "gym":
        series = a.get("series") or []
        if series:
            gyms = [x for x in db.actividades(chat_id, d - timedelta(days=120), 300)
                    if x["tipo"] == "gym" and x["id"] != a["id"] and x["fecha"] <= a["fecha"]]
            previas = _previas(gyms + [a]).get(a["id"], {})
            out += entrenador.resumen_fuerza(series, previas)
        else:
            out.append("El reloj no ha registrado series: empieza la *Fuerza* desde tu "
                       "entreno de Garmin Connect para que se guarden ejercicios y pesos.")
    texto = "\n".join(out)
    db.alta_usuario(chat_id, "atleta")
    db.guardar_actividad(chat_id, a, texto)
    await _notion_dia(chat_id, d)
    return texto


async def _notion_dia(chat_id: int, d: date) -> bool:
    """Copia en Notion las actividades del día (una fila por día)."""
    acts = [x for x in db.actividades(chat_id, d, 50) if x["fecha"] == d.isoformat()]
    if not acts:
        return False
    titulo = " + ".join(x.get("nombre") or x["tipo"] for x in reversed(acts))
    tipos = list(dict.fromkeys(plan.TIPOS.get(x["tipo"], "Otro") for x in acts))
    db.marcar_hecho(chat_id, d, titulo, "Garmin")
    return await notion_sync.sync.guardar_entreno(
        fecha=d, titulo=titulo, nota="Garmin", semana=d.isocalendar().week, fase=None,
        dia=nombre(d), tipos=tipos, descarga=False)


async def sync_garmin(chat_id: int) -> list[str]:
    """Baja las actividades nuevas del reloj. La primera vez importa 14 días sin avisar
    (solo avisa de las de hoy). Devuelve un análisis por actividad nueva."""
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
        texto = await procesar_actividad(chat_id, a)
        if not (primera and a["fecha"] < h.isoformat()):
            msgs.append(texto)
    if db.programados(chat_id):
        try:
            await limpiar_reloj(chat_id)
        except Exception as e:  # noqa: BLE001
            log.info("Garmin: no se pudo limpiar el calendario: %s", e)
    return msgs


async def revisar_bienestar(chat_id: int, forzar: bool = False) -> str:
    """Lee el estado de forma de hoy. Devuelve texto la primera vez que hay datos en el día
    (o siempre si `forzar`). El consejo de /carrera y /bici ya lo tiene en cuenta."""
    _exigir_garmin(chat_id)
    h = hoy()
    db.alta_usuario(chat_id, "atleta")
    db.guardar_bienestar(chat_id, h, await _g(garmin.bienestar, h))
    b = dict(db.bienestar(chat_id, h))
    hay_datos = b["readiness"] is not None or b["hrv_estado"] is not None
    if not forzar and (b["aplicado"] or not hay_datos):
        return ""
    if hay_datos:
        db.marcar_bienestar_aplicado(chat_id, h)
    return entrenador.texto_bienestar(b)


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
