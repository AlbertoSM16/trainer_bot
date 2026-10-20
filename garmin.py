"""Cliente de Garmin Connect con la librería no oficial `garminconnect`.

Lectura: actividades (resumen, vueltas y series de fuerza) y estado de forma (Training
Readiness, HRV, sueño, Body Battery y predicción de media maratón).
Escritura: entrenos estructurados (carrera con ritmos, fuerza con ejercicios, natación y
bici) que se programan en el calendario y llegan al reloj al sincronizar.

Las funciones son bloqueantes (llámalas con `asyncio.to_thread`) y lanzan excepciones: el
llamador decide cómo degradar. Los JSON de entrenos se construyen con funciones puras.
"""
import hashlib
import json
import logging
import os

import plan
from config import (GARMIN_CHAT_ID, GARMIN_EMAIL, GARMIN_PASSWORD, GARMIN_TOKENS_DIR)

log = logging.getLogger(__name__)

PREFIJO = "[Coach]"

_api = None
ultimo_error: str = ""


def configurado() -> bool:
    return bool(GARMIN_CHAT_ID) and (os.path.isdir(GARMIN_TOKENS_DIR)
                                     or bool(GARMIN_EMAIL and GARMIN_PASSWORD))


def cliente():
    """Sesión de Garmin reutilizable (tokens de GARMIN_TOKENS_DIR; si no hay, usuario y
    contraseña sin MFA)."""
    global _api
    if _api is None:
        from garminconnect import Garmin
        g = Garmin(GARMIN_EMAIL or None, GARMIN_PASSWORD or None)
        g.login(GARMIN_TOKENS_DIR)
        _api = g
    return _api


def reiniciar():
    global _api
    _api = None


# ---------- lectura ----------

TIPOS_ACTIVIDAD = {
    "carrera": ("running", "treadmill_running", "trail_running", "track_running",
                "indoor_running", "virtual_run", "street_running"),
    "gym": ("strength_training",),
    "natacion": ("lap_swimming", "open_water_swimming", "swimming"),
    "bici": ("cycling", "road_biking", "indoor_cycling", "virtual_ride", "mountain_biking",
             "gravel_cycling", "e_bike_fitness"),
}


def tipo_actividad(clave: str) -> str:
    return next((t for t, claves in TIPOS_ACTIVIDAD.items() if clave in claves), "otro")


def resumen(r: dict) -> dict:
    inicio = r.get("startTimeLocal") or ""
    clave = (r.get("activityType") or {}).get("typeKey", "")
    return {
        "id": int(r["activityId"]),
        "fecha": inicio[:10],
        "hora": inicio[11:16],
        "tipo": tipo_actividad(clave),
        "tipo_garmin": clave,
        "nombre": r.get("activityName"),
        "distancia_m": r.get("distance"),
        "duracion_s": r.get("duration"),
        "fc_media": r.get("averageHR"),
        "fc_max": r.get("maxHR"),
        "te_aerobico": r.get("aerobicTrainingEffect"),
        "te_anaerobico": r.get("anaerobicTrainingEffect"),
        "calorias": r.get("calories"),
        "desnivel_m": r.get("elevationGain"),
        "cadencia": r.get("averageRunningCadenceInStepsPerMinute"),
        "vo2max": r.get("vO2MaxValue"),
    }


def actividades(desde, hasta) -> list[dict]:
    raw = cliente().get_activities_by_date(desde.isoformat(), hasta.isoformat())
    return [resumen(r) for r in raw or [] if r.get("activityId")]


def vueltas(actividad_id: int) -> list[dict]:
    d = cliente().get_activity_splits(actividad_id) or {}
    return [{"distancia_m": v.get("distance"), "duracion_s": v.get("duration"),
             "fc_media": v.get("averageHR")} for v in d.get("lapDTOs") or []]


def series_fuerza(actividad_id: int) -> list[dict]:
    d = cliente().get_activity_exercise_sets(actividad_id) or {}
    out = []
    for s in d.get("exerciseSets") or []:
        if s.get("setType") != "ACTIVE":
            continue
        ejercicios = s.get("exercises") or [{}]
        e = max(ejercicios, key=lambda x: x.get("probability") or 0)
        peso = s.get("weight")
        # Garmin da el peso en gramos
        peso_kg = round(peso / 1000, 2) if peso and peso > 500 else (peso or None)
        out.append({"categoria": e.get("category"), "nombre": e.get("name"),
                    "reps": int(s.get("repetitionCount") or 0), "peso_kg": peso_kg})
    return out


def _probar(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log.debug("Garmin %s: %s", getattr(fn, "__name__", fn), e)
        return None


def bienestar(fecha) -> dict:
    """Estado de forma del día. Cada dato es opcional (None si Garmin no lo tiene)."""
    g, f = cliente(), fecha.isoformat()
    out: dict = {}
    tr = _probar(g.get_morning_training_readiness, f) or _probar(g.get_training_readiness, f)
    if isinstance(tr, list):
        tr = tr[0] if tr else None
    if isinstance(tr, dict):
        out["readiness"] = tr.get("score")
        out["readiness_txt"] = tr.get("level")
    hrv = _probar(g.get_hrv_data, f) or {}
    resumen_hrv = hrv.get("hrvSummary") or {}
    out["hrv_estado"] = resumen_hrv.get("status")
    out["hrv_media"] = resumen_hrv.get("lastNightAvg")
    sueno = (_probar(g.get_sleep_data, f) or {}).get("dailySleepDTO") or {}
    if sueno.get("sleepTimeSeconds"):
        out["sueno_h"] = round(sueno["sleepTimeSeconds"] / 3600, 1)
    out["sueno_puntos"] = ((sueno.get("sleepScores") or {}).get("overall") or {}).get("value")
    bb = _probar(g.get_body_battery, f)
    if isinstance(bb, list) and bb:
        valores = []
        for fila in bb[0].get("bodyBatteryValuesArray") or []:
            nums = [x for x in fila[1:] if isinstance(x, (int, float))]
            if nums:
                valores.append(nums[0])
        out["body_battery"] = max(valores) if valores else None
    pred = _probar(g.get_race_predictions)
    if isinstance(pred, list):
        pred = pred[-1] if pred else None
    if isinstance(pred, dict) and pred.get("timeHalfMarathon"):
        out["pred_media_s"] = int(pred["timeHalfMarathon"])
    return out


# ---------- entrenos estructurados (JSON puro) ----------

DEPORTES = {
    "carrera": {"sportTypeId": 1, "sportTypeKey": "running"},
    "bici": {"sportTypeId": 2, "sportTypeKey": "cycling"},
    "natacion": {"sportTypeId": 4, "sportTypeKey": "swimming"},
    "gym": {"sportTypeId": 5, "sportTypeKey": "strength_training"},
}
TIPOS_PASO = {
    "calentamiento": (1, "warmup"),
    "enfriamiento": (2, "cooldown"),
    "intervalo": (3, "interval"),
    "recuperacion": (4, "recovery"),
    "descanso": (5, "rest"),
    "repetir": (6, "repeat"),
}
CONDICIONES = {
    "lap": (1, "lap.button"),
    "tiempo": (2, "time"),
    "distancia": (3, "distance"),
    "iteraciones": (7, "iterations"),
    "reps": (10, "reps"),
}
SIN_OBJETIVO = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"}
RITMO = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
KILOS = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}


class _Orden:
    def __init__(self):
        self.paso = 0
        self.grupo = 0

    def siguiente(self) -> int:
        self.paso += 1
        return self.paso


def _tipo(clave: str) -> dict:
    i, k = TIPOS_PASO[clave]
    return {"stepTypeId": i, "stepTypeKey": k}


def _condicion(clave: str) -> dict:
    i, k = CONDICIONES[clave]
    return {"conditionTypeId": i, "conditionTypeKey": k}


def _paso(o: _Orden, tipo: str, cond: str, valor: float | None, grupo: int | None,
          **extra) -> dict:
    p = {"type": "ExecutableStepDTO", "stepOrder": o.siguiente(), "stepType": _tipo(tipo),
         "endCondition": _condicion(cond), "targetType": SIN_OBJETIVO}
    if cond != "lap":
        p["endConditionValue"] = valor
    if grupo is not None:
        p["childStepId"] = grupo
    p.update(extra)
    return p


def _repetir(o: _Orden, veces: int, hijos) -> dict:
    o.grupo += 1
    grupo = o.grupo
    p = {"type": "RepeatGroupDTO", "stepOrder": o.siguiente(), "stepType": _tipo("repetir"),
         "childStepId": grupo, "numberOfIterations": veces, "smartRepeat": False,
         "endCondition": _condicion("iteraciones"), "endConditionValue": veces}
    p["workoutSteps"] = hijos(grupo)
    return p


def objetivo_ritmo(ref: int, zona: str) -> dict:
    """Rango de ritmo de una zona del plan en m/s (valor 1 = lento, valor 2 = rápido)."""
    a, b = plan.ZONAS[zona]
    rapido, lento = ref + a, ref + b
    if rapido == lento:
        rapido, lento = rapido - 5, lento + 5
    return {"targetType": RITMO, "targetValueOne": round(1000 / lento, 3),
            "targetValueTwo": round(1000 / rapido, 3)}


def _pasos_carrera(o: _Orden, pasos: list, ref: int, grupo: int | None = None) -> list[dict]:
    out = []
    for p in pasos:
        if p[0] == "repetir":
            out.append(_repetir(o, p[1], lambda g, hijos=p[2]: _pasos_carrera(o, hijos, ref, g)))
            continue
        tipo, (cond, valor), zona = p
        extra = objetivo_ritmo(ref, zona) if zona else {}
        out.append(_paso(o, tipo, cond, valor, grupo, **extra))
    return out


def _entreno(nombre: str, deporte: str, pasos: list[dict], descripcion: str = "",
             **extra) -> dict:
    return {"workoutName": nombre[:80], "description": descripcion[:500],
            "sportType": DEPORTES[deporte], **extra,
            "workoutSegments": [{"segmentOrder": 1, "sportType": DEPORTES[deporte],
                                 "workoutSteps": pasos}]}


def json_carrera(nombre: str, entreno: dict, ref: int) -> dict:
    return _entreno(nombre, "carrera", _pasos_carrera(_Orden(), entreno["pasos"], ref),
                    entreno.get("texto", ""))


def json_fuerza(nombre: str, filas: list[dict], entreno: dict, categorias: bool = True) -> dict:
    """`filas`: ejercicios de la rutina (ejercicio, series, reps_min/max, peso_kg, descanso_s,
    garmin_cat). Aplica `series_menos` y `carga` del entreno (versiones corta y suave)."""
    o = _Orden()
    pasos = []
    for f in filas:
        series = max(1, f["series"] - entreno.get("series_menos", 0))
        peso = f.get("peso_kg")
        if peso:
            peso = round(peso * entreno.get("carga", 1.0) * 2) / 2

        def hijos(g, f=f, peso=peso):
            extra = {"description": f["ejercicio"][:100]}
            if categorias and f.get("garmin_cat"):
                extra["category"] = f["garmin_cat"]
            if peso:
                extra["weightValue"] = peso
                extra["weightUnit"] = KILOS
            serie = (_paso(o, "intervalo", "reps", f["reps_max"], g, **extra) if f["reps_max"]
                     else _paso(o, "intervalo", "lap", None, g, **extra))
            return [serie, _paso(o, "descanso", "tiempo", f.get("descanso_s") or 90, g)]

        pasos.append(_repetir(o, series, hijos))
    return _entreno(nombre, "gym", pasos)


def json_cardio(nombre: str, entreno: dict) -> dict:
    o = _Orden()
    texto = entreno.get("texto", "")
    if entreno["tipo"] == "natacion":
        return _entreno(nombre, "natacion", [_paso(o, "intervalo", "lap", None, None,
                                                   description=texto[:500])], texto,
                        poolLength=25.0,
                        poolLengthUnit={"unitId": 1, "unitKey": "meter", "factor": 100.0})
    return _entreno(nombre, "bici", [_paso(o, "intervalo", "tiempo", entreno["minutos"] * 60,
                                           None, description=texto[:500])], texto)


def huella(j: dict) -> str:
    return hashlib.sha1(json.dumps(j, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


# ---------- escritura ----------

def subir(j: dict) -> int:
    """Sube un entreno y devuelve su id. Si Garmin rechaza las categorías de ejercicio,
    reintenta sin ellas."""
    g = cliente()
    try:
        return int(g.upload_workout(j)["workoutId"])
    except Exception:
        if j["sportType"]["sportTypeKey"] != "strength_training":
            raise
        sin = json.loads(json.dumps(j))

        def limpiar(pasos):
            for p in pasos:
                p.pop("category", None)
                limpiar(p.get("workoutSteps", []))

        limpiar(sin["workoutSegments"][0]["workoutSteps"])
        return int(g.upload_workout(sin)["workoutId"])


def programar(workout_id: int, fecha) -> None:
    cliente().schedule_workout(workout_id, fecha.isoformat())


def borrar(workout_id: int) -> None:
    cliente().delete_workout(workout_id)
