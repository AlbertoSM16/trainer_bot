"""Plan de entrenamiento periodizado: gimnasio + carrera + natación + bici.

Estructura semanal fija (adaptada a jornada laboral 8-18 L-J, viernes hasta 14h):
  Lunes      -> Gimnasio PIERNA
  Martes     -> Gimnasio PECHO + TRÍCEPS
  Miércoles  -> Gimnasio ESPALDA + BÍCEPS
  Jueves     -> Rodaje de CALIDAD + Gimnasio HOMBRO + ABDOMEN
  Viernes    -> NATACIÓN + rodaje suave
  Sábado     -> TIRADA LARGA
  Domingo    -> BICI Z2 / descanso activo
"""
from datetime import date, timedelta

import gym
from config import GOAL_PACE_SEC, PLAN_START, RACE_DATE, RACE_NAME

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

FASES = {
    1: "Fase 1 · Reconstrucción aeróbica",
    2: "Fase 2 · Base y fuerza aeróbica",
    3: "Fase 3 · Construcción específica",
    4: "Fase 4 · Afinado y competición",
}


def _s(fase, descarga, calidad, larga, suave, bici, nata, nota=""):
    return {
        "fase": fase,
        "descarga": descarga,
        "calidad": calidad,
        "larga": larga,
        "suave": suave,
        "bici": bici,
        "nata": nata,
        "nota": nota,
    }


BICI_BASE = "60' en Z2 (RPE 5-6, cadencia 85-95 rpm). Rodar cómodo, sin subir pulsaciones."
BICI_MEDIA = "75' en Z2 con 3x6' en Z3 (RPE 7) y 4' suave entre bloques."
BICI_LARGA = "90' en Z2 con 3x8' en Z3 (RPE 7). Llano o repechos suaves."
BICI_SUAVE = "45' muy suave en Z1 (RPE 3-4). Solo descarga de piernas."

NATA_TEC = "40' · técnica. 200 calentamiento + 8x50 a RPE 6 (r 20\") + 6x100 técnica + 200 suave."
NATA_BASE = "45' · aeróbico. 300 calentamiento + 10x100 a RPE 6-7 (r 20\") + 4x50 piernas + 200 suave."
NATA_FUERTE = "50' · mixto. 400 calentamiento + 6x200 a RPE 7 (r 30\") + 8x50 fuerte (r 20\") + 200 suave."
NATA_SUAVE = "30' muy suave, RPE 4-5. Recuperación activa y movilidad de hombro."

# 24 semanas hasta la media maratón
WEEKS = [
    # ---- Fase 1: reconstrucción (3 meses de parón) ----
    _s(1, False, "Rodaje 35' en Z2 + 6x30\" progresivos (recta) con 90\" de trote",
       "8 km a 5:50-6:00 /km (Z2 puro)", "30' suave a 5:50-6:10 /km", BICI_BASE, NATA_TEC,
       "Objetivo del mes: volver a correr sin molestias. Nada de forzar ritmos."),
    _s(1, False, "Rodaje 40': 10' suave + 8x(1' a 4:20 / 1' trote) + 10' suave",
       "10 km a 5:50 /km", "35' suave a 5:50-6:00 /km", BICI_BASE, NATA_TEC),
    _s(1, False, "Rodaje 45': 12' suave + 10x(1' a 4:15 / 1' trote) + 10' suave",
       "12 km a 5:45 /km", "40' suave a 5:45-6:00 /km", BICI_MEDIA, NATA_BASE),
    _s(1, True, "Rodaje 35': 10' suave + 6x(1' a 4:20 / 1' trote) + 10' suave",
       "9 km a 5:50 /km", "30' suave", BICI_SUAVE, NATA_SUAVE,
       "SEMANA DE DESCARGA: baja un 30% el volumen y quita una serie en cada ejercicio de gimnasio."),
    _s(1, False, "5x3' a 4:25 /km con 90\" de trote (+ 12' calentamiento y 10' vuelta a la calma)",
       "13 km a 5:40 /km", "45' suave a 5:45 /km", BICI_MEDIA, NATA_BASE),

    # ---- Fase 2: base ----
    _s(2, False, "20' continuos a ritmo medio 4:50 /km (+ 15' cal. y 10' v. a la calma)",
       "14 km a 5:35 /km", "45' suave a 5:45 /km", BICI_MEDIA, NATA_BASE,
       "Entramos en fase de umbral. En gimnasio mantén cargas altas: la fuerza protege del impacto."),
    _s(2, False, "2x10' a 4:35 /km con 3' de trote entre bloques",
       "15 km a 5:35 /km", "45' suave", BICI_LARGA, NATA_BASE),
    _s(2, True, "15' continuos a 4:50 /km",
       "11 km a 5:45 /km", "35' suave", BICI_SUAVE, NATA_SUAVE,
       "SEMANA DE DESCARGA."),
    _s(2, False, "3x8' a 4:35 /km con 2' de trote",
       "16 km a 5:30 /km", "50' suave a 5:40 /km", BICI_MEDIA, NATA_FUERTE),
    _s(2, False, "5x1000 m a 4:15 /km con 2' de recuperación",
       "17 km a 5:30 /km", "50' suave", BICI_LARGA, NATA_BASE),
    _s(2, False, "30' continuos a 4:45 /km",
       "18 km a 5:25 /km", "50' suave", BICI_LARGA, NATA_FUERTE),
    _s(2, True, "4x800 m a 4:10 /km con 2' de recuperación",
       "12 km a 5:40 /km", "40' suave", BICI_SUAVE, NATA_SUAVE,
       "SEMANA DE DESCARGA. Buen momento para un test de 5 km si te apetece medir forma."),

    # ---- Fase 3: construcción específica ----
    _s(3, False, "6x1000 m a 4:12 /km con 2' de recuperación",
       "16 km: 12 a 5:30 + últimos 4 a 4:45 /km", "50' suave", BICI_MEDIA, NATA_BASE,
       "Empieza el trabajo específico de media. En PIERNA baja a 3 series en los básicos para llegar fresco al sábado."),
    _s(3, False, "2x15' a 4:32 /km con 3' de trote",
       "18 km a 5:25 /km", "55' suave a 5:40 /km", BICI_MEDIA, NATA_FUERTE),
    _s(3, False, "8x800 m a 4:05 /km con 90\" de recuperación",
       "19 km a 5:20 /km", "50' suave", BICI_LARGA, NATA_BASE),
    _s(3, True, "20' continuos a 4:45 /km",
       "13 km a 5:35 /km", "40' suave", BICI_SUAVE, NATA_SUAVE,
       "SEMANA DE DESCARGA."),
    _s(3, False, "4x2000 m a 4:25 /km con 2'30\" de recuperación",
       "20 km: 8 suaves + 3x2 km a 4:38 (r 3') + resto suave", "55' suave", BICI_MEDIA, NATA_BASE),
    _s(3, False, "3x3000 m a 4:33 /km con 3' de recuperación",
       "18 km a 5:20 /km", "55' suave", BICI_MEDIA, NATA_FUERTE),
    _s(3, False, "12x400 m a 3:52 /km con 1' de recuperación",
       "21 km a 5:20 /km (tirada más larga del plan)", "55' suave", BICI_BASE, NATA_BASE),
    _s(3, True, "2x10' a 4:30 /km con 3' de trote",
       "14 km a 5:30 /km", "40' suave", BICI_SUAVE, NATA_SUAVE,
       "SEMANA DE DESCARGA."),

    # ---- Fase 4: afinado ----
    _s(4, False, "5x1600 m a 4:20 /km con 2' de recuperación",
       "20 km: 10 suaves + 10 km a 4:40 /km", "50' suave", BICI_BASE, NATA_BASE,
       "Fase específica: la clave es el ritmo de carrera (4:38). En gimnasio reduce volumen un 20%."),
    _s(4, False, "3x2000 m a 4:25 /km (r 2') + 10' a ritmo de media",
       "SIMULACRO: 18 km con 12 km a 4:38 /km. Prueba zapatillas, avituallamiento y desayuno.",
       "50' suave", BICI_SUAVE, NATA_SUAVE),
    _s(4, True, "4x1000 m a 4:15 /km con 2' de recuperación",
       "12 km suaves a 5:30 /km", "40' suave", BICI_SUAVE, NATA_SUAVE,
       "TAPER: -40% de volumen. Gimnasio al 50% (2 series, sin llegar al fallo)."),
    _s(4, True, "Martes: activación 20' suave + 3x1000 a 4:38 /km (r 2')",
       f"🏁 DOMINGO: {RACE_NAME}. Salida a 4:40 los primeros 5 km, 4:38 hasta el 15 y aprieta lo que quede.",
       "Jueves: 30' muy suave + 4x100 m progresivos", "Descanso", "Descanso",
       "SEMANA DE CARRERA: solo gimnasio muy ligero lunes (movilidad y core). Duerme e hidrátate."),
]


def total_semanas() -> int:
    return len(WEEKS)


def semana_indice(dia: date) -> int:
    """Devuelve el número de semana del plan (1-based) para una fecha dada."""
    delta = (dia - PLAN_START).days
    return delta // 7 + 1


def variante(week_num: int) -> str:
    return "A" if week_num % 2 == 1 else "B"


def datos_semana(week_num: int) -> dict | None:
    if 1 <= week_num <= len(WEEKS):
        return WEEKS[week_num - 1]
    return None


def fecha_lunes(week_num: int) -> date:
    return PLAN_START + timedelta(weeks=week_num - 1)


def sesiones_dia(dia: date) -> dict:
    """Devuelve la sesión completa de un día concreto."""
    wn = semana_indice(dia)
    w = datos_semana(wn)
    dow = dia.weekday()

    if w is None:
        estado = "pre" if wn < 1 else "post"
        return {
            "fecha": dia,
            "semana": wn,
            "fase": None,
            "fuera_de_plan": estado,
            "titulo": "Fuera del plan",
            "bloques": [],
        }

    var = variante(wn)
    es_carrera = wn == len(WEEKS)
    bloques = []

    if es_carrera:
        return {
            "fecha": dia, "semana": wn, "fase": w["fase"], "descarga": True,
            "fuera_de_plan": None,
            "titulo": _titulo_carrera(dow),
            "bloques": _semana_carrera(dow, w),
            "nota": w["nota"],
        }

    if dow == 0:
        titulo = "Gimnasio · Pierna"
        bloques.append(("🏋️ Gimnasio", gym.formatear("pierna", var)))
        bloques.append(("🧘 Extra", "10' de movilidad de cadera y tobillo al terminar."))
    elif dow == 1:
        titulo = "Gimnasio · Pecho y tríceps"
        bloques.append(("🏋️ Gimnasio", gym.formatear("pecho", var)))
    elif dow == 2:
        titulo = "Gimnasio · Espalda y bíceps"
        bloques.append(("🏋️ Gimnasio", gym.formatear("espalda", var)))
    elif dow == 3:
        titulo = "Carrera de calidad + Gimnasio · Hombro y abdomen"
        bloques.append(("🏃 Series / calidad", w["calidad"]))
        bloques.append(("🏋️ Gimnasio", gym.formatear("hombro", var)))
        bloques.append(("ℹ️ Orden", "Corre primero (o por la mañana). El gimnasio de hoy no carga piernas, por eso va junto."))
    elif dow == 4:
        titulo = "Natación + rodaje suave"
        bloques.append(("🏊 Natación", w["nata"]))
        bloques.append(("🏃 Rodaje suave", w["suave"]))
        bloques.append(("ℹ️ Orden", "Sales a las 14h: rodaje primero y piscina después, o al revés si prefieres."))
    elif dow == 5:
        titulo = "Tirada larga"
        bloques.append(("🏃 Tirada larga", w["larga"]))
        bloques.append(("🥤 Nutrición", "Si pasas de 90', lleva gel o bebida isotónica cada 40-45'."))
    else:
        titulo = "Bici · descarga activa"
        bloques.append(("🚴 Bici", w["bici"]))
        bloques.append(("😴 Alternativa", "Si vienes muy cargado del sábado, descansa del todo. Es una opción válida."))

    if w["nota"] and dow == 0:
        bloques.append(("📌 Nota de la semana", w["nota"]))

    return {
        "fecha": dia, "semana": wn, "fase": w["fase"], "descarga": w["descarga"],
        "fuera_de_plan": None, "titulo": titulo, "bloques": bloques, "nota": w["nota"],
    }


def _titulo_carrera(dow: int) -> str:
    return {
        0: "Movilidad y core (muy ligero)",
        1: "Activación con ritmo de carrera",
        2: "Descanso total",
        3: "Rodaje suave + progresivos",
        4: "Descanso total",
        5: "Activación pre-carrera",
        6: f"🏁 {RACE_NAME}",
    }[dow]


def _semana_carrera(dow: int, w: dict) -> list:
    tabla = {
        0: [("🧘 Sesión", "30' de movilidad general + core suave. Nada de cargas pesadas.")],
        1: [("🏃 Activación", w["calidad"])],
        2: [("😴 Descanso", "Descanso total. Empieza a cuidar la hidratación y el sueño.")],
        3: [("🏃 Rodaje", w["suave"])],
        4: [("😴 Descanso", "Descanso. Prepara dorsal, ropa y desayuno. Cena con hidratos.")],
        5: [("🏃 Activación", "15' muy suave + 3x100 m a ritmo de carrera. Piernas despiertas, nada más.")],
        6: [("🏁 Carrera", w["larga"])],
    }
    return tabla[dow]


def resumen_semana(week_num: int) -> str:
    w = datos_semana(week_num)
    if w is None:
        return "Esa semana está fuera del plan."
    lunes = fecha_lunes(week_num)
    cab = [
        f"📅 *Semana {week_num}/{len(WEEKS)}* ({lunes.strftime('%d/%m')} - "
        f"{(lunes + timedelta(days=6)).strftime('%d/%m')})",
        f"_{FASES[w['fase']]}_" + ("  ·  ⚠️ *DESCARGA*" if w["descarga"] else ""),
        "",
    ]
    lineas = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        s = sesiones_dia(d)
        lineas.append(f"*{DIAS[i]}* — {s['titulo']}")
    if w["nota"]:
        lineas += ["", f"📌 _{w['nota']}_"]
    return "\n".join(cab + lineas)


def formatear_dia(dia: date) -> str:
    s = sesiones_dia(dia)
    if s["fuera_de_plan"] == "pre":
        faltan = (PLAN_START - dia).days
        return (f"El plan arranca el lunes {PLAN_START.strftime('%d/%m/%Y')} "
                f"(faltan {faltan} días). Usa /semana 1 para ver cómo empieza.")
    if s["fuera_de_plan"] == "post":
        return "El plan ya ha terminado. ¡Enhorabuena! Usa /reiniciar para planificar un nuevo ciclo."

    cab = [
        f"*{DIAS[dia.weekday()]} {dia.strftime('%d/%m/%Y')}*",
        f"Semana {s['semana']}/{len(WEEKS)} · {FASES[s['fase']]}"
        + ("  ⚠️ DESCARGA" if s.get("descarga") else ""),
        f"⏳ Faltan {(RACE_DATE - dia).days} días para la media",
        "",
        f"👉 *{s['titulo']}*",
        "",
    ]
    cuerpo = []
    for titulo, texto in s["bloques"]:
        cuerpo.append(f"*{titulo}*")
        cuerpo.append(texto)
        cuerpo.append("")
    return "\n".join(cab + cuerpo).strip()


def fase_nombre_corto(fase: int | None) -> str | None:
    """Nombre de la fase sin el prefijo 'Fase N · ' (para el select de Notion)."""
    if fase is None:
        return None
    return FASES[fase].split(" · ", 1)[1]


def tipos_dia(dia: date) -> list[str]:
    """Categorías de entrenamiento de un día, para etiquetar en Notion."""
    wn = semana_indice(dia)
    if datos_semana(wn) is None:
        return []
    dow = dia.weekday()
    if wn == len(WEEKS):
        return {
            0: ["Gimnasio"], 1: ["Carrera"], 2: ["Descanso"], 3: ["Carrera"],
            4: ["Descanso"], 5: ["Carrera"], 6: ["Carrera"],
        }[dow]
    return {
        0: ["Gimnasio"], 1: ["Gimnasio"], 2: ["Gimnasio"],
        3: ["Carrera", "Gimnasio"], 4: ["Natación", "Carrera"],
        5: ["Carrera"], 6: ["Bici"],
    }[dow]


def _pace(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def tabla_ritmos() -> str:
    g = GOAL_PACE_SEC
    filas = [
        ("Regenerativo (Z1)", g + 85, g + 100, "RPE 3-4 · charlar sin esfuerzo"),
        ("Suave / rodaje (Z2)", g + 55, g + 70, "RPE 5 · base del plan"),
        ("Medio (Z3)", g + 8, g + 20, "RPE 6-7 · cómodo-duro"),
        ("Ritmo media maratón", g, g, "RPE 8 · objetivo de carrera"),
        ("Umbral (Z4)", g - 12, g - 3, "RPE 8-9 · 1h a tope"),
        ("VO2máx (Z5)", g - 38, g - 25, "RPE 9-10 · series cortas"),
    ]
    out = ["🎯 *Ritmos de referencia* (objetivo 4:38 /km en media)", ""]
    for nombre, a, b, desc in filas:
        rango = _pace(a) if a == b else f"{_pace(a)}-{_pace(b)}"
        out.append(f"• *{nombre}*: `{rango} /km`\n  _{desc}_")
    out += ["", "🏊 *Natación*: Z2 = RPE 5-6 (respiras cómodo), Z3 = RPE 7 (puedes decir 3-4 palabras).",
            "🚴 *Bici*: Z1 = RPE 3-4, Z2 = RPE 5-6 (85-95 rpm), Z3 = RPE 7 (80-90 rpm)."]
    return "\n".join(out)
