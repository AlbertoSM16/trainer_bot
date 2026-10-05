"""Catálogo de alimentos para el volumen limpio y su búsqueda en Mercadona.

Los macros son valores de referencia por 100 g de producto tal y como se compra
(crudo, con piel en la fruta), porque la API de Mercadona no da tabla nutricional.
"""
import re
import unicodedata
from dataclasses import dataclass

from config import ALIMENTOS_EXCLUIDOS, WHEY_GRAMOS

# Secciones de la lista de la compra, en orden de aparición
SECCIONES = {
    "proteina": "🥩 Carne, pescado y huevos",
    "lacteos": "🥛 Lácteos",
    "fruta": "🍎 Fruta",
    "verdura": "🥕 Verdura",
    "pan": "🍞 Pan",
    "despensa": "🥫 Despensa (revisa lo que te queda)",
}


@dataclass(frozen=True)
class Alimento:
    clave: str
    nombre: str
    seccion: str
    kcal: float
    proteina: float
    hidratos: float
    grasa: float
    categorias: tuple[int, ...]
    incluye: tuple[str, ...]            # deben aparecer todas en el nombre
    alguna: tuple[str, ...] = ()        # debe aparecer al menos una
    excluye: tuple[str, ...] = ()
    gramos_unidad: float | None = None  # para productos vendidos por unidades (huevos)
    duradero: bool = False              # despensa o congelado: no hace falta comprarlo cada semana


CATALOGO = {a.clave: a for a in [
    # --- carne y pescado ---
    Alimento("pollo_pechuga", "Pechuga de pollo", "proteina", 110, 23, 0, 1.5, (38,),
             ("pechuga", "pollo"),
             excluye=("marinad", "empanad", "lonchas", "braseada", "tiras", "hierbas", "certificado")),
    Alimento("ternera_picada", "Ternera picada", "proteina", 165, 20, 0.5, 9, (44,),
             ("preparado de carne picada vacuno",), excluye=("cerdo",)),
    Alimento("pavo_solomillo", "Solomillo de pavo", "proteina", 107, 24, 0, 1.2, (38,),
             ("solomillo", "pavo")),
    Alimento("cerdo_solomillo", "Solomillo de cerdo", "proteina", 120, 21, 0, 4, (37,),
             ("solomillo de cerdo",), excluye=("iberico", "preparado", "provenzal", "medallones",
                                               "adobado", "relleno")),
    Alimento("salchichas_pollo", "Salchichas de pollo", "proteina", 210, 12, 3, 17, (52,),
             ("salchichas", "pollo"), excluye=("cerdo", "queso", "bocata")),
    Alimento("merluza", "Merluza congelada", "proteina", 75, 17, 0, 1, (34,),
             ("merluza",), alguna=("filetes", "porciones", "medallones", "lomos", "rodajas"),
             excluye=("empanad", "rebozad", "romana", "al huevo", "langostino", "varitas", "palitos"),
             duradero=True),
    Alimento("salmon", "Salmón congelado", "proteina", 180, 20, 0, 11, (34,),
             ("salmon",), alguna=("lomos", "filete", "medallones"),
             excluye=("ahumado", "verduras", "poke"), duradero=True),
    Alimento("pota", "Pota congelada", "proteina", 72, 15, 1, 1, (34,),
             ("pota",), excluye=("romana", "rebozad"), duradero=True),
    Alimento("atun", "Atún al natural", "proteina", 110, 25, 0, 1, (122,),
             ("atun", "natural"), duradero=True),
    Alimento("huevos", "Huevos", "proteina", 143, 12.6, 0.7, 9.5, (77,),
             ("huevos",), excluye=("cocidos", "codorniz"), gramos_unidad=60),
    Alimento("pavo_lonchas", "Pechuga de pavo en lonchas", "proteina", 100, 18, 2, 2, (48,),
             ("pechuga de pavo", "lonchas"), excluye=("cocida",)),
    # --- lácteos ---
    Alimento("leche", "Leche semidesnatada", "lacteos", 46, 3.2, 4.7, 1.6, (72,),
             ("leche semidesnatada",),
             excluye=("sin lactosa", "calcio", "cabra", "omega", "proteinas", "batido")),
    Alimento("queso_batido", "Queso fresco batido 0%", "lacteos", 46, 8, 3.4, 0.1, (53,),
             ("queso fresco batido", "0%")),
    Alimento("yogur", "Yogur natural 0%", "lacteos", 40, 4.5, 5, 0.1, (103,),
             ("yogur natural", "0%"), excluye=("edulcorado",)),
    Alimento("queso_cabra", "Queso fresco de cabra", "lacteos", 200, 12, 1.5, 16, (53,),
             ("queso fresco de cabra",)),
    # --- fruta ---
    Alimento("platano", "Plátano", "fruta", 60, 0.7, 14, 0.2, (27,),
             (), alguna=("platano", "banana"), excluye=("macho", "deshidratado")),
    Alimento("manzana", "Manzana", "fruta", 48, 0.3, 12, 0.2, (27,), ("manzana",)),
    Alimento("pera", "Pera", "fruta", 50, 0.4, 12, 0.1, (27,), ("pera",)),
    Alimento("naranja", "Naranja / mandarina", "fruta", 35, 0.7, 8, 0.1, (27,),
             (), alguna=("naranja", "mandarina"), excluye=("zumo",)),
    Alimento("kiwi", "Kiwi", "fruta", 50, 0.9, 11, 0.5, (27,), ("kiwi",)),
    Alimento("aguacate", "Aguacate", "fruta", 115, 1.4, 2, 10.5, (27,), ("aguacate",)),
    # --- verdura ---
    Alimento("tomate", "Tomate", "verdura", 18, 0.9, 3.5, 0.2, (29,),
             ("tomate",), excluye=("cherry", "rallado", "rama", "rosa", "negro", "frito", "kumato")),
    Alimento("pimiento_rojo", "Pimiento rojo", "verdura", 31, 1, 6, 0.3, (29,),
             ("pimiento rojo",)),
    Alimento("pimiento_verde", "Pimiento verde", "verdura", 25, 1, 5, 0.3, (29,),
             ("pimiento verde",)),
    Alimento("cebolla", "Cebolla", "verdura", 40, 1.1, 9, 0.1, (29,),
             ("cebolla",), excluye=("tierna", "morada", "frita", "salteado", "crujiente")),
    Alimento("calabacin", "Calabacín", "verdura", 17, 1.2, 3, 0.3, (29,), ("calabacin",)),
    Alimento("zanahoria", "Zanahoria", "verdura", 40, 0.9, 9, 0.2, (29,),
             ("zanahoria",), excluye=("palitos", "rallada")),
    Alimento("canonigos", "Canónigos", "verdura", 21, 2, 3.6, 0.4, (28,), ("canonigos",)),
    # --- hidratos ---
    Alimento("pan", "Pan de molde integral", "pan", 250, 10, 42, 4, (60,),
             ("pan de molde", "integral"),
             excluye=("sin corteza", "espelta", "avena", "burger", "tortilla", "hogaza")),
    Alimento("patata", "Patata", "verdura", 77, 2, 17, 0.1, (29,),
             ("patata",), excluye=("freir", "guarnicion", "boniato", "dulce")),
    Alimento("batata", "Batata", "verdura", 86, 1.6, 20, 0.1, (29,),
             ("batata",), excluye=("microondas",)),
    Alimento("arroz", "Arroz", "despensa", 350, 7, 78, 0.6, (118,),
             ("arroz",), alguna=("redondo", "largo"), excluye=("cocido", "integral"), duradero=True),
    Alimento("pasta", "Pasta", "despensa", 355, 12, 72, 1.5, (120,),
             (), alguna=("macarron", "espagueti", "espiral", "helice", "pluma"),
             excluye=("sin gluten", "rellen", "fresca", "lenteja", "garbanzo", "integral", "proteina",
                      "vegetales"),
             duradero=True),
    Alimento("avena", "Copos de avena", "despensa", 370, 13, 60, 7, (78,),
             ("avena",), alguna=("copos", "molida"),
             excluye=("chocolate", "cacao", "crunchy", "sin gluten", "barrita", "semillas", "cereales"),
             duradero=True),
    Alimento("masa_empanada", "Masa fresca de empanada", "pan", 320, 6.5, 42, 14, (69,),
             ("masa fresca empanada",)),
    Alimento("noodles", "Noodles", "despensa", 360, 11, 72, 2, (120,),
             ("noodles",), excluye=("yatekomo", "sabor"), duradero=True),
    Alimento("tortillas", "Tortillas de trigo", "pan", 310, 8.5, 50, 7.5, (60,),
             ("tortillas de trigo",),
             excluye=("integral", "maxi", "mini", "delibreads", "maiz", "avena"), gramos_unidad=45),
    # --- legumbre de bote (plato listo) ---
    Alimento("fabada", "Fabada de bote", "despensa", 130, 6.5, 9, 7.5, (140,),
             ("fabada",), duradero=True),
    Alimento("lentejas", "Lentejas a la riojana de bote", "despensa", 100, 5.5, 11, 3.5, (140,),
             ("lentejas",), alguna=("riojana", "jardinera"), excluye=("listo",), duradero=True),
    Alimento("cocido", "Cocido de bote", "despensa", 120, 7, 9, 6, (140,),
             ("cocido",), excluye=("arroz",), duradero=True),
    # --- grasas y otros ---
    Alimento("aove", "Aceite de oliva virgen extra", "despensa", 900, 0, 0, 100, (112,),
             ("aceite de oliva virgen extra",), excluye=("spray", "seleccion", "picual"),
             duradero=True),
    Alimento("nueces", "Nueces", "despensa", 650, 15, 7, 65, (133,),
             ("nuez",), alguna=("pelada", "troceada"), excluye=("brasil", "pecana"), duradero=True),
    Alimento("crema_cacahuete", "Crema de cacahuete 100%", "despensa", 590, 25, 12, 49, (92,),
             ("crema de cacahuete",), duradero=True),
    Alimento("miel", "Miel", "despensa", 304, 0.3, 82, 0, (90,), ("miel de flores",),
             duradero=True),
    Alimento("soja", "Salsa de soja", "despensa", 60, 8, 6, 0, (117,),
             ("salsa de soja",), excluye=("sin gluten",), duradero=True),
]}

# Proteína whey de HSN (Evowhey Protein). No se compra en Mercadona, pero cuenta para los
# macros. Valores medios por 100 g: revisa la etiqueta de tu sabor.
WHEY = Alimento("whey", "Whey Evowhey (HSN)", "suplementos", 380, 75, 7, 6, (), ())

# Se miden en ml
LIQUIDOS = {"leche", "aove", "soja"}

# Mínimo de aceite para cocinar comida y cena, aunque la grasa ya esté cubierta
AOVE_MINIMO = 10


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


_EXCLUIDOS = [re.compile(rf"\b{re.escape(normalizar(e))}\b") for e in ALIMENTOS_EXCLUIDOS]


def excluido(nombre: str) -> bool:
    n = normalizar(nombre)
    return any(p.search(n) for p in _EXCLUIDOS)


def encaja(alimento: Alimento, nombre_producto: str) -> bool:
    n = normalizar(nombre_producto)
    if excluido(n):
        return False
    if not all(normalizar(k) in n for k in alimento.incluye):
        return False
    if alimento.alguna and not any(normalizar(k) in n for k in alimento.alguna):
        return False
    return not any(normalizar(k) in n for k in alimento.excluye)


def disponibles() -> dict[str, Alimento]:
    """Catálogo sin los alimentos que el usuario ha excluido."""
    return {k: a for k, a in CATALOGO.items() if not excluido(a.nombre)}


def categorias() -> list[int]:
    return sorted({c for a in disponibles().values() for c in a.categorias})


def con_suplementos() -> dict[str, Alimento]:
    """Catálogo disponible más la whey, para cuadrar macros (no para la compra)."""
    return {**disponibles(), WHEY.clave: WHEY} if WHEY_GRAMOS > 0 else disponibles()


def suplementos_diarios() -> dict[str, float]:
    return {WHEY.clave: WHEY_GRAMOS} if WHEY_GRAMOS > 0 else {}
