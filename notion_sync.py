"""Sincronización con Notion.

SQLite es la fuente de verdad; Notion es un espejo. Si Notion falla o no está
configurado, el bot sigue funcionando con normalidad.

Crea automáticamente dos bases de datos dentro de la página padre indicada:
  • Entrenos — registro de sesiones completadas
  • Métricas — peso y % de grasa corporal
"""
import json
import logging
import os
from datetime import date

from config import NOTION_PARENT_PAGE_ID, NOTION_STATE_FILE, NOTION_TOKEN

log = logging.getLogger(__name__)

try:
    from notion_client import AsyncClient
    from notion_client.errors import APIResponseError
except ImportError:  # pragma: no cover
    AsyncClient = None
    APIResponseError = Exception

DB_ENTRENOS = "Entrenos"
DB_METRICAS = "Métricas"

ESQUEMA_ENTRENOS = {
    "Sesión": {"title": {}},
    "Fecha": {"date": {}},
    "Día": {"select": {"options": [
        {"name": d, "color": c} for d, c in zip(
            ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
            ["blue", "green", "yellow", "orange", "pink", "purple", "gray"])]}},
    "Semana": {"number": {}},
    "Fase": {"select": {"options": [
        {"name": "Reconstrucción aeróbica", "color": "gray"},
        {"name": "Base y fuerza aeróbica", "color": "blue"},
        {"name": "Construcción específica", "color": "orange"},
        {"name": "Afinado y competición", "color": "red"},
    ]}},
    "Tipo": {"multi_select": {"options": [
        {"name": "Gimnasio", "color": "purple"},
        {"name": "Carrera", "color": "red"},
        {"name": "Natación", "color": "blue"},
        {"name": "Bici", "color": "green"},
        {"name": "Descanso", "color": "gray"},
    ]}},
    "Descarga": {"checkbox": {}},
    "Notas": {"rich_text": {}},
}

ESQUEMA_METRICAS = {
    "Registro": {"title": {}},
    "Fecha": {"date": {}},
    "Peso (kg)": {"number": {"format": "number"}},
    "Grasa (%)": {"number": {"format": "percent"}},
    "IMC": {"number": {"format": "number"}},
}


def activo() -> bool:
    return bool(NOTION_TOKEN and NOTION_PARENT_PAGE_ID and AsyncClient)


class NotionSync:
    def __init__(self):
        self.client = AsyncClient(auth=NOTION_TOKEN) if activo() else None
        self.db_ids: dict[str, str] = self._cargar_estado()

    # ---------- estado local (ids de las bases creadas) ----------

    def _cargar_estado(self) -> dict:
        if os.path.exists(NOTION_STATE_FILE):
            try:
                with open(NOTION_STATE_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                log.warning("No se pudo leer %s: %s", NOTION_STATE_FILE, e)
        return {}

    def _guardar_estado(self):
        try:
            with open(NOTION_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.db_ids, f, indent=2)
        except OSError as e:
            log.warning("No se pudo guardar %s: %s", NOTION_STATE_FILE, e)

    # ---------- creación de las bases ----------

    async def preparar(self) -> bool:
        """Crea las bases de datos si no existen. Devuelve True si Notion está listo."""
        if not self.client:
            return False
        try:
            for nombre, esquema in ((DB_ENTRENOS, ESQUEMA_ENTRENOS),
                                    (DB_METRICAS, ESQUEMA_METRICAS)):
                if nombre in self.db_ids and await self._existe(self.db_ids[nombre]):
                    continue
                db = await self.client.databases.create(
                    parent={"type": "page_id", "page_id": NOTION_PARENT_PAGE_ID},
                    title=[{"type": "text", "text": {"content": nombre}}],
                    properties=esquema,
                )
                self.db_ids[nombre] = db["id"]
                log.info("Base de Notion creada: %s (%s)", nombre, db["id"])
            self._guardar_estado()
            return True
        except APIResponseError as e:
            log.error("Error preparando Notion: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            log.error("Error inesperado preparando Notion: %s", e)
            return False

    async def _existe(self, db_id: str) -> bool:
        try:
            await self.client.databases.retrieve(database_id=db_id)
            return True
        except Exception:  # noqa: BLE001
            return False

    # ---------- escritura ----------

    async def guardar_entreno(self, fecha: date, titulo: str, nota: str,
                              semana: int, fase: str | None, dia: str,
                              tipos: list[str], descarga: bool) -> bool:
        db_id = self.db_ids.get(DB_ENTRENOS)
        if not self.client or not db_id:
            return False
        props = {
            "Sesión": {"title": [{"text": {"content": titulo[:200]}}]},
            "Fecha": {"date": {"start": fecha.isoformat()}},
            "Día": {"select": {"name": dia}},
            "Semana": {"number": semana},
            "Tipo": {"multi_select": [{"name": t} for t in tipos]},
            "Descarga": {"checkbox": descarga},
            "Notas": {"rich_text": [{"text": {"content": (nota or "")[:2000]}}]},
        }
        if fase:
            props["Fase"] = {"select": {"name": fase}}
        return await self._upsert(db_id, "Fecha", fecha, props)

    async def guardar_metrica(self, fecha: date, peso: float | None,
                              grasa: float | None, imc: float | None) -> bool:
        db_id = self.db_ids.get(DB_METRICAS)
        if not self.client or not db_id:
            return False
        props = {
            "Registro": {"title": [{"text": {"content": fecha.strftime("%d/%m/%Y")}}]},
            "Fecha": {"date": {"start": fecha.isoformat()}},
        }
        if peso is not None:
            props["Peso (kg)"] = {"number": peso}
        if grasa is not None:
            props["Grasa (%)"] = {"number": grasa / 100}
        if imc is not None:
            props["IMC"] = {"number": round(imc, 1)}
        return await self._upsert(db_id, "Fecha", fecha, props)

    async def _upsert(self, db_id: str, campo_fecha: str, fecha: date, props: dict) -> bool:
        """Actualiza la fila de esa fecha si existe; si no, la crea."""
        try:
            existentes = await self.client.databases.query(
                database_id=db_id,
                filter={"property": campo_fecha, "date": {"equals": fecha.isoformat()}},
                page_size=1,
            )
            if existentes["results"]:
                await self.client.pages.update(
                    page_id=existentes["results"][0]["id"], properties=props)
            else:
                await self.client.pages.create(
                    parent={"database_id": db_id}, properties=props)
            return True
        except APIResponseError as e:
            log.error("Error escribiendo en Notion: %s", e)
            return False
        except Exception as e:  # noqa: BLE001
            log.error("Error inesperado escribiendo en Notion: %s", e)
            return False

    async def url_bases(self) -> dict[str, str]:
        urls = {}
        for nombre, db_id in self.db_ids.items():
            urls[nombre] = f"https://notion.so/{db_id.replace('-', '')}"
        return urls

    async def cerrar(self):
        if self.client:
            await self.client.aclose()


sync = NotionSync()
