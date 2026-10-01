"""Menú diario por comidas: gramos de cada alimento para cuadrar los macros del día.

Es puro (sin I/O) y es la única fuente de cantidades: la lista de la compra suma los
menús de los 7 días, así que lo que se compra coincide con lo que se come.
La proteína y el hidrato principales de la comida y la cena rotan por día de la semana,
en las proporciones de `VARIANTES_PROTEINA` y `REPARTO_HIDRATOS`.
"""
from dataclasses import dataclass

from alimentos import (AOVE_MINIMO, BASE_DIARIA, LIQUIDOS, REPARTO_HIDRATOS,
                       VARIANTES_PROTEINA, WHEY, Alimento, con_suplementos, disponibles,
                       suplementos_diarios)
from config import CREATINA_GRAMOS
from nutricion import Objetivo
from plan import DIAS

COMIDAS = {
    "desayuno": "🌅 Desayuno",
    "comida": "🥡 Comida (táper)",
    "merienda": "🥤 Merienda / post-entreno",
    "cena": "🌙 Cena",
}

# Reparto fijo entre comidas de los alimentos que no rotan
REPARTO_FIJO = {
    "avena": {"desayuno": 1},
    "leche": {"desayuno": 1},
    "nueces": {"desayuno": 1},
    "pan": {"desayuno": 0.6, "merienda": 0.4},
    "platano": {"desayuno": 0.5, "merienda": 0.5},
    "manzana": {"comida": 1},
    "naranja": {"cena": 1},
    "queso_batido": {"merienda": 1},
    "cacahuete": {"merienda": 1},
    WHEY.clave: {"merienda": 1},
    "huevos": {"cena": 1},
    "tomate": {"comida": 0.5, "cena": 0.5},
    "cebolla": {"comida": 0.5, "cena": 0.5},
    "pimiento": {"comida": 1},
    "zanahoria": {"comida": 1},
    "calabacin": {"cena": 1},
    "espinacas": {"cena": 1},
    "aove": {"desayuno": 0.2, "comida": 0.4, "cena": 0.4},
}

# Hidratos que van siempre en desayuno/merienda; el resto rota en comida y cena
HC_FIJOS = ("avena", "pan")
# Proteína mínima (g) que aporta la fuente principal de la comida y de la cena,
# para que haya una ración de verdad aunque los lácteos y la whey cubran casi todo
PROTEINA_MIN_PRINCIPAL = 30
# La patata pesa mucho para los hidratos que da: en su comida solo cubre esta parte
# y el resto va en pan
PARTE_PATATA = 0.6


@dataclass
class Menu:
    objetivo: Objetivo
    variante: str
    gramos: dict[str, float]                     # total del día por alimento
    comidas: dict[str, list[tuple[str, float]]]  # comida -> [(clave, gramos)]
    total: dict[str, float]


def _md(texto: str) -> str:
    """Quita los caracteres que rompen el Markdown de Telegram."""
    return texto.translate(str.maketrans("", "", "*_`["))


def _macros(gramos: dict[str, float], cat: dict[str, Alimento]) -> dict[str, float]:
    t = {"kcal": 0.0, "proteina": 0.0, "hidratos": 0.0, "grasa": 0.0}
    for clave, g in gramos.items():
        a = cat[clave]
        for m in t:
            t[m] += getattr(a, m) * g / 100
    return t


def _repartir(necesario: float, reparto: dict[str, float], macro: str,
              cat: dict[str, Alimento]) -> dict[str, float]:
    reparto = {k: v for k, v in reparto.items() if k in cat}
    total = sum(reparto.values())
    if necesario <= 0 or not total:
        return {}
    return {k: necesario * v / total / (getattr(cat[k], macro) / 100) for k, v in reparto.items()}


def cantidades_diarias(prot: float, grasa: float, hc: float, reparto_prot: dict[str, float],
                       base_extra: dict[str, float], reparto_hc: dict[str, float],
                       prot_min: float = 0) -> dict[str, float]:
    """Gramos diarios de cada alimento (incluida la whey) para cubrir los macros objetivo.
    Si la proteína principal no llega a `prot_min`, se sube y se quitan hidratos
    para mantener las kcal."""
    cat = con_suplementos()
    base = {k: g for k, g in {**BASE_DIARIA, **base_extra, **suplementos_diarios()}.items()
            if k in cat}
    g_prot, g_hc = {}, {}
    for _ in range(8):
        resto = _macros({**base, **g_hc}, cat)
        falta = prot - resto["proteina"]
        necesario = max(falta, prot_min)
        g_prot = _repartir(necesario, reparto_prot, "proteina", cat)
        resto = _macros({**base, **g_prot}, cat)
        g_hc = _repartir(hc - (necesario - falta) - resto["hidratos"], reparto_hc, "hidratos", cat)

    gramos = dict(base)
    for parcial in (g_prot, g_hc):
        for k, g in parcial.items():
            gramos[k] = gramos.get(k, 0) + g

    # Cuadrar la grasa con el aceite (y, si sobra, quitando frutos secos)
    falta_grasa = grasa - _macros(gramos, cat)["grasa"]
    if "aove" in gramos:
        gramos["aove"] = max(AOVE_MINIMO, gramos["aove"] + falta_grasa)
        exceso = grasa - _macros(gramos, cat)["grasa"]
        for k in ("cacahuete", "nueces"):
            if exceso < 0 and k in gramos:
                quitar = min(gramos[k], -exceso / (cat[k].grasa / 100))
                gramos[k] -= quitar
                exceso += quitar * cat[k].grasa / 100
    return {k: g for k, g in gramos.items() if g > 1}


def _rotacion(reparto: dict[str, float], huecos: int = 14) -> list[str]:
    """Reparte `huecos` platos (comida y cena de 7 días) en proporción al reparto,
    sin repetir el mismo alimento en dos platos seguidos si se puede evitar."""
    total = sum(reparto.values())
    exactos = {k: v / total * huecos for k, v in reparto.items()}
    cuenta = {k: int(x) for k, x in exactos.items()}
    for k in sorted(exactos, key=lambda k: exactos[k] - cuenta[k], reverse=True):
        if sum(cuenta.values()) >= huecos:
            break
        cuenta[k] += 1
    orden, previo = [], None
    for _ in range(huecos):
        previo = min((k for k in cuenta if cuenta[k] > 0),
                     key=lambda k: (k == previo, -cuenta[k], k))
        cuenta[previo] -= 1
        orden.append(previo)
    return orden


def _variante(nombre: str) -> tuple[str, dict[str, float], dict[str, float]]:
    return next(v for v in VARIANTES_PROTEINA if v[0] == nombre)


def principales(variante: str, dia_semana: int) -> dict[str, tuple[str, str]]:
    """Proteína e hidrato principales de la comida y la cena para ese día de la semana."""
    cat = disponibles()
    _, reparto_prot, _ = _variante(variante)
    prot = _rotacion({k: v for k, v in reparto_prot.items() if k in cat})
    hc = _rotacion({k: v for k, v in REPARTO_HIDRATOS.items()
                    if k in cat and k not in HC_FIJOS})
    i = dia_semana * 2
    return {"comida": (prot[i], hc[i]), "cena": (prot[i + 1], hc[i + 1])}


def menu_dia(o: Objetivo, variante: str = "equilibrada") -> Menu:
    _, _, base_extra = _variante(variante)
    cat = disponibles()
    parte_comida = sum(v for k, v in REPARTO_HIDRATOS.items()
                       if k in cat and k not in HC_FIJOS) / 2

    # Peso de cada comida dentro del reparto de proteína / hidratos del día
    reparto_prot: dict[str, dict[str, float]] = {}
    reparto_hc: dict[str, dict[str, float]] = {
        k: {m: REPARTO_HIDRATOS[k] * p for m, p in REPARTO_FIJO[k].items()}
        for k in HC_FIJOS if k in cat}
    for comida, (p, h) in principales(variante, o.fecha.weekday()).items():
        reparto_prot.setdefault(p, {})[comida] = 1
        parte = parte_comida
        if h == "patata" and "pan" in cat:
            reparto_hc["pan"][comida] = parte * (1 - PARTE_PATATA)
            parte *= PARTE_PATATA
        reparto_hc.setdefault(h, {})[comida] = parte

    gramos = cantidades_diarias(
        o.proteina, o.grasa, o.hidratos,
        {k: sum(v.values()) for k, v in reparto_prot.items()}, base_extra,
        {k: sum(v.values()) for k, v in reparto_hc.items()},
        prot_min=PROTEINA_MIN_PRINCIPAL * 2)

    comidas: dict[str, list[tuple[str, float]]] = {c: [] for c in COMIDAS}
    for clave, g in gramos.items():
        reparto = (reparto_prot.get(clave) or reparto_hc.get(clave) or REPARTO_FIJO.get(clave)
                   or {"comida": 1, "cena": 1})
        total = sum(reparto.values())
        for comida, peso in reparto.items():
            comidas[comida].append((clave, g * peso / total))
    return Menu(o, variante, gramos, comidas, _macros(gramos, con_suplementos()))


def _cantidad(clave: str, gramos: float) -> str:
    a = con_suplementos()[clave]
    if a.gramos_unidad:
        return f"{max(1, round(gramos / a.gramos_unidad))} ud"
    if clave == WHEY.clave:
        return f"{gramos:.0f} g (≈ {gramos / 30:.2g} cacito)".replace(".", ",")
    return f"{round(gramos / 5) * 5:.0f} {'ml' if clave in LIQUIDOS else 'g'}"


def formatear(m: Menu) -> str:
    o, cat = m.objetivo, con_suplementos()
    out = [
        f"🍽️ *Menú · {DIAS[o.fecha.weekday()]} {o.fecha.strftime('%d/%m')}*",
        f"_{_md(o.titulo)}_",
        f"Objetivo: *{o.kcal} kcal* · P ≥{o.proteina} g · G {o.grasa} g · HC {o.hidratos} g",
    ]
    for comida, titulo in COMIDAS.items():
        items = [(k, g) for k, g in sorted(m.comidas[comida], key=lambda x: -x[1]) if g >= 3]
        if items:
            out += ["", f"*{titulo}*"]
            out += [f"• {cat[k].nombre} — {_cantidad(k, g)}" for k, g in items]
    if CREATINA_GRAMOS > 0:
        out += ["", f"💊 *Creatina*: {CREATINA_GRAMOS:g} g con cualquier comida, "
                    "también los días de descanso"]
    t = m.total
    out += [
        "",
        f"_Total: {t['kcal']:.0f} kcal · P {t['proteina']:.0f} · G {t['grasa']:.0f} · "
        f"HC {t['hidratos']:.0f}_",
        "_Pesos en crudo; legumbres de bote, escurridas._",
    ]
    if m.variante != "equilibrada":
        out.append(f"_Proteína en modo {m.variante} para cuadrar el presupuesto._")
    return "\n".join(out)
