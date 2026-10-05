"""Configuración del bot de entrenamiento."""
import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

# Lunes en el que arranca el plan (debe ser lunes)
PLAN_START = date.fromisoformat(os.getenv("PLAN_START", "2026-10-05"))

# Hora del recordatorio diario (zona horaria local del servidor)
REMINDER_HOUR = int(os.getenv("REMINDER_HOUR", "7"))
REMINDER_MINUTE = int(os.getenv("REMINDER_MINUTE", "30"))
TIMEZONE = os.getenv("TIMEZONE", "Europe/Madrid")

DB_PATH = os.getenv("DB_PATH", "entrenos.db")

# --- Notion (opcional) ---
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "")
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID", "")
NOTION_STATE_FILE = os.getenv("NOTION_STATE_FILE", "notion_dbs.json")

# --- Dieta y compra en Mercadona ---
# Almacén de Mercadona (depende del código postal; 18002 -> 2183)
MERCADONA_WH = os.getenv("MERCADONA_WH", "2183")
PRESUPUESTO_SEMANAL = float(os.getenv("PRESUPUESTO_SEMANAL", "55"))
# Superávit diario para un volumen muy limpio (~+0,1 kg/semana)
SUPERAVIT_KCAL = int(os.getenv("SUPERAVIT_KCAL", "150"))
OBJETIVO_KG_SEMANA = float(os.getenv("OBJETIVO_KG_SEMANA", "0.1"))
# Al llegar a este peso se pasa a mantenimiento (superávit 0)
PESO_OBJETIVO = float(os.getenv("PESO_OBJETIVO", "81"))
# Envío automático de la lista de la compra (los sábados)
COMPRA_HOUR = int(os.getenv("COMPRA_HOUR", "8"))
COMPRA_MINUTE = int(os.getenv("COMPRA_MINUTE", "30"))
# Suplementación diaria (HSN). La whey cuenta para los macros; 0 = no tomas
WHEY_GRAMOS = float(os.getenv("WHEY_GRAMOS", "30"))
CREATINA_GRAMOS = float(os.getenv("CREATINA_GRAMOS", "5"))
# Alimentos que no quieres en la lista (separados por comas)
ALIMENTOS_EXCLUIDOS = [
    s.strip() for s in os.getenv(
        "ALIMENTOS_EXCLUIDOS",
        "brócoli,col,coles,repollo,lombarda,coles de bruselas,coliflor").split(",")
    if s.strip()
]

# --- Coach conversacional con Claude (opcional) ---
# API de Anthropic (console.anthropic.com, pago por uso). La suscripción Claude Pro no sirve.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
# Tope de mensajes al día por chat para controlar el gasto
CLAUDE_MAX_MENSAJES_DIA = int(os.getenv("CLAUDE_MAX_MENSAJES_DIA", "40"))

# Perfil del atleta
ATHLETE = {
    "nombre": "Alberto",
    "edad": 25,
    "altura_cm": 188,
    "peso_inicial_kg": 79.0,
    "marca_media": "4:38 min/km (marzo 2026)",
    "trabajo": "Ingeniero informático, trabaja de lunes a jueves de 8:00 a 17:30",
    "objetivo": "Mejorar forma física y cardio y rendir al máximo corriendo, sin competir. "
                "Subir a 81 kg ganando músculo y bajando algo el % de grasa.",
}

# Mejor marca en media maratón (segundos por km). Mientras no haya un test registrado,
# los ritmos salen de aquí más un margen por las semanas sin correr.
MARCA_MEDIA_SEC = 4 * 60 + 38
DESENTRENO_SEC = int(os.getenv("DESENTRENO_SEC", "15"))
