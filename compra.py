"""Lista de la compra semanal a partir de los objetivos de macros y los precios de Mercadona."""
import math
from dataclasses import dataclass, field
from datetime import date

from alimentos import LIQUIDOS, SECCIONES, WHEY, Alimento, disponibles, suplementos_diarios
from config import CREATINA_GRAMOS, PRESUPUESTO_SEMANAL
from menu import VARIANTES, _md, menu_dia
from mercadona import Producto
from nutricion import Objetivo


@dataclass
class Linea:
    alimento: Alimento
    gramos_semana: float
    producto: Producto | None = None
    envases: int = 0
    coste: float = 0.0  # lo que cuenta para el presupuesto semanal


@dataclass
class Lista:
    desde: date
    hasta: date
    media: dict
    variante: str
    lineas: list[Linea] = field(default_factory=list)
    con_precios: bool = True

    @property
    def total(self) -> float:
        return sum(l.coste for l in self.lineas)


def _elegir(alimento: Alimento, gramos: float, opciones: list[Producto]) -> Linea:
    linea = Linea(alimento, gramos)
    if not opciones:
        return linea
    if alimento.duradero:
        # Envase más barato por kg entre los que se gastan en ~6 semanas (evita garrafas de 5 l)
        razonables = [o for o in opciones if o.gramos <= max(gramos * 6, 1000)] or opciones
        p = min(razonables, key=lambda o: (o.precio_kg, o.precio))
        linea.coste = gramos / 1000 * p.precio_kg
    else:
        p = min(opciones, key=lambda o: (math.ceil(gramos / o.gramos) * o.precio, o.precio_kg))
        linea.coste = math.ceil(gramos / p.gramos) * p.precio
    linea.producto = p
    linea.envases = max(1, math.ceil(gramos / p.gramos))
    return linea


def construir(objetivos: list[Objetivo], opciones: dict[str, list[Producto]]) -> Lista:
    """`opciones` asocia la clave de cada alimento con los productos candidatos de Mercadona."""
    n = len(objetivos)
    media = {m: sum(getattr(o, m) for o in objetivos) / n
             for m in ("kcal", "proteina", "grasa", "hidratos")}
    cat = disponibles()
    con_precios = any(opciones.values())
    lista = None
    for nombre, _ in VARIANTES:
        semana: dict[str, float] = {}
        for o in objetivos:
            for k, g in menu_dia(o, nombre).gramos.items():
                semana[k] = semana.get(k, 0) + g
        lineas = [_elegir(cat[k], g, opciones.get(k, [])) for k, g in semana.items() if k in cat]
        lista = Lista(objetivos[0].fecha, objetivos[-1].fecha, media, nombre, lineas, con_precios)
        if not con_precios or lista.total <= PRESUPUESTO_SEMANAL:
            break
    return lista


def _peso(gramos: float, liquido: bool) -> str:
    if round(gramos / 10) * 10 < 1000:
        return f"{round(gramos / 10) * 10:.0f} {'ml' if liquido else 'g'}"
    return f"{gramos / 1000:.1f} {'l' if liquido else 'kg'}".replace(".", ",")


def _cantidad(l: Linea) -> str:
    if l.alimento.gramos_unidad:
        return f"{round(l.gramos_semana / l.alimento.gramos_unidad)} ud"
    return _peso(l.gramos_semana, l.alimento.clave in LIQUIDOS)


def _euros(x: float) -> str:
    return f"{x:.2f} €".replace(".", ",")


def formatear(lista: Lista) -> str:
    m = lista.media
    out = [
        f"🛒 *Lista de la compra* ({lista.desde.strftime('%d/%m')} – {lista.hasta.strftime('%d/%m')})",
        f"Media diaria: *{m['kcal']:.0f} kcal* · P {m['proteina']:.0f} g · "
        f"G {m['grasa']:.0f} g · HC {m['hidratos']:.0f} g",
    ]
    for seccion, titulo in SECCIONES.items():
        lineas = [l for l in lista.lineas if l.alimento.seccion == seccion]
        if not lineas:
            continue
        out += ["", f"*{titulo}*"]
        for l in sorted(lineas, key=lambda x: -x.coste):
            if not l.producto:
                out.append(f"• {l.alimento.nombre} — {_cantidad(l)}")
            elif l.alimento.duradero:
                envase = _peso(l.producto.gramos, l.alimento.clave in LIQUIDOS)
                out.append(f"• {_md(l.producto.nombre)} ({envase}, {_euros(l.producto.precio)}) "
                           f"— usas {_cantidad(l)}/sem")
            else:
                out.append(f"• {l.envases} × {_md(l.producto.nombre)} — "
                           f"{_cantidad(l)} · {_euros(l.coste)}")

    sup = []
    if WHEY.clave in suplementos_diarios():
        sup.append(f"whey {suplementos_diarios()[WHEY.clave] * 7:g} g")
    if CREATINA_GRAMOS > 0:
        sup.append(f"creatina {CREATINA_GRAMOS * 7:g} g")
    if sup:
        out += ["", f"💊 *Suplementos HSN* (no van en la lista): {' · '.join(sup)} por semana"]

    out.append("")
    if not lista.con_precios:
        out.append("⚠️ No he podido consultar Mercadona: lista sin precios.")
        return "\n".join(out)
    total = lista.total
    estado = "✅" if total <= PRESUPUESTO_SEMANAL else "⚠️"
    out.append(f"💶 *Total estimado: {_euros(total)}* / {_euros(PRESUPUESTO_SEMANAL)} {estado}")
    out.append("_La despensa cuenta solo lo que gastas en la semana._")
    if lista.variante != "equilibrada":
        out.append(f"_Proteína en modo {lista.variante} para cuadrar el presupuesto._")
    if total > PRESUPUESTO_SEMANAL:
        out.append("Ni con las opciones más baratas entra en el presupuesto: "
                   "prioriza proteína, arroz, avena y huevos.")
    return "\n".join(out)
