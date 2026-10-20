"""Categorías de ejercicio de fuerza de Garmin (FIT SDK) a partir del nombre en español.

Solo se usa la categoría (BENCH_PRESS, SQUAT…): es lo que el reloj necesita para
contar repeticiones y lo que devuelve en las series registradas. Las reglas se miran
en orden, así que las más específicas van primero.
"""
import alimentos

REGLAS: list[tuple[tuple[str, ...], str | None]] = [
    (("curl femoral", "femoral tumbado", "femoral sentado"), "LEG_CURL"),
    (("extension de cuadriceps", "extension de pierna"), None),
    (("sentadilla bulgara", "zancada", "lunge", "split squat"), "LUNGE"),
    (("sentadilla", "squat", "prensa", "hack"), "SQUAT"),
    (("peso muerto", "deadlift"), "DEADLIFT"),
    (("hip thrust", "puente de gluteo", "elevacion de cadera"), "HIP_RAISE"),
    (("gemelo", "pantorrilla", "calf"), "CALF_RAISE"),
    (("plancha", "plank"), "PLANK"),
    (("elevacion de piernas", "leg raise"), "LEG_RAISE"),
    (("crunch", "abdominal"), "CRUNCH"),
    (("rueda", "dead bug", "hollow", "pallof", "core"), "CORE"),
    (("remo al menton", "face pull", "remo", "row", "pendlay"), "ROW"),
    (("press militar", "press arnold", "press de hombro", "press hombro", "shoulder press"),
     "SHOULDER_PRESS"),
    (("press frances", "extension de triceps", "patada de triceps", "fondos", "triceps"),
     "TRICEPS_EXTENSION"),
    (("press banca", "press plano", "press inclinado", "press declinado", "press cerrado",
      "bench"), "BENCH_PRESS"),
    (("apertura", "cruces", "peck deck", "contractora", "flye"), "FLYE"),
    (("dominada", "jalon", "pull up", "chin up"), "PULL_UP"),
    (("elevaciones laterales", "elevacion lateral", "pajaros", "deltoide posterior",
      "lateral"), "LATERAL_RAISE"),
    (("encogimiento", "trapecio", "shrug"), "SHRUG"),
    (("curl",), "CURL"),
    (("flexiones", "push up"), "PUSH_UP"),
    (("hiperextension", "lumbar"), "HYPEREXTENSION"),
]


def categoria(nombre: str) -> str | None:
    n = alimentos.normalizar(nombre)
    for claves, cat in REGLAS:
        if any(k in n for k in claves):
            return cat
    return None
