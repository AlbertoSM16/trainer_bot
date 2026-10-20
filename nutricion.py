"""Objetivos de calorías y macros para un volumen limpio, ajustados al entreno de cada día.

Todo es puro (sin I/O): recibe el peso y el ajuste acumulado y devuelve números.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import plan
from config import ATHLETE, OBJETIVO_KG_SEMANA, PESO_OBJETIVO, SUPERAVIT_KCAL

# Actividad diaria sin contar el entreno (trabajo de oficina + vida normal)
FACTOR_NEAT = 1.35
PROTEINA_G_KG = 2.0
GRASA_G_KG = 0.9

KCAL_GYM = {"pecho": 300, "espalda": 300, "pierna": 350, "hombro": 250}
KCAL_GYM_OTRO = 300
# Ejercicios de una sesión completa de un grupo (para prorratear las sesiones combinadas)
EJERCICIOS_SESION_COMPLETA = 6
KCAL_MIN_BICI = 8
KCAL_MIN_NATACION = 9
KCAL_FUERA_DE_PLAN = 250  # actividad suave estimada antes del plan

AJUSTE_PASO = 100
AJUSTE_MIN, AJUSTE_MAX = -300, 500


@dataclass
class Objetivo:
    fecha: date
    titulo: str
    base: int
    ejercicio: int
    superavit: int
    ajuste: int
    kcal: int
    proteina: int
    grasa: int
    hidratos: int
    consejo: str = ""


def tmb(peso: float) -> float:
    """Tasa metabólica basal (Mifflin-St Jeor, hombre)."""
    return 10 * peso + 6.25 * ATHLETE["altura_cm"] - 5 * ATHLETE["edad"] + 5


_minutos = plan.minutos_texto


def superavit(peso: float) -> int:
    """Superávit del volumen; al llegar al peso objetivo se pasa a mantenimiento."""
    return 0 if peso >= PESO_OBJETIVO else SUPERAVIT_KCAL


def kg_semana_objetivo(peso: float) -> float:
    return 0.0 if peso >= PESO_OBJETIVO else OBJETIVO_KG_SEMANA


def gasto_ejercicio(dia: date, peso: float, cambio: plan.Cambio | None = None,
                    est: plan.Estructura | None = None) -> int:
    """Estimación de las kcal gastadas en la sesión que se hace ese día (con los cambios)."""
    est = est or plan.ESTRUCTURA_DEFECTO
    if cambio and plan.datos_semana(plan.semana_indice(dia)):
        if cambio.modo == "descanso":
            return 0
        if cambio.modo == "bici":
            return plan.minutos_alternativa(cambio.origen, est) * KCAL_MIN_BICI
        if cambio.modo == "natacion":
            return plan.minutos_alternativa(cambio.origen, est) * KCAL_MIN_NATACION
        base = _gasto_base(cambio.origen, peso, est, dia)
        factor = {"corta": 0.5, "suave": 0.7}.get(cambio.modo, 1.0)
        return round(base * factor)
    return _gasto_base(dia, peso, est, dia)


def _gasto_base(dia: date, peso: float, est: plan.Estructura, ejecucion: date) -> int:
    """Kcal de la sesión base de `dia` hecha en `ejecucion` (con su reparto de gimnasio)."""
    s = plan._sesion_base(dia, None, est, ejecucion)
    if s["fuera_de_plan"]:
        return KCAL_FUERA_DE_PLAN
    w = plan.datos_semana(s["semana"], None, est)
    total = 0.0
    for e in s["entrenos"]:
        if e["tipo"] == "gym":
            kcal = KCAL_GYM.get(e["grupo"], KCAL_GYM_OTRO) * (0.8 if w["descarga"] else 1.0)
            if e.get("max_ejercicios"):
                kcal *= min(1.0, e["max_ejercicios"] / EJERCICIOS_SESION_COMPLETA)
            total += kcal
        elif e["tipo"] == "carrera":
            total += e["km"] * peso  # ~1 kcal por kg y km
        elif e["tipo"] == "natacion":
            total += _minutos(w["nata"]) * KCAL_MIN_NATACION
        elif e["tipo"] == "bici":
            total += _minutos(w["bici"]) * KCAL_MIN_BICI
    return round(total)


def objetivo_dia(dia: date, peso: float, ajuste: int = 0,
                 cambio: plan.Cambio | None = None, est: plan.Estructura | None = None,
                 ejercicio_kcal: int | None = None, entreno: str | None = None,
                 reloj: bool = True) -> Objetivo:
    """Objetivo del día. Con `ejercicio_kcal` (media del reloj, o la estimada si `reloj` es
    False) no se mira el plan; `entreno` es lo que toca hoy (``carrera:umbral``…) para el
    consejo."""
    base = round(tmb(peso) * FACTOR_NEAT)
    if ejercicio_kcal is None:
        ejercicio = gasto_ejercicio(dia, peso, cambio, est)
        titulo = plan.sesiones_dia(dia, cambio, None, est)["titulo"]
        txt = consejo(dia, cambio, est)
    else:
        ejercicio = ejercicio_kcal
        titulo = ("Entreno: media de tu reloj (14 días)" if reloj else
                  "Entreno: estimación media (sin datos del reloj)")
        txt = consejo_entreno(entreno)
    sup = superavit(peso)
    kcal = round((base + ejercicio + sup + ajuste) / 50) * 50
    proteina = round(PROTEINA_G_KG * peso)
    grasa = round(GRASA_G_KG * peso)
    hidratos = max(0, round((kcal - proteina * 4 - grasa * 9) / 4))
    return Objetivo(dia, titulo, base, ejercicio, sup, ajuste, kcal, proteina, grasa, hidratos,
                    txt)


def objetivos_semana(desde: date, peso: float, ajuste: int = 0,
                     cambios: dict[date, plan.Cambio] | None = None,
                     est: plan.Estructura | None = None,
                     ejercicio_kcal: int | None = None,
                     entrenos: dict[date, str] | None = None,
                     reloj: bool = True) -> list[Objetivo]:
    dias = [desde + timedelta(days=i) for i in range(7)]
    return [objetivo_dia(d, peso, ajuste, (cambios or {}).get(d), est, ejercicio_kcal,
                         (entrenos or {}).get(d), reloj) for d in dias]


def kcal_ejercicio_estimada(peso: float, dia: date) -> int:
    """Media diaria de kcal de ejercicio de una semana tipo (cuando no hay datos del reloj)."""
    lun = dia - timedelta(days=dia.weekday())
    return round(sum(gasto_ejercicio(lun + timedelta(days=i), peso) for i in range(7)) / 7)


def kcal_ejercicio_reloj(actividades: list[dict], peso: float, dias: int = 14) -> int:
    """Media diaria de kcal de ejercicio de las actividades del reloj, sin el metabolismo
    basal del tiempo activo (ya va en la base)."""
    basal_min = tmb(peso) / 1440
    total = sum(max(0.0, (a.get("calorias") or 0) - basal_min * (a.get("duracion_s") or 0) / 60)
                for a in actividades)
    return round(total / dias)


def consejo_entreno(entreno: str | None) -> str:
    """Consejo de comida según el entreno que toca hoy (`deporte:clave`)."""
    deporte, _, clave = (entreno or "").partition(":")
    if clave in ("umbral", "vo2", "media", "tempo"):
        return ("Hoy toca calidad: desayuno o merienda con hidratos 2-3 h antes (avena, plátano, "
                "miel) y proteína + hidratos al acabar.")
    if clave == "larga" or (deporte == "bici" and clave == "fondo"):
        return ("Sesión larga: hidratos la comida de antes, agua y algo de hidratos si pasa de "
                "1h15', y recupera con proteína + hidratos.")
    if clave in ("rodaje", "recuperacion", "fondo"):
        return "Día aeróbico: reparte la proteína en 4 tomas y no te saltes comidas."
    return "Reparte la proteína en 4 tomas de 30-40 g. Hidratos alrededor de tus entrenos."


def nuevo_ajuste(pesos: list[tuple[date, float]], hoy: date, actual: int,
                 objetivo_kg: float = OBJETIVO_KG_SEMANA) -> tuple[int, str]:
    """Compara la media de peso de los últimos 7 días con la de los 7 anteriores
    y corrige las kcal para acercarse al objetivo de ganancia semanal (`objetivo_kg`)."""
    recientes = [p for d, p in pesos if hoy - timedelta(days=7) < d <= hoy]
    previos = [p for d, p in pesos if hoy - timedelta(days=14) < d <= hoy - timedelta(days=7)]
    if not recientes or not previos:
        return actual, "Sin registros de peso suficientes (usa /peso al menos una vez por semana)."
    cambio = sum(recientes) / len(recientes) - sum(previos) / len(previos)
    if cambio < objetivo_kg - 0.15:
        delta, motivo = AJUSTE_PASO, "subes menos de lo previsto"
    elif cambio > objetivo_kg + 0.15:
        delta, motivo = -AJUSTE_PASO, "subes más rápido de lo previsto"
    else:
        delta, motivo = 0, "vas en el ritmo objetivo"
    nuevo = max(AJUSTE_MIN, min(AJUSTE_MAX, actual + delta))
    return nuevo, f"Cambio semanal {cambio:+.2f} kg: {motivo} ({nuevo - actual:+d} kcal)."


def consejo(dia: date, cambio: plan.Cambio | None = None,
            est: plan.Estructura | None = None) -> str:
    est = est or plan.ESTRUCTURA_DEFECTO
    s = plan.sesiones_dia(dia, None, None, est)
    if s["fuera_de_plan"]:
        return "Reparte la proteína en 4 tomas de 30-40 g."
    if cambio and cambio.modo in ("descanso", "bici", "natacion"):
        return "Día más ligero: reparte la proteína en 4 tomas y no te saltes comidas."
    ejecucion = dia
    if cambio:
        dia = cambio.origen
    dow = dia.weekday()
    tipos = est.tipos_en(ejecucion, dia)
    w = plan.datos_semana(plan.semana_indice(dia), None, est)
    if "carrera" in tipos:
        if dow == est.dia_carrera() and w and w["test"]:
            return (f"Día de test: cena con hidratos el {plan.DIAS[(dow - 1) % 7].lower()} "
                    "y desayuno ligero 2-3 h antes.")
        grupos = est.grupos_en(ejecucion, dia)
        que = "Carrera + " + " y ".join(grupos) if grupos else "Carrera"
        return (f"{que}: desayuno con hidratos 2-3 h antes (avena, plátano, miel) "
                "y recupera con proteína + hidratos.")
    if est.pierna_en(ejecucion, dia):
        return "Día de pierna: 30-40 g de proteína + hidratos justo después del gimnasio."
    if "gym" in tipos:
        return "Reparte la proteína en 4 tomas de 30-40 g. Hidratos alrededor del gimnasio."
    if "natacion" in tipos:
        return ("Natación tras el trabajo: merienda con hidratos 1-2 h antes." if dow <= 3 else
                "Natación: come algo con hidratos 1-2 h antes.")
    if "bici" in tipos:
        return "Bici: si pasa de 75', lleva agua y algo de hidratos."
    return "Descanso: misma proteína, algo menos de hidratos. Buen día para cocinar."


def formatear_objetivo(o: Objetivo) -> str:
    ajuste = f" {o.ajuste:+d} ajuste" if o.ajuste else ""
    return "\n".join([
        f"🍽️ *Dieta · {plan.DIAS[o.fecha.weekday()]} {o.fecha.strftime('%d/%m')}*",
        f"_{o.titulo}_",
        "",
        f"🔥 *{o.kcal} kcal*",
        f"• Proteína: {o.proteina} g",
        f"• Grasa: {o.grasa} g",
        f"• Hidratos: {o.hidratos} g",
        "",
        f"_Base {o.base} + entreno {o.ejercicio} + superávit {o.superavit}{ajuste}_",
        "",
        f"💡 {o.consejo or consejo(o.fecha)}",
    ])
