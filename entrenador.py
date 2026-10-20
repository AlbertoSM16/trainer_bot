"""Reglas del entrenador, puras (sin I/O).

- Progresión de cargas (doble progresión) a partir de las series hechas.
- Emparejar las series que registra el reloj con los ejercicios de la rutina.
- Análisis de carreras (volumen, ritmo frente a la zona, deriva cardiaca) y detección de tests.
- Ajuste de la sesión del día según Training Readiness, HRV y sueño.
"""
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
    """Comentarios de una carrera frente a la sesión del plan (`sesion` = entreno del plan
    con km y pasos, o None si no tocaba correr)."""
    out = []
    km = (a.get("distancia_m") or 0) / 1000
    ritmo = _ritmo(a)
    if sesion:
        dif = km / sesion["km"] - 1 if sesion["km"] else 0
        if dif < -0.2:
            out.append(f"📉 Te has quedado corto: {km:.1f} de ~{sesion['km']:g} km previstos.")
        elif dif > 0.25:
            out.append(f"📈 Más volumen del previsto (~{sesion['km']:g} km): ojo con acumular fatiga.")
        else:
            out.append(f"✅ Volumen según el plan (~{sesion['km']:g} km).")
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
