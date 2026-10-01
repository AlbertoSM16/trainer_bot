# 🏃‍♂️ Bot de entrenamiento

Bot de Telegram que gestiona un plan de **24 semanas** combinando gimnasio, carrera, natación y bici, con el objetivo de correr una media maratón por debajo de **4:38 min/km**.

## Puesta en marcha

Requisitos (Linux): Python 3.10 o superior con `venv` y `git`. En Debian/Ubuntu:

```bash
sudo apt install python3 python3-venv git
```

1. **Crea el bot en Telegram**: habla con [@BotFather](https://t.me/BotFather), envía `/newbot` y copia el token.
2. **Descarga el código**:
   ```bash
   git clone https://github.com/AlbertoSM16/trainer_bot.git coach_bot
   cd coach_bot
   ```
3. **Configura el entorno**:
   ```bash
   cp .env.example .env
   # edita .env y pega tu TELEGRAM_TOKEN
   ```
4. **Crea el entorno virtual e instala dependencias**:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
5. **Arranca**:
   ```bash
   .venv/bin/python bot.py
   ```
   Se para con `Ctrl+C`.
6. En Telegram, busca tu bot y envía `/start`.

### Dejarlo corriendo en Linux (systemd)

Para que el bot siga funcionando al cerrar la terminal y arranque solo al encender el equipo, créalo como servicio de usuario (no hace falta `sudo`).

1. Crea `~/.config/systemd/user/coach-bot.service` (con `mkdir -p ~/.config/systemd/user` si la carpeta no existe), cambiando la ruta por la carpeta donde está el bot:
   ```ini
   [Unit]
   Description=Bot de entrenamiento (Telegram)
   After=network-online.target
   Wants=network-online.target

   [Service]
   WorkingDirectory=%h/coach_bot
   ExecStart=%h/coach_bot/.venv/bin/python bot.py
   Restart=on-failure
   RestartSec=10

   [Install]
   WantedBy=default.target
   ```
   `%h` es tu carpeta personal. `.env`, `entrenos.db` y `notion_dbs.json` se leen desde `WorkingDirectory`.
2. Actívalo y arráncalo:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now coach-bot
   ```
3. Permite que arranque aunque no hayas iniciado sesión:
   ```bash
   loginctl enable-linger $USER
   ```

Uso habitual:

```bash
systemctl --user status coach-bot     # estado
journalctl --user -u coach-bot -f     # logs en vivo
systemctl --user restart coach-bot    # tras cambiar código o .env
systemctl --user stop coach-bot       # pararlo
```

> ⚠️ No lances `bot.py` a mano mientras el servicio está activo: Telegram rechaza dos procesos con el mismo token (error `Conflict`).

## Comandos

| Comando | Descripción |
|---|---|
| `/hoy` | Sesión completa de hoy |
| `/manana` | Sesión de mañana |
| `/semana [nº]` | Resumen de la semana |
| `/gym` | Detalle de la rutina de gimnasio de hoy |
| `/ritmos` | Tabla de ritmos y zonas (carrera, natación, bici) |
| `/fases` | Estructura completa del plan |
| `/mover jueves viernes` | Intercambia las sesiones de dos días de esta semana (o recupera una saltada) |
| `/saltar [día]` | Ese día descansas (por defecto, hoy) |
| `/cambiar corta\|bici\|natacion [día]` | Versión corta o alternativa sin impacto (`normal` la deja como estaba) |
| `/deshacer [día\|semana]` | Quita los cambios |
| `/hecho [nota]` | Marca el entreno de hoy como completado |
| `/peso 78.4` | Registra tu peso |
| `/grasa 12.5` | Registra tu % de grasa corporal |
| `/stats` | Progreso de métricas y adherencia semanal |
| `/dieta [mañana]` | Kcal y macros del día según el entreno |
| `/menu [mañana]` | Qué comer en cada comida, en gramos, con whey y creatina |
| `/compra` | Lista de la compra de Mercadona para los próximos 7 días |
| `/faltan` | Cuenta atrás para la carrera |
| `/notion` | Estado de la sincronización con Notion |
| `/recordatorio on\|off` | Aviso diario automático |
| `/perfil` | Tus datos actuales |

Cada mañana a las 07:30 (configurable) el bot te envía el entreno y el menú del día, y los sábados a las 08:30 la lista de la compra.

## 🔀 Cambios en la semana

Si te surge un plan, adapta la semana sin tocar el plan general:

- `/mover sábado domingo` intercambia las sesiones de los dos días. Si el primer día está saltado, recupera su sesión en el segundo y sustituye la que hubiera.
- `/saltar [día]` convierte el día en descanso. Si era la tirada larga o la sesión de calidad, te sugiere el día más ligero que queda para recuperarla.
- `/cambiar corta [día]` deja la sesión a la mitad de volumen. `/cambiar bici` y `/cambiar natacion` la sustituyen por cardio sin impacto de duración parecida.
- `/deshacer [día|semana]` vuelve al plan original. En un intercambio, deshace los dos días.

Solo se pueden cambiar días de la semana en curso, de hoy en adelante, y el día de la carrera no se toca. El bot avisa si la semana queda mal montada: pierna justo antes de la tirada larga, series y tirada larga en días seguidos, o tres días duros seguidos. Las kcal, el menú y la lista de la compra se recalculan con la sesión que haces de verdad.

## 🥗 Dieta y compra en Mercadona

Objetivo: **volumen limpio**, ganando unos 0,25 kg por semana sin acumular grasa.

- **Kcal diarias** = metabolismo basal (Mifflin-St Jeor) × 1,35 + gasto del entreno de ese día (sale del plan: minutos, km, gimnasio, descargas) + superávit (250 kcal) + ajuste automático.
- **Macros**: proteína 2 g/kg, grasa 0,9 g/kg y el resto hidratos. Los días de tirada larga o calidad suben los hidratos.
- **Ajuste automático**: cada sábado se mira la evolución de `/peso` en las últimas 2 semanas. Si subes menos de lo previsto se añaden 100 kcal, y si subes demasiado se quitan 100 (limitado entre −300 y +500). Para que funcione, pésate al menos 2-3 veces por semana.
- **Menú diario** (`/menu`): un plato por comida (desayuno, comida/táper, merienda/post-entreno y cena), con los gramos en crudo de cada ingrediente. Se escalan la proteína y el hidrato de cada plato para cuadrar los macros; el resto de ingredientes van en cantidad fija.

  | Día | Comida | Cena |
  |---|---|---|
  | Lunes | Ternera picada con pimientos y cebolla + arroz | Salmón en airfryer + patata + canónigos |
  | Martes | Arroz frito con pollo, verduras, huevo y soja | Merluza en airfryer con calabacín + patata |
  | Miércoles | Pollo a la plancha con pimientos + arroz | Solomillo de pavo + batata y pimientos |
  | Jueves | Pasta en el trabajo (fuera) | Pota encebollada + arroz |
  | Viernes | Macarrones con ternera y tomate | Cena fuera |
  | Sábado | Fabada (semanas impares) o salchichas de pollo + batata | Empanada de atún, huevo y tomate |
  | Domingo | Empanada (la otra mitad) | Pollo + ensalada de canónigos, aguacate y queso de cabra + arroz |

  - Desayuno: tostadas con AOVE y pavo, o bol de queso batido/yogur con avena, miel y plátano, siempre con café con leche. Merienda: whey con tostada y crema de cacahuete, o yogur con whey, avena, nueces y fruta. La fruta del postre rota cada día.
  - Las comidas fuera salen con las kcal y proteína aproximadas que te tocan; cuentan para el total del día pero no entran en la lista de la compra.
  - Hay un tope de pan, avena, patata, arroz, etc. por plato; lo que no cabe pasa al arroz, la pasta o la patata del día.
  - Cada plato con proteína lleva al menos 30 g, así que la proteína suele quedar algo por encima de 2 g/kg. Para no pasarte de kcal, se quitan hidratos.
  - Para cambiar platos, edita `PLATOS` y `SEMANA` en `menu.py`.
- **Suplementos de HSN**: la whey (1 cacito de 30 g en la merienda o después de entrenar) cuenta para los macros y reduce la proteína que tiene que venir de la comida. La creatina (5 g al día, también los días de descanso) aparece en el menú como recordatorio. Ninguno de los dos entra en la lista de Mercadona. Los macros de la whey (Evowhey) son valores medios y están en `alimentos.py`.
- **Lista de la compra**: suma los menús de los 7 días y busca el producto más barato que encaja en la [API no oficial de Mercadona](https://tienda.mercadona.es) (almacén de tu código postal). Si se pasa del presupuesto, cambia primero el salmón por merluza y, si aún no entra, también la pota por merluza y la ternera por pollo.
  - Los productos frescos se cuentan por envases enteros.
  - Los de despensa (arroz, pasta, aceite, frutos secos…) y el pescado congelado solo cuentan lo que gastas en la semana. Revisa lo que te queda antes de comprarlos.
- Los precios se guardan en SQLite durante 6 días. Si Mercadona no responde, se usa la caché, y si no hay caché, la lista sale sin precios.

Configuración en `.env`:

| Variable | Por defecto | Descripción |
|---|---|---|
| `MERCADONA_WH` | `2183` | Almacén (sale de tu código postal) |
| `PRESUPUESTO_SEMANAL` | `55` | Euros por semana |
| `SUPERAVIT_KCAL` | `250` | Superávit diario de partida |
| `OBJETIVO_KG_SEMANA` | `0.25` | Ganancia de peso buscada |
| `COMPRA_HOUR` / `COMPRA_MINUTE` | `8` / `30` | Hora del aviso del sábado |
| `WHEY_GRAMOS` | `30` | Whey de HSN al día (0 si no tomas) |
| `CREATINA_GRAMOS` | `5` | Creatina al día (0 si no tomas) |
| `ALIMENTOS_EXCLUIDOS` | brócoli, col… | Palabras separadas por comas que nunca entran en la lista |

Para sacar el almacén de otro código postal:

```bash
curl -si -X PUT https://tienda.mercadona.es/api/postal-codes/actions/change-pc/ \
  -H 'Content-Type: application/json' -d '{"new_postal_code":"18002"}' | grep -i x-customer-wh
```

> La API de Mercadona no es oficial y puede cambiar. Los macros de cada alimento son valores de referencia (en `alimentos.py`), no los de la etiqueta del producto concreto.

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
- **Estructura de los días de la semana**: función `_sesion_base()` en `plan.py`.
- **Perfil y ritmo objetivo**: `config.py`.
- **Alimentos, cantidades base y variantes de ahorro**: `alimentos.py`.
- **Presupuesto, superávit y alimentos excluidos**: `.env`.

## Archivos

```
bot.py          Handlers de Telegram, aviso diario y aviso de compra
plan.py         Plan de 24 semanas y montaje de cada día
gym.py          Rutinas de gimnasio A/B por grupo muscular
nutricion.py    Kcal y macros por día según el entreno; ajuste por peso
menu.py         Menú diario por comidas (gramos de cada alimento)
alimentos.py    Catálogo de alimentos (macros y cómo buscarlos en Mercadona)
mercadona.py    Cliente de la API de Mercadona con caché en SQLite
compra.py       Cálculo de cantidades y lista de la compra con presupuesto
db.py           Persistencia SQLite (métricas, entrenos, ajuste y caché)
notion_sync.py  Espejo en Notion
config.py       Configuración y perfil
```
