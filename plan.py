"""Plan cíclico de entrenamiento: gimnasio + carrera + natación + bici.

La semana tipo es una `Estructura`: qué lleva cada día (gimnasio de un grupo, carrera,
natación, bici o descanso). Cada usuario puede configurar la suya; por defecto
(`ESTRUCTURA_DEFECTO`, jornada de lunes a jueves de 8:00 a 17:30):
  Lunes      -> Gimnasio PECHO + TRÍCEPS
  Martes     -> Gimnasio ESPALDA + BÍCEPS
  Miércoles  -> Gimnasio PIERNA
  Jueves     -> NATACIÓN
  Viernes    -> DESCANSO flexible (hueco para recuperar una sesión movida o saltada)
  Sábado     -> CARRERA (la principal de la semana) + Gimnasio HOMBRO + CORE
  Domingo    -> BICI

No hay fecha final: el plan se repite en bloques de 8 semanas (3 de carga + 1 de
descarga, dos veces). El primer día de carrera de la semana lleva la sesión clave; en la
semana 8 es un test de 5 km (bloques impares) o 10 km (pares) que sirve para recalcular
los ritmos. Otros días de carrera son rodajes en Z2. El volumen de carrera crece de
bloque en bloque hasta `NIVEL_MAX`.

Las sesiones incluyen `entrenos`: la versión estructurada (pasos de carrera con zonas,
grupo de gimnasio, minutos de cardio) que se envía al reloj.

Los ritmos salen de `ref`: el ritmo equivalente de media maratón en s/km
(ver `ritmo_ref`). Sin `ref` se usa la marca de config más un margen por desentreno.

Cada usuario puede cambiar su semana (/mover, /saltar, /cambiar). Los cambios se
guardan fuera (SQLite) y llegan aquí como `Cambio`: el día hace la sesión base de
`origen` (dentro de la misma semana) en el `modo` indicado.
"""
import re
from dataclasses import dataclass, field
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


# ---------- estructura semanal ----------

TIPOS = {
    "gym": "Gimnasio",
    "carrera": "Carrera",
    "natacion": "Natación",
    "bici": "Bici",
    "descanso": "Descanso",
}

Item = tuple[str, str | None]  # (tipo, grupo de gimnasio o None)


@dataclass(frozen=True)
class Estructura:
    """Qué lleva cada día de la semana (0 = lunes) y las rutinas propias de gimnasio
    (`{grupo: {variante: {"titulo", "ejercicios", "filas"}}}`, ver `gym.formatear`)."""
    dias: tuple[tuple[Item, ...], ...]
    rutinas: dict = field(default_factory=dict, compare=False, hash=False)

    def items(self, dow: int) -> tuple[Item, ...]:
        return self.dias[dow] or (("descanso", None),)

    def tipos(self, dow: int) -> set[str]:
        tipos = {t for t, _ in self.items(dow)}
        return tipos - {"descanso"} or {"descanso"}

    def grupos(self, dow: int) -> list[str]:
        return [g for t, g in self.items(dow) if t == "gym" and g]

    def dia_carrera(self) -> int | None:
        """Día de la carrera principal (la primera de la semana)."""
        return next((i for i in range(7) if "carrera" in self.tipos(i)), None)

    def descanso(self, dow: int) -> bool:
        return self.tipos(dow) == {"descanso"}

    def pierna(self, dow: int) -> bool:
        return any(gym.zona(g) != "superior" for g in self.grupos(dow))


ESTRUCTURA_DEFECTO = Estructura((
    (("gym", "pecho"),),
    (("gym", "espalda"),),
    (("gym", "pierna"),),
    (("natacion", None),),
    (("descanso", None),),
    (("carrera", None), ("gym", "hombro")),
    (("bici", None),),
))


def _est(est: Estructura | None) -> Estructura:
    return est or ESTRUCTURA_DEFECTO


def avisos_estructura(est: Estructura) -> list[str]:
    """Problemas de una semana tipo (no impiden guardarla)."""
    out = []
    dc = est.dia_carrera()
    if dc is None:
        out.append("No hay ningún día de carrera: sin carrera no hay tests ni progresión de ritmos.")
    if not any(est.descanso(i) for i in range(7)):
        out.append("No hay ningún día de descanso.")
    for i in range(7):
        sig = (i + 1) % 7
        if est.pierna(i) and "carrera" in est.tipos(sig):
            out.append(f"Pierna el {DIAS[i].lower()} y carrera el {DIAS[sig].lower()}: "
                       "llegarás con las piernas cargadas.")
        if est.pierna(i) and "carrera" in est.tipos(i):
            out.append(f"Pierna y carrera el mismo día ({DIAS[i].lower()}): mucha carga.")
    return out


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


# Pasos estructurados de carrera (para el reloj):
#   ("calentamiento" | "intervalo" | "recuperacion" | "enfriamiento", (condición, valor), zona)
#   ("repetir", veces, [pasos])
# condición: "tiempo" (s), "distancia" (m) o "lap" (botón de vuelta). zona: clave de ZONAS o None.

def _paso(tipo: str, cond: str, valor: float = 0, zona: str | None = None) -> tuple:
    return (tipo, (cond, valor), zona)


def _rep(veces: int, *pasos) -> tuple:
    return ("repetir", veces, list(pasos))


def _progresivos(n: int) -> tuple:
    return _rep(n, _paso("intervalo", "tiempo", 20), _paso("recuperacion", "tiempo", 60))


def _km(km: float) -> float:
    return round(km * 1000)


def escalar_pasos(pasos: list, f: float) -> list:
    """Pasos con el volumen multiplicado por `f` (repeticiones, tiempos y distancias)."""
    out = []
    for p in pasos:
        if p[0] == "repetir":
            out.append(("repetir", max(1, round(p[1] * f)), p[2]))
        else:
            cond, valor = p[1]
            nuevo = valor if cond == "lap" or p[0] == "recuperacion" else round(valor * f)
            out.append((p[0], (cond, nuevo), p[2]))
    return out


def carrera_extra(ref: int, descarga: bool = False) -> dict:
    """Rodaje para los días de carrera que no son el principal de la semana."""
    m = 30 if descarga else 40
    return {"tipo": "Rodaje Z2", "km": round(m / 6, 1),
            "texto": f"{m}' en Z2 ({_r(ref, 'z2')}) + 4x20\" progresivos al acabar.",
            "pasos": [_paso("intervalo", "tiempo", m * 60, "z2"), _progresivos(4)]}


def _carrera(sb: int, bloque: int, ref: int) -> dict:
    """Sesión de carrera principal de la semana: {tipo, texto, km, pasos}.
    `sb` = semana dentro del bloque."""
    n = min(bloque, NIVEL_MAX) - 1
    z2, medio, media = _r(ref, "z2"), _r(ref, "medio"), _r(ref, "media")
    umbral, vo2 = _r(ref, "umbral"), _r(ref, "vo2")
    cal = _paso("calentamiento", "tiempo", 900, "z2")
    enf = _paso("enfriamiento", "tiempo", 600, "z2")
    if sb == 1:
        km = (8, 11, 13, 15)[n]
        if n == 0:
            return {"tipo": "Rodaje largo", "km": km,
                    "texto": f"{km} km en Z2 ({z2}) + 6x20\" progresivos al acabar.",
                    "pasos": [_paso("intervalo", "distancia", _km(km), "z2"), _progresivos(6)]}
        return {"tipo": "Rodaje largo", "km": km,
                "texto": f"{km} km en Z2 ({z2}). Los 2 últimos a ritmo medio ({medio}).",
                "pasos": [_paso("intervalo", "distancia", _km(km - 2), "z2"),
                          _paso("intervalo", "distancia", _km(2), "medio")]}
    if sb == 2:
        if n == 0:
            return {"tipo": "Fartlek", "km": 7.5, "texto":
                    f"Rodaje 40': 10' en Z2 ({z2}) + 8x(1' a umbral {umbral} / 1' trote) "
                    "+ 10' en Z2.",
                    "pasos": [_paso("calentamiento", "tiempo", 600, "z2"),
                              _rep(8, _paso("intervalo", "tiempo", 60, "umbral"),
                                   _paso("recuperacion", "tiempo", 60)), enf]}
        reps, mins = ((3, 8), (3, 10), (2, 15))[n - 1]
        total = 25 + reps * mins + (reps - 1) * 2
        return {"tipo": "Tempo", "km": round(total / 5.3, 1), "texto":
                f"15' en Z2 ({z2}) + {reps}x{mins}' a umbral ({umbral}) con 2' de trote "
                "+ 10' en Z2.",
                "pasos": [cal, _rep(reps, _paso("intervalo", "tiempo", mins * 60, "umbral"),
                                    _paso("recuperacion", "tiempo", 120)), enf]}
    if sb == 3:
        km, fin = ((10, 0), (13, 3), (15, 4), (17, 5))[n]
        if not fin:
            return {"tipo": "Tirada larga", "km": km,
                    "texto": f"{km} km en Z2 ({z2}). Sin mirar el reloj: que sea cómodo.",
                    "pasos": [_paso("intervalo", "distancia", _km(km), "z2")]}
        return {"tipo": "Tirada larga", "km": km,
                "texto": f"{km} km: los {km - fin} primeros en Z2 ({z2}) y los {fin} últimos "
                         f"a ritmo de media ({media}).",
                "pasos": [_paso("intervalo", "distancia", _km(km - fin), "z2"),
                          _paso("intervalo", "distancia", _km(fin), "media")]}
    if sb == 4:
        return {"tipo": "Rodaje suave", "km": 7, "texto":
                f"Rodaje 40' en Z2 ({z2}) + 4x20\" progresivos. Descarga: sin forzar.",
                "pasos": [_paso("intervalo", "tiempo", 2400, "z2"), _progresivos(4)]}
    if sb == 5:
        if n == 0:
            return {"tipo": "Series", "km": 8.5, "texto":
                    f"Rodaje 45': 12' en Z2 ({z2}) + 10x(1' a umbral {umbral} / 1' trote) "
                    "+ 10' en Z2.",
                    "pasos": [_paso("calentamiento", "tiempo", 720, "z2"),
                              _rep(10, _paso("intervalo", "tiempo", 60, "umbral"),
                                   _paso("recuperacion", "tiempo", 60)), enf]}
        (veces, metros), rec, rec_s, km = (((5, 1000), "2'", 120, 9), ((6, 1000), "2'", 120, 10),
                                           ((8, 800), "90\"", 90, 10))[n - 1]
        return {"tipo": "Series", "km": km, "texto":
                f"15' en Z2 ({z2}) + {veces}x{metros} m a ritmo VO2 ({vo2}) con {rec} de trote "
                "+ 10' en Z2.",
                "pasos": [cal, _rep(veces, _paso("intervalo", "distancia", metros, "vo2"),
                                    _paso("recuperacion", "tiempo", rec_s)), enf]}
    if sb == 6:
        km = (11, 14, 16, 18)[n]
        if n == 0:
            return {"tipo": "Larga progresiva", "km": km,
                    "texto": f"{km} km: los {km - 3} primeros en Z2 ({z2}) y los 3 últimos a "
                             f"ritmo medio ({medio}).",
                    "pasos": [_paso("intervalo", "distancia", _km(km - 3), "z2"),
                              _paso("intervalo", "distancia", _km(3), "medio")]}
        tercio = round(km / 3)
        return {"tipo": "Larga progresiva", "km": km,
                "texto": f"{km} km progresivos: un tercio en Z2 ({z2}), otro a ritmo medio "
                         f"({medio}) y el último a ritmo de media ({media}).",
                "pasos": [_paso("intervalo", "distancia", _km(tercio), "z2"),
                          _paso("intervalo", "distancia", _km(tercio), "medio"),
                          _paso("intervalo", "distancia", _km(km - 2 * tercio), "media")]}
    if sb == 7:
        if n == 0:
            return {"tipo": "Ritmo medio", "km": 9, "texto":
                    f"Rodaje 50': 15' en Z2 ({z2}) + 3x6' a ritmo medio ({medio}) con 2' "
                    "de trote + 10' en Z2.",
                    "pasos": [cal, _rep(3, _paso("intervalo", "tiempo", 360, "medio"),
                                        _paso("recuperacion", "tiempo", 120)), enf]}
        km, reps, dist = ((13, 2, 3), (15, 3, 3), (17, 3, 4))[n - 1]
        resto = km - 4 - reps * dist
        return {"tipo": "Ritmo de media", "km": km, "texto":
                f"{km} km: 4 en Z2 ({z2}) + {reps}x{dist} km a ritmo de media ({media}) "
                "con 3' de trote + el resto en Z2.",
                "pasos": [_paso("calentamiento", "distancia", _km(4), "z2"),
                          _rep(reps, _paso("intervalo", "distancia", _km(dist), "media"),
                               _paso("recuperacion", "tiempo", 180)),
                          _paso("enfriamiento", "distancia", _km(resto), "z2") if resto > 0
                          else _paso("enfriamiento", "lap")]}
    d = km_test(bloque)
    salida = pace(round(tiempo_previsto(ref, d) / d))
    return {"tipo": f"Test {d} km", "km": d + 5, "texto":
            f"15' en Z2 + 4x20\" progresivos + {d} km a tope y lo más uniforme posible "
            f"(sal a {salida} /km y aprieta en el último km) + 10' muy suave. "
            f"Después registra el tiempo: `/test {d}k mm:ss`.",
            "pasos": [cal, _progresivos(4), _paso("intervalo", "distancia", _km(d)),
                      _paso("enfriamiento", "tiempo", 600, "z1")]}


def semana_indice(dia: date) -> int:
    """Número de semana del plan (1-based) para una fecha dada."""
    return (dia - PLAN_START).days // 7 + 1


def bloque_de(week_num: int) -> int:
    return (week_num - 1) // SEMANAS_BLOQUE + 1


def semana_en_bloque(week_num: int) -> int:
    return (week_num - 1) % SEMANAS_BLOQUE + 1


def variante(week_num: int) -> str:
    return "A" if week_num % 2 == 1 else "B"


def datos_semana(week_num: int, ref: int | None = None,
                 est: Estructura | None = None) -> dict | None:
    if week_num < 1:
        return None
    est = _est(est)
    ref = ref or ritmo_ref()
    b, sb = bloque_de(week_num), semana_en_bloque(week_num)
    descarga = sb in SEMANAS_DESCARGA
    dc = est.dia_carrera()
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
    if sb == SEMANAS_BLOQUE and dc is not None:
        previo = (dc - 1) % 7
        antes = "descanso de verdad" if est.descanso(previo) else "suave (o descanso)"
        notas.append(f"El {DIAS[dc].lower()} toca test de {km_test(b)} km: que el "
                     f"{DIAS[previo].lower()} sea {antes}. Con el resultado se recalculan "
                     "tus ritmos.")
    return {
        "fase": min(b, len(FASES)),
        "bloque": b,
        "semana_bloque": sb,
        "descarga": descarga,
        "test": sb == SEMANAS_BLOQUE and dc is not None,
        "carrera": _carrera(sb, b, ref),
        "nata": NATA[nata],
        "bici": BICI[bici],
        "nota": " ".join(notas),
    }


def fecha_lunes(week_num: int) -> date:
    return PLAN_START + timedelta(weeks=week_num - 1)


def proximo_test(dia: date, est: Estructura | None = None) -> tuple[date, int] | None:
    """Fecha y distancia (km) del próximo test a partir de `dia` (incluido).
    None si la semana tipo no tiene carrera."""
    dc = _est(est).dia_carrera()
    if dc is None:
        return None
    wn = max(1, semana_indice(dia))
    b = bloque_de(wn)
    fecha = fecha_lunes(b * SEMANAS_BLOQUE) + timedelta(days=dc)
    if dia > fecha:
        b += 1
        fecha += timedelta(weeks=SEMANAS_BLOQUE)
    return fecha, km_test(b)


def carrera_de(dia: date, ref: int | None = None, est: Estructura | None = None) -> dict | None:
    """Carrera que toca en `dia` según la semana tipo (sin cambios), o None."""
    est = _est(est)
    w = datos_semana(semana_indice(dia), ref, est)
    if w is None or "carrera" not in est.tipos(dia.weekday()):
        return None
    if dia.weekday() == est.dia_carrera():
        return w["carrera"]
    return carrera_extra(ref or ritmo_ref(), w["descarga"])


def minutos_texto(texto: str) -> int:
    m = re.search(r"(\d+)'", texto)
    return int(m.group(1)) if m else 0


def _entreno_gym(grupo: str, var: str, descarga: bool) -> dict:
    return {"tipo": "gym", "grupo": grupo, "variante": var, "series_menos": 1 if descarga else 0,
            "carga": 1.0}


def sesiones_dia(dia: date, cambio: Cambio | None = None, ref: int | None = None,
                 est: Estructura | None = None) -> dict:
    """Sesión completa de un día, aplicando el cambio del usuario si lo hay."""
    est = _est(est)
    if cambio is None or (cambio.origen == dia and cambio.modo is None):
        return _sesion_base(dia, ref, est)
    s = dict(_sesion_base(cambio.origen, ref, est))
    s["fecha"], s["origen"], s["modo"] = dia, cambio.origen, cambio.modo
    if s["fuera_de_plan"]:
        return s
    if cambio.origen != dia:
        s["titulo"] += f" (movida del {DIAS[cambio.origen.weekday()].lower()})"
    tipos = est.tipos(cambio.origen.weekday())
    entrenos = [dict(e) for e in s["entrenos"]]
    if cambio.modo == "descanso":
        s["titulo"] = f"Descanso (saltas: {s['titulo']})"
        s["bloques"] = [("😴 Descanso", "Hoy no entrenas. Si te apetece, paseo o 10' de movilidad.")]
        entrenos = []
    elif cambio.modo == "corta":
        s["titulo"] += " · versión corta"
        s["bloques"] = [("✂️ Versión corta",
                         "Haz la mitad del volumen: mitad de minutos, km o repeticiones de "
                         "carrera, y 1-2 series menos por ejercicio de gimnasio. "
                         "Mantén los ritmos e intensidades.")] + s["bloques"]
        for e in entrenos:
            if e["tipo"] == "carrera":
                e["pasos"] = escalar_pasos(e["pasos"], 0.5)
            elif e["tipo"] == "gym":
                e["series_menos"] += 1
            else:
                e["minutos"] = max(15, round(e["minutos"] / 2))
    elif cambio.modo == "suave":
        s["titulo"] += " · versión suave"
        if "carrera" in tipos:
            c = carrera_de(cambio.origen, ref, est)
            m = int(max(30, min(60, round(c["km"] * 6 * 0.7 / 5) * 5)))
            s["bloques"] = [("🐢 Rodaje suave",
                             f"{m}' en Z2 ({_r(ref or ritmo_ref(), 'z2')}) sin series ni cambios "
                             "de ritmo. Si algo duele al correr, para y cambia a bici.")] + [
                b for b in s["bloques"] if b[0].startswith("🏋️")]
            for e in entrenos:
                if e["tipo"] == "carrera":
                    e["nombre"] = "Rodaje suave"
                    e["pasos"] = [_paso("intervalo", "tiempo", m * 60, "z2")]
        elif "gym" in tipos:
            s["bloques"] = [("🐢 Versión suave",
                             "Mismos ejercicios con un 30-40 % menos de carga, RIR 3-4 y sin "
                             "llegar al fallo. Si un ejercicio molesta, sáltalo.")] + s["bloques"]
        else:
            s["bloques"] = [("🐢 Versión suave",
                             "Mismo tiempo, todo en Z1-Z2 (RPE 4-5) y sin bloques fuertes.")
                            ] + s["bloques"]
        for e in entrenos:
            if e["tipo"] == "gym":
                e["carga"] = 0.65
            elif e["tipo"] in ("natacion", "bici"):
                e["texto"] = "Todo en Z1-Z2 (RPE 4-5), sin bloques fuertes."
    elif cambio.modo in ("bici", "natacion"):
        m = s["minutos"] = minutos_alternativa(cambio.origen, est)
        original = s["titulo"]
        if cambio.modo == "bici":
            s["titulo"] = f"Bici Z2 (en lugar de: {original})"
            s["bloques"] = [("🚴 Bici", f"{m}' en Z2 (RPE 5-6, 85-95 rpm). Sin impacto.")]
        else:
            s["titulo"] = f"Natación (en lugar de: {original})"
            s["bloques"] = [("🏊 Natación", f"{m}': 300 calentamiento + series de 100-200 a "
                                           "RPE 6 con 20\" de descanso + 200 suave.")]
        entrenos = [{"tipo": cambio.modo, "minutos": m, "texto": s["bloques"][0][1]}]
    s["entrenos"] = entrenos
    return s


def _sesion_base(dia: date, ref: int | None = None, est: Estructura | None = None) -> dict:
    """Sesión del plan para un día, sin cambios del usuario."""
    est = _est(est)
    wn = semana_indice(dia)
    w = datos_semana(wn, ref, est)
    dow = dia.weekday()

    if w is None:
        return {
            "fecha": dia,
            "semana": wn,
            "fase": None,
            "fuera_de_plan": "pre",
            "titulo": "Fuera del plan",
            "bloques": [],
            "entrenos": [],
        }

    var = variante(wn)
    tipos = est.tipos(dow)
    dc = est.dia_carrera()
    titulos, bloques, entrenos = [], [], []
    c = carrera_de(dia, ref, est)
    for tipo, grupo in est.items(dow):
        if tipo == "gym" and grupo:
            titulos.append(f"Gimnasio · {gym.titulo(grupo)}")
            bloques.append(("🏋️ Gimnasio", gym.formatear(grupo, var, est.rutinas.get(grupo))))
            entrenos.append(_entreno_gym(grupo, var, w["descarga"]))
            if gym.zona(grupo) == "pierna" and "carrera" not in tipos:
                extra = "10' de movilidad de cadera y tobillo al terminar."
                if dc is not None and dc - dow >= 2:
                    extra += (f" La pierna va el {DIAS[dow].lower()} para llegar fresco a la "
                              f"carrera del {DIAS[dc].lower()}.")
                bloques.append(("🧘 Extra", extra))
        elif tipo == "natacion":
            titulos.append("Natación")
            bloques.append(("🏊 Natación", w["nata"]))
            if dow <= 3:
                bloques.append(("🥤 Antes", "Tras el trabajo: merienda con hidratos 1-2 h antes "
                                           "de nadar."))
            entrenos.append({"tipo": "natacion", "minutos": minutos_texto(w["nata"]) or 45,
                             "texto": w["nata"]})
        elif tipo == "carrera" and c:
            titulos.append(f"Carrera · {c['tipo']}")
            bloques.append((f"🏃 Carrera · {c['tipo']}", c["texto"]))
            entrenos.append({"tipo": "carrera", "nombre": c["tipo"], "km": c["km"],
                             "pasos": c["pasos"], "texto": c["texto"]})
        elif tipo == "bici":
            titulos.append("Bici")
            bloques.append(("🚴 Bici", w["bici"]))
            previo = (dow - 1) % 7
            cargado = (f"Si vienes muy cargado del {DIAS[previo].lower()}"
                       if "carrera" in est.tipos(previo) else "Si vienes muy cargado")
            bloques.append(("😴 Alternativa", f"{cargado}, rueda en Z1 o descansa. Es una "
                                             "opción válida."))
            entrenos.append({"tipo": "bici", "minutos": minutos_texto(w["bici"]) or 60,
                             "texto": w["bici"]})
    if tipos == {"descanso"}:
        titulos.append("Descanso flexible")
        bloques.append(("😴 Descanso", "Día libre. Si esta semana has saltado algo, este es el "
                                      "hueco para recuperarlo (`/mover`). Si no, descansa, "
                                      "pasea o haz 10' de movilidad."))
    if c and est.grupos(dow):
        nombres = " y ".join(gym.titulo(g) for g in est.grupos(dow))
        if est.pierna(dow):
            orden = (f"Corre primero. {nombres} después (o por la tarde). Ojo: pierna y "
                     "carrera el mismo día cargan mucho; baja el volumen si vas justo.")
        else:
            orden = f"Corre primero. {nombres} después (o por la tarde): no cargan las piernas."
        if w["test"] and dow == dc:
            orden += " Tras el test, haz solo 2 series por ejercicio."
        bloques.append(("ℹ️ Orden", orden))
    if c and c["km"] >= 15:
        bloques.append(("🥤 Nutrición", "Pasas de 90': lleva agua y un gel o bebida "
                                       "isotónica a partir del minuto 45."))

    if w["nota"] and dow == 0:
        bloques.append(("📌 Nota de la semana", w["nota"]))

    return {
        "fecha": dia, "semana": wn, "fase": w["fase"], "bloque": w["bloque"],
        "semana_bloque": w["semana_bloque"], "descarga": w["descarga"],
        "fuera_de_plan": None, "titulo": " + ".join(titulos), "bloques": bloques,
        "nota": w["nota"], "entrenos": entrenos,
    }


def _cabecera_semana(week_num: int) -> str:
    w = datos_semana(week_num)
    return (f"Semana {week_num} · Bloque {w['bloque']} ({w['semana_bloque']}/{SEMANAS_BLOQUE})"
            f" · {FASES[w['fase']]}")


def resumen_semana(week_num: int, cambios: dict[date, Cambio] | None = None,
                   est: Estructura | None = None) -> str:
    w = datos_semana(week_num, None, est)
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
        s = sesiones_dia(d, c, None, est)
        marca = MODOS.get(c.modo, "🔀") + " " if c else ""
        lineas.append(f"*{DIAS[i]}* — {marca}{s['titulo']}")
    if w["nota"]:
        lineas += ["", f"📌 _{w['nota']}_"]
    if cambios and any(lunes <= d <= lunes + timedelta(days=6) for d in cambios):
        lineas += ["", "_🔀 movida · ⏭️ saltada · ✂️ corta · 🐢 suave · 🔁 cambiada · /deshacer_"]
    return "\n".join(cab + lineas)


def resumen_bloque(week_num: int, ref: int | None = None) -> str:
    """Las 8 semanas del bloque de `week_num` con su carrera principal."""
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


def formatear_dia(dia: date, cambio: Cambio | None = None, ref: int | None = None,
                  est: Estructura | None = None) -> str:
    s = sesiones_dia(dia, cambio, ref, est)
    if s["fuera_de_plan"]:
        faltan = (PLAN_START - dia).days
        return (f"El plan arranca el lunes {PLAN_START.strftime('%d/%m/%Y')} "
                f"(faltan {faltan} días). Usa /semana 1 para ver cómo empieza.")
    pt = proximo_test(dia, est)
    if pt is None:
        lineas_test = []
    elif pt[0] == dia:
        lineas_test = [f"⏱️ Hoy toca test de {pt[1]} km"]
    else:
        lineas_test = [f"⏱️ Próximo test de {pt[1]} km: {DIAS[pt[0].weekday()].lower()} "
                       f"{pt[0].strftime('%d/%m')}"]
    cab = [
        f"*{DIAS[dia.weekday()]} {dia.strftime('%d/%m/%Y')}*",
        _cabecera_semana(s["semana"]) + ("  ⚠️ DESCARGA" if s.get("descarga") else ""),
        *lineas_test,
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


def tipos_dia(dia: date, cambio: Cambio | None = None,
              est: Estructura | None = None) -> list[str]:
    """Categorías de entrenamiento de un día, para etiquetar en Notion."""
    est = _est(est)
    if cambio:
        especiales = {"descanso": ["Descanso"], "bici": ["Bici"], "natacion": ["Natación"]}
        if cambio.modo in especiales and datos_semana(semana_indice(dia)):
            return especiales[cambio.modo]
        dia = cambio.origen
    if datos_semana(semana_indice(dia)) is None:
        return []
    out = []
    for t, _ in est.items(dia.weekday()):
        if TIPOS[t] not in out and (t != "descanso" or est.descanso(dia.weekday())):
            out.append(TIPOS[t])
    return out


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

def minutos_alternativa(origen: date, est: Estructura | None = None) -> int:
    """Duración de la bici o natación que sustituye a la sesión de `origen`."""
    est = _est(est)
    w = datos_semana(semana_indice(origen), None, est)
    if w is None:
        return 30
    tipos = est.tipos(origen.weekday())
    if "carrera" in tipos:
        m = carrera_de(origen, None, est)["km"] * 6 * 1.2
    elif "natacion" in tipos:
        m = minutos_texto(w["nata"])
    elif "bici" in tipos:
        m = minutos_texto(w["bici"]) or 60
    elif tipos == {"descanso"}:
        m = 30
    else:
        m = 60
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


def carga_dia(dia: date, cambios: dict[date, Cambio], est: Estructura | None = None) -> str:
    """pierna | carrera | otra | ligera, según la sesión que se hace ese día."""
    est = _est(est)
    c = efectivo(cambios, dia)
    if c.modo in ("descanso", "bici", "natacion"):
        return "ligera"
    dow = c.origen.weekday()
    if "carrera" in est.tipos(dow):
        carga = "carrera"
    elif est.pierna(dow):
        carga = "pierna"
    elif est.descanso(dow):
        carga = "ligera"
    else:
        carga = "otra"
    return "otra" if c.modo == "suave" and carga != "ligera" else carga


def avisos_semana(lunes: date, cambios: dict[date, Cambio],
                  est: Estructura | None = None) -> list[str]:
    """Combinaciones poco recomendables tras los cambios de la semana."""
    if datos_semana(semana_indice(lunes)) is None:
        return []
    dias = [lunes + timedelta(days=i) for i in range(7)]
    carga = [carga_dia(d, cambios, est) for d in dias]
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


def sugerir_recuperacion(cambios: dict[date, Cambio], saltado: date, desde: date,
                         est: Estructura | None = None) -> date | None:
    """Día de la semana (a partir de `desde`) más ligero para recuperar una sesión clave
    saltada sin generar avisos. None si no hay hueco razonable."""
    lunes = saltado - timedelta(days=saltado.weekday())
    orden = {"ligera": 0, "otra": 1}
    candidatos = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        if d < desde or d == saltado:
            continue
        carga = carga_dia(d, cambios, est)
        if carga not in orden:
            continue
        if not avisos_semana(lunes, aplicar(cambios, mover(cambios, saltado, d)), est):
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
               zona: str, est: Estructura | None = None) -> tuple[str | None, str]:
    """Modo a aplicar a `dia` según cómo te encuentras (energía 1-5 y zona de molestia).
    Devuelve (modo, o None si no hace falta tocar nada; explicación)."""
    est = _est(est)
    c = efectivo(cambios, dia)
    dow = c.origen.weekday()
    tipos = est.tipos(dow)
    if c.modo == "descanso" or (tipos == {"descanso"} and c.modo is None):
        return None, "Hoy ya es de descanso: aprovecha para recuperar."
    grupos = est.grupos(dow)
    superior = [g for g in grupos if gym.zona(g) != "pierna"]
    impacto = "carrera" in tipos or est.pierna(dow)
    tren_superior = "carrera" not in tipos and ("natacion" in tipos or bool(superior))
    if zona == "general" or energia <= 1:
        return "descanso", "Con malestar general o sin energía, lo que más suma es descansar."
    if zona == "piernas":
        if impacto:
            return "natacion", "Con molestias en las piernas, mejor sin impacto: natación suave."
        if "bici" in tipos:
            return "suave", "Bici muy suave en Z1: si la molestia aumenta, para."
        return None, "La sesión de hoy no carga las piernas: puedes hacerla con normalidad."
    if zona == "superior":
        if tren_superior:
            return "bici", "Con molestias en el tren superior, cambia a bici en Z2."
        if "carrera" in tipos and superior:
            return None, (f"Corre con normalidad y sáltate la parte de "
                          f"{' y '.join(superior)} si molesta.")
        return None, "La sesión de hoy apenas carga el tren superior: hazla sin forzar."
    if energia == 2:
        modo = "suave" if impacto else "corta"
        return modo, f"Poca energía: hoy versión {modo}."
    if energia == 3:
        return None, "Energía normal: haz la sesión y escucha al cuerpo. Si a mitad vas mal, recorta."
    return None, "¡Buenas sensaciones! Sesión completa."
