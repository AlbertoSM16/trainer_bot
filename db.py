"""Persistencia en SQLite: usuarios, métricas corporales y registro de entrenos."""
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
"""


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
