"""Objetivos de calorías y macros para un volumen limpio, ajustados al entreno de cada día.

Todo es puro (sin I/O): recibe el peso y el ajuste acumulado y devuelve números.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import plan
from config import ATHLETE, OBJETIVO_KG_SEMANA, SUPERAVIT_KCAL

# Actividad diaria sin contar el entreno (trabajo de oficina + vida normal)
FACTOR_NEAT = 1.35
PROTEINA_G_KG = 2.0
GRASA_G_KG = 0.9

KCAL_GYM = {0: 350, 1: 300, 2: 300, 3: 250}  # pierna / pecho / espalda / hombro
KCAL_CALIDAD = {1: 450, 2: 600, 3: 650, 4: 600}  # por fase, con calentamiento
KCAL_MIN_BICI = 8
KCAL_MIN_NATACION = 9
RITMO_SUAVE_MIN_KM = 5.75
KCAL_FUERA_DE_PLAN = 250  # actividad suave estimada antes/después del plan

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
_km = plan.km_texto


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
        return round(base * 0.5) if cambio.modo == "corta" else base
    s = plan.sesiones_dia(dia)
    if s["fuera_de_plan"]:
        return KCAL_FUERA_DE_PLAN
    w = plan.datos_semana(s["semana"])
    dow = dia.weekday()

    def carrera_km(km: float) -> float:
        return km * peso  # ~1 kcal por kg y km

    if s["semana"] == plan.total_semanas():
        return round({
            0: 150, 1: 400, 2: 0,
            3: carrera_km(_minutos(w["suave"]) / RITMO_SUAVE_MIN_KM),
            4: 0, 5: 200, 6: carrera_km(21.1),
        }[dow])

    factor = 0.8 if w["descarga"] else 1.0
    if dow in (0, 1, 2):
        return round(KCAL_GYM[dow] * factor)
    if dow == 3:
        return round((KCAL_CALIDAD[w["fase"]] + KCAL_GYM[3]) * factor)
    if dow == 4:
        return round(_minutos(w["nata"]) * KCAL_MIN_NATACION
                     + carrera_km(_minutos(w["suave"]) / RITMO_SUAVE_MIN_KM))
    if dow == 5:
        return round(carrera_km(_km(w["larga"])))
    return round(_minutos(w["bici"]) * KCAL_MIN_BICI)


def objetivo_dia(dia: date, peso: float, ajuste: int = 0,
                 cambio: plan.Cambio | None = None) -> Objetivo:
    base = round(tmb(peso) * FACTOR_NEAT)
    ejercicio = gasto_ejercicio(dia, peso, cambio)
    kcal = round((base + ejercicio + SUPERAVIT_KCAL + ajuste) / 50) * 50
    proteina = round(PROTEINA_G_KG * peso)
    grasa = round(GRASA_G_KG * peso)
    hidratos = max(0, round((kcal - proteina * 4 - grasa * 9) / 4))
    return Objetivo(dia, plan.sesiones_dia(dia, cambio)["titulo"], base, ejercicio, SUPERAVIT_KCAL,
                    ajuste, kcal, proteina, grasa, hidratos, consejo(dia, cambio))


def objetivos_semana(desde: date, peso: float, ajuste: int = 0,
                     cambios: dict[date, plan.Cambio] | None = None) -> list[Objetivo]:
    dias = [desde + timedelta(days=i) for i in range(7)]
    return [objetivo_dia(d, peso, ajuste, (cambios or {}).get(d)) for d in dias]


def nuevo_ajuste(pesos: list[tuple[date, float]], hoy: date, actual: int) -> tuple[int, str]:
    """Compara la media de peso de los últimos 7 días con la de los 7 anteriores
    y corrige las kcal para acercarse al objetivo de ganancia semanal."""
    recientes = [p for d, p in pesos if hoy - timedelta(days=7) < d <= hoy]
    previos = [p for d, p in pesos if hoy - timedelta(days=14) < d <= hoy - timedelta(days=7)]
    if not recientes or not previos:
        return actual, "Sin registros de peso suficientes (usa /peso al menos una vez por semana)."
    cambio = sum(recientes) / len(recientes) - sum(previos) / len(previos)
    if cambio < OBJETIVO_KG_SEMANA - 0.15:
        delta, motivo = AJUSTE_PASO, "subes menos de lo previsto"
    elif cambio > OBJETIVO_KG_SEMANA + 0.2:
        delta, motivo = -AJUSTE_PASO, "subes más rápido de lo previsto"
    else:
        delta, motivo = 0, "vas en el ritmo objetivo"
    nuevo = max(AJUSTE_MIN, min(AJUSTE_MAX, actual + delta))
    return nuevo, f"Cambio semanal {cambio:+.2f} kg: {motivo} ({nuevo - actual:+d} kcal)."


def consejo(dia: date, cambio: plan.Cambio | None = None) -> str:
    s = plan.sesiones_dia(dia)
    if s["fuera_de_plan"]:
        return "Reparte la proteína en 4-5 tomas de 30-40 g."
    if cambio and cambio.modo in ("descanso", "bici", "natacion"):
        return "Día más ligero: reparte la proteína en 4-5 tomas y no te saltes comidas."
    if cambio:
        dia = cambio.origen
    dow = dia.weekday()
    if s["semana"] == plan.total_semanas() and dow in (4, 5):
        return "Carga de hidratos: arroz, pasta y pan en cada comida; poca fibra y grasa."
    return {
        0: "Día de pierna: 30-40 g de proteína + hidratos justo después del gimnasio.",
        1: "Reparte la proteína en 4-5 tomas de 30-40 g.",
        2: "Reparte la proteína en 4-5 tomas de 30-40 g.",
        3: "Hidratos antes de las series (avena, plátano) y recupera con proteína + arroz.",
        4: "Doble sesión: snack con hidratos entre rodaje y piscina.",
        5: "Tirada larga: cena rica en hidratos el viernes y desayuno 2-3 h antes.",
        6: "Día más suave: buen momento para preparar comidas de la semana.",
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
