# Copilot instructions

Telegram bot (python-telegram-bot 21 with `job-queue`) that acts as a personal coach and nutritionist for a non-competing athlete who wants to improve at 10 km, half marathon and cycling. There is **no fixed weekly plan**: the athlete writes his gym routine in Garmin Connect and does it with the watch; the bot reads what he trains (Garmin Connect, Forerunner 265), gives rule-based daily advice for running (`/carrera`) and cycling (`/bici`) that can be pushed to the watch, shows a 14-day history (`/historial`, gym with per-exercise progress), and keeps a very lean bulk diet with a Mercadona shopping list. An optional conversational coach is built on the Claude API (tool use). SQLite stores data, and Notion can optionally mirror it. Code identifiers, user-facing messages and docs are all in **Spanish**, so keep new code in Spanish too.

## Running

```bash
cp .env.example .env              # then set TELEGRAM_TOKEN (and optionally ANTHROPIC_API_KEY, NOTION_*)
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python bot.py
```

There is no test suite, linter or CI. To smoke-test the pure logic without Telegram, run:

```bash
.venv/bin/python -c "
from datetime import date; import consejo
e = consejo.Estado(hoy=date(2026, 10, 14), actividades=[], umbral=170)
print(consejo.formatear(consejo.carrera(e, 290))); print(consejo.formatear(consejo.bici(e)))"
.venv/bin/python -c "from datetime import date; import nutricion; print(nutricion.formatear_objetivo(nutricion.objetivo_dia(date(2026, 10, 10), 78, ejercicio_kcal=400)))"
.venv/bin/python -c "from datetime import date; import menu, nutricion; print(menu.formatear(menu.menu_dia(nutricion.objetivo_dia(date(2026, 10, 10), 78))))"
```

`consejo.Estado.actividades` takes the same dicts as `db.actividades` (`fecha` ISO, `tipo`, `distancia_m`, `duracion_s`, `fc_media`, `te_aerobico`, `te_anaerobico`, `series` for gym).

To smoke-test the shopping list against a throwaway DB (it downloads Mercadona prices):

```bash
DB_PATH=/tmp/prueba.db .venv/bin/python -c "
import asyncio; from datetime import date; import db, alimentos, compra, mercadona, nutricion
db.init(); asyncio.run(mercadona.actualizar())
op = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
print(compra.formatear(compra.construir(nutricion.objetivos_semana(date(2026, 10, 10), 78), op)))"
```

`servicios.py`, `entrenador.py` and `coach.py` can be exercised the same way with `DB_PATH=/tmp/prueba.db` (call `db.init()` and `db.alta_usuario(1, "x")` first). To test the coach's tool loop without spending API credits, set `ANTHROPIC_API_KEY=x` and replace `coach._cliente` with a fake object whose `messages.create(**kw)` returns objects with `stop_reason` and `content` (`anthropic.types.TextBlock`/`ToolUseBlock`). To test the Garmin flow offline, set `servicios.GARMIN_CHAT_ID = 1`, replace `garmin.configurado` and the I/O functions (`garmin.actividades`, `vueltas`, `series_fuerza`, `bienestar`, `subir`, `programar`, `borrar`) with fakes, insert activities with `db.guardar_actividad`, and call `servicios.sync_garmin`/`revisar_bienestar`/`consejo_texto`/`mandar_reloj`/`historial_texto`. Override `servicios.hoy` to pin the date. The `garmin.json_*` builders are pure.

## Architecture

- **`config.py`** calls `load_dotenv()` at import time and exposes module-level constants (`TIMEZONE`, `ATHLETE`, `MARCA_MEDIA_SEC`, `DESENTRENO_SEC`, `PESO_OBJETIVO`, `FC_UMBRAL`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, …). `PLAN_START` (a Monday) now only drives `plan.semana_indice`, used by the Saturday-lunch rotation in `menu.py` and the no-Garmin kcal estimate.
- **`consejo.py`** is pure and is the **rule engine for `/carrera` and `/bici`** (no AI):
  - `Estado(hoy, actividades, bienestar, energia, molestia, umbral, umbral_zonas, consejos)` is built by `servicios.estado(chat_id)`. `umbral` is only a measured/configured lactate-threshold HR (used to classify hard sessions); `umbral_zonas` may be an estimate and is only used for bike zones. `consejos` is `{(fecha_iso, deporte): datos}` of past advice (used to know which quality type was done).
  - Classifiers: `dura(a, umbral)` (TE anaerobic ≥ 2, aerobic ≥ 4, or avg HR ≥ `DURA_FC` × threshold), `larga(a)`, `pierna(a)` (≥ 3 leg sets), `calidad_hecha`.
  - `carrera(e, ref)` and `bici(e)` return a `Consejo(deporte, clave, titulo, texto, motivo, alternativa, minutos, km, pasos)`. Order of rules: stop (illness, readiness < 25, energy 1) → leg pain → already done today → tiredness → day after hard/long → legs yesterday → volume jump → long run every ≥ 6 days → ≤ `MAX_DURAS_SEMANA` hard sessions in 7 days → quality rotation (umbral/vo2/media; bike tempo/umbral/vo2) with level from `nivel()`. `_calidad_en_otro` avoids advising hard sessions in both sports on the same day.
  - Run `pasos` reuse `plan._paso`/`plan.ZONAS` keys (`z1`, `z2`, `medio`, `media`, `umbral`, `vo2`); bike `pasos` use `b1`–`b5` (`ZONAS_BICI`, % of threshold − `UMBRAL_BICI_MENOS`), which only `garmin.json_bici` understands. Keep `texto` and `pasos` consistent.
  - `formatear(c)` renders the advice; `resumen_14(e)` feeds the coach context.
- **`plan.py`** is legacy support (the old cyclic weekly plan). What is still used: `ritmo_ref`, `ZONAS`, `pace`, `tiempo_txt`, `tabla_ritmos`, `_paso`, `DIAS`, `TIPOS`, `ZONAS_MOLESTIA`, `MEDIA_KM`, `semana_indice`, and `ESTRUCTURA_DEFECTO` for the diet fallback. **Paces** come from `ref`, the half-marathon-equivalent pace in s/km (Riegel, exponent 1.06): `servicios.ritmo(chat_id)` derives it from the last test if newer than `TEST_VIGENCIA_DIAS`, else from the Garmin half-marathon prediction, else an older test or `MARCA_MEDIA_SEC + DESENTRENO_SEC`. `gym.py` and the reparto code in `plan.py`/`entrenador.py` are no longer called by the bot.
- **`entrenador.py`** is pure: run analysis vs the day's advice (`analizar_carrera(a, sesion, ref)`, compares km or minutes, Z2 pace, quality laps, `deriva_cardiaca`), `resumen_actividad`, `texto_bienestar`, and strength helpers: `nombre_ejercicio` (Garmin category/name → Spanish, `NOMBRE_GARMIN`, `UNKNOWN` → «Sin identificar»), `limpiar_series` (drops 0-rep sets, gives an unidentified set its neighbour's exercise if the weight matches), `por_ejercicio`, `progreso` and `resumen_fuerza(series, previas)`.
- **`garmin.py`** wraps the unofficial `garminconnect` library (blocking: call it through `servicios._g`, which uses `asyncio.to_thread`). Parsers turn API dicts into plain dicts; `bienestar()` also reads the lactate threshold (`fc_umbral`). `json_carrera(nombre, entreno, ref)` (pace targets in m/s) and `json_bici(nombre, entreno, zonas)` (HR targets in bpm, no target without zones) build workout JSON. One Garmin account is linked to `GARMIN_CHAT_ID`; tokens live in `GARMIN_TOKENS_DIR` (created by `garmin_login.py`). The workout JSON IDs are not officially documented.
- **`db.py`** is the SQLite **source of truth**. `conn()` is a context manager that commits on exit. `metricas` keeps one row per chat/day, and peso/grasa are merged with `COALESCE`. Dates are stored as ISO strings.
  - Tables in use: `garmin_actividades` (raw JSON incl. `series`/`vueltas` + analysis; `db.actividades(chat_id, desde, limite)` is date DESC), `garmin_bienestar` (one row per day, `aplicado` = wellness already sent, `fc_umbral`), `garmin_programados` (workouts the bot put on the calendar), `consejos` (one per chat/date/sport, JSON `Consejo.datos()`), `entrenos` (one row per day mirrored from Garmin for Notion), `tests`, `sensaciones`, `menu_cambios`, `conversacion`, `coach_uso`, Mercadona cache. `semana_usuario`, `rutina`, `series_hechas`, `cambios` and `gym_reparto` are legacy.
  - Schema migrations use `PRAGMA user_version` (`VERSION`, now 5: `consejos` table and `garmin_bienestar.fc_umbral`, added with `ALTER TABLE` if missing).
- **`servicios.py`** is the shared action layer used by both `bot.py` commands and `coach.py` tools. It reads SQLite, calls the pure modules, writes to SQLite (then Notion), and returns Markdown text. User-facing errors are raised as `servicios.ErrorUsuario`. "Today" always comes from `servicios.hoy()`, which is timezone-aware, not from `date.today()`.
  - `umbral(chat_id)` → `(bpm, fuente)` from `FC_UMBRAL`, the watch, or 90 % of the 60-day max HR (estimate); `umbral_fiable` drops the estimate.
  - `calcular_consejo`/`consejo_texto(chat_id, deporte)` compute and store today's advice (not when the result is `hecho`); `mandar_reloj` pushes the stored advice to today's calendar, replacing the previous one for that sport; `limpiar_reloj` (called after each sync) deletes any other bot workout.
  - `historial_texto` (14 days, weekly totals, gym progress via `_previas`), `stats_texto`, `procesar_actividad` (analysis vs today's advice, saved with the activity, Notion row per day), `sync_garmin`, `revisar_bienestar` (info only, once per day), `aplicar_sensaciones` (records and returns both advices plus which can go to the watch).
- **`coach.py`** is the conversational coach. `responder(chat_id, texto)` calls `anthropic.AsyncAnthropic().messages.create` with `INSTRUCCIONES` + `_contexto(chat_id)` (14-day activities with dura/larga/pierna tags, wellness, today's advice, sensations, diet) as system prompt and `HERRAMIENTAS` as tools, looping up to `MAX_VUELTAS` tool-use rounds. `_ejecutar` maps each tool to a `servicios` function; `ErrorUsuario` becomes a `tool_result` with `is_error`. Only the user text and the final reply are stored in `conversacion`. `comentar_actividad` and `comentar_consejo` add a short comment. It is best-effort: without `ANTHROPIC_API_KEY`, over `CLAUDE_MAX_MENSAJES_DIA`, or on any API error it returns a fallback text and never raises.
- **`notion_sync.py`** is a **best-effort mirror**. The module-level singleton `sync = NotionSync()` is created at import. On `post_init`, `preparar()` auto-creates the "Entrenos" and "Métricas" databases under `NOTION_PARENT_PAGE_ID` and caches their IDs in `notion_dbs.json`. Writes go through `_upsert()`, which keys on the date. Every public method returns `bool` and swallows exceptions. Notion must never break the bot.
- **`bot.py`** contains thin async handlers over `servicios`, wrapped with `@comando` (registers the user and turns `ErrorUsuario` into a reply). `_enviar` splits long texts and retries without Markdown if Telegram rejects it. `main()` registers commands from a `handlers` dict, `CallbackQueryHandler`s for the `/sensaciones` buttons (`sens:e:N`, then `sens:m:N:zona`) and the watch button (`reloj:carrera`/`reloj:bici`, built by `_boton_reloj`), and a text `MessageHandler` that forwards free text to `coach.responder`. With Garmin configured it runs `sync_garmin` every `GARMIN_SYNC_MIN` minutes (new activities → analysis + `coach.comentar_actividad`, morning wellness 06:00–13:00). It schedules `aviso_diario` (wellness + run advice + bike advice, each with its watch button, + menu) with `job_queue.run_daily` at `REMINDER_HOUR:REMINDER_MINUTE` in `TIMEZONE`.
  - It also schedules `aviso_compra` on Saturdays (weight adjustment + shopping list, then the weekly menu as a separate message). PTB 21 `run_daily(days=...)` counts **0 = Sunday … 6 = Saturday**, so Saturday is `days=(6,)`.
- **Diet module (clean bulk):**
  - `nutricion.py` is pure. `objetivo_dia(fecha, peso, ajuste, cambio, est, ejercicio_kcal, entreno, reloj)` = TMB (Mifflin-St Jeor) × 1.35 + exercise kcal + `superavit(peso)` (`SUPERAVIT_KCAL`, 0 once at `PESO_OBJETIVO`) + the per-user adjustment, rounded to 50. `servicios` always passes `ejercicio_kcal`: the 14-day daily average of watch activity kcal minus basal for the active minutes (`kcal_ejercicio_reloj`), or without Garmin data the default week's average (`kcal_ejercicio_estimada`, `reloj=False`). The same value applies every day; `entreno` (`"carrera:umbral"`…, today's advice) only picks the food tip (`consejo_entreno`). Without `ejercicio_kcal` it still falls back to the legacy per-day plan estimate (`gasto_ejercicio`). Protein is 2 g/kg, fat 0.9 g/kg, carbs the remainder. `nuevo_ajuste()` moves the adjustment ±100 kcal from the 14-day weight trend, clamped to [-300, 500].
  - `alimentos.py` is the food catalog: macros per 100 g as purchased, Mercadona category IDs, and include/`alguna`/exclude keywords for matching product names. Frozen fish (merluza, salmón, pota) is `duradero` like pantry items. `ALIMENTOS_EXCLUIDOS` (from `.env`) is applied with word-boundary matching.
  - `mercadona.py` is a **best-effort** client of the unofficial API (`tienda.mercadona.es/api`, warehouse `MERCADONA_WH`). `actualizar()` refreshes categories older than 6 days into SQLite (`mercadona_productos`/`mercadona_categorias`) and swallows network errors. `candidatos()` reads only from the cache. The API has no nutrition data.
  - `menu.py` is pure and is the **only source of gram amounts**. It is dish-based and personalised to the athlete's foods.
    - `PLATOS` holds `Plato`s: `nombre` (with `{p}` for the protein), a scaled `proteina`, scaled `hidratos` weights, fixed `fijos` grams, `secos` (nuts, cut first if fat overflows), `aceite`, `nota` and `fuera` (eaten out: it counts for macros as an estimate but is excluded from `Menu.gramos`, so it never reaches the shopping list).
    - `DESAYUNOS`/`MERIENDAS`/`SEMANA` map weekday to dish. Saturday lunch rotates canned legumes with `COMIDA_SABADO` by plan week. Fruit is fixed by `POSTRES` (manzana at lunch, kiwi at dinner, moved to the merienda when that meal is eaten out) plus a plátano at breakfast if no dish has one.
    - `platos_dia`/`menu_dia` accept per-user overrides `{comida: clave}` (from `menu_cambios`); `platos_validos(comida)` lists the allowed keys. `formatear_semana(menus)` renders the compact weekly menu.
    - `_resolver` sizes components: each protein dish gets at least `PROTEINA_MIN_PLATO` g, carbs are split by `PESO_HIDRATOS` per meal and capped by `TOPE_HIDRATOS` (overflow goes to `DESBORDE_HIDRATOS`), and olive oil fills the remaining fat (min `AOVE_MINIMO`). Protein or fat excess is removed from carbs to keep kcal on target.
    - `VARIANTES` are budget swaps of the main protein, from most to least expensive.
    - HSN whey (`alimentos.WHEY`, `WHEY_GRAMOS`) is added to the merienda and counts for macros, but it isn't in `CATALOGO`, so it never reaches Mercadona. Use `con_suplementos()` when you need macros including whey. Creatine (`CREATINA_GRAMOS`) is only a reminder line.
  - `compra.py` sums the 7 daily menus into weekly grams (`construir`), picks products, and tries the budget variants in order. Fresh items cost whole packages, while durable ("despensa") items count only the prorated weekly cost. `formatear()` must stay under Telegram's 4096-char limit and strips Markdown characters from product names with `menu._md`.
  - `compra.construir(objetivos, opciones, platos_usuario)` also takes the per-day dish overrides.
  - `servicios._objetivos()` builds the 7 objectives; `_menus_semana()` reuses the variant that `compra.construir` picks for that day's Saturday–Friday week, using cached prices only. The daily reminder sends the menu as a second message.
  - Per-user data flow in `servicios`: weight = `db.ultimo_peso(chat_id)`, falling back to `ATHLETE["peso_inicial_kg"]` (`peso_actual`). The kcal adjustment is stored with its date in SQLite (`db.ajuste_kcal`/`guardar_ajuste_kcal`). It is recalculated **only** by the Saturday `aviso_compra` via `ajuste_semanal`, at most once per day. `/dieta` and `/compra` only read it.
  - `mercadona.actualizar()` derives which categories to fetch from `alimentos.categorias()`, which skips excluded foods. Adding a food with a new category ID is enough to get it cached.

## Conventions

- **Adding or renaming a command** touches several places in `bot.py`: the handler function, the `handlers` dict in `main()`, `set_my_commands` in `post_init`, and the `AYUDA` help text (escape `_` as `\\_`). Also update the command table in `README.md`. Put the logic in `servicios.py`; if the coach should be able to do it too, add a tool to `coach.HERRAMIENTAS` and `_ejecutar`.
- **No fixed plan**: never reintroduce weekday → session mappings for user-facing output. Daily decisions come from `consejo.py` with data from the watch. Changing a rule means editing `consejo.carrera`/`bici` and the README section «Consejo diario».
- **Garmin is best-effort**: every call goes through `servicios._g` and callers in `bot.py` catch and log errors.
- **Notion**: "Tipo" options = `plan.TIPOS` values, "Día" = `plan.DIAS`; options are auto-created. `preparar()` doesn't migrate existing Notion databases, so schema changes only apply to newly created ones.
- In `servicios`, write to SQLite first, then call `notion_sync.sync.*`, and only use its `bool` result to append "Guardado también en Notion". Call `db.alta_usuario(chat_id, …)` before any per-user write.
- Replies use legacy `ParseMode.MARKDOWN` (`MD`): `*bold*`, `_italic_`, `` `code` ``. Unbalanced `*`/`_` in user notes, activity or exercise names will make Telegram reject the message: sanitise them with `menu._md`.
- **Adding an `.env` setting** touches `config.py` (with a default), `.env.example`, and the README config table.
- **Adding a food** goes in `alimentos.CATALOGO` (`Alimento` dataclass):
  - Product-name matching uses `incluye` (all must appear), `alguna` (at least one) and `excluye`, all compared against `normalizar()` names (lowercase, accents stripped).
  - Set `gramos_unidad` for items sold by the unit and `duradero=True` for pantry items.
- **Pending work:** `.github/menu-fase2.md` has the agreed requirements for Phase 2. Per-meal dishes and grams (`menu.py`) and the Saturday weekly menu are done. Still pending: choosing recipes by cooking time based on training load.
- Run and bike sessions are edited in `consejo.py` (`c_*`/`b_*`, `NIVELES_*`), run zones in `plan.ZONAS`, and the athlete profile and reference half-marathon time in `config.py`.
- Never commit `.env`, `*.db`, `notion_dbs.json` or Garmin tokens (they are in `.gitignore`).
