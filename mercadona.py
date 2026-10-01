"""Cliente de la API (no oficial) de tienda.mercadona.es con caché en SQLite.

Es best-effort: si la API falla se usan los precios guardados y, si no hay,
la lista se genera sin precios. Nunca debe romper el bot.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import db
from alimentos import Alimento, categorias, encaja
from config import MERCADONA_WH

log = logging.getLogger(__name__)

BASE_URL = "https://tienda.mercadona.es/api"
CADUCIDAD = timedelta(days=6)
CABECERAS = {"User-Agent": "Mozilla/5.0 (bot de entrenamiento personal)",
             "Accept": "application/json"}

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None


@dataclass
class Producto:
    id: str
    nombre: str
    precio: float          # precio del envase
    gramos: float          # contenido del envase en g (o ml)
    url: str

    @property
    def precio_kg(self) -> float:
        return self.precio / (self.gramos / 1000)


def _caducada(categoria: int) -> bool:
    cuando = db.categoria_actualizada(categoria)
    return not cuando or datetime.now() - datetime.fromisoformat(cuando) > CADUCIDAD


def _num(valor) -> float | None:
    try:
        return float(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _descargar(client, categoria: int) -> list[dict]:
    r = await client.get(f"{BASE_URL}/categories/{categoria}/",
                         params={"lang": "es", "wh": MERCADONA_WH})
    r.raise_for_status()
    productos = []
    for sub in r.json().get("categories", []):
        for p in sub.get("products", []):
            pi = p.get("price_instructions") or {}
            productos.append({
                "id": str(p["id"]),
                "nombre": p.get("display_name", ""),
                "precio": _num(pi.get("unit_price")),
                "tam": _num(pi.get("unit_size")),
                "formato": pi.get("size_format"),
                "precio_ref": _num(pi.get("reference_price")),
                "formato_ref": pi.get("reference_format"),
                "url": p.get("share_url", ""),
            })
    return productos


async def actualizar(forzar: bool = False) -> bool:
    """Descarga las categorías caducadas. Devuelve False si alguna ha fallado."""
    if httpx is None:
        return False
    pendientes = [c for c in categorias() if forzar or _caducada(c)]
    if not pendientes:
        return True
    limite = asyncio.Semaphore(4)
    ok = True

    async def una(client, cat):
        nonlocal ok
        async with limite:
            try:
                productos = await _descargar(client, cat)
                db.guardar_categoria(cat, productos, datetime.now().isoformat(timespec="seconds"))
            except Exception as e:  # noqa: BLE001
                ok = False
                log.warning("No se pudo actualizar la categoría %s de Mercadona: %s", cat, e)

    async with httpx.AsyncClient(headers=CABECERAS, timeout=20) as client:
        await asyncio.gather(*(una(client, c) for c in pendientes))
    log.info("Mercadona: %d categorías actualizadas (wh=%s)", len(pendientes), MERCADONA_WH)
    return ok


def _gramos(fila, alimento: Alimento) -> float | None:
    formato = (fila["formato"] or "").lower()
    if formato in ("kg", "l"):
        if fila["tam"]:
            return fila["tam"] * 1000
        # algunos congelados no traen tamaño: se deduce del precio por kg
        if fila["precio_ref"] and (fila["formato_ref"] or "").lower() in ("kg", "l"):
            return fila["precio"] / fila["precio_ref"] * 1000
    if formato == "ud" and alimento.gramos_unidad and fila["tam"]:
        return fila["tam"] * alimento.gramos_unidad
    return None


def candidatos(alimento: Alimento) -> list[Producto]:
    """Productos en caché que encajan con un alimento del catálogo."""
    vistos, out = set(), []
    for fila in db.productos_en(list(alimento.categorias)):
        if fila["id"] in vistos or not fila["precio"] or not encaja(alimento, fila["nombre"]):
            continue
        gramos = _gramos(fila, alimento)
        if not gramos:
            continue
        vistos.add(fila["id"])
        out.append(Producto(fila["id"], fila["nombre"], fila["precio"], gramos, fila["url"]))
    return out
