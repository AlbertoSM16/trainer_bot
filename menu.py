"""Menú diario por platos: qué comer en cada comida y cuántos gramos de cada alimento.

Es puro (sin I/O) y es la única fuente de cantidades: la lista de la compra suma los
menús de los 7 días, así que lo que se compra coincide con lo que se come.

Cada día tiene un plato fijo por comida (según el día de la semana y, la comida del
sábado, la semana del plan). Los gramos de la proteína y los hidratos principales se
escalan para cuadrar los macros del día. El resto de ingredientes van en cantidad fija,
y el aceite (y, si sobra grasa, los frutos secos) ajusta la grasa.
"""
from dataclasses import dataclass, field
from datetime import date

from alimentos import AOVE_MINIMO, LIQUIDOS, WHEY, Alimento, con_suplementos, suplementos_diarios
from config import CREATINA_GRAMOS
from nutricion import Objetivo
from plan import DIAS, semana_indice

COMIDAS = {
    "desayuno": "🌅 Desayuno",
    "comida": "🥡 Comida (táper)",
    "merienda": "🥤 Merienda / post-entreno",
    "cena": "🌙 Cena",
}
# Peso de cada comida en el reparto de los hidratos del día
PESO_HIDRATOS = {"desayuno": 18, "merienda": 12, "comida": 35, "cena": 35}
# Proteína mínima (g) de la fuente principal de cada plato que la tenga
PROTEINA_MIN_PLATO = 30
FRUTAS = ("manzana", "pera", "naranja", "kiwi")
# Máximo razonable por plato (g en crudo). Lo que no cabe pasa a los otros hidratos del día
TOPE_HIDRATOS = {"pan": 100, "avena": 110, "masa_empanada": 230, "patata": 450, "batata": 400,
                 "arroz": 200, "pasta": 200}
# Si todo llega al tope, lo que falta va al primer grupo de estos que haya en el día
DESBORDE_HIDRATOS = (("arroz", "pasta", "patata", "batata"), ("avena", "pan"))


@dataclass(frozen=True)
class Plato:
    nombre: str                                          # "{p}" = proteína principal
    proteina: str | None = None                          # se escala
    hidratos: dict[str, float] = field(default_factory=dict)  # se escalan con estos pesos
    fijos: dict[str, float] = field(default_factory=dict)     # gramos fijos
    secos: dict[str, float] = field(default_factory=dict)     # frutos secos: se recortan si sobra grasa
    aceite: bool = False                                 # lleva aceite para cocinar
    nota: str = ""
    fuera: bool = False  # se come fuera: cuenta en los macros (estimado) pero no en la compra


PLATOS = {
    # --- desayunos ---
    "tostadas": Plato(
        "Tostadas con AOVE y pavo + café con leche", hidratos={"pan": 1},
        fijos={"pavo_lonchas": 60, "aove": 10, "leche": 200}),
    "bol_queso": Plato(
        "Bol de queso batido con avena, miel y plátano + café con leche",
        hidratos={"avena": 1}, fijos={"queso_batido": 250, "miel": 15, "platano": 120, "leche": 150}),
    "bol_yogur": Plato(
        "Bol de yogur 0% con avena, miel y plátano + café con leche",
        hidratos={"avena": 1}, fijos={"yogur": 250, "miel": 15, "platano": 120, "leche": 150}),
    # --- meriendas ---
    "whey_tostada": Plato(
        "Batido de whey + tostada con crema de cacahuete y plátano",
        hidratos={"pan": 1}, fijos={"platano": 120}, secos={"crema_cacahuete": 20}),
    "yogur_whey": Plato(
        "Yogur 0% con whey, avena, nueces y fruta", hidratos={"avena": 1},
        fijos={"yogur": 200, "fruta": 180}, secos={"nueces": 20}),
    # --- comidas y cenas ---
    "ternera_arroz": Plato(
        "{p} con pimientos y cebolla + arroz", "ternera_picada", {"arroz": 1},
        {"pimiento_rojo": 70, "pimiento_verde": 70, "cebolla": 60}, aceite=True,
        nota="Sofríe cebolla y pimientos, añade la carne y salpimienta. Arroz hervido aparte."),
    "macarrones_ternera": Plato(
        "Macarrones con {p} y tomate", "ternera_picada", {"pasta": 1},
        {"tomate": 150, "cebolla": 50, "zanahoria": 40}, aceite=True,
        nota="Boloñesa rápida: sofrito, carne y tomate rallado 10'."),
    "arroz_frito": Plato(
        "Arroz frito con {p}, verduras, huevo y soja", "pollo_pechuga", {"arroz": 1},
        {"zanahoria": 60, "pimiento_rojo": 50, "cebolla": 40, "huevos": 60, "soja": 15},
        aceite=True,
        nota="Mejor con arroz del día anterior. Saltea a fuego fuerte y la soja al final."),
    "pollo_pimientos": Plato(
        "{p} a la plancha con pimientos y cebolla + arroz", "pollo_pechuga", {"arroz": 1},
        {"pimiento_verde": 80, "pimiento_rojo": 60, "cebolla": 60}, aceite=True),
    "pollo_ensalada": Plato(
        "{p} + ensalada de canónigos, tomate, aguacate y queso de cabra + arroz",
        "pollo_pechuga", {"arroz": 1},
        {"canonigos": 50, "tomate": 120, "aguacate": 70, "queso_cabra": 40}, aceite=True),
    "pasta_atun": Plato(
        "Pasta con {p}, tomate y cebolla", "atun", {"pasta": 1},
        {"tomate": 150, "cebolla": 50}, aceite=True),
    "salmon_patata": Plato(
        "{p} en airfryer + patata + canónigos con tomate", "salmon", {"patata": 0.6, "pan": 0.4},
        {"canonigos": 40, "tomate": 100}, aceite=True,
        nota="Airfryer 180 °C: patata en gajos 20' y el pescado 10-12' más."),
    "merluza_verduras": Plato(
        "{p} en airfryer con calabacín y zanahoria + patata", "merluza",
        {"patata": 0.6, "pan": 0.4}, {"calabacin": 150, "zanahoria": 60}, aceite=True,
        nota="Airfryer 180 °C: patata 20', luego pescado y verduras 10-12'."),
    "pavo_batata": Plato(
        "{p} en airfryer + batata y pimientos asados", "pavo_solomillo",
        {"batata": 0.7, "pan": 0.3}, {"pimiento_rojo": 80, "pimiento_verde": 60}, aceite=True,
        nota="Airfryer 190 °C: batata en dados 20', carne y pimientos 12-15'."),
    "pasta_empresa": Plato(
        "Pasta en el trabajo", "pollo_pechuga", {"pasta": 1}, aceite=True, fuera=True,
        nota="Te la paga la empresa. Ración generosa y, si hay, que lleve carne, atún o huevo."),
    "cena_fuera": Plato(
        "Cena fuera", "pollo_pechuga", {"arroz": 1}, aceite=True, fuera=True,
        nota="Elige carne o pescado con guarnición de patata, arroz o pan. "
             "Evita fritos y salsas si puedes."),
    "pota": Plato(
        "{p} encebollada con pimiento + arroz", "pota", {"arroz": 1},
        {"cebolla": 100, "pimiento_verde": 60}, aceite=True,
        nota="Pocha la cebolla, añade la pota en anillas y cocina 15-20' a fuego medio."),
    "empanada": Plato(
        "Empanada casera de {p}, huevo duro y tomate", "atun", {"masa_empanada": 1},
        {"huevos": 60, "tomate": 80, "cebolla": 40}, aceite=True,
        nota="Con una masa haces la cena del sábado y la comida del domingo. "
             "Horno o airfryer a 180 °C unos 25'."),
    "fabada": Plato(
        "Fabada de bote + pan", hidratos={"pan": 1}, fijos={"fabada": 420},
        nota="Un bote. Es el plato con más grasa: el resto del día lleva menos aceite."),
    "salchichas": Plato(
        "Salchichas de pollo en airfryer + batata + canónigos con tomate + pan",
        hidratos={"batata": 0.7, "pan": 0.3}, fijos={"salchichas_pollo": 160, "canonigos": 40, "tomate": 100},
        nota="Airfryer 190 °C: batata 20' y salchichas 8' más."),
}

DESAYUNOS = ("tostadas", "bol_queso", "tostadas", "bol_yogur", "tostadas", "bol_queso", "tostadas")
MERIENDAS = ("whey_tostada", "yogur_whey") * 3 + ("whey_tostada",)
# (comida, cena) por día de la semana. La comida del sábado alterna por semana del plan.
SEMANA = (
    ("ternera_arroz", "salmon_patata"),
    ("arroz_frito", "merluza_verduras"),
    ("pollo_pimientos", "pavo_batata"),
    ("pasta_empresa", "pota"),
    ("macarrones_ternera", "cena_fuera"),
    (None, "empanada"),
    ("empanada", "pollo_ensalada"),
)
COMIDA_SABADO = ("fabada", "salchichas")  # semanas impares / pares

# Variantes de presupuesto: sustituciones de la proteína principal, de más cara a más barata
VARIANTES = [
    ("equilibrada", {}),
    ("ahorro", {"salmon": "merluza"}),
    ("ahorro máximo", {"salmon": "merluza", "pota": "merluza", "ternera_picada": "pollo_pechuga"}),
]
NOMBRE_CORTO = {
    "pollo_pechuga": "Pollo", "ternera_picada": "Ternera picada", "pavo_solomillo": "Solomillo de pavo",
    "merluza": "Merluza", "salmon": "Salmón", "pota": "Pota", "atun": "Atún",
}


@dataclass
class Menu:
    objetivo: Objetivo
    variante: str
    platos: dict[str, Plato]                     # comida -> plato
    comidas: dict[str, list[tuple[str, float]]]  # comida -> [(clave, gramos)]
    gramos: dict[str, float]                     # total del día por alimento
    total: dict[str, float]

    def nombre_plato(self, comida: str) -> str:
        p = self.platos[comida]
        corto = NOMBRE_CORTO.get(p.proteina, "")
        return p.nombre.format(p=corto if p.nombre.startswith("{p}") else corto.lower())


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


def _fruta(dia: date, n: int) -> str:
    return FRUTAS[(dia.toordinal() + n) % len(FRUTAS)]


def platos_dia(dia: date, variante: str = "equilibrada") -> dict[str, Plato]:
    dow = dia.weekday()
    comida, cena = SEMANA[dow]
    if comida is None:
        comida = COMIDA_SABADO[(semana_indice(dia) + 1) % 2]
    cambios = dict(VARIANTES)[variante]
    out = {}
    for nombre_comida, clave in (("desayuno", DESAYUNOS[dow]), ("comida", comida),
                                 ("merienda", MERIENDAS[dow]), ("cena", cena)):
        p = PLATOS[clave]
        if p.proteina in cambios:
            p = Plato(**{**p.__dict__, "proteina": cambios[p.proteina]})
        out[nombre_comida] = p
    return out


# Un componente es (comida, clave, rol, valor). rol: fijo | seco | prot | hc | aceite
Componente = tuple[str, str, str, float]


def _componentes(dia: date, platos: dict[str, Plato]) -> list[Componente]:
    cat = con_suplementos()
    out: list[Componente] = []
    for comida, p in platos.items():
        fijos = dict(p.fijos)
        if "fruta" in fijos:
            fijos[_fruta(dia, 1)] = fijos.pop("fruta")
        if comida == "comida" and not p.fuera:
            fijos[_fruta(dia, 0)] = fijos.get(_fruta(dia, 0), 0) + 180  # postre en el táper
        if comida == "merienda":
            fijos.update(suplementos_diarios())
        out += [(comida, k, "fijo", g) for k, g in fijos.items()]
        out += [(comida, k, "seco", g) for k, g in p.secos.items()]
        if p.proteina:
            out.append((comida, p.proteina, "prot", 1))
        total_hc = sum(p.hidratos.values())
        out += [(comida, k, "hc", PESO_HIDRATOS[comida] * w / total_hc)
                for k, w in p.hidratos.items()]
        if p.aceite:
            out.append((comida, "aove", "aceite", 1))
    return [c for c in out if c[1] in cat]


def _repartir(necesario: float, comps: list[Componente], macro: str,
              cat: dict[str, Alimento]) -> dict[int, float]:
    """Gramos para cada componente (por índice) que suman `necesario` del macro."""
    total = sum(c[3] for _, c in comps)
    if necesario <= 0 or not total:
        return {}
    return {i: necesario * c[3] / total / (getattr(cat[c[1]], macro) / 100) for i, c in comps}


def _repartir_hidratos(necesario: float, comps: list[Componente],
                       cat: dict[str, Alimento]) -> dict[int, float]:
    """Como `_repartir`, pero sin pasar de `TOPE_HIDRATOS` en ningún plato mientras se pueda."""
    out: dict[int, float] = {}
    libres, queda = list(comps), necesario
    while libres and queda > 0:
        parcial = _repartir(queda, libres, "hidratos", cat)
        topados = [(i, c) for i, c in libres if parcial[i] > TOPE_HIDRATOS.get(c[1], float("inf"))]
        if not topados:
            out.update(parcial)
            return out
        for i, c in topados:
            out[i] = TOPE_HIDRATOS[c[1]]
            queda -= out[i] * cat[c[1]].hidratos / 100
        libres = [x for x in libres if x not in topados]
    if queda > 0:
        extra = next((x for grupo in DESBORDE_HIDRATOS
                      if (x := [(i, c) for i, c in comps if c[1] in grupo])), comps)
        for i, g in _repartir(queda, extra, "hidratos", cat).items():
            out[i] = out.get(i, 0) + g
    return out


def _resolver(o: Objetivo, comps: list[Componente]) -> list[float]:
    """Gramos de cada componente para cuadrar los macros del día.
    Si la proteína mínima de los platos o la grasa de los fijos se pasan del objetivo,
    se quitan hidratos para mantener las kcal."""
    cat = con_suplementos()
    idx = list(enumerate(comps))
    fijos = {i: c[3] for i, c in idx if c[2] == "fijo"}
    secos_nominal = {i: c[3] for i, c in idx if c[2] == "seco"}
    prot = [(i, c) for i, c in idx if c[2] == "prot"]
    hc = [(i, c) for i, c in idx if c[2] == "hc"]
    aceite = [(i, c) for i, c in idx if c[2] == "aceite"]
    prot_min = PROTEINA_MIN_PLATO * len(prot)

    def macros(*partes: dict[int, float]) -> dict[str, float]:
        g: dict[str, float] = {}
        for parte in partes:
            for i, x in parte.items():
                g[comps[i][1]] = g.get(comps[i][1], 0) + x
        return _macros(g, cat)

    g_prot: dict[int, float] = {}
    g_hc: dict[int, float] = {}
    g_aceite: dict[int, float] = {}
    secos = dict(secos_nominal)
    exceso_prot = exceso_grasa = 0.0
    for _ in range(12):
        falta = o.proteina - macros(fijos, secos, g_hc, g_aceite)["proteina"]
        necesario = max(falta, prot_min if prot else 0)
        exceso_prot = necesario - falta
        g_prot = _repartir(necesario, prot, "proteina", cat)

        objetivo_hc = o.hidratos - exceso_prot - exceso_grasa * 9 / 4
        g_hc = _repartir_hidratos(
            objetivo_hc - macros(fijos, secos, g_prot, g_aceite)["hidratos"], hc, cat)

        # Grasa: aceite hasta cuadrar; si ni con el mínimo de aceite cabe, se recortan secos
        resto = o.grasa - macros(fijos, g_prot, g_hc)["grasa"]
        grasa_secos = macros(secos_nominal)["grasa"]
        minimo = AOVE_MINIMO if aceite else 0
        aceite_total = resto - grasa_secos if aceite else 0
        exceso_grasa = 0.0
        factor = 1.0
        if aceite_total < minimo:
            deficit = minimo - aceite_total
            aceite_total = minimo
            if grasa_secos:
                factor = max(0.0, 1 - deficit / grasa_secos)
            exceso_grasa = max(0.0, deficit - grasa_secos)
        secos = {i: g * factor for i, g in secos_nominal.items()}
        g_aceite = _repartir(aceite_total, aceite, "grasa", cat)

    out = [0.0] * len(comps)
    for parte in (fijos, secos, g_prot, g_hc, g_aceite):
        for i, x in parte.items():
            out[i] += x
    return out


def menu_dia(o: Objetivo, variante: str = "equilibrada") -> Menu:
    platos = platos_dia(o.fecha, variante)
    comps = _componentes(o.fecha, platos)
    gramos_comp = _resolver(o, comps)
    comidas: dict[str, list[tuple[str, float]]] = {c: [] for c in COMIDAS}
    gramos: dict[str, float] = {}
    todo: dict[str, float] = {}
    for (comida, clave, _, _), g in zip(comps, gramos_comp):
        if g <= 1:
            continue
        previos = dict(comidas[comida])
        previos[clave] = previos.get(clave, 0) + g
        comidas[comida] = list(previos.items())
        todo[clave] = todo.get(clave, 0) + g
        if not platos[comida].fuera:
            gramos[clave] = gramos.get(clave, 0) + g
    return Menu(o, variante, platos, comidas, gramos, _macros(todo, con_suplementos()))


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
        if not items:
            continue
        if m.platos[comida].fuera:
            titulo = titulo.replace(" (táper)", "")
        out += ["", f"*{titulo}* — {_md(m.nombre_plato(comida))}"]
        if m.platos[comida].fuera:
            e = _macros(dict(items), cat)
            out.append(f"• Apunta a ≈ {round(e['kcal'], -1):.0f} kcal: ~{e['hidratos']:.0f} g de "
                       f"hidratos y ≥{e['proteina']:.0f} g de proteína")
        else:
            out += [f"• {cat[k].nombre} — {_cantidad(k, g)}" for k, g in items]
        if m.platos[comida].nota:
            out.append(f"_{_md(m.platos[comida].nota)}_")
    if CREATINA_GRAMOS > 0:
        out += ["", f"💊 *Creatina*: {CREATINA_GRAMOS:g} g con cualquier comida, "
                    "también los días de descanso"]
    t = m.total
    out += [
        "",
        f"_Total: {t['kcal']:.0f} kcal · P {t['proteina']:.0f} · G {t['grasa']:.0f} · "
        f"HC {t['hidratos']:.0f}_",
        "_Pesos en crudo._",
    ]
    if any(p.fuera for p in m.platos.values()):
        out.append("_Lo que comes fuera está estimado y no va en la lista de la compra._")
    if m.variante != "equilibrada":
        out.append(f"_Proteína en modo {m.variante} para cuadrar el presupuesto._")
    return "\n".join(out)
