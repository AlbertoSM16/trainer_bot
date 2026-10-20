"""Reglas del entrenador, puras (sin I/O).

- Progresión de cargas (doble progresión) a partir de las series hechas.
- Emparejar las series que registra el reloj con los ejercicios de la rutina.
- Análisis de carreras (volumen, ritmo frente a la zona, deriva cardiaca) y detección de tests.
- Ajuste de la sesión del día según Training Readiness, HRV y sueño.
- Reparto de los grupos de gimnasio pendientes en los días que quedan de la semana.
"""
from collections import Counter
from itertools import product

import gym
import menu
import plan

INCREMENTO_SUPERIOR = 2.5
INCREMENTO_PIERNA = 5.0
BAJADA = 0.95
FALLOS_PARA_BAJAR = 2
# Categorías de Garmin que suben de 5 en 5 kg (básicos de pierna)
CATEGORIAS_PIERNA = {"SQUAT", "DEADLIFT", "LUNGE", "HIP_RAISE"}


NIVEL_READINESS = {"PRIME": "óptimo", "HIGH": "alto", "MODERATE": "moderado", "LOW": "bajo",
                   "POOR": "muy bajo"}
ESTADO_HRV = {"BALANCED": "equilibrada", "UNBALANCED": "desequilibrada", "LOW": "baja",
              "POOR": "muy baja"}


def redondear_peso(kg: float) -> float:
    return round(kg * 2) / 2


def _kg(x: float | None) -> str:
    return "PC" if x is None else f"{x:g}"


# ---------- fuerza ----------

def progresion(fila: dict, series: list[dict]) -> tuple[dict, str]:
    """Doble progresión de un ejercicio de la rutina tras una sesión.
    Devuelve (campos a cambiar en la fila, explicación)."""
    if not fila["reps_max"]:
        return {}, ""
    objetivo = fila["peso_kg"]
    trabajo = [s for s in series
               if objetivo is None or s["peso_kg"] is None or s["peso_kg"] >= objetivo * 0.95]
    if len(trabajo) < fila["series"]:
        return {}, f"{len(trabajo)}/{fila['series']} series de trabajo: sin cambios."
    trabajo = trabajo[:fila["series"]]
    reps = [s["reps"] for s in trabajo]
    pesos = [s["peso_kg"] for s in trabajo if s["peso_kg"]]
    if objetivo is None:
        if not pesos:
            return {}, ""
        nuevo = min(pesos)
        return {"peso_kg": nuevo, "fallos": 0}, f"📌 Peso objetivo fijado en {nuevo:g} kg."
    if all(r >= fila["reps_max"] for r in reps):
        inc = INCREMENTO_PIERNA if fila.get("garmin_cat") in CATEGORIAS_PIERNA else INCREMENTO_SUPERIOR
        nuevo = redondear_peso(objetivo + inc)
        return ({"peso_kg": nuevo, "fallos": 0},
                f"⬆️ Todas a {fila['reps_max']} reps: la próxima vez {nuevo:g} kg.")
    if any(r < fila["reps_min"] for r in reps):
        fallos = fila["fallos"] + 1
        if fallos >= FALLOS_PARA_BAJAR:
            nuevo = redondear_peso(objetivo * BAJADA)
            return ({"peso_kg": nuevo, "fallos": 0},
                    f"⬇️ {fallos} sesiones por debajo de {fila['reps_min']} reps: bajamos a "
                    f"{nuevo:g} kg para volver a progresar.")
        return ({"fallos": fallos},
                f"⚠️ Por debajo de {fila['reps_min']} reps. Si se repite, bajamos un 5 %.")
    return {"fallos": 0}, f"👍 En rango: sigue con {objetivo:g} kg hasta hacer todas a {fila['reps_max']}."


def emparejar(series: list[dict], filas: list[dict]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Asigna las series del reloj ({categoria, nombre, reps, peso_kg}) a los ejercicios de la
    rutina. Las series seguidas del mismo ejercicio forman un bloque; cada bloque va al primer
    ejercicio libre de la rutina con esa categoría (o sin categoría si el reloj no la sabe).
    Si un bloque es más largo que las series del ejercicio y quedan más ejercicios de la misma
    categoría, se reparte. Devuelve ({rutina_id: series}, series sin asignar)."""
    bloques: list[tuple[tuple, list[dict]]] = []
    for s in series:
        cat = s.get("categoria")
        cat = None if cat in (None, "UNKNOWN") else cat
        clave = (cat, s.get("nombre"))
        if bloques and bloques[-1][0] == clave:
            bloques[-1][1].append(s)
        else:
            bloques.append((clave, [s]))
    libres = list(filas)
    asignadas: dict[int, list[dict]] = {}
    sueltas = []
    for (cat, _), ss in bloques:
        while ss:
            fila = next((f for f in libres if f.get("garmin_cat") == cat), None)
            if fila is None:
                sueltas += ss
                break
            libres.remove(fila)
            quedan = any(f.get("garmin_cat") == cat for f in libres)
            n = fila["series"] if quedan else len(ss)
            asignadas[fila["id"]], ss = ss[:n], ss[n:]
    return asignadas, sueltas


def texto_series(series: list[dict]) -> str:
    return ", ".join(f"{_kg(s['peso_kg'])}×{s['reps']}" for s in series)


# ---------- carrera ----------

def _pasos_planos(pasos: list) -> list[tuple]:
    out = []
    for p in pasos:
        out += _pasos_planos(p[2]) if p[0] == "repetir" else [p]
    return out


def zonas_sesion(pasos: list) -> set[str]:
    return {p[2] for p in _pasos_planos(pasos) if p[2]}


def _ritmo(v: dict) -> float | None:
    if not v.get("distancia_m") or not v.get("duracion_s"):
        return None
    return v["duracion_s"] / (v["distancia_m"] / 1000)


def analizar_carrera(a: dict, sesion: dict | None, ref: int) -> list[str]:
    """Comentarios de una carrera frente a la sesión aconsejada (`sesion` = consejo del día
    con km y pasos, o None si no hubo consejo)."""
    out = []
    km = (a.get("distancia_m") or 0) / 1000
    ritmo = _ritmo(a)
    if sesion:
        if sesion.get("km"):
            hecho, previsto, unidad = km, sesion["km"], "km"
        else:
            hecho, previsto, unidad = (a.get("duracion_s") or 0) / 60, sesion.get("minutos"), "min"
        if previsto:
            dif = hecho / previsto - 1
            if dif < -0.2:
                out.append(f"📉 Te has quedado corto: {hecho:.0f} de ~{previsto:g} {unidad} previstos."
                           if unidad == "min" else
                           f"📉 Te has quedado corto: {hecho:.1f} de ~{previsto:g} km previstos.")
            elif dif > 0.25:
                out.append(f"📈 Más volumen del previsto (~{previsto:g} {unidad}): ojo con "
                           "acumular fatiga.")
            else:
                out.append(f"✅ Volumen según lo aconsejado (~{previsto:g} {unidad}).")
        zonas = zonas_sesion(sesion["pasos"])
        if ritmo and zonas and zonas <= {"z1", "z2"}:
            limite = ref + plan.ZONAS["z2"][0]
            if ritmo < limite - 10:
                out.append(f"🐇 Has ido a {plan.pace(round(ritmo))} /km, más rápido que Z2 "
                           f"({plan.pace(limite)} /km o más lento). Los rodajes, suaves de verdad.")
            else:
                out.append("✅ Ritmo de rodaje correcto.")
        calidad = [z for z in ("vo2", "umbral", "media", "medio") if z in zonas]
        if calidad:
            a_, b_ = plan.ZONAS[calidad[0]]
            rapido, lento = ref + a_, ref + b_
            buenas = [r for v in a.get("vueltas", []) if (r := _ritmo(v))
                      and v["distancia_m"] >= 300 and r <= ref + plan.ZONAS["z2"][0] - 15]
            if buenas:
                out.append(f"⚡ Tramos rápidos: {', '.join(plan.pace(round(r)) for r in buenas[:10])} /km "
                           f"(objetivo {plan.pace(rapido)}-{plan.pace(lento)}).")
                if sum(buenas) / len(buenas) > lento + 8:
                    out.append("Te has quedado algo lento en la parte de calidad: revisa el "
                               "descanso de la víspera o baja un punto la exigencia.")
                elif sum(buenas) / len(buenas) < rapido - 8:
                    out.append("Has ido más rápido de lo previsto en la calidad: si sale fácil, "
                               "un test confirmará que tus ritmos han mejorado.")
    deriva = deriva_cardiaca(a.get("vueltas", []))
    if deriva is not None and (not sesion or zonas_sesion(sesion["pasos"]) <= {"z1", "z2"}):
        if deriva > 0.05:
            out.append(f"💓 Deriva cardiaca del {deriva:.0%}: falta base aeróbica, o calor o "
                       "deshidratación. Más rodajes en Z2.")
        elif deriva <= 0.03:
            out.append(f"💓 Pulso estable (deriva {deriva:.0%}): buena base aeróbica.")
    return out


def deriva_cardiaca(vueltas: list[dict]) -> float | None:
    """Pérdida de eficiencia (pulsaciones por velocidad) entre la 1.ª y la 2.ª mitad."""
    v = [x for x in vueltas if (x.get("distancia_m") or 0) >= 800 and x.get("fc_media")
         and x.get("duracion_s")]
    if len(v) < 4:
        return None
    mitad = len(v) // 2

    def coste(tramo):
        dist = sum(x["distancia_m"] for x in tramo)
        dur = sum(x["duracion_s"] for x in tramo)
        fc = sum(x["fc_media"] * x["duracion_s"] for x in tramo) / dur
        return fc / (dist / dur)

    return coste(v[mitad:]) / coste(v[:mitad]) - 1


def detectar_test(a: dict, km: float) -> int | None:
    """Segundos del tramo de test: las vueltas seguidas que suman ~`km` km (la más rápida si
    hay varias, sirve con autolap de 1 km o con el paso del entreno) o la actividad entera."""
    objetivo = km * 1000
    vs = [v for v in a.get("vueltas") or [] if v.get("distancia_m") and v.get("duracion_s")]
    mejor = None
    for i in range(len(vs)):
        dist = seg = 0.0
        for v in vs[i:]:
            dist += v["distancia_m"]
            seg += v["duracion_s"]
            if abs(dist / objetivo - 1) <= 0.03:
                ritmo = seg / dist
                if mejor is None or ritmo < mejor[0]:
                    mejor = (ritmo, seg * objetivo / dist)
                break
            if dist > objetivo * 1.03:
                break
    if mejor:
        return round(mejor[1])
    if a.get("distancia_m") and abs(a["distancia_m"] / objetivo - 1) <= 0.03:
        return round(a["duracion_s"])
    return None


def resumen_actividad(a: dict) -> str:
    emoji = {"carrera": "🏃", "gym": "🏋️", "natacion": "🏊", "bici": "🚴"}.get(a["tipo"], "⌚")
    partes = []
    if a.get("distancia_m"):
        partes.append(f"{a['distancia_m'] / 1000:.2f} km")
    if a.get("duracion_s"):
        partes.append(plan.tiempo_txt(round(a["duracion_s"])))
    if a["tipo"] == "carrera" and (r := _ritmo(a)):
        partes.append(f"{plan.pace(round(r))} /km")
    if a.get("fc_media"):
        partes.append(f"FC {round(a['fc_media'])}" + (f"/{round(a['fc_max'])}" if a.get("fc_max") else ""))
    linea = f"{emoji} *{menu._md(a.get('nombre') or a['tipo'])}* ({a['fecha']}): " + " · ".join(partes)
    if a.get("te_aerobico"):
        linea += (f"\nEfecto de entreno: aeróbico {a['te_aerobico']:.1f}"
                  + (f" · anaeróbico {a['te_anaerobico']:.1f}" if a.get("te_anaerobico") else ""))
    return linea


# ---------- estado de forma ----------

def regla_bienestar(b: dict, carga: str) -> tuple[str | None, str]:
    """Modo para la sesión de hoy según Readiness (0-100), estado de la HRV y sueño.
    `carga` es la de `plan.carga_dia` (pierna | carrera | otra | ligera)."""
    r, hrv, sueno = b.get("readiness"), b.get("hrv_estado"), b.get("sueno_h")
    if carga == "ligera":
        return None, ""
    if r is not None and r < 20:
        return "descanso", f"Training Readiness muy bajo ({r}): hoy toca recuperar."
    if (r is not None and r < 35) or hrv in ("LOW", "POOR"):
        motivo = (f"Readiness {r}" if r is not None and r < 35
                  else f"HRV {ESTADO_HRV.get(hrv, hrv.lower())}")
        return "suave", f"{motivo}: hoy versión suave."
    if sueno is not None and sueno < 5 and carga in ("carrera", "pierna"):
        return "suave", f"Solo {sueno:.1f} h de sueño: hoy versión suave."
    if r is not None and r < 50 and carga in ("carrera", "pierna"):
        return None, (f"Readiness moderado ({r}): haz la sesión sin apurar y recorta si a "
                      "mitad vas mal.")
    return None, ""


def texto_bienestar(b: dict) -> str:
    partes = []
    if b.get("readiness") is not None:
        partes.append(f"Readiness {b['readiness']}"
                      + (f" ({NIVEL_READINESS.get(b['readiness_txt'], b['readiness_txt'].lower())})"
                         if b.get("readiness_txt") else ""))
    if b.get("hrv_estado") or b.get("hrv_media"):
        partes.append("HRV " + " ".join(str(x) for x in (
            b.get("hrv_media") and f"{b['hrv_media']:g} ms",
            b.get("hrv_estado") and ESTADO_HRV.get(b["hrv_estado"], b["hrv_estado"].lower()))
            if x))
    if b.get("sueno_h") is not None:
        partes.append(f"sueño {b['sueno_h']:.1f} h"
                      + (f" ({b['sueno_puntos']} pts)" if b.get("sueno_puntos") else ""))
    if b.get("body_battery") is not None:
        partes.append(f"Body Battery {b['body_battery']}")
    return "⌚ " + " · ".join(partes) if partes else "⌚ Sin datos de hoy todavía."


# ---------- reparto del gimnasio ----------

PRIORIDAD_GRUPOS = ("pierna", "espalda", "pecho", "hombro")
# Categoría de Garmin -> grupo por defecto (si la rutina propia no la tiene)
GRUPO_DE_CATEGORIA = {
    "BENCH_PRESS": "pecho", "FLYE": "pecho", "PUSH_UP": "pecho", "TRICEPS_EXTENSION": "pecho",
    "ROW": "espalda", "PULL_UP": "espalda", "CURL": "espalda", "HYPEREXTENSION": "espalda",
    "SQUAT": "pierna", "LUNGE": "pierna", "DEADLIFT": "pierna", "HIP_RAISE": "pierna",
    "LEG_CURL": "pierna", "CALF_RAISE": "pierna",
    "SHOULDER_PRESS": "hombro", "LATERAL_RAISE": "hombro", "SHRUG": "hombro",
}
COSTE_NO_CABE = 100
COSTE_PIERNA_CARRERA = 120
COSTE_OTRO_DIA = 10
COSTE_JUNTAR = 5
COSTE_MEZCLAR = 3
MAX_COMBINACIONES = 200_000


def _coste_descartar(grupo: str) -> int:
    """Descartar cuesta más cuanto más prioritario es el grupo (se pierde antes el hombro)."""
    rango = PRIORIDAD_GRUPOS.index(grupo) if grupo in PRIORIDAD_GRUPOS else len(PRIORIDAD_GRUPOS)
    return COSTE_NO_CABE + 2 * (len(PRIORIDAD_GRUPOS) - rango)


def repartir(pendientes: list[str], huecos: list[dict],
             max_grupos: int = 2) -> tuple[dict, list[str]]:
    """Reparte los grupos pendientes de la semana en los huecos de gimnasio que quedan.

    `huecos`: [{"fecha", "grupos": grupos que tenía ese día, "carrera": bool (hay carrera
    ese día o el siguiente)}], en orden. Como mucho `max_grupos` por día; la pierna no va
    junto a una carrera ni el día antes; se mueve lo mínimo y se prefiere repartir a juntar.
    Devuelve ({fecha: (grupos…)} para todos los huecos, grupos que no caben)."""
    pend = list(pendientes)
    n = len(huecos)
    if not pend or not n:
        return {h["fecha"]: () for h in huecos}, pend
    opciones = list(range(n)) + [None]
    if (n + 1) ** len(pend) > MAX_COMBINACIONES:
        pend = sorted(pend, key=_coste_descartar, reverse=True)
        while (n + 1) ** len(pend) > MAX_COMBINACIONES:
            pend.pop()
    mejor, mejor_coste = None, None
    for asign in product(opciones, repeat=len(pend)):
        cuenta = Counter(a for a in asign if a is not None)
        if any(v > max_grupos for v in cuenta.values()):
            continue
        coste = 0
        vistos = set()
        for g, a in zip(pend, asign):
            if a is None:
                coste += _coste_descartar(g)
                continue
            if (a, g) in vistos:
                coste = None
                break
            vistos.add((a, g))
            h = huecos[a]
            if h["carrera"] and gym.zona(g) != "superior":
                coste += COSTE_PIERNA_CARRERA
            if g not in h["grupos"]:
                coste += COSTE_OTRO_DIA
        if coste is None:
            continue
        coste += sum(COSTE_JUNTAR * (v - 1) for v in cuenta.values())
        piernas = Counter(a for g, a in zip(pend, asign)
                          if a is not None and gym.zona(g) != "superior")
        coste += sum(30 * (v - 1) for v in piernas.values())
        # Juntar pierna con tren superior hace la sesión más pesada que torso + torso
        coste += sum(COSTE_MEZCLAR for a in set(piernas) if cuenta[a] > piernas[a])
        if mejor_coste is None or coste < mejor_coste:
            mejor, mejor_coste = asign, coste
    reparto = {}
    for i, h in enumerate(huecos):
        grupos = [g for g, a in zip(pend, mejor) if a == i]
        grupos.sort(key=lambda g: (g not in h["grupos"],
                                   h["grupos"].index(g) if g in h["grupos"] else 0))
        reparto[h["fecha"]] = tuple(grupos)
    descartados = [g for g, a in zip(pend, mejor) if a is None]
    descartados += list((Counter(pendientes) - Counter(pend)).elements())
    return reparto, descartados


def grupos_de_series(series: list[dict], categoria_grupo: dict[str, str],
                     minimo: int) -> list[str]:
    """Grupos trabajados en una sesión de fuerza del reloj (≥ `minimo` series)."""
    cuenta = Counter()
    for s in series:
        cat = s.get("categoria")
        g = categoria_grupo.get(cat) or GRUPO_DE_CATEGORIA.get(cat)
        if g:
            cuenta[g] += 1
    return [g for g, v in cuenta.items() if v >= minimo]


# ---------- fuerza escrita en Garmin Connect ----------

NOMBRE_CATEGORIA = {
    "BENCH_PRESS": "Press banca", "FLYE": "Aperturas", "PUSH_UP": "Flexiones",
    "TRICEPS_EXTENSION": "Extensión de tríceps", "ROW": "Remo", "PULL_UP": "Dominadas",
    "CURL": "Curl", "SQUAT": "Sentadilla", "LUNGE": "Zancadas", "DEADLIFT": "Peso muerto",
    "HIP_RAISE": "Hip thrust", "LEG_CURL": "Curl femoral", "CALF_RAISE": "Gemelos",
    "SHOULDER_PRESS": "Press militar", "LATERAL_RAISE": "Elevaciones laterales",
    "SHRUG": "Encogimientos", "PLANK": "Plancha", "CRUNCH": "Abdominales", "CORE": "Core",
    "HYPEREXTENSION": "Hiperextensiones", "CARRY": "Paseo del granjero",
}


NOMBRE_GARMIN = {
    "LAT_PULLDOWN": "Jalón al pecho", "BARBELL_BENCH_PRESS": "Press banca con barra",
    "DUMBBELL_BENCH_PRESS": "Press banca con mancuernas",
    "INCLINE_DUMBBELL_BENCH_PRESS": "Press inclinado con mancuernas",
    "INCLINE_BARBELL_BENCH_PRESS": "Press inclinado con barra", "BARBELL_ROW": "Remo con barra",
    "DUMBBELL_ROW": "Remo con mancuerna", "SEATED_CABLE_ROW": "Remo en polea",
    "BARBELL_BACK_SQUAT": "Sentadilla con barra", "BARBELL_DEADLIFT": "Peso muerto con barra",
    "ROMANIAN_DEADLIFT": "Peso muerto rumano", "LEG_PRESS": "Prensa",
    "BARBELL_SHOULDER_PRESS": "Press militar con barra",
    "DUMBBELL_SHOULDER_PRESS": "Press de hombro con mancuernas",
    "DUMBBELL_LATERAL_RAISE": "Elevaciones laterales", "BARBELL_BICEPS_CURL": "Curl con barra",
    "DUMBBELL_BICEPS_CURL": "Curl con mancuernas", "HAMMER_CURL": "Curl martillo",
    "TRICEPS_PRESSDOWN": "Extensión de tríceps en polea", "CABLE_CROSSOVER": "Cruces en polea",
}
SIN_IDENTIFICAR = "Sin identificar"


def nombre_ejercicio(s: dict) -> str:
    """Nombre legible de una serie del reloj (el ejercicio concreto si lo hay)."""
    n = s.get("nombre") or ""
    cat = s.get("categoria") or ""
    if n and n != cat:
        return NOMBRE_GARMIN.get(n) or n.replace("_", " ").lower().capitalize()
    if not cat or cat == "UNKNOWN":
        return SIN_IDENTIFICAR
    return NOMBRE_CATEGORIA.get(cat) or cat.replace("_", " ").lower().capitalize()


def limpiar_series(series: list[dict]) -> list[dict]:
    """Quita las series sin repeticiones y asigna las que el reloj no identificó al ejercicio
    de al lado si llevan el mismo peso (suele ser una serie más de ese ejercicio)."""
    ss = [dict(s) for s in series if s.get("reps")]
    nombres = [nombre_ejercicio(s) for s in ss]
    for i, s in enumerate(ss):
        if nombres[i] != SIN_IDENTIFICAR:
            continue
        for j in (i - 1, i + 1):
            if (0 <= j < len(ss) and nombres[j] != SIN_IDENTIFICAR
                    and ss[j].get("peso_kg") == s.get("peso_kg")):
                s["categoria"], s["nombre"] = ss[j].get("categoria"), ss[j].get("nombre")
                nombres[i] = nombres[j]
                break
    return ss


def por_ejercicio(series: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in limpiar_series(series):
        out.setdefault(nombre_ejercicio(s), []).append(s)
    return out


def _mejor(series: list[dict]) -> tuple[float, int]:
    """(peso máximo, reps máximas con ese peso)."""
    peso = max((s.get("peso_kg") or 0) for s in series)
    return peso, max(s.get("reps") or 0 for s in series if (s.get("peso_kg") or 0) == peso)


def progreso(actual: list[dict], anterior: list[dict] | None) -> str:
    """Comparación de un ejercicio con la última vez que se hizo."""
    if not anterior:
        return ""
    (p1, r1), (p0, r0) = _mejor(actual), _mejor(anterior)
    if p1 > p0:
        return f"📈 +{_kg(p1 - p0)} kg"
    if p1 < p0:
        return f"📉 {_kg(p1 - p0)} kg"
    if r1 > r0:
        return f"📈 +{r1 - r0} reps"
    if r1 < r0:
        return f"📉 {r1 - r0} reps"
    return "＝"


def resumen_fuerza(series: list[dict], previas: dict[str, list[dict]]) -> list[str]:
    """Líneas por ejercicio con sus series y el progreso frente a `previas` (último día en
    que se hizo cada ejercicio)."""
    out = []
    for ej, ss in por_ejercicio(series).items():
        p = progreso(ss, previas.get(ej)) if ej != SIN_IDENTIFICAR else ""
        out.append(f"• {menu._md(ej)}: {texto_series(ss)}" + (f"  {p}" if p else ""))
    return out
