"""Plan cíclico de entrenamiento: gimnasio + carrera + natación + bici.

Estructura semanal fija (jornada de lunes a jueves de 8:00 a 17:30):
  Lunes      -> Gimnasio PECHO + TRÍCEPS
  Martes     -> Gimnasio ESPALDA + BÍCEPS
  Miércoles  -> Gimnasio PIERNA
  Jueves     -> NATACIÓN
  Viernes    -> DESCANSO flexible (hueco para recuperar una sesión movida o saltada)
  Sábado     -> CARRERA (la única de la semana) + Gimnasio HOMBRO + CORE
  Domingo    -> BICI

No hay fecha final: el plan se repite en bloques de 8 semanas (3 de carga + 1 de
descarga, dos veces). El sábado de la semana 8 hay un test de 5 km (bloques impares)
o 10 km (pares) que sirve para recalcular los ritmos. El volumen de carrera crece de
bloque en bloque hasta `NIVEL_MAX`.

Los ritmos salen de `ref`: el ritmo equivalente de media maratón en s/km
(ver `ritmo_ref`). Sin `ref` se usa la marca de config más un margen por desentreno.

Cada usuario puede cambiar su semana (/mover, /saltar, /cambiar). Los cambios se
guardan fuera (SQLite) y llegan aquí como `Cambio`: el día hace la sesión base de
`origen` (dentro de la misma semana) en el `modo` indicado.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

import gym
from config import DESENTRENO_SEC, MARCA_MEDIA_SEC, PLAN_START

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

FASES = {
    1: "Fase 1 · Reconstrucción",
    2: "Fase 2 · Base aeróbica",
    3: "Fase 3 · Desarrollo",
}

SEMANAS_BLOQUE = 8
SEMANAS_DESCARGA = (4, 8)
NIVEL_MAX = 4
MEDIA_KM = 21.0975

MODOS = {
    "descanso": "⏭️",
    "corta": "✂️",
    "suave": "🐢",
    "bici": "🔁",
    "natacion": "🔁",
}


@dataclass(frozen=True)
class Cambio:
    origen: date              # día del plan cuya sesión se hace
    modo: str | None = None   # None o una clave de MODOS


# ---------- ritmos ----------

# Zonas de carrera: (desde, hasta) en s/km sumados al ritmo de media
ZONAS = {
    "z1": (85, 100),
    "z2": (55, 70),
    "medio": (8, 20),
    "media": (0, 0),
    "umbral": (-12, -3),
    "vo2": (-38, -25),
}


def ritmo_ref(test: tuple[float, int] | None = None) -> int:
    """Ritmo equivalente de media maratón (s/km) a partir de un test (km, segundos)
    con la fórmula de Riegel. Sin test: la marca de config + margen por desentreno."""
    if not test:
        return MARCA_MEDIA_SEC + DESENTRENO_SEC
    km, seg = test
    return round(seg * (MEDIA_KM / km) ** 1.06 / MEDIA_KM)


def tiempo_previsto(ref: int, km: float) -> int:
    """Segundos previstos para `km` según el ritmo de media `ref` (Riegel)."""
    return round(ref * MEDIA_KM * (km / MEDIA_KM) ** 1.06)


def pace(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def tiempo_txt(sec: int) -> str:
    h, resto = divmod(sec, 3600)
    return f"{h}:{resto // 60:02d}:{resto % 60:02d}" if h else f"{resto // 60}:{resto % 60:02d}"


def _r(ref: int, zona: str) -> str:
    a, b = ZONAS[zona]
    return f"{pace(ref + a)} /km" if a == b else f"{pace(ref + a)}-{pace(ref + b)} /km"


# ---------- sesiones del bloque ----------

NATA = {
    "tec": "40' · técnica. 200 calentamiento + 8x50 a RPE 6 (r 20\") + 6x100 técnica + 200 suave.",
    "base": "45' · aeróbico. 300 calentamiento + 10x100 a RPE 6-7 (r 20\") + 4x50 piernas + 200 suave.",
    "fuerte": "50' · mixto. 400 calentamiento + 6x200 a RPE 7 (r 30\") + 8x50 fuerte (r 20\") + 200 suave.",
    "suave": "30' muy suave, RPE 4-5. Recuperación activa y movilidad de hombro.",
}
BICI = {
    "base": "60' en Z2 (RPE 5-6, cadencia 85-95 rpm). Rodar cómodo, sin subir pulsaciones.",
    "media": "75' en Z2 con 3x6' en Z3 (RPE 7) y 4' suave entre bloques.",
    "larga": "90' en Z2 con 3x8' en Z3 (RPE 7). Llano o repechos suaves.",
    "suave": "45' muy suave en Z1 (RPE 3-4). Solo descarga de piernas.",
}
_SECUENCIA_NATA = ("tec", "base", "fuerte", "suave", "base", "fuerte", "base", "tec")
_SECUENCIA_BICI = ("base", "media", "larga", "suave", "media", "larga", "media", "suave")


def km_test(bloque: int) -> int:
    return 5 if bloque % 2 == 1 else 10


def _carrera(sb: int, bloque: int, ref: int) -> dict:
    """Sesión de carrera del sábado: {tipo, texto, km}. `sb` = semana dentro del bloque."""
    n = min(bloque, NIVEL_MAX) - 1
    z2, medio, media = _r(ref, "z2"), _r(ref, "medio"), _r(ref, "media")
    umbral, vo2 = _r(ref, "umbral"), _r(ref, "vo2")
    if sb == 1:
        km = (8, 11, 13, 15)[n]
        texto = (f"{km} km en Z2 ({z2}) + 6x20\" progresivos al acabar." if n == 0 else
                 f"{km} km en Z2 ({z2}). Los 2 últimos a ritmo medio ({medio}).")
        return {"tipo": "Rodaje largo", "texto": texto, "km": km}
    if sb == 2:
        if n == 0:
            return {"tipo": "Fartlek", "km": 7.5, "texto":
                    f"Rodaje 40': 10' en Z2 ({z2}) + 8x(1' a umbral {umbral} / 1' trote) "
                    "+ 10' en Z2."}
        reps, mins = ((3, 8), (3, 10), (2, 15))[n - 1]
        total = 25 + reps * mins + (reps - 1) * 2
        return {"tipo": "Tempo", "km": round(total / 5.3, 1), "texto":
                f"15' en Z2 ({z2}) + {reps}x{mins}' a umbral ({umbral}) con 2' de trote "
                "+ 10' en Z2."}
    if sb == 3:
        km, fin = ((10, 0), (13, 3), (15, 4), (17, 5))[n]
        texto = (f"{km} km en Z2 ({z2}). Sin mirar el reloj: que sea cómodo." if not fin else
                 f"{km} km: los {km - fin} primeros en Z2 ({z2}) y los {fin} últimos "
                 f"a ritmo de media ({media}).")
        return {"tipo": "Tirada larga", "texto": texto, "km": km}
    if sb == 4:
        return {"tipo": "Rodaje suave", "km": 7, "texto":
                f"Rodaje 40' en Z2 ({z2}) + 4x20\" progresivos. Descarga: sin forzar."}
    if sb == 5:
        if n == 0:
            return {"tipo": "Series", "km": 8.5, "texto":
                    f"Rodaje 45': 12' en Z2 ({z2}) + 10x(1' a umbral {umbral} / 1' trote) "
                    "+ 10' en Z2."}
        series, rec, km = (("5x1000 m", "2'", 9), ("6x1000 m", "2'", 10),
                           ("8x800 m", "90\"", 10))[n - 1]
        return {"tipo": "Series", "km": km, "texto":
                f"15' en Z2 ({z2}) + {series} a ritmo VO2 ({vo2}) con {rec} de trote "
                "+ 10' en Z2."}
    if sb == 6:
        km = (11, 14, 16, 18)[n]
        texto = (f"{km} km: los {km - 3} primeros en Z2 ({z2}) y los 3 últimos a ritmo "
                 f"medio ({medio})." if n == 0 else
                 f"{km} km progresivos: un tercio en Z2 ({z2}), otro a ritmo medio ({medio}) "
                 f"y el último a ritmo de media ({media}).")
        return {"tipo": "Larga progresiva", "texto": texto, "km": km}
    if sb == 7:
        if n == 0:
            return {"tipo": "Ritmo medio", "km": 9, "texto":
                    f"Rodaje 50': 15' en Z2 ({z2}) + 3x6' a ritmo medio ({medio}) con 2' "
                    "de trote + 10' en Z2."}
        km, reps, dist = ((13, 2, 3), (15, 3, 3), (17, 3, 4))[n - 1]
        return {"tipo": "Ritmo de media", "km": km, "texto":
                f"{km} km: 4 en Z2 ({z2}) + {reps}x{dist} km a ritmo de media ({media}) "
                "con 3' de trote + el resto en Z2."}
    d = km_test(bloque)
    salida = pace(round(tiempo_previsto(ref, d) / d))
    return {"tipo": f"Test {d} km", "km": d + 5, "texto":
            f"15' en Z2 + 4x20\" progresivos + {d} km a tope y lo más uniforme posible "
            f"(sal a {salida} /km y aprieta en el último km) + 10' muy suave. "
            f"Después registra el tiempo: `/test {d}k mm:ss`."}


def semana_indice(dia: date) -> int:
    """Número de semana del plan (1-based) para una fecha dada."""
    return (dia - PLAN_START).days // 7 + 1


def bloque_de(week_num: int) -> int:
    return (week_num - 1) // SEMANAS_BLOQUE + 1


def semana_en_bloque(week_num: int) -> int:
    return (week_num - 1) % SEMANAS_BLOQUE + 1


def variante(week_num: int) -> str:
    return "A" if week_num % 2 == 1 else "B"


def datos_semana(week_num: int, ref: int | None = None) -> dict | None:
    if week_num < 1:
        return None
    ref = ref or ritmo_ref()
    b, sb = bloque_de(week_num), semana_en_bloque(week_num)
    descarga = sb in SEMANAS_DESCARGA
    nata, bici = _SECUENCIA_NATA[sb - 1], _SECUENCIA_BICI[sb - 1]
    if b == 1:
        nata = "base" if nata == "fuerte" else nata
        bici = "media" if bici == "larga" else bici
    notas = []
    if sb == 1:
        notas.append(
            "Bloque de reconstrucción: llevas semanas sin correr con frecuencia. Objetivo: volver "
            "a correr sin molestias, sin forzar ritmos." if b == 1 else
            f"Empieza el bloque {b}: sube algo el volumen de carrera. En el gimnasio, intenta "
            "superar las cargas del bloque anterior.")
    if descarga:
        notas.append("SEMANA DE DESCARGA: un 30 % menos de volumen y una serie menos por "
                     "ejercicio en el gimnasio.")
    if sb == SEMANAS_BLOQUE:
        notas.append(f"El sábado toca test de {km_test(b)} km: que el viernes sea descanso de "
                     "verdad. Con el resultado se recalculan tus ritmos.")
    return {
        "fase": min(b, len(FASES)),
        "bloque": b,
        "semana_bloque": sb,
        "descarga": descarga,
        "test": sb == SEMANAS_BLOQUE,
        "carrera": _carrera(sb, b, ref),
        "nata": NATA[nata],
        "bici": BICI[bici],
        "nota": " ".join(notas),
    }


def fecha_lunes(week_num: int) -> date:
    return PLAN_START + timedelta(weeks=week_num - 1)


def proximo_test(dia: date) -> tuple[date, int]:
    """Fecha y distancia (km) del próximo test a partir de `dia` (incluido)."""
    wn = max(1, semana_indice(dia))
    b = bloque_de(wn)
    sabado = fecha_lunes(b * SEMANAS_BLOQUE) + timedelta(days=5)
    if dia > sabado:
        b += 1
        sabado += timedelta(weeks=SEMANAS_BLOQUE)
    return sabado, km_test(b)


def sesiones_dia(dia: date, cambio: Cambio | None = None, ref: int | None = None) -> dict:
    """Sesión completa de un día, aplicando el cambio del usuario si lo hay."""
    if cambio is None or (cambio.origen == dia and cambio.modo is None):
        return _sesion_base(dia, ref)
    s = dict(_sesion_base(cambio.origen, ref))
    s["fecha"], s["origen"], s["modo"] = dia, cambio.origen, cambio.modo
    if s["fuera_de_plan"]:
        return s
    if cambio.origen != dia:
        s["titulo"] += f" (movida del {DIAS[cambio.origen.weekday()].lower()})"
    dow = cambio.origen.weekday()
    if cambio.modo == "descanso":
        s["titulo"] = f"Descanso (saltas: {s['titulo']})"
        s["bloques"] = [("😴 Descanso", "Hoy no entrenas. Si te apetece, paseo o 10' de movilidad.")]
    elif cambio.modo == "corta":
        s["titulo"] += " · versión corta"
        s["bloques"] = [("✂️ Versión corta",
                         "Haz la mitad del volumen: mitad de minutos, km o repeticiones de "
                         "carrera, y 1-2 series menos por ejercicio de gimnasio. "
                         "Mantén los ritmos e intensidades.")] + s["bloques"]
    elif cambio.modo == "suave":
        s["titulo"] += " · versión suave"
        if dow == 5:
            w = datos_semana(semana_indice(cambio.origen), ref)
            m = int(max(30, min(60, round(w["carrera"]["km"] * 6 * 0.7 / 5) * 5)))
            s["bloques"] = [("🐢 Rodaje suave",
                             f"{m}' en Z2 ({_r(ref or ritmo_ref(), 'z2')}) sin series ni cambios "
                             "de ritmo. Si algo duele al correr, para y cambia a bici.")] + [
                b for b in s["bloques"] if b[0].startswith("🏋️")]
        elif dow in (0, 1, 2):
            s["bloques"] = [("🐢 Versión suave",
                             "Mismos ejercicios con un 30-40 % menos de carga, RIR 3-4 y sin "
                             "llegar al fallo. Si un ejercicio molesta, sáltalo.")] + s["bloques"]
        else:
            s["bloques"] = [("🐢 Versión suave",
                             "Mismo tiempo, todo en Z1-Z2 (RPE 4-5) y sin bloques fuertes.")
                            ] + s["bloques"]
    elif cambio.modo in ("bici", "natacion"):
        m = s["minutos"] = minutos_alternativa(cambio.origen)
        original = s["titulo"]
        if cambio.modo == "bici":
            s["titulo"] = f"Bici Z2 (en lugar de: {original})"
            s["bloques"] = [("🚴 Bici", f"{m}' en Z2 (RPE 5-6, 85-95 rpm). Sin impacto.")]
        else:
            s["titulo"] = f"Natación (en lugar de: {original})"
            s["bloques"] = [("🏊 Natación", f"{m}': 300 calentamiento + series de 100-200 a "
                                           "RPE 6 con 20\" de descanso + 200 suave.")]
    return s


def _sesion_base(dia: date, ref: int | None = None) -> dict:
    """Sesión del plan para un día, sin cambios del usuario."""
    wn = semana_indice(dia)
    w = datos_semana(wn, ref)
    dow = dia.weekday()

    if w is None:
        return {
            "fecha": dia,
            "semana": wn,
            "fase": None,
            "fuera_de_plan": "pre",
            "titulo": "Fuera del plan",
            "bloques": [],
        }

    var = variante(wn)
    bloques = []
    if dow == 0:
        titulo = "Gimnasio · Pecho y tríceps"
        bloques.append(("🏋️ Gimnasio", gym.formatear("pecho", var)))
    elif dow == 1:
        titulo = "Gimnasio · Espalda y bíceps"
        bloques.append(("🏋️ Gimnasio", gym.formatear("espalda", var)))
    elif dow == 2:
        titulo = "Gimnasio · Pierna"
        bloques.append(("🏋️ Gimnasio", gym.formatear("pierna", var)))
        bloques.append(("🧘 Extra", "10' de movilidad de cadera y tobillo al terminar. "
                                   "La pierna va el miércoles para llegar fresco a la carrera "
                                   "del sábado."))
    elif dow == 3:
        titulo = "Natación"
        bloques.append(("🏊 Natación", w["nata"]))
        bloques.append(("🥤 Antes", "Tras el trabajo: merienda con hidratos 1-2 h antes de nadar."))
    elif dow == 4:
        titulo = "Descanso flexible"
        bloques.append(("😴 Descanso", "Día libre. Si esta semana has saltado algo, este es el "
                                      "hueco para recuperarlo (`/mover`). Si no, descansa, "
                                      "pasea o haz 10' de movilidad."))
    elif dow == 5:
        c = w["carrera"]
        titulo = f"Carrera · {c['tipo']} + Gimnasio · Hombro y core"
        bloques.append((f"🏃 Carrera · {c['tipo']}", c["texto"]))
        bloques.append(("🏋️ Gimnasio", gym.formatear("hombro", var)))
        orden = "Corre primero. Hombro y core después (o por la tarde): no cargan las piernas."
        if w["test"]:
            orden += " Tras el test, haz solo 2 series por ejercicio."
        bloques.append(("ℹ️ Orden", orden))
        if c["km"] >= 15:
            bloques.append(("🥤 Nutrición", "Pasas de 90': lleva agua y un gel o bebida "
                                           "isotónica a partir del minuto 45."))
    else:
        titulo = "Bici"
        bloques.append(("🚴 Bici", w["bici"]))
        bloques.append(("😴 Alternativa", "Si vienes muy cargado del sábado, rueda en Z1 o "
                                         "descansa. Es una opción válida."))

    if w["nota"] and dow == 0:
        bloques.append(("📌 Nota de la semana", w["nota"]))

    return {
        "fecha": dia, "semana": wn, "fase": w["fase"], "bloque": w["bloque"],
        "semana_bloque": w["semana_bloque"], "descarga": w["descarga"],
        "fuera_de_plan": None, "titulo": titulo, "bloques": bloques, "nota": w["nota"],
    }


def _cabecera_semana(week_num: int) -> str:
    w = datos_semana(week_num)
    return (f"Semana {week_num} · Bloque {w['bloque']} ({w['semana_bloque']}/{SEMANAS_BLOQUE})"
            f" · {FASES[w['fase']]}")


def resumen_semana(week_num: int, cambios: dict[date, Cambio] | None = None) -> str:
    w = datos_semana(week_num)
    if w is None:
        return "Esa semana está fuera del plan."
    lunes = fecha_lunes(week_num)
    cab = [
        f"📅 *Semana {week_num}* ({lunes.strftime('%d/%m')} - "
        f"{(lunes + timedelta(days=6)).strftime('%d/%m')})",
        f"_Bloque {w['bloque']} ({w['semana_bloque']}/{SEMANAS_BLOQUE}) · {FASES[w['fase']]}_"
        + ("  ·  ⚠️ *DESCARGA*" if w["descarga"] else ""),
        "",
    ]
    lineas = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        c = (cambios or {}).get(d)
        s = sesiones_dia(d, c)
        marca = MODOS.get(c.modo, "🔀") + " " if c else ""
        lineas.append(f"*{DIAS[i]}* — {marca}{s['titulo']}")
    if w["nota"]:
        lineas += ["", f"📌 _{w['nota']}_"]
    if cambios and any(lunes <= d <= lunes + timedelta(days=6) for d in cambios):
        lineas += ["", "_🔀 movida · ⏭️ saltada · ✂️ corta · 🐢 suave · 🔁 cambiada · /deshacer_"]
    return "\n".join(cab + lineas)


def resumen_bloque(week_num: int, ref: int | None = None) -> str:
    """Las 8 semanas del bloque de `week_num` con la carrera del sábado."""
    wn = max(1, week_num)
    b = bloque_de(wn)
    primera = (b - 1) * SEMANAS_BLOQUE + 1
    out = [f"🗺️ *Bloque {b}* · {FASES[min(b, len(FASES))]}", ""]
    for i in range(primera, primera + SEMANAS_BLOQUE):
        w = datos_semana(i, ref)
        c = w["carrera"]
        marca = " ⚠️" if w["descarga"] else ""
        actual = " 👈" if i == week_num else ""
        out.append(f"S{w['semana_bloque']} ({fecha_lunes(i).strftime('%d/%m')}) — "
                   f"{c['tipo']}, ~{c['km']:g} km{marca}{actual}")
    out += ["", "⚠️ = descarga · 👈 = esta semana",
            "_Cada bloque: 3 semanas de carga + 1 de descarga, dos veces. La semana 8 acaba "
            "con un test (5 km en bloques impares, 10 km en pares) que recalcula los ritmos. "
            "Luego empieza otro bloque con algo más de volumen._"]
    return "\n".join(out)


def formatear_dia(dia: date, cambio: Cambio | None = None, ref: int | None = None) -> str:
    s = sesiones_dia(dia, cambio, ref)
    if s["fuera_de_plan"]:
        faltan = (PLAN_START - dia).days
        return (f"El plan arranca el lunes {PLAN_START.strftime('%d/%m/%Y')} "
                f"(faltan {faltan} días). Usa /semana 1 para ver cómo empieza.")
    test, km = proximo_test(dia)
    if test == dia:
        linea_test = f"⏱️ Hoy toca test de {km} km"
    else:
        linea_test = f"⏱️ Próximo test de {km} km: sábado {test.strftime('%d/%m')}"
    cab = [
        f"*{DIAS[dia.weekday()]} {dia.strftime('%d/%m/%Y')}*",
        _cabecera_semana(s["semana"]) + ("  ⚠️ DESCARGA" if s.get("descarga") else ""),
        linea_test,
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


def tipos_dia(dia: date, cambio: Cambio | None = None) -> list[str]:
    """Categorías de entrenamiento de un día, para etiquetar en Notion."""
    if cambio:
        especiales = {"descanso": ["Descanso"], "bici": ["Bici"], "natacion": ["Natación"]}
        if cambio.modo in especiales and datos_semana(semana_indice(dia)):
            return especiales[cambio.modo]
        dia = cambio.origen
    if datos_semana(semana_indice(dia)) is None:
        return []
    return {
        0: ["Gimnasio"], 1: ["Gimnasio"], 2: ["Gimnasio"], 3: ["Natación"],
        4: ["Descanso"], 5: ["Carrera", "Gimnasio"], 6: ["Bici"],
    }[dia.weekday()]


def tabla_ritmos(ref: int | None = None, fuente: str = "") -> str:
    ref = ref or ritmo_ref()
    filas = [
        ("Regenerativo (Z1)", "z1", "RPE 3-4 · charlar sin esfuerzo"),
        ("Suave / rodaje (Z2)", "z2", "RPE 5 · base del plan"),
        ("Medio (Z3)", "medio", "RPE 6-7 · cómodo-duro"),
        ("Ritmo de media", "media", "RPE 8 · lo que aguantarías ~1h30"),
        ("Umbral (Z4)", "umbral", "RPE 8-9 · ~1h a tope"),
        ("VO2máx (Z5)", "vo2", "RPE 9-10 · series cortas"),
    ]
    out = ["🎯 *Ritmos de referencia*"]
    if fuente:
        out.append(f"_{fuente}_")
    out.append("")
    for nombre, zona, desc in filas:
        out.append(f"• *{nombre}*: `{_r(ref, zona)}`\n  _{desc}_")
    out += ["", "⏱️ *Predicción hoy*: " + " · ".join(
        f"{n} ≈ {tiempo_txt(tiempo_previsto(ref, km))}"
        for n, km in (("5 km", 5), ("10 km", 10), ("media", MEDIA_KM)))]
    out += ["", "🏊 *Natación*: Z2 = RPE 5-6 (respiras cómodo), Z3 = RPE 7 (puedes decir 3-4 palabras).",
            "🚴 *Bici*: Z1 = RPE 3-4, Z2 = RPE 5-6 (85-95 rpm), Z3 = RPE 7 (80-90 rpm).",
            "", "_Registra cada test con /test y los ritmos se recalculan solos._"]
    return "\n".join(out)


# ---------- cambios del usuario ----------

def minutos_texto(texto: str) -> int:
    m = re.search(r"(\d+)'", texto)
    return int(m.group(1)) if m else 0


def minutos_alternativa(origen: date) -> int:
    """Duración de la bici o natación que sustituye a la sesión de `origen`."""
    w = datos_semana(semana_indice(origen))
    if w is None:
        return 30
    m = {
        3: minutos_texto(w["nata"]),
        4: 30,
        5: w["carrera"]["km"] * 6 * 1.2,
        6: minutos_texto(w["bici"]) or 60,
    }.get(origen.weekday(), 60)
    return int(max(30, min(120, round(m / 5) * 5)))


def efectivo(cambios: dict[date, Cambio], dia: date) -> Cambio:
    return cambios.get(dia) or Cambio(dia)


def _normalizar(dia: date, c: Cambio) -> Cambio | None:
    return None if c.origen == dia and c.modo is None else c


def mover(cambios: dict[date, Cambio], a: date, b: date) -> dict[date, Cambio | None]:
    """Cambios a guardar para mover la sesión de `a` a `b`.
    Si `a` está saltada, recupera su sesión en `b` (sustituye a la de `b`).
    Si no, intercambia las sesiones de los dos días."""
    ea, eb = efectivo(cambios, a), efectivo(cambios, b)
    if ea.modo == "descanso":
        return {b: _normalizar(b, Cambio(ea.origen))}
    return {a: _normalizar(a, eb), b: _normalizar(b, ea)}


def cambiar(cambios: dict[date, Cambio], dia: date, modo: str | None) -> dict[date, Cambio | None]:
    return {dia: _normalizar(dia, Cambio(efectivo(cambios, dia).origen, modo))}


def deshacer(cambios: dict[date, Cambio], dia: date) -> dict[date, Cambio | None]:
    """Quita el cambio de `dia` y los días que hacen su sesión (el otro lado de un intercambio)."""
    borrar = {dia} | {d for d, c in cambios.items() if c.origen == dia}
    c = cambios.get(dia)
    if c and c.origen in cambios and cambios[c.origen].origen == dia:
        borrar.add(c.origen)
    return {d: None for d in borrar if d in cambios}


def aplicar(cambios: dict[date, Cambio], nuevos: dict[date, Cambio | None]) -> dict[date, Cambio]:
    out = {**cambios, **nuevos}
    return {d: c for d, c in out.items() if c is not None}


def carga_dia(dia: date, cambios: dict[date, Cambio]) -> str:
    """pierna | carrera | otra | ligera, según la sesión que se hace ese día."""
    c = efectivo(cambios, dia)
    if c.modo in ("descanso", "bici", "natacion"):
        return "ligera"
    carga = {2: "pierna", 4: "ligera", 5: "carrera"}.get(c.origen.weekday(), "otra")
    return "otra" if c.modo == "suave" and carga != "ligera" else carga


def avisos_semana(lunes: date, cambios: dict[date, Cambio]) -> list[str]:
    """Combinaciones poco recomendables tras los cambios de la semana."""
    if datos_semana(semana_indice(lunes)) is None:
        return []
    dias = [lunes + timedelta(days=i) for i in range(7)]
    carga = [carga_dia(d, cambios) for d in dias]
    out = []
    for i in range(6):
        nombres = f"{DIAS[i].lower()} y {DIAS[i + 1].lower()}"
        if carga[i] == "pierna" and carga[i + 1] == "carrera":
            out.append(f"Pierna justo antes de la carrera ({nombres}): llegarás con las "
                       "piernas cargadas.")
        elif carga[i] == "carrera" and carga[i + 1] == "pierna":
            out.append(f"Pierna justo después de la carrera ({nombres}): mejor dejar 48 h.")
    if "ligera" not in carga:
        out.append("Esta semana no te queda ningún día ligero o de descanso.")
    return out


def sugerir_recuperacion(cambios: dict[date, Cambio], saltado: date, desde: date) -> date | None:
    """Día de la semana (a partir de `desde`) más ligero para recuperar una sesión clave
    saltada sin generar avisos. None si no hay hueco razonable."""
    lunes = saltado - timedelta(days=saltado.weekday())
    orden = {"ligera": 0, "otra": 1}
    candidatos = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        if d < desde or d == saltado:
            continue
        carga = carga_dia(d, cambios)
        if carga not in orden:
            continue
        if not avisos_semana(lunes, aplicar(cambios, mover(cambios, saltado, d))):
            candidatos.append((orden[carga], abs((d - saltado).days), d))
    return min(candidatos)[2] if candidatos else None


# ---------- sensaciones (reglas sin IA) ----------

ZONAS_MOLESTIA = {
    "ninguna": "Sin molestias",
    "piernas": "Piernas (gemelo, rodilla, tobillo…)",
    "superior": "Espalda, hombro o brazos",
    "general": "Malestar general / enfermo",
}


def recomendar(dia: date, cambios: dict[date, Cambio], energia: int,
               zona: str) -> tuple[str | None, str]:
    """Modo a aplicar a `dia` según cómo te encuentras (energía 1-5 y zona de molestia).
    Devuelve (modo, o None si no hace falta tocar nada; explicación)."""
    c = efectivo(cambios, dia)
    if c.modo == "descanso" or (c.origen.weekday() == 4 and c.modo is None):
        return None, "Hoy ya es de descanso: aprovecha para recuperar."
    dow = c.origen.weekday()
    tren_superior = dow in (0, 1, 3)  # pecho, espalda, natación
    impacto = dow in (2, 5)           # pierna, carrera
    if zona == "general" or energia <= 1:
        return "descanso", "Con malestar general o sin energía, lo que más suma es descansar."
    if zona == "piernas":
        if impacto:
            return "natacion", "Con molestias en las piernas, mejor sin impacto: natación suave."
        if dow == 6:
            return "suave", "Bici muy suave en Z1: si la molestia aumenta, para."
        return None, "La sesión de hoy no carga las piernas: puedes hacerla con normalidad."
    if zona == "superior":
        if tren_superior:
            return "bici", "Con molestias en el tren superior, cambia a bici en Z2."
        if dow == 5:
            return None, "Corre con normalidad y sáltate la parte de hombro si molesta."
        return None, "La sesión de hoy apenas carga el tren superior: hazla sin forzar."
    if energia == 2:
        modo = "suave" if impacto else "corta"
        return modo, f"Poca energía: hoy versión {modo}."
    if energia == 3:
        return None, "Energía normal: haz la sesión y escucha al cuerpo. Si a mitad vas mal, recorta."
    return None, "¡Buenas sensaciones! Sesión completa."
