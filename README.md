# 🏃‍♂️ Bot de entrenamiento

Bot de Telegram que gestiona un plan de **24 semanas** combinando gimnasio, carrera, natación y bici, con el objetivo de correr una media maratón por debajo de **4:38 min/km**.

## Puesta en marcha

1. **Crea el bot en Telegram**: habla con [@BotFather](https://t.me/BotFather), envía `/newbot` y copia el token.
2. **Configura el entorno**:
   ```bash
   cp .env.example .env
   # edita .env y pega tu TELEGRAM_TOKEN
   ```
3. **Instala dependencias**:
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```
4. **Arranca**:
   ```bash
   .venv/bin/python bot.py
   ```
5. En Telegram, busca tu bot y envía `/start`.

## Comandos

| Comando | Descripción |
|---|---|
| `/hoy` | Sesión completa de hoy |
| `/manana` | Sesión de mañana |
| `/semana [nº]` | Resumen de la semana |
| `/gym` | Detalle de la rutina de gimnasio de hoy |
| `/ritmos` | Tabla de ritmos y zonas (carrera, natación, bici) |
| `/fases` | Estructura completa del plan |
| `/hecho [nota]` | Marca el entreno de hoy como completado |
| `/peso 78.4` | Registra tu peso |
| `/grasa 12.5` | Registra tu % de grasa corporal |
| `/stats` | Progreso de métricas y adherencia semanal |
| `/faltan` | Cuenta atrás para la carrera |
| `/notion` | Estado de la sincronización con Notion |
| `/recordatorio on\|off` | Aviso diario automático |
| `/perfil` | Tus datos actuales |

Cada mañana a las 07:30 (configurable) el bot te envía el entreno del día.

## 📓 Notion (opcional)

El bot puede guardar tus entrenos y métricas en Notion automáticamente. SQLite sigue siendo la base de datos principal, así que **si Notion falla o no lo configuras, el bot funciona igual**.

### Configuración

1. Entra en [notion.so/my-integrations](https://www.notion.so/my-integrations) y pulsa **New integration**.
2. Ponle un nombre (por ejemplo "Bot entrenos"), guarda y copia el **Internal Integration Secret**.
3. Crea una página en Notion donde quieras tener los datos (por ejemplo "Entrenamiento").
4. En esa página: menú `•••` → **Conexiones** → añade tu integración.
5. Copia el ID de la página: son los 32 caracteres del final de su URL.
   ```
   https://notion.so/Entrenamiento-a1b2c3d4e5f6789012345678901234ab
                                  └──────── este es el ID ────────┘
   ```
6. Añádelos a tu `.env`:
   ```
   NOTION_TOKEN=secret_xxxxxxxxxxxx
   NOTION_PARENT_PAGE_ID=a1b2c3d4e5f6789012345678901234ab
   ```
7. Reinicia el bot. Creará dos bases de datos dentro de esa página y te confirmará con `/notion`.

### Qué se guarda

**Base "Entrenos"** — una fila por sesión completada con `/hecho`:

| Campo | Contenido |
|---|---|
| Sesión | Título del entreno |
| Fecha / Día | Fecha y día de la semana |
| Semana / Fase | Semana del plan y fase de periodización |
| Tipo | Gimnasio, Carrera, Natación, Bici o Descanso |
| Descarga | Si es semana de descarga |
| Notas | Lo que escribas tras `/hecho` |

**Base "Métricas"** — una fila por día con `/peso` y `/grasa`:

| Campo | Contenido |
|---|---|
| Registro / Fecha | Fecha del registro |
| Peso (kg) | Tu peso |
| Grasa (%) | Tu % de grasa corporal |
| IMC | Calculado con tu altura |

Si registras dos veces el mismo día, se actualiza la fila en vez de duplicarla. Desde Notion puedes crear gráficas de evolución, vistas de calendario o filtrar por tipo de entrenamiento.

## Estructura semanal

Diseñada para jornada laboral de 8:00 a 18:00 de lunes a jueves, viernes hasta las 14:00 y fines de semana libres.

| Día | Sesión |
|---|---|
| Lunes | Gimnasio · Pierna |
| Martes | Gimnasio · Pecho y tríceps |
| Miércoles | Gimnasio · Espalda y bíceps |
| Jueves | Carrera de calidad (series/tempo) + Gimnasio · Hombro y abdomen |
| Viernes | Natación + rodaje suave |
| Sábado | Tirada larga |
| Domingo | Bici en Z2 o descanso activo |

**Por qué está así montado:** el día de pierna va el lunes para dejar 5 días hasta la tirada larga del sábado. La calidad de carrera se junta con hombro/abdomen porque es el día de gimnasio que menos carga las piernas. El viernes, al salir a las 14:00, concentra las dos sesiones aeróbicas suaves.

## Fases del plan

1. **Semanas 1-5 · Reconstrucción aeróbica** — vuelta progresiva tras el parón de 3 meses. Volumen suave, sin ritmos exigentes.
2. **Semanas 6-12 · Base y fuerza aeróbica** — entra el trabajo de umbral y tempo. Tiradas largas hasta 18 km.
3. **Semanas 13-20 · Construcción específica** — series largas, VO2máx y tiradas de hasta 21 km con bloques a ritmo de carrera.
4. **Semanas 21-24 · Afinado y competición** — simulacro de carrera, taper y media maratón.

Hay **semana de descarga cada 4 semanas** (marcada con ⚠️): baja el volumen ~30% y quita una serie en cada ejercicio de gimnasio.

El gimnasio alterna rutinas **A** y **B** cada semana para variar estímulos.

## Personalización

- **Fechas y horarios**: `.env` (`PLAN_START`, `RACE_DATE`, `REMINDER_HOUR`).
- **Ejercicios de gimnasio**: `gym.py`.
- **Sesiones de carrera, natación y bici**: la lista `WEEKS` en `plan.py`.
- **Estructura de los días de la semana**: función `sesiones_dia()` en `plan.py`.
- **Perfil y ritmo objetivo**: `config.py`.

## Archivos

```
bot.py          Handlers de Telegram y aviso diario
plan.py         Plan de 24 semanas y montaje de cada día
gym.py          Rutinas de gimnasio A/B por grupo muscular
db.py           Persistencia SQLite (métricas y entrenos)
notion_sync.py  Espejo en Notion
config.py       Configuración y perfil
```
