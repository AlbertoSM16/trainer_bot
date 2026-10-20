"""Consejo diario de carrera y bici sin plan fijo.

Decide qué entrenamiento hacer hoy a partir de lo entrenado (actividades del reloj), el estado
de forma (readiness, HRV, sueño) y las sensaciones. Objetivo de carrera: 10 km y media maratón
con reparto ~80/20 (la mayoría suave, 2 sesiones duras por semana entre carrera y bici).
Puro: sin I/O.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

import alimentos
import entrenador
import plan
from plan import _paso, _progresivos, _r, _rep

# Zonas de bici en % de la FC de umbral de bici (Friel)
ZONAS_BICI = {
    "b1": (0.65, 0.80, "Z1"),
    "b2": (0.81, 0.89, "Z2"),
    "b3": (0.90, 0.93, "Z3"),
    "b4": (0.94, 0.99, "Z4"),
    "b5": (1.00, 1.04, "Z5"),
}
UMBRAL_BICI_MENOS = 5          # la FC de umbral en bici suele ser algo más baja que corriendo
DURA_FC = 0.93                # FC media ≥ 93 % del umbral en toda la sesión = dura
MAX_DURAS_SEMANA = 2           # sesiones duras en 7 días (carrera + bici)
LARGA_MIN = 75                 # una carrera de 75' o más cuenta como tirada larga
LARGA_KM = 16
LARGA_BICI_MIN = 110
LARGA_TOPE = 105               # tirada larga como mucho 1h45'
CALIDADES_CARRERA = ("umbral", "vo2", "media")
CALIDADES_BICI = ("tempo", "umbral", "vo2")
ZONA_PIERNAS = ("pierna", "gemelo", "rodilla", "tobillo", "isquio", "cuadriceps", "fascia",
                "cadera", "pie", "aquiles", "tibia", "periostio")
ZONA_GENERAL = ("malestar", "enfermo", "fiebre", "resfriado", "gripe", "catarro", "covid")


@dataclass
class Consejo:
    deporte: str                    # carrera | bici
    clave: str                      # descanso | hecho | recuperacion | rodaje | larga | umbral…
    titulo: str
    texto: str
    motivo: str
    alternativa: str = ""
    minutos: int = 0
    km: float = 0
    pasos: list = field(default_factory=list)

    @property
    def para_reloj(self) -> bool:
        return bool(self.pasos)

    def datos(self) -> dict:
        return {"deporte": self.deporte, "clave": self.clave, "titulo": self.titulo,
                "texto": self.texto, "motivo": self.motivo, "alternativa": self.alternativa,
                "minutos": self.minutos, "km": self.km, "pasos": self.pasos}


# ---------- lectura de lo entrenado ----------

def minutos(a: dict) -> float:
    return (a.get("duracion_s") or 0) / 60


def km(a: dict) -> float:
    return (a.get("distancia_m") or 0) / 1000


def larga(a: dict) -> bool:
    if a.get("tipo") == "carrera":
        return minutos(a) >= LARGA_MIN or km(a) >= LARGA_KM
    if a.get("tipo") == "bici":
        return minutos(a) >= LARGA_BICI_MIN
    return False


def dura(a: dict, umbral: int | None = None) -> bool:
    """Sesión intensa de carrera o bici (por Training Effect o pulso medio cerca del umbral)."""
    if a.get("tipo") not in ("carrera", "bici") or larga(a):
        return False
    if (a.get("te_anaerobico") or 0) >= 2.0 or (a.get("te_aerobico") or 0) >= 4.0:
        return True
    ref = umbral - (UMBRAL_BICI_MENOS if a.get("tipo") == "bici" else 0) if umbral else None
    return bool(ref and a.get("fc_media") and minutos(a) >= 20 and a["fc_media"] >= DURA_FC * ref)


def pierna(a: dict) -> bool:
    """Gimnasio con trabajo de pierna (series del reloj de sentadilla, zancada, peso muerto…)."""
    if a.get("tipo") != "gym":
        return False
    n = sum(1 for s in a.get("series") or []
            if entrenador.GRUPO_DE_CATEGORIA.get(s.get("categoria")) == "pierna")
    return n >= 3


def calidad_hecha(a: dict, consejo_del_dia: dict | None, umbral: int | None = None) -> str | None:
    """Tipo de calidad de una sesión dura: lo que se aconsejó ese día o, si no, por el TE."""
    if not dura(a, umbral):
        return None
    tipos = CALIDADES_CARRERA if a["tipo"] == "carrera" else CALIDADES_BICI
    if consejo_del_dia and consejo_del_dia.get("clave") in tipos:
        return consejo_del_dia["clave"]
    return "vo2" if (a.get("te_anaerobico") or 0) >= 2.5 else "umbral"


@dataclass
class Estado:
    hoy: date
    actividades: list[dict]            # 14+ días, cualquier orden
    bienestar: dict = field(default_factory=dict)
    energia: int | None = None
    molestia: str = "ninguna"          # ninguna | piernas | superior | general
    umbral: int | None = None          # FC de umbral de lactato (carrera), medida o puesta
    umbral_zonas: int | None = None    # la de arriba o una estimación, solo para zonas de bici
    consejos: dict = field(default_factory=dict)   # {(fecha, deporte): datos del consejo}

    def _de(self, tipo: str | None = None, desde: int = 14, hasta: int = 0) -> list[dict]:
        ini, fin = self.hoy - timedelta(days=desde), self.hoy - timedelta(days=hasta)
        return [a for a in self.actividades if (tipo is None or a.get("tipo") == tipo)
                and ini.isoformat() <= a["fecha"] <= fin.isoformat()]

    def hoy_hecho(self, tipo: str) -> list[dict]:
        return self._de(tipo, 0, 0)

    def duras(self, dias: int = 6) -> list[dict]:
        return [a for a in self._de(None, dias) if dura(a, self.umbral)]

    def dias_desde(self, cond) -> int | None:
        fechas = [a["fecha"] for a in self.actividades if a["fecha"] <= self.hoy.isoformat()
                  and cond(a)]
        return (self.hoy - date.fromisoformat(max(fechas))).days if fechas else None

    def ayer(self, cond) -> bool:
        return any(cond(a) for a in self._de(None, 1, 1))

    def km_semana(self, atras: int = 0) -> float:
        return sum(km(a) for a in self._de("carrera", 6 + 7 * atras, 7 * atras))

    def rodaje_min(self) -> int:
        ms = [minutos(a) for a in self._de("carrera") if not larga(a) and not dura(a, self.umbral)]
        return int(min(60, max(30, 5 * round(median(ms) / 5)))) if ms else 40

    def ultima_larga_min(self) -> int | None:
        ls = sorted((a for a in self.actividades if a.get("tipo") == "carrera" and larga(a)),
                    key=lambda a: a["fecha"])
        return round(minutos(ls[-1])) if ls else None

    def bici_min(self) -> int:
        ms = [minutos(a) for a in self._de("bici", 21) if not dura(a, self.umbral)]
        return int(min(120, max(45, 5 * round(median(ms) / 5)))) if ms else 60

    def ultima_calidad(self, deporte: str) -> dict[str, int]:
        """Días desde la última sesión de cada tipo de calidad (sin hacer = 99)."""
        tipos = CALIDADES_CARRERA if deporte == "carrera" else CALIDADES_BICI
        out = {t: 99 for t in tipos}
        for a in self.actividades:
            if a.get("tipo") != deporte or not dura(a, self.umbral):
                continue
            t = calidad_hecha(a, self.consejos.get((a["fecha"], deporte)), self.umbral)
            if t in out:
                out[t] = min(out[t], (self.hoy - date.fromisoformat(a["fecha"])).days)
        return out

    def nivel(self, deporte: str, clave: str, dias: int = 42) -> int:
        """Sesiones de ese tipo hechas en las últimas semanas (para progresar el volumen)."""
        n = 0
        for a in self._de(deporte, dias, 1):
            if calidad_hecha(a, self.consejos.get((a["fecha"], deporte)), self.umbral) == clave:
                n += 1
        return n


def zona_molestia(texto: str | None) -> str:
    t = alimentos.normalizar(texto or "")
    if not t or t.startswith("sin molestias") or t == "ninguna":
        return "ninguna"
    if any(p in t for p in ZONA_GENERAL):
        return "general"
    if any(p in t for p in ZONA_PIERNAS):
        return "piernas"
    return "superior"


def _cansancio(e: Estado) -> str | None:
    """Motivo para ir suave hoy según el estado de forma, o None."""
    b = e.bienestar
    r, hrv, sueno = b.get("readiness"), b.get("hrv_estado"), b.get("sueno_h")
    if r is not None and r < 50:
        return f"Readiness {r}"
    if hrv in ("LOW", "POOR"):
        return f"HRV {entrenador.ESTADO_HRV.get(hrv, hrv.lower())}"
    if sueno is not None and sueno < 5.5:
        return f"Solo {sueno:.1f} h de sueño"
    if e.energia is not None and e.energia <= 2:
        return f"Energía {e.energia}/5"
    return None


def _parar(e: Estado) -> str | None:
    """Motivo para no entrenar hoy, o None."""
    r = e.bienestar.get("readiness")
    if e.molestia == "general":
        return "Malestar general: hoy descanso total"
    if r is not None and r < 25:
        return f"Readiness muy bajo ({r}): hoy toca recuperar"
    if e.energia == 1:
        return "Energía 1/5: hoy descansa"
    return None


# ---------- carrera ----------

def _c_descanso(motivo: str, alternativa: str = "") -> Consejo:
    return Consejo("carrera", "descanso", "Descanso", "Hoy no corras. Movilidad o un paseo de "
                   "20-30' si te apetece.", motivo, alternativa)


def c_recuperacion(ref: int, m: int = 30) -> Consejo:
    return Consejo("carrera", "recuperacion", f"Recuperación {m}'",
                   f"{m}' muy suave en Z1 ({_r(ref, 'z1')}). Si el pulso se dispara, camina.",
                   "", minutos=m, km=round(m * 60 / (ref + 92), 1),
                   pasos=[_paso("intervalo", "tiempo", m * 60, "z1")])


def c_rodaje(ref: int, m: int) -> Consejo:
    return Consejo("carrera", "rodaje", f"Rodaje Z2 {m}'",
                   f"{m}' en Z2 ({_r(ref, 'z2')}) + 4x20\" progresivos al acabar (1' trote "
                   "entre ellos).", "", minutos=m + 5, km=round(m * 60 / (ref + 62), 1),
                   pasos=[_paso("intervalo", "tiempo", m * 60, "z2"), _progresivos(4)])


def c_larga(ref: int, m: int, final_media: bool = False) -> Consejo:
    if final_media and m >= 80:
        base = m - 15
        return Consejo("carrera", "larga", f"Tirada larga {m}' con final a ritmo de media",
                       f"{base}' en Z2 ({_r(ref, 'z2')}) + 15' a ritmo de media "
                       f"({_r(ref, 'media')}). Lleva agua si hace calor.", "", minutos=m,
                       km=round((base * 60 / (ref + 62)) + 15 * 60 / ref, 1),
                       pasos=[_paso("intervalo", "tiempo", base * 60, "z2"),
                              _paso("intervalo", "tiempo", 15 * 60, "media")])
    return Consejo("carrera", "larga", f"Tirada larga {m}'",
                   f"{m}' en Z2 ({_r(ref, 'z2')}). Cómoda, que puedas hablar. Lleva agua si "
                   "pasa de 1h15'.", "", minutos=m, km=round(m * 60 / (ref + 62), 1),
                   pasos=[_paso("intervalo", "tiempo", m * 60, "z2")])


NIVELES_UMBRAL = ((3, 8), (4, 8), (3, 10), (4, 10), (3, 12), (2, 18))
NIVELES_VO2 = ((5, 3), (6, 3), (5, 4), (6, 4))
NIVELES_MEDIA = ((2, 12), (2, 15), (3, 12), (2, 20))


def _calidad(ref: int, titulo: str, veces: int, mins: int, zona: str, rec: int,
             clave: str) -> Consejo:
    cal, enf = 15, 10
    total = cal + veces * mins + (veces - 1) * rec + enf
    texto = (f"{cal}' en Z2 ({_r(ref, 'z2')}) + {veces}x{mins}' a "
             f"{_r(ref, zona)} con {rec}' de trote + {enf}' suaves.")
    kms = ((cal + enf + (veces - 1) * rec) * 60 / (ref + 70)
           + veces * mins * 60 / (ref + plan.ZONAS[zona][0]))
    return Consejo("carrera", clave, titulo, texto, "", minutos=total, km=round(kms, 1),
                   pasos=[_paso("calentamiento", "tiempo", cal * 60, "z2"),
                          _rep(veces, _paso("intervalo", "tiempo", mins * 60, zona),
                               _paso("recuperacion", "tiempo", rec * 60, None)),
                          _paso("enfriamiento", "tiempo", enf * 60, "z2")])


def c_umbral(ref: int, nivel: int) -> Consejo:
    v, m = NIVELES_UMBRAL[min(nivel // 2, len(NIVELES_UMBRAL) - 1)]
    return _calidad(ref, f"Umbral {v}x{m}'", v, m, "umbral", 2, "umbral")


def c_vo2(ref: int, nivel: int) -> Consejo:
    v, m = NIVELES_VO2[min(nivel // 2, len(NIVELES_VO2) - 1)]
    return _calidad(ref, f"Series {v}x{m}'", v, m, "vo2", m - 1 if m > 3 else 2, "vo2")


def c_media(ref: int, nivel: int) -> Consejo:
    v, m = NIVELES_MEDIA[min(nivel // 2, len(NIVELES_MEDIA) - 1)]
    return _calidad(ref, f"Ritmo de media {v}x{m}'", v, m, "media", 3, "media")


CALIDAD_CARRERA = {"umbral": c_umbral, "vo2": c_vo2, "media": c_media}


def carrera(e: Estado, ref: int) -> Consejo:
    rodaje = e.rodaje_min()
    corto = c_rodaje(ref, max(30, rodaje - 10))
    alt_corta = f"Si vas justo: {_min(corto.titulo)}."
    if (m := _parar(e)):
        return _c_descanso(m + ".", "Si te ves bien a media tarde, 20' de paseo o bici muy suave.")
    if e.molestia == "piernas":
        return _c_descanso("Molestias en las piernas: hoy sin impacto.",
                           "Mejor bici o natación suave si no te molestan (`/bici`).")
    hecho = e.hoy_hecho("carrera")
    if hecho:
        a = hecho[-1]
        return Consejo("carrera", "hecho", "Hoy ya has corrido",
                       f"Ya tienes {km(a):.1f} km hoy. Estira, come proteína + hidratos y a "
                       "recuperar.", "No hace falta más carrera hoy.")
    if (m := _cansancio(e)):
        c = c_recuperacion(ref, 30) if (e.bienestar.get("readiness") or 100) < 35 else corto
        c.motivo = f"{m}: hoy suave, sin calidad."
        c.alternativa = "Si te ves peor al arrancar, cambia a 30' de bici suave o descansa."
        return c
    if e.ayer(lambda a: dura(a, e.umbral) or larga(a)):
        c = c_recuperacion(ref, 30) if e.ayer(lambda a: a.get("tipo") == "carrera") else corto
        c.motivo = "Ayer hiciste una sesión dura o larga: hoy toca soltar piernas."
        c.alternativa = "O descanso si notas las piernas cargadas."
        return c
    pierna_ayer = e.ayer(pierna)
    if pierna_ayer or any(pierna(a) for a in e.hoy_hecho("gym")):
        corto.motivo = ("Pierna en el gimnasio ayer: nada de calidad hoy." if pierna_ayer else
                        "Hoy ya has hecho pierna: si corres, que sea suave.")
        corto.alternativa = "O bici suave para soltar."
        return corto
    bici_hoy = [a for a in e.hoy_hecho("bici") if dura(a, e.umbral) or larga(a)]
    if bici_hoy:
        corto.motivo = "Hoy ya has hecho bici exigente: si corres, que sea suave."
        corto.alternativa = "O descansa."
        return corto
    if len(e._de("carrera")) < 2:
        c = c_rodaje(ref, 30 if not e._de("carrera") else rodaje)
        c.motivo = "Apenas has corrido estas 2 semanas: primero rodajes para coger base."
        c.alternativa = "Si te notas bien, alarga 5-10' sin subir el ritmo."
        return c
    sube = e.km_semana(1) > 0 and e.km_semana() > 1.15 * e.km_semana(1) and e.km_semana() >= 15
    if sube:
        corto.motivo = (f"Llevas {e.km_semana():.0f} km en 7 días frente a {e.km_semana(1):.0f} "
                        "la semana anterior: no subas más el volumen de golpe.")
        corto.alternativa = "O descanso."
        return corto
    dl = e.dias_desde(lambda a: a.get("tipo") == "carrera" and larga(a))
    duras = e.duras()
    if dl is None or dl >= 6:
        ultima = e.ultima_larga_min()
        m = min(LARGA_TOPE, 5 * round(ultima * 1.1 / 5)) if ultima else min(80, max(60, rodaje + 25))
        final = bool(ultima and ultima >= 80 and len(duras) < MAX_DURAS_SEMANA)
        c = c_larga(ref, m, final)
        c.motivo = ("No llevas tirada larga en las últimas 2 semanas: es la base para la media."
                    if dl is None else f"Hace {dl} días de tu última tirada larga.")
        c.alternativa = f"Si vas justo: {min(m, 60)}' en Z2, sin forzar."
        return c
    dd = e.dias_desde(lambda a: dura(a, e.umbral))
    if len(duras) >= MAX_DURAS_SEMANA or (dd is not None and dd < 2):
        c = c_rodaje(ref, rodaje)
        c.motivo = (f"Ya llevas {len(duras)} sesiones duras en 7 días: el resto, suave (80/20)."
                    if len(duras) >= MAX_DURAS_SEMANA else
                    "Hace poco de tu última sesión dura: hoy aeróbico.")
        c.alternativa = alt_corta
        return c
    hace = e.ultima_calidad("carrera")
    tipo = max(CALIDADES_CARRERA, key=lambda t: (hace[t], -CALIDADES_CARRERA.index(t)))
    c = CALIDAD_CARRERA[tipo](ref, e.nivel("carrera", tipo))
    if _calidad_en_otro(e, "bici"):
        r = c_rodaje(ref, rodaje)
        r.motivo = "Hoy la calidad la tienes en bici (/bici): corriendo, aeróbico."
        r.alternativa = f"Si hoy no coges la bici: {_min(c.titulo)}."
        return r
    nombres = {"umbral": "umbral", "vo2": "series", "media": "ritmo de media"}
    c.motivo = ("Estás fresco y llevas " + ("tiempo" if hace[tipo] >= 99 else f"{hace[tipo]} días")
                + f" sin {nombres[tipo]}: toca calidad para mejorar en 10K y media.")
    c.alternativa = alt_corta
    return c


def _min(titulo: str) -> str:
    return titulo[:1].lower() + titulo[1:]


def _calidad_en_otro(e: Estado, deporte: str) -> bool:
    """Si hoy ya se ha aconsejado una sesión exigente en el otro deporte (para no aconsejar
    dos sesiones duras el mismo día)."""
    c = e.consejos.get((e.hoy.isoformat(), deporte))
    exigentes = (CALIDADES_BICI if deporte == "bici" else CALIDADES_CARRERA + ("larga",))
    return bool(c and c.get("clave") in exigentes)


# ---------- bici ----------

def zonas_bici(umbral: int) -> dict[str, tuple[int, int]]:
    u = umbral - UMBRAL_BICI_MENOS
    return {z: (round(u * a), round(u * b)) for z, (a, b, _) in ZONAS_BICI.items()}


def _z(umbral: int | None, zona: str) -> str:
    nombre = ZONAS_BICI[zona][2]
    if not umbral:
        rpe = {"b1": "RPE 3", "b2": "RPE 5", "b3": "RPE 6-7", "b4": "RPE 8", "b5": "RPE 9"}[zona]
        return f"{nombre} ({rpe})"
    a, b = zonas_bici(umbral)[zona]
    return f"{nombre} ({a}-{b} ppm)"


def b_recuperacion(u: int | None, m: int = 40) -> Consejo:
    return Consejo("bici", "recuperacion", f"Bici suave {m}'",
                   f"{m}' en {_z(u, 'b1')}, cadencia alta (90+ rpm), sin desarrollos duros.", "",
                   minutos=m, pasos=[_paso("intervalo", "tiempo", m * 60, "b1")])


def b_fondo(u: int | None, m: int) -> Consejo:
    return Consejo("bici", "fondo", f"Fondo Z2 {m}'",
                   f"{m}' en {_z(u, 'b2')}, cadencia 85-95 rpm. Come algo cada 45' si pasa de "
                   "1h30'.", "", minutos=m,
                   pasos=[_paso("calentamiento", "tiempo", 10 * 60, "b1"),
                          _paso("intervalo", "tiempo", (m - 10) * 60, "b2")])


NIVELES_BICI = {
    "tempo": ((3, 10, 5), (3, 12, 5), (3, 15, 5), (2, 20, 5)),
    "umbral": ((3, 8, 4), (3, 10, 5), (2, 15, 5), (2, 20, 5)),
    "vo2": ((4, 3, 3), (5, 3, 3), (6, 3, 3), (5, 4, 4)),
}
ZONA_CALIDAD_BICI = {"tempo": "b3", "umbral": "b4", "vo2": "b5"}
NOMBRE_BICI = {"tempo": "Tempo", "umbral": "Umbral", "vo2": "VO2"}


def b_calidad(u: int | None, tipo: str, nivel: int) -> Consejo:
    niveles = NIVELES_BICI[tipo]
    v, m, rec = niveles[min(nivel // 2, len(niveles) - 1)]
    zona = ZONA_CALIDAD_BICI[tipo]
    total = 15 + v * m + (v - 1) * rec + 10
    return Consejo("bici", tipo, f"{NOMBRE_BICI[tipo]} {v}x{m}'",
                   f"15' en {_z(u, 'b2')} + {v}x{m}' en {_z(u, zona)} con {rec}' suave en "
                   f"{_z(u, 'b1')} + 10' suaves.", "", minutos=total,
                   pasos=[_paso("calentamiento", "tiempo", 15 * 60, "b2"),
                          _rep(v, _paso("intervalo", "tiempo", m * 60, zona),
                               _paso("recuperacion", "tiempo", rec * 60, "b1")),
                          _paso("enfriamiento", "tiempo", 10 * 60, "b1")])


def bici(e: Estado) -> Consejo:
    u = e.umbral_zonas or e.umbral
    if (m := _parar(e)):
        return Consejo("bici", "descanso", "Descanso", "Hoy sin bici.", m + ".",
                       "Si mejoras, 20-30' muy suaves como mucho.")
    hecho = e.hoy_hecho("bici")
    if hecho:
        return Consejo("bici", "hecho", "Hoy ya has hecho bici",
                       f"Ya llevas {round(minutos(hecho[-1]))}' de bici hoy.",
                       "Recupera: proteína + hidratos y a descansar.")
    if e.molestia == "piernas":
        c = b_recuperacion(u, 30)
        c.motivo = "Molestias en las piernas: solo si pedalear no duele, y muy suave."
        c.alternativa = "Si molesta, descanso o natación."
        return c
    base = e.bici_min()
    if (m := _cansancio(e)):
        c = b_recuperacion(u, 40)
        c.motivo = f"{m}: hoy solo bici suave."
        c.alternativa = "O descanso."
        return c
    corrido_hoy = e.hoy_hecho("carrera")
    if (e.ayer(lambda a: dura(a, e.umbral) or larga(a)) or e.ayer(pierna)
            or any(dura(a, e.umbral) or larga(a) for a in corrido_hoy)):
        c = b_recuperacion(u, 45)
        c.motivo = "Vienes de sesión dura, larga o de pierna: bici suave para recuperar."
        c.alternativa = "30' si vas justo."
        return c
    duras = e.duras()
    dd = e.dias_desde(lambda a: dura(a, e.umbral))
    dl = e.dias_desde(lambda a: a.get("tipo") == "bici" and larga(a))
    if len(duras) >= MAX_DURAS_SEMANA or (dd is not None and dd < 2) or corrido_hoy:
        larga_toca = dl is None or dl >= 7
        m = min(150, base + 30) if larga_toca and not corrido_hoy else base
        c = b_fondo(u, m)
        c.motivo = ("Hoy ya has corrido: la bici, aeróbica." if corrido_hoy else
                    f"Ya llevas {len(duras)} sesiones duras en 7 días: hoy fondo (80/20)."
                    if len(duras) >= MAX_DURAS_SEMANA else
                    "Hace poco de tu última sesión dura: hoy aeróbico.")
        c.alternativa = f"Si vas justo: {max(45, m - 30)}' en Z2."
        return c
    if not e._de("bici", 21):
        c = b_fondo(u, 60)
        c.motivo = "Hace semanas que no coges la bici: fondo cómodo para retomar."
        c.alternativa = "45' si vas justo."
        return c
    if dl is None or dl >= 10:
        c = b_fondo(u, min(150, max(90, base + 30)))
        c.motivo = "Hace tiempo de tu última salida larga en bici: hoy fondo largo."
        c.alternativa = f"Si vas justo: {base}' en Z2."
        return c
    hace = e.ultima_calidad("bici")
    tipo = max(CALIDADES_BICI, key=lambda t: (hace[t], -CALIDADES_BICI.index(t)))
    c = b_calidad(u, tipo, e.nivel("bici", tipo))
    if _calidad_en_otro(e, "carrera"):
        f = b_fondo(u, base)
        f.motivo = "Hoy lo exigente lo tienes corriendo (/carrera): en bici, aeróbico."
        f.alternativa = f"Si hoy no corres: {_min(c.titulo)}."
        return f
    c.motivo = "Estás fresco: una sesión de calidad en bici mejora tu motor sin impacto."
    c.alternativa = f"Si vas justo: {min(base, 60)}' en Z2."
    return c


# ---------- texto ----------

ICONO = {"carrera": "🏃", "bici": "🚴"}


def formatear(c: Consejo) -> str:
    out = [f"{ICONO[c.deporte]} *{c.deporte.capitalize()} de hoy · {c.titulo}*", "", c.texto]
    extra = []
    if c.minutos:
        extra.append(f"~{c.minutos}'")
    if c.km:
        extra.append(f"~{c.km:g} km")
    if extra:
        out[-1] += f" ({', '.join(extra)})"
    if c.motivo:
        out += ["", f"💡 {c.motivo}"]
    if c.alternativa:
        out.append(f"🔁 {c.alternativa}")
    return "\n".join(out)


def resumen_14(e: Estado) -> dict:
    """Números de las 2 últimas semanas para el coach y el historial."""
    return {
        "km_7": round(e.km_semana(), 1), "km_7_previos": round(e.km_semana(1), 1),
        "duras_7": len(e.duras()),
        "dias_desde_larga": e.dias_desde(lambda a: a.get("tipo") == "carrera" and larga(a)),
        "dias_desde_dura": e.dias_desde(lambda a: dura(a, e.umbral)),
    }
