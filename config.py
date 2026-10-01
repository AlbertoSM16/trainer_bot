"""Configuración del bot de entrenamiento."""
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# Lunes en el que arranca el plan (debe ser lunes)
PLAN_START = date.fromisoformat(os.getenv("PLAN_START", "2026-10-05"))

# Día de la media maratón objetivo
RACE_DATE = date.fromisoformat(os.getenv("RACE_DATE", "2027-03-21"))
RACE_NAME = os.getenv("RACE_NAME", "Media Maratón")

# Hora del recordatorio diario (zona horaria local del servidor)
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "7"))
REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "30"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Madrid")

DB_PATH = os.getenv("DB_PATH", "entrenos.db")

# --- Notion (opcional) ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")
NOTION_STATE_FILE = os.getenv("NOTION_STATE_FILE", "notion_dbs.json")

# Perfil del atleta
ATHLETE = {
    "nombre": "Alberto",
    "edad": 25,
    "altura_cm": 188,
    "peso_inicial_kg": 78.0,
    "marca_media": "4:38 min/km",
}

# Ritmo objetivo en media maratón (segundos por km)
GOAL_PACE_SEC = 4 * 60 + 38
