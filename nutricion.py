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

KCAL_GYM = {0: 300, 1: 300, 2: 350, 5: 250}  # pecho / espalda / pierna / hombro+core
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


def gasto_ejercicio(dia: date, peso: float, cambio: plan.Cambio | None = None) -> int:
    """Estimación de las kcal gastadas en la sesión que se hace ese día (con los cambios)."""
    if cambio and plan.datos_semana(plan.semana_indice(dia)):
        if cambio.modo == "descanso":
            return 0
        if cambio.modo == "bici":
            return plan.minutos_alternativa(cambio.origen) * KCAL_MIN_BICI
        if cambio.modo == "natacion":
            return plan.minutos_alternativa(cambio.origen) * KCAL_MIN_NATACION
        base = gasto_ejercicio(cambio.origen, peso)
        factor = {"corta": 0.5, "suave": 0.7}.get(cambio.modo, 1.0)
        return round(base * factor)
    s = plan.sesiones_dia(dia)
    if s["fuera_de_plan"]:
        return KCAL_FUERA_DE_PLAN
    w = plan.datos_semana(s["semana"])
    dow = dia.weekday()
    gym = KCAL_GYM.get(dow, 0) * (0.8 if w["descarga"] else 1.0)
    if dow == 3:
        return round(_minutos(w["nata"]) * KCAL_MIN_NATACION)
    if dow == 4:
        return 0
    if dow == 5:
        return round(w["carrera"]["km"] * peso + gym)  # ~1 kcal por kg y km
    if dow == 6:
        return round(_minutos(w["bici"]) * KCAL_MIN_BICI)
    return round(gym)


def objetivo_dia(dia: date, peso: float, ajuste: int = 0,
                 cambio: plan.Cambio | None = None) -> Objetivo:
    base = round(tmb(peso) * FACTOR_NEAT)
    ejercicio = gasto_ejercicio(dia, peso, cambio)
    sup = superavit(peso)
    kcal = round((base + ejercicio + sup + ajuste) / 50) * 50
    proteina = round(PROTEINA_G_KG * peso)
    grasa = round(GRASA_G_KG * peso)
    hidratos = max(0, round((kcal - proteina * 4 - grasa * 9) / 4))
    return Objetivo(dia, plan.sesiones_dia(dia, cambio)["titulo"], base, ejercicio, sup,
                    ajuste, kcal, proteina, grasa, hidratos, consejo(dia, cambio))


def objetivos_semana(desde: date, peso: float, ajuste: int = 0,
                     cambios: dict[date, plan.Cambio] | None = None) -> list[Objetivo]:
    dias = [desde + timedelta(days=i) for i in range(7)]
    return [objetivo_dia(d, peso, ajuste, (cambios or {}).get(d)) for d in dias]


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


def consejo(dia: date, cambio: plan.Cambio | None = None) -> str:
    s = plan.sesiones_dia(dia)
    if s["fuera_de_plan"]:
        return "Reparte la proteína en 4 tomas de 30-40 g."
    if cambio and cambio.modo in ("descanso", "bici", "natacion"):
        return "Día más ligero: reparte la proteína en 4 tomas y no te saltes comidas."
    if cambio:
        dia = cambio.origen
    dow = dia.weekday()
    w = plan.datos_semana(plan.semana_indice(dia))
    if dow == 5 and w and w["test"]:
        return "Día de test: cena con hidratos el viernes y desayuno ligero 2-3 h antes."
    return {
        0: "Reparte la proteína en 4 tomas de 30-40 g. Hidratos alrededor del gimnasio.",
        1: "Reparte la proteína en 4 tomas de 30-40 g. Hidratos alrededor del gimnasio.",
        2: "Día de pierna: 30-40 g de proteína + hidratos justo después del gimnasio.",
        3: "Natación tras el trabajo: merienda con hidratos 1-2 h antes.",
        4: "Descanso: misma proteína, algo menos de hidratos. Buen día para cocinar.",
        5: "Carrera + hombro: desayuno con hidratos 2-3 h antes (avena, plátano, miel) "
           "y recupera con proteína + hidratos.",
        6: "Bici: si pasa de 75', lleva agua y algo de hidratos.",
    }[dow]


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
