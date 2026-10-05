# 🏃‍♂️ Bot de entrenamiento

Bot de Telegram que hace de entrenador y nutricionista. Gestiona un plan **cíclico** de gimnasio (4 días), carrera, natación y bici para mejorar la forma física y rendir al máximo corriendo, **sin competir**. Incluye una dieta de **volumen muy limpio** (de 79 a 81 kg), la lista de la compra de Mercadona y un **coach conversacional con Claude** que adapta el plan según tus sensaciones.

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
   # edita .env y pega tu TELEGRAM_TOKEN (y, si quieres el coach, ANTHROPIC_API_KEY)
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
| _texto libre_ | Habla con el coach: «hoy estoy reventado», «me molesta el gemelo», «peso 79,4»… (necesita Claude) |
| `/hoy` | Sesión completa de hoy |
| `/manana` | Sesión de mañana |
| `/semana [nº]` | Resumen de la semana |
| `/sensaciones` | Botones de energía y molestias; adapta la sesión de hoy con reglas fijas (sin IA) |
| `/gym` | Detalle de la rutina de gimnasio de hoy |
| `/ritmos` | Tabla de ritmos y zonas (carrera, natación, bici) |
| `/bloque` | Bloque de 8 semanas actual y fecha del próximo test |
| `/test 10k 44:30` | Registra un test (5k, 10k, media o km sueltos) y recalcula los ritmos |
| `/mover jueves viernes` | Intercambia las sesiones de dos días de esta semana (o recupera una saltada) |
| `/saltar [día]` | Ese día descansas (por defecto, hoy) |
| `/cambiar corta\|suave\|bici\|natacion [día]` | Versión corta, suave o alternativa sin impacto (`normal` la deja como estaba) |
| `/deshacer [día\|semana]` | Quita los cambios |
| `/hecho [nota]` | Marca el entreno de hoy como completado |
| `/peso 79.4` | Registra tu peso |
| `/grasa 12.5` | Registra tu % de grasa corporal |
| `/stats` | Progreso de métricas y adherencia semanal |
| `/dieta [mañana]` | Kcal y macros del día según el entreno |
| `/menu [mañana\|semana]` | Qué comer en cada comida, en gramos, con whey y creatina (o la semana resumida) |
| `/plato [día] comida plato` | Cambia el plato de una comida (`normal` vuelve al del plan; sin argumentos lista los platos) |
| `/compra` | Lista de la compra de Mercadona para los próximos 7 días |
| `/notion` | Estado de la sincronización con Notion |
| `/recordatorio on\|off` | Aviso diario automático |
| `/perfil` | Tus datos actuales |
| `/olvidar` | Borra la memoria de la conversación con el coach |

Cada mañana (a las 08:30 con el `.env` de ejemplo) el bot te envía el entreno y el menú del día. Los sábados envía además la lista de la compra y el menú de la semana.

## 💬 Coach conversacional (Claude)

Escríbele al bot como a un entrenador. Usa la API de Anthropic con *tool use*: ve tu plan, tus sensaciones de los últimos 7 días, tu peso y tu dieta, y **aplica los cambios directamente** (sin pedir confirmación) y te explica qué ha hecho:

- «Hoy estoy reventado, he dormido 5 horas» → anota energía baja y te pone la sesión corta, suave o descanso.
- «Me molesta la rodilla» → cambia la carrera o la pierna por natación o bici, o mueve la carrera al viernes.
- «Peso 79,6», «he hecho el 10k en 44:50» → lo registra y recalcula los ritmos.
- «Esta noche no me apetece pescado, ponme fajitas» → cambia el plato y recalcula los gramos.
- Dudas de entreno o de dieta.

Ante un dolor agudo, con hinchazón o que dura más de una semana te recomendará ir al fisio o al médico: no diagnostica.

**Coste**: la suscripción **Claude Pro (~18 €/mes) no incluye la API**. Hace falta una API key de [console.anthropic.com](https://console.anthropic.com) con saldo prepago (pago por uso). Con el modelo por defecto (`claude-haiku-4-5`) y un uso normal (unos pocos mensajes al día) son céntimos al mes. `CLAUDE_MAX_MENSAJES_DIA` limita el gasto.

Sin API key el bot funciona igual: el texto libre te remite a `/sensaciones`, que adapta el día con reglas fijas.

| Variable | Por defecto | Descripción |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(vacía)_ | API key de Anthropic |
| `CLAUDE_MODEL` | `claude-haiku-4-5` | Modelo (p. ej. `claude-sonnet-4-5`, más listo y más caro) |
| `CLAUDE_MAX_MENSAJES_DIA` | `40` | Mensajes al coach por día |

El coach recuerda los últimos mensajes de la conversación; `/olvidar` la borra.

## 🔀 Cambios en la semana

Si te surge un plan o no estás fino, adapta la semana sin tocar el plan general (a mano, con `/sensaciones` o hablando con el coach):

- `/mover sábado viernes` intercambia las sesiones de los dos días. Si el primer día está saltado, recupera su sesión en el segundo y sustituye la que hubiera.
- `/saltar [día]` convierte el día en descanso. Si era la carrera o la pierna, te sugiere el día más ligero que queda para recuperarla (normalmente el viernes).
- `/cambiar corta [día]` deja la sesión a la mitad de volumen con la misma intensidad. `/cambiar suave` mantiene la duración pero baja la intensidad (carrera en Z2, gimnasio con un 30-40 % menos de carga). `/cambiar bici` y `/cambiar natacion` la sustituyen por cardio sin impacto de duración parecida.
- `/deshacer [día|semana]` vuelve al plan original. En un intercambio, deshace los dos días.

Solo se pueden cambiar días de la semana en curso, de hoy en adelante. El bot avisa si la semana queda mal montada: pierna el día antes o después de la carrera, o una semana sin ningún día ligero. Las kcal, el menú y la lista de la compra se recalculan con la sesión que haces de verdad.

## 🥗 Dieta y compra en Mercadona

Objetivo: **volumen muy limpio**, de 79 a **81 kg** ganando unos 0,1 kg por semana, para subir músculo sin subir grasa (o bajándola un poco).

- **Kcal diarias** = metabolismo basal (Mifflin-St Jeor) × 1,35 + gasto del entreno de ese día (sale del plan: gimnasio, km del sábado, minutos de piscina y bici, descargas) + superávit (150 kcal) + ajuste automático. Al llegar a `PESO_OBJETIVO` el superávit pasa a 0 (mantenimiento).
- **Macros**: proteína 2 g/kg, grasa 0,9 g/kg y el resto hidratos.
- **Ajuste automático**: cada sábado se mira la evolución de `/peso` en las últimas 2 semanas. Si subes menos de lo previsto se añaden 100 kcal, y si subes demasiado se quitan 100 (limitado entre −300 y +500). Para que funcione, pésate al menos 2-3 veces por semana, en ayunas.
- **Menú diario** (`/menu`): un plato por comida (desayuno, comida, merienda ligera y cena), con los gramos en crudo de cada ingrediente. Se escalan la proteína y el hidrato de cada plato para cuadrar los macros; el resto de ingredientes van en cantidad fija. Carne en el táper de lunes a miércoles y pescado por la noche o el fin de semana.

  | Día | Comida | Cena |
  |---|---|---|
  | Lunes | Ternera picada con pimientos y cebolla + arroz | Salmón en airfryer + patata + canónigos |
  | Martes | Noodles salteados con pollo y verduras | Merluza en airfryer con verduras + patata |
  | Miércoles | Solomillo de cerdo con pimientos + arroz | Fajitas de pollo con pimientos y cebolla |
  | Jueves | Pasta en el trabajo (fuera) | Pota encebollada + arroz |
  | Viernes | Solomillo de pavo + batata y pimientos | Cena fuera |
  | Sábado | Legumbre de bote: lentejas, cocido o fabada (rota cada semana) | Empanada de atún, huevo y tomate |
  | Domingo | Empanada (la otra mitad) | Pollo + ensalada de canónigos, tomate, aguacate y queso de cabra + arroz |

  - Desayuno: tostadas con AOVE y pavo, o bol de queso batido/yogur con avena y miel, siempre con café con leche. Merienda ligera: whey con tostada y crema de cacahuete, o yogur con whey, miel y nueces.
  - Fruta: plátano en el desayuno, manzana de postre en la comida y kiwi en la cena. Si esa comida es fuera, la fruta pasa a la merienda.
  - `/plato` (o el coach) cambia cualquier plato de los próximos 7 días; la lista de la compra lo tiene en cuenta.
  - Las comidas fuera salen con las kcal y proteína aproximadas que te tocan; cuentan para el total del día pero no entran en la lista de la compra.
  - Hay un tope de pan, avena, patata, arroz, noodles, etc. por plato; lo que no cabe pasa al arroz, la pasta o la patata del día.
  - Cada plato con proteína lleva al menos 30 g, así que la proteína suele quedar algo por encima de 2 g/kg. Para no pasarte de kcal, se quitan hidratos.
  - Para cambiar el menú tipo, edita `PLATOS` y `SEMANA` en `menu.py`.
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
| `SUPERAVIT_KCAL` | `150` | Superávit diario de partida |
| `OBJETIVO_KG_SEMANA` | `0.1` | Ganancia de peso buscada |
| `PESO_OBJETIVO` | `81` | Peso al que pasa a mantenimiento |
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
| Semana / Fase | Semana del plan y fase (Reconstrucción, Base aeróbica, Desarrollo) |
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

Si registras dos veces el mismo día, se actualiza la fila en vez de duplicarla. Las bases ya creadas no se migran: si las creaste con una versión anterior, Notion añade solas las fases nuevas al guardar. Desde Notion puedes crear gráficas de evolución, vistas de calendario o filtrar por tipo de entrenamiento.

## Estructura semanal

Pensada para una jornada de lunes a jueves de 8:00 a 17:30.

| Día | Sesión |
|---|---|
| Lunes | Gimnasio · Pecho y tríceps |
| Martes | Gimnasio · Espalda y bíceps |
| Miércoles | Gimnasio · Pierna |
| Jueves | Natación |
| Viernes | Descanso flexible |
| Sábado | Carrera (la sesión clave) + Gimnasio · Hombro y core |
| Domingo | Bici |

**Por qué está así montado:** la pierna va el miércoles para llegar con 72 h de margen a la carrera del sábado, que es la única de la semana y la más importante. La natación del jueves es cardio sin impacto que ayuda a recuperar. El viernes queda libre como hueco para recuperar una sesión movida o saltada. El hombro y core van después de correr porque no cargan las piernas, y el domingo la bici suma volumen aeróbico sin impacto.

El gimnasio se hace con RIR 1-2 (dejando 1-2 repeticiones en reserva) y alterna rutinas **A** y **B** cada semana.

## Bloques y tests

No hay fecha final: el plan se repite en **bloques de 8 semanas** (3 de carga + 1 de descarga, dos veces). En las descargas (semanas 4 y 8, marcadas con ⚠️) baja el volumen y se quita una serie en cada ejercicio de gimnasio.

| Semana del bloque | Carrera del sábado |
|---|---|
| 1 | Rodaje largo en Z2 |
| 2 | Fartlek o tempo a umbral |
| 3 | Tirada larga (con final a ritmo de media desde el bloque 2) |
| 4 | Descarga: rodaje suave |
| 5 | Series (VO2) |
| 6 | Larga progresiva |
| 7 | Ritmo medio o ritmo de media |
| 8 | **Test**: 5 km en bloques impares y 10 km en pares |

El volumen de carrera crece de bloque en bloque (tirada larga de 10 a 17 km) hasta estabilizarse en el bloque 4. Fases: bloque 1 *Reconstrucción* (vuelves tras 2 meses sin correr apenas), bloque 2 *Base aeróbica* y desde el 3 *Desarrollo*.

**Ritmos**: salen de tu último test (`/test`), convertido a ritmo equivalente de media maratón con la fórmula de Riegel. Hasta el primer test se usa tu marca de media (4:38 /km) más `DESENTRENO_SEC` (15 s/km). Las zonas Z1-Z4, ritmo medio y VO2 se calculan a partir de ahí.

## Personalización

- **Fechas y horarios**: `.env` (`PLAN_START`, `REMINDER_HOUR`).
- **Ejercicios de gimnasio**: `gym.py`.
- **Sesiones de carrera**: `_carrera()` en `plan.py`; natación y bici, `datos_semana()`.
- **Estructura de los días de la semana**: función `_sesion_base()` en `plan.py`.
- **Perfil, marca de referencia y peso objetivo**: `config.py` y `.env`.
- **Lo que sabe y cómo actúa el coach**: `INSTRUCCIONES` y `_contexto()` en `coach.py`.
- **Alimentos, cantidades base y variantes de ahorro**: `alimentos.py`.
- **Presupuesto, superávit y alimentos excluidos**: `.env`.

## Archivos

```
bot.py          Handlers de Telegram, aviso diario y aviso de compra
servicios.py    Acciones compartidas por los comandos y el coach
coach.py        Coach conversacional con Claude (tool use)
plan.py         Plan cíclico por bloques, ritmos y montaje de cada día
gym.py          Rutinas de gimnasio A/B por grupo muscular
nutricion.py    Kcal y macros por día según el entreno; ajuste por peso
menu.py         Menú diario por comidas (gramos de cada alimento)
alimentos.py    Catálogo de alimentos (macros y cómo buscarlos en Mercadona)
mercadona.py    Cliente de la API de Mercadona con caché en SQLite
compra.py       Cálculo de cantidades y lista de la compra con presupuesto
db.py           Persistencia SQLite (métricas, entrenos, tests, sensaciones, cambios, conversación y caché)
notion_sync.py  Espejo en Notion
config.py       Configuración y perfil
```
