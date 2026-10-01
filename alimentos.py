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
    duradero: bool = False              # despensa: no hace falta comprarlo cada semana


CATALOGO = {a.clave: a for a in [
    # --- proteína ---
    Alimento("pollo_pechuga", "Pechuga de pollo", "proteina", 110, 23, 0, 1.5, (38,),
             ("pechuga", "pollo"),
             excluye=("marinad", "empanad", "lonchas", "braseada", "tiras", "hierbas", "certificado")),
    Alimento("pollo_contramuslo", "Contramuslo de pollo sin piel", "proteina", 130, 19, 0, 6, (38,),
             ("contramuslo", "pollo", "sin piel"), excluye=("certificado", "marinad")),
    Alimento("pavo", "Pechuga de pavo", "proteina", 105, 24, 0, 1, (38,),
             ("pechuga", "pavo"), excluye=("marinad", "lonchas", "empanad")),
    Alimento("merluza", "Merluza congelada", "proteina", 75, 17, 0, 1, (34,),
             ("merluza",), alguna=("filetes", "porciones", "medallones", "lomos", "rodajas"),
             excluye=("empanad", "rebozad", "romana", "al huevo", "langostino", "varitas", "palitos")),
    Alimento("atun", "Atún al natural", "proteina", 110, 25, 0, 1, (122,),
             ("atun", "natural"), duradero=True),
    Alimento("huevos", "Huevos", "proteina", 143, 12.6, 0.7, 9.5, (77,),
             ("huevos",), excluye=("cocidos", "codorniz"), gramos_unidad=60),
    Alimento("garbanzos", "Garbanzos cocidos", "despensa", 120, 7, 16, 2.5, (121,),
             ("garbanzo", "cocido"), duradero=True),
    Alimento("lentejas", "Lentejas cocidas", "despensa", 100, 8, 13, 0.5, (121,),
             ("lenteja", "cocida"), duradero=True),
    # --- lácteos ---
    Alimento("leche", "Leche semidesnatada", "lacteos", 46, 3.2, 4.7, 1.6, (72,),
             ("leche semidesnatada",),
             excluye=("sin lactosa", "calcio", "cabra", "omega", "proteinas", "batido")),
    Alimento("queso_batido", "Queso fresco batido 0%", "lacteos", 46, 8, 3.4, 0.1, (53,),
             ("queso fresco batido", "0%")),
    # --- fruta ---
    Alimento("platano", "Plátano", "fruta", 60, 0.7, 14, 0.2, (27,),
             (), alguna=("platano", "banana"), excluye=("macho", "deshidratado")),
    Alimento("manzana", "Manzana", "fruta", 48, 0.3, 12, 0.2, (27,), ("manzana",)),
    Alimento("naranja", "Naranja / mandarina", "fruta", 35, 0.7, 8, 0.1, (27,),
             (), alguna=("naranja", "mandarina"), excluye=("zumo",)),
    # --- verdura ---
    Alimento("tomate", "Tomate", "verdura", 18, 0.9, 3.5, 0.2, (29,),
             ("tomate",), excluye=("cherry", "rallado", "rama", "rosa", "negro", "frito", "kumato")),
    Alimento("calabacin", "Calabacín", "verdura", 17, 1.2, 3, 0.3, (29,), ("calabacin",)),
    Alimento("pimiento", "Pimiento", "verdura", 25, 1, 5, 0.3, (29,),
             ("pimiento",), excluye=("semipicante", "salteado", "padron", "tricolor", "asado")),
    Alimento("cebolla", "Cebolla", "verdura", 40, 1.1, 9, 0.1, (29,),
             ("cebolla",), excluye=("tierna", "morada", "frita", "salteado", "crujiente")),
    Alimento("zanahoria", "Zanahoria", "verdura", 40, 0.9, 9, 0.2, (29,),
             ("zanahoria",), excluye=("palitos", "rallada")),
    Alimento("espinacas", "Espinacas", "verdura", 23, 2.9, 3.6, 0.4, (29, 28), ("espinaca",)),
    # --- hidratos ---
    Alimento("pan", "Pan de molde integral", "pan", 250, 10, 42, 4, (60,),
             ("pan de molde", "integral"),
             excluye=("sin corteza", "espelta", "avena", "burger", "tortilla", "hogaza")),
    Alimento("patata", "Patata", "verdura", 77, 2, 17, 0.1, (29,),
             ("patata",), excluye=("freir", "guarnicion", "boniato", "dulce")),
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
    # --- grasas ---
    Alimento("aove", "Aceite de oliva virgen extra", "despensa", 900, 0, 0, 100, (112,),
             ("aceite de oliva virgen extra",), excluye=("spray", "seleccion", "picual"),
             duradero=True),
    Alimento("nueces", "Nueces", "despensa", 650, 15, 7, 65, (133,),
             ("nuez",), alguna=("pelada", "troceada"), excluye=("brasil", "pecana"), duradero=True),
    Alimento("cacahuete", "Cacahuete tostado sin sal", "despensa", 590, 26, 12, 49, (133,),
             ("cacahuete", "tostado", "0% sal"), duradero=True),
]}

# Proteína whey de HSN (Evowhey Protein). No se compra en Mercadona, pero cuenta para los
# macros. Valores medios por 100 g: revisa la etiqueta de tu sabor.
WHEY = Alimento("whey", "Whey Evowhey (HSN)", "suplementos", 380, 75, 7, 6, (), ())

# Cantidades fijas diarias (g o ml), independientes de las kcal
BASE_DIARIA = {
    "huevos": 120, "leche": 300, "queso_batido": 200,
    "platano": 250, "manzana": 190, "naranja": 250,
    "tomate": 120, "calabacin": 100, "pimiento": 60, "cebolla": 50,
    "zanahoria": 50, "espinacas": 40,
    "aove": 25, "nueces": 20, "cacahuete": 15,
}

# Reparto de la proteína que falta (tras la base) entre las fuentes principales.
# Se prueban en orden: si una variante se pasa del presupuesto, se usa la siguiente.
VARIANTES_PROTEINA = [
    ("equilibrada", {"pollo_pechuga": 40, "pavo": 15, "atun": 15, "merluza": 10,
                     "garbanzos": 10, "lentejas": 10}, {}),
    ("ahorro", {"pollo_pechuga": 35, "pollo_contramuslo": 25, "atun": 15,
                "garbanzos": 12.5, "lentejas": 12.5}, {}),
    ("ahorro máximo", {"pollo_contramuslo": 45, "pollo_pechuga": 15,
                       "garbanzos": 20, "lentejas": 20}, {"huevos": 180}),
]

# Reparto de los hidratos que faltan
REPARTO_HIDRATOS = {"arroz": 35, "avena": 20, "pasta": 20, "pan": 15, "patata": 10}

# Se miden en ml
LIQUIDOS = {"leche", "aove"}

# Mínimo de grasa añadida (AOVE) aunque se cubra la grasa con otros alimentos
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
