"""Persistencia en SQLite: usuarios, métricas, entrenos, cambios del plan, tests de carrera,
sensaciones, platos cambiados y memoria del coach."""
import sqlite3
from contextlib import contextmanager
from datetime import date

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    chat_id     INTEGER PRIMARY KEY,
    nombre      TEXT,
    alta        TEXT,
    recordatorio INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS metricas (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    fecha    TEXT NOT NULL,
    peso     REAL,
    grasa    REAL
);
CREATE TABLE IF NOT EXISTS entrenos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    fecha    TEXT NOT NULL,
    titulo   TEXT,
    nota     TEXT,
    UNIQUE(chat_id, fecha)
);
CREATE TABLE IF NOT EXISTS mercadona_productos (
    id          TEXT NOT NULL,
    categoria   INTEGER NOT NULL,
    nombre      TEXT NOT NULL,
    precio      REAL,
    tam         REAL,
    formato     TEXT,
    precio_ref  REAL,
    formato_ref TEXT,
    url         TEXT,
    PRIMARY KEY (id, categoria)
);
CREATE TABLE IF NOT EXISTS mercadona_categorias (
    id          INTEGER PRIMARY KEY,
    actualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cambios (
    chat_id  INTEGER NOT NULL,
    fecha    TEXT NOT NULL,
    origen   TEXT NOT NULL,  -- día del plan cuya sesión se hace en `fecha`
    modo     TEXT,           -- NULL | descanso | corta | bici | natacion
    PRIMARY KEY (chat_id, fecha)
);
CREATE TABLE IF NOT EXISTS nutricion_ajuste (
    chat_id     INTEGER PRIMARY KEY,
    kcal        INTEGER NOT NULL DEFAULT 0,
    actualizado TEXT
);
CREATE TABLE IF NOT EXISTS tests (
    chat_id   INTEGER NOT NULL,
    fecha     TEXT NOT NULL,
    km        REAL NOT NULL,
    segundos  INTEGER NOT NULL,
    PRIMARY KEY (chat_id, fecha)
);
CREATE TABLE IF NOT EXISTS sensaciones (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    fecha     TEXT NOT NULL,
    energia   INTEGER,          -- 1 (fundido) a 5 (a tope)
    molestias TEXT,
    nota      TEXT
);
CREATE TABLE IF NOT EXISTS menu_cambios (
    chat_id  INTEGER NOT NULL,
    fecha    TEXT NOT NULL,
    comida   TEXT NOT NULL,     -- desayuno | comida | merienda | cena
    plato    TEXT NOT NULL,     -- clave de menu.PLATOS
    PRIMARY KEY (chat_id, fecha, comida)
);
CREATE TABLE IF NOT EXISTS conversacion (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id   INTEGER NOT NULL,
    rol       TEXT NOT NULL,    -- user | assistant
    texto     TEXT NOT NULL,
    creado    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coach_uso (
    chat_id   INTEGER NOT NULL,
    fecha     TEXT NOT NULL,
    mensajes  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, fecha)
);
"""
# Versión del esquema (PRAGMA user_version). 2: semana nueva (L pecho … D bici), así que
# los cambios guardados con el mapeo anterior dejan de tener sentido y se borran.
VERSION = 2


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        version = c.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            c.execute("DELETE FROM cambios")
        if version < VERSION:
            c.execute(f"PRAGMA user_version = {VERSION}")


def alta_usuario(chat_id: int, nombre: str):
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO usuarios (chat_id, nombre, alta) VALUES (?, ?, ?)",
            (chat_id, nombre, date.today().isoformat()),
        )


def usuarios_con_recordatorio() -> list[int]:
    with conn() as c:
        return [r["chat_id"] for r in c.execute(
            "SELECT chat_id FROM usuarios WHERE recordatorio = 1")]


def set_recordatorio(chat_id: int, activo: bool):
    with conn() as c:
        c.execute("UPDATE usuarios SET recordatorio = ? WHERE chat_id = ?",
                  (1 if activo else 0, chat_id))


def registrar_metrica(chat_id: int, peso: float | None = None, grasa: float | None = None):
    hoy = date.today().isoformat()
    with conn() as c:
        fila = c.execute(
            "SELECT id, peso, grasa FROM metricas WHERE chat_id = ? AND fecha = ?",
            (chat_id, hoy)).fetchone()
        if fila:
            c.execute(
                "UPDATE metricas SET peso = COALESCE(?, peso), grasa = COALESCE(?, grasa) WHERE id = ?",
                (peso, grasa, fila["id"]))
        else:
            c.execute("INSERT INTO metricas (chat_id, fecha, peso, grasa) VALUES (?, ?, ?, ?)",
                      (chat_id, hoy, peso, grasa))


def historico_metricas(chat_id: int, limite: int = 10) -> list[sqlite3.Row]:
    with conn() as c:
        return list(c.execute(
            "SELECT * FROM metricas WHERE chat_id = ? ORDER BY fecha DESC LIMIT ?",
            (chat_id, limite)))


def marcar_hecho(chat_id: int, fecha: date, titulo: str, nota: str = ""):
    with conn() as c:
        c.execute(
            "INSERT INTO entrenos (chat_id, fecha, titulo, nota) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id, fecha) DO UPDATE SET titulo = excluded.titulo, nota = excluded.nota",
            (chat_id, fecha.isoformat(), titulo, nota))


def entrenos_entre(chat_id: int, desde: date, hasta: date) -> list[sqlite3.Row]:
    with conn() as c:
        return list(c.execute(
            "SELECT * FROM entrenos WHERE chat_id = ? AND fecha BETWEEN ? AND ? ORDER BY fecha",
            (chat_id, desde.isoformat(), hasta.isoformat())))


def total_entrenos(chat_id: int) -> int:
    with conn() as c:
        return c.execute("SELECT COUNT(*) n FROM entrenos WHERE chat_id = ?",
                         (chat_id,)).fetchone()["n"]


# ---------- cambios en el plan ----------

def cambios_entre(chat_id: int, desde: date, hasta: date) -> dict[date, tuple[date, str | None]]:
    """{fecha: (origen, modo)} de los días cambiados en el rango."""
    with conn() as c:
        return {date.fromisoformat(r["fecha"]): (date.fromisoformat(r["origen"]), r["modo"])
                for r in c.execute(
                    "SELECT fecha, origen, modo FROM cambios WHERE chat_id = ? "
                    "AND fecha BETWEEN ? AND ?", (chat_id, desde.isoformat(), hasta.isoformat()))}


def guardar_cambios(chat_id: int, cambios: dict[date, tuple[date, str | None] | None]):
    """Aplica los cambios de varios días a la vez. `None` borra el cambio de ese día."""
    with conn() as c:
        for fecha, cambio in cambios.items():
            if cambio is None:
                c.execute("DELETE FROM cambios WHERE chat_id = ? AND fecha = ?",
                          (chat_id, fecha.isoformat()))
            else:
                c.execute(
                    "INSERT INTO cambios (chat_id, fecha, origen, modo) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, fecha) DO UPDATE SET origen = excluded.origen, "
                    "modo = excluded.modo",
                    (chat_id, fecha.isoformat(), cambio[0].isoformat(), cambio[1]))


# ---------- dieta ----------

def ultimo_peso(chat_id: int) -> float | None:
    with conn() as c:
        fila = c.execute(
            "SELECT peso FROM metricas WHERE chat_id = ? AND peso IS NOT NULL "
            "ORDER BY fecha DESC LIMIT 1", (chat_id,)).fetchone()
        return fila["peso"] if fila else None


def pesos_desde(chat_id: int, desde: date) -> list[tuple[date, float]]:
    with conn() as c:
        return [(date.fromisoformat(r["fecha"]), r["peso"]) for r in c.execute(
            "SELECT fecha, peso FROM metricas WHERE chat_id = ? AND peso IS NOT NULL "
            "AND fecha >= ? ORDER BY fecha", (chat_id, desde.isoformat()))]


def ajuste_kcal(chat_id: int) -> tuple[int, str | None]:
    """Ajuste calórico acumulado y fecha de la última actualización."""
    with conn() as c:
        fila = c.execute("SELECT kcal, actualizado FROM nutricion_ajuste WHERE chat_id = ?",
                         (chat_id,)).fetchone()
        return (fila["kcal"], fila["actualizado"]) if fila else (0, None)


def guardar_ajuste_kcal(chat_id: int, kcal: int, fecha: date):
    with conn() as c:
        c.execute(
            "INSERT INTO nutricion_ajuste (chat_id, kcal, actualizado) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET kcal = excluded.kcal, actualizado = excluded.actualizado",
            (chat_id, kcal, fecha.isoformat()))


# ---------- tests de carrera ----------

def guardar_test(chat_id: int, fecha: date, km: float, segundos: int):
    with conn() as c:
        c.execute(
            "INSERT INTO tests (chat_id, fecha, km, segundos) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id, fecha) DO UPDATE SET km = excluded.km, "
            "segundos = excluded.segundos",
            (chat_id, fecha.isoformat(), km, segundos))


def ultimo_test(chat_id: int) -> tuple[date, float, int] | None:
    with conn() as c:
        fila = c.execute("SELECT fecha, km, segundos FROM tests WHERE chat_id = ? "
                         "ORDER BY fecha DESC LIMIT 1", (chat_id,)).fetchone()
        return (date.fromisoformat(fila["fecha"]), fila["km"], fila["segundos"]) if fila else None


def tests(chat_id: int, limite: int = 10) -> list[sqlite3.Row]:
    with conn() as c:
        return list(c.execute("SELECT * FROM tests WHERE chat_id = ? ORDER BY fecha DESC "
                              "LIMIT ?", (chat_id, limite)))


# ---------- sensaciones ----------

def guardar_sensaciones(chat_id: int, fecha: date, energia: int | None,
                        molestias: str = "", nota: str = ""):
    with conn() as c:
        c.execute("INSERT INTO sensaciones (chat_id, fecha, energia, molestias, nota) "
                  "VALUES (?, ?, ?, ?, ?)", (chat_id, fecha.isoformat(), energia, molestias, nota))


def sensaciones_desde(chat_id: int, desde: date) -> list[sqlite3.Row]:
    with conn() as c:
        return list(c.execute("SELECT * FROM sensaciones WHERE chat_id = ? AND fecha >= ? "
                              "ORDER BY id", (chat_id, desde.isoformat())))


# ---------- platos cambiados ----------

def platos_entre(chat_id: int, desde: date, hasta: date) -> dict[date, dict[str, str]]:
    """{fecha: {comida: plato}} de los platos cambiados por el usuario en el rango."""
    out: dict[date, dict[str, str]] = {}
    with conn() as c:
        for r in c.execute("SELECT fecha, comida, plato FROM menu_cambios WHERE chat_id = ? "
                           "AND fecha BETWEEN ? AND ?",
                           (chat_id, desde.isoformat(), hasta.isoformat())):
            out.setdefault(date.fromisoformat(r["fecha"]), {})[r["comida"]] = r["plato"]
    return out


def guardar_plato(chat_id: int, fecha: date, comida: str, plato: str | None):
    """`None` vuelve al plato del plan."""
    with conn() as c:
        if plato is None:
            c.execute("DELETE FROM menu_cambios WHERE chat_id = ? AND fecha = ? AND comida = ?",
                      (chat_id, fecha.isoformat(), comida))
        else:
            c.execute(
                "INSERT INTO menu_cambios (chat_id, fecha, comida, plato) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, fecha, comida) DO UPDATE SET plato = excluded.plato",
                (chat_id, fecha.isoformat(), comida, plato))


# ---------- memoria del coach ----------

def historial(chat_id: int, limite: int = 20) -> list[dict]:
    """Últimos mensajes de la conversación con el coach, del más antiguo al más reciente."""
    with conn() as c:
        filas = list(c.execute("SELECT rol, texto FROM conversacion WHERE chat_id = ? "
                               "ORDER BY id DESC LIMIT ?", (chat_id, limite)))
    return [{"role": r["rol"], "content": r["texto"]} for r in reversed(filas)]


def guardar_mensajes(chat_id: int, mensajes: list[tuple[str, str]], cuando: str,
                     conservar: int = 60):
    with conn() as c:
        c.executemany("INSERT INTO conversacion (chat_id, rol, texto, creado) VALUES (?, ?, ?, ?)",
                      [(chat_id, rol, texto, cuando) for rol, texto in mensajes])
        c.execute("DELETE FROM conversacion WHERE chat_id = ? AND id NOT IN (SELECT id FROM "
                  "conversacion WHERE chat_id = ? ORDER BY id DESC LIMIT ?)",
                  (chat_id, chat_id, conservar))


def borrar_historial(chat_id: int):
    with conn() as c:
        c.execute("DELETE FROM conversacion WHERE chat_id = ?", (chat_id,))


def sumar_uso_coach(chat_id: int, fecha: date) -> int:
    """Suma un mensaje al contador del día y devuelve el total."""
    with conn() as c:
        c.execute("INSERT INTO coach_uso (chat_id, fecha, mensajes) VALUES (?, ?, 1) "
                  "ON CONFLICT(chat_id, fecha) DO UPDATE SET mensajes = mensajes + 1",
                  (chat_id, fecha.isoformat()))
        return c.execute("SELECT mensajes FROM coach_uso WHERE chat_id = ? AND fecha = ?",
                         (chat_id, fecha.isoformat())).fetchone()["mensajes"]


# ---------- caché de Mercadona ----------

def categoria_actualizada(categoria: int) -> str | None:
    with conn() as c:
        fila = c.execute("SELECT actualizado FROM mercadona_categorias WHERE id = ?",
                         (categoria,)).fetchone()
        return fila["actualizado"] if fila else None


def guardar_categoria(categoria: int, productos: list[dict], cuando: str):
    """Sustituye los productos guardados de una categoría por los recién descargados."""
    with conn() as c:
        c.execute("DELETE FROM mercadona_productos WHERE categoria = ?", (categoria,))
        c.executemany(
            "INSERT OR REPLACE INTO mercadona_productos "
            "(id, categoria, nombre, precio, tam, formato, precio_ref, formato_ref, url) "
            "VALUES (:id, :categoria, :nombre, :precio, :tam, :formato, :precio_ref, :formato_ref, :url)",
            [{**p, "categoria": categoria} for p in productos])
        c.execute(
            "INSERT INTO mercadona_categorias (id, actualizado) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET actualizado = excluded.actualizado",
            (categoria, cuando))


def productos_en(categorias: list[int]) -> list[sqlite3.Row]:
    if not categorias:
        return []
    marcas = ",".join("?" * len(categorias))
    with conn() as c:
        return list(c.execute(
            f"SELECT * FROM mercadona_productos WHERE categoria IN ({marcas})", categorias))
