"""Rutinas de gimnasio (4 días/semana, alternancia A/B semana a semana)."""

PIERNA = {
    "A": {
        "titulo": "PIERNA A · Cuádriceps dominante",
        "ejercicios": [
            "Sentadilla trasera — 4x6-8 (RPE 8, 2-3' descanso)",
            "Prensa 45° — 3x10-12",
            "Zancadas caminando con mancuernas — 3x10 por pierna",
            "Extensión de cuádriceps — 3x12-15 (1s isométrico arriba)",
            "Curl femoral tumbado — 3x12",
            "Gemelo de pie — 4x12-15 (pausa abajo)",
            "Core: plancha frontal 3x45s",
        ],
    },
    "B": {
        "titulo": "PIERNA B · Cadena posterior",
        "ejercicios": [
            "Peso muerto rumano — 4x8 (RPE 8)",
            "Hip thrust — 4x10",
            "Sentadilla búlgara — 3x10 por pierna",
            "Curl femoral sentado — 3x12-15",
            "Prensa pies altos — 3x12",
            "Gemelo sentado — 4x15",
            "Core: dead bug 3x12 por lado",
        ],
    },
}

PECHO_TRICEPS = {
    "A": {
        "titulo": "PECHO + TRÍCEPS A · Fuerza",
        "ejercicios": [
            "Press banca plano con barra — 4x6-8 (RPE 8)",
            "Press inclinado con mancuernas — 3x10",
            "Fondos en paralelas (lastre si puedes) — 3x8-10",
            "Aperturas en polea alta a baja — 3x12-15",
            "Press francés con barra Z — 3x10-12",
            "Extensión de tríceps en polea (cuerda) — 3x12-15",
        ],
    },
    "B": {
        "titulo": "PECHO + TRÍCEPS B · Hipertrofia",
        "ejercicios": [
            "Press inclinado con barra — 4x8",
            "Press plano con mancuernas — 3x10-12",
            "Peck deck o cruces en polea — 3x12-15",
            "Fondos en máquina asistida — 3x12",
            "Press cerrado — 3x8-10",
            "Patada de tríceps en polea unilateral — 3x15 por brazo",
        ],
    },
}

ESPALDA_BICEPS = {
    "A": {
        "titulo": "ESPALDA + BÍCEPS A · Vertical",
        "ejercicios": [
            "Dominadas (lastradas si haces >10) — 4x6-8",
            "Remo con barra pronado — 4x8",
            "Jalón agarre neutro — 3x10-12",
            "Remo en polea baja — 3x12",
            "Curl con barra Z — 3x10",
            "Curl martillo — 3x12",
        ],
    },
    "B": {
        "titulo": "ESPALDA + BÍCEPS B · Horizontal",
        "ejercicios": [
            "Remo Pendlay — 4x6",
            "Dominadas al fallo — 3 series",
            "Remo con mancuerna unilateral — 3x10 por lado",
            "Pullover en polea alta — 3x12-15",
            "Curl inclinado con mancuernas — 3x10",
            "Curl araña o concentrado — 3x12",
        ],
    },
}

HOMBRO_ABS = {
    "A": {
        "titulo": "HOMBRO + ABDOMEN A",
        "ejercicios": [
            "Press militar con barra de pie — 4x6-8",
            "Elevaciones laterales con mancuernas — 4x12-15",
            "Pájaros / deltoide posterior en banco — 3x15",
            "Face pull — 3x15",
            "Encogimientos de trapecio — 3x12",
            "ABS: rueda abdominal 3x10 + elevación de piernas colgado 3x12 + plancha lateral 3x30s/lado",
        ],
    },
    "B": {
        "titulo": "HOMBRO + ABDOMEN B",
        "ejercicios": [
            "Press Arnold — 4x10",
            "Elevaciones laterales en polea — 4x12 por brazo",
            "Remo al mentón agarre ancho — 3x12",
            "Pájaros en máquina — 3x15",
            "Face pull — 3x15",
            "ABS: crunch en polea 3x15 + hollow hold 3x30s + russian twist 3x20",
        ],
    },
}


def bloque(grupo: str, variante: str) -> dict:
    return {
        "pierna": PIERNA,
        "pecho": PECHO_TRICEPS,
        "espalda": ESPALDA_BICEPS,
        "hombro": HOMBRO_ABS,
    }[grupo][variante]


def formatear(grupo: str, variante: str) -> str:
    b = bloque(grupo, variante)
    lineas = [f"🏋️ *{b['titulo']}*", ""]
    lineas += [f"• {e}" for e in b["ejercicios"]]
    lineas.append("")
    lineas.append("_Calienta 8' + series de aproximación. Progresa peso o reps cada semana._")
    return "\n".join(lineas)
