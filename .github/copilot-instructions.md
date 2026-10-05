# Copilot instructions

Telegram bot (python-telegram-bot 21 with `job-queue`) that acts as a personal coach and nutritionist. It serves a cyclic plan of 8-week blocks mixing gym (4 days), running, swimming and cycling for a non-competing athlete, a very lean bulk diet with a Mercadona shopping list, and an optional conversational coach built on the Claude API (tool use). SQLite stores data, and Notion can optionally mirror it. Code identifiers, user-facing messages and docs are all in **Spanish**, so keep new code in Spanish too.

## Running

```bash
cp .env.example .env              # then set TELEGRAM_TOKEN (and optionally ANTHROPIC_API_KEY, NOTION_*)
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python bot.py
```

There is no test suite, linter or CI. To smoke-test changes to the pure plan logic without Telegram, run:

```bash
.venv/bin/python -c "from datetime import date; import plan; print(plan.formatear_dia(date(2026, 10, 8)))"
.venv/bin/python -c "import plan; print(plan.resumen_semana(8))"
.venv/bin/python -c "from datetime import date; import nutricion; print(nutricion.formatear_objetivo(nutricion.objetivo_dia(date(2026, 10, 10), 78)))"
.venv/bin/python -c "from datetime import date; import menu, nutricion; print(menu.formatear(menu.menu_dia(nutricion.objetivo_dia(date(2026, 10, 10), 78))))"
```

To smoke-test the shopping list against a throwaway DB (it downloads Mercadona prices):

```bash
DB_PATH=/tmp/prueba.db .venv/bin/python -c "
import asyncio; from datetime import date; import db, alimentos, compra, mercadona, nutricion
db.init(); asyncio.run(mercadona.actualizar())
op = {k: mercadona.candidatos(a) for k, a in alimentos.disponibles().items()}
print(compra.formatear(compra.construir(nutricion.objetivos_semana(date(2026, 10, 10), 78), op)))"
```

`servicios.py` and `coach.py` can be exercised the same way with `DB_PATH=/tmp/prueba.db` (call `db.init()` and `db.alta_usuario(1, "x")` first). To test the coach's tool loop without spending API credits, set `ANTHROPIC_API_KEY=x` and replace `coach._cliente` with a fake object whose `messages.create(**kw)` returns objects with `stop_reason` and `content` (`anthropic.types.TextBlock`/`ToolUseBlock`).

## Architecture

- **`config.py`** calls `load_dotenv()` at import time and exposes module-level constants (`PLAN_START`, `TIMEZONE`, `ATHLETE`, `MARCA_MEDIA_SEC`, `DESENTRENO_SEC`, `PESO_OBJETIVO`, `ANTHROPIC_API_KEY`, `CLAUDE_MODEL`, …). `PLAN_START` must be a Monday because all week math depends on it. There is no race date: the plan never ends.
- **`plan.py`** is pure and deterministic, with no I/O:
  - The week number comes from `semana_indice(dia) = (dia - PLAN_START).days // 7 + 1`. Block = `(wn-1)//8 + 1`, week in block = `(wn-1)%8 + 1`. Weeks 4 and 8 of each block are deloads; Saturday of week 8 is a test (5 km in odd blocks, 10 km in even ones, `km_test`). Fase = `min(bloque, len(FASES))`. Gym variant is `"A"` on odd weeks and `"B"` on even weeks (`variante()`).
  - `datos_semana(wn, ref)` builds the week (`{fase, bloque, semana_bloque, descarga, test, carrera{tipo,texto,km}, nata, bici, nota}`); the Saturday run comes from `_carrera(sb, bloque, ref)`, whose volume grows per block up to `NIVEL_MAX`.
  - **Paces** come from `ref`, the half-marathon-equivalent pace in s/km (Riegel, exponent 1.06). Without a test it is `MARCA_MEDIA_SEC + DESENTRENO_SEC`. Zones are offsets from `ref` (`_r`). `sesiones_dia`, `formatear_dia`, `datos_semana` and `tabla_ritmos` take an optional `ref`; `servicios.ritmo(chat_id)` derives it from the user's last test. `pace()`/`tiempo_txt()` format s/km and seconds.
  - `_sesion_base(dia)` holds the fixed weekday → session mapping (L pecho, M espalda, X pierna, J natación, V descanso flexible, S carrera + hombro/core, D bici).
  - **Per-user changes** arrive as `plan.Cambio(origen, modo)`. `origen` is the same-week day whose base session is done; `modo` is `None|descanso|corta|suave|bici|natacion`.
    - `sesiones_dia(dia, cambio)`, `tipos_dia(dia, cambio)`, `formatear_dia(dia, cambio)` and `resumen_semana(n, cambios)` apply them.
    - `mover`/`cambiar`/`deshacer` are pure. They return `{fecha: Cambio | None}` updates, where `None` deletes. Moving a skipped day recovers its session onto the target instead of swapping.
    - `carga_dia` returns `pierna`, `carrera`, `ligera` or `otra`; `avisos_semana` warns about pierna next to carrera and weeks with no light day; `sugerir_recuperacion` finds a slot for a skipped key session.
    - `ZONAS_MOLESTIA` and `recomendar(dia, cambios, energia, zona)` are the rule-based (no AI) adaptation used by `/sensaciones`.
- **`gym.py`** contains routine dicts keyed by group (`"pierna" | "pecho" | "espalda" | "hombro"`) and then variant (`"A" | "B"`). `formatear()` renders them as Markdown.
- **`db.py`** is the SQLite **source of truth**. `conn()` is a context manager that commits on exit. `entrenos` has `UNIQUE(chat_id, fecha)` and is upserted. `metricas` keeps one row per chat/day, and peso/grasa are merged with `COALESCE`. Dates are stored as ISO strings.
  - Other per-user tables: `cambios` (plan changes), `tests` (km, segundos), `sensaciones` (energia 1-5, molestias, nota), `menu_cambios` (dish overrides per date and meal), `conversacion` (coach history, trimmed to the last 60 messages) and `coach_uso` (daily message count).
  - Schema migrations use `PRAGMA user_version` (`VERSION`). Version 2 wiped `cambios` because the weekday mapping changed.
- **`servicios.py`** is the shared action layer used by both `bot.py` commands and `coach.py` tools. It fetches the user's cambios, ref pace, weight and dish overrides and passes them to the pure modules, writes to SQLite (then Notion), and returns Markdown text. User-facing errors are raised as `servicios.ErrorUsuario`. "Today" always comes from `servicios.hoy()`, which is timezone-aware, not from `date.today()`. Never call `plan.sesiones_dia(d)` or `menu.menu_dia(o)` without the user's cambios/overrides for user-facing output: go through `servicios`.
- **`coach.py`** is the conversational coach. `responder(chat_id, texto)` calls `anthropic.AsyncAnthropic().messages.create` with `INSTRUCCIONES` + `_contexto(chat_id)` as system prompt and `HERRAMIENTAS` as tools, looping up to `MAX_VUELTAS` tool-use rounds. `_ejecutar` maps each tool to a `servicios` function; `ErrorUsuario` becomes a `tool_result` with `is_error`. Only the user text and the final reply are stored in `conversacion`. It is best-effort: without `ANTHROPIC_API_KEY`, over `CLAUDE_MAX_MENSAJES_DIA`, or on any API error it returns a fallback text and never raises. The coach applies plan changes without asking for confirmation.
- **`notion_sync.py`** is a **best-effort mirror**. The module-level singleton `sync = NotionSync()` is created at import. On `post_init`, `preparar()` auto-creates the "Entrenos" and "Métricas" databases under `NOTION_PARENT_PAGE_ID` and caches their IDs in `notion_dbs.json`. Writes go through `_upsert()`, which keys on the date. Every public method returns `bool` and swallows exceptions. Notion must never break the bot.
- **`bot.py`** contains thin async handlers over `servicios`, wrapped with `@comando` (registers the user and turns `ErrorUsuario` into a reply). `_enviar` splits long texts and retries without Markdown if Telegram rejects it. `main()` registers commands from a `handlers` dict, a `CallbackQueryHandler` for the `/sensaciones` buttons (`sens:e:N`, then `sens:m:N:zona`) and a text `MessageHandler` that forwards free text to `coach.responder`. It schedules `aviso_diario` (session + menu) with `job_queue.run_daily` at `REMINDER_HOUR:REMINDER_MINUTE` in `TIMEZONE`.
  - It also schedules `aviso_compra` on Saturdays (weight adjustment + shopping list, then the weekly menu as a separate message). PTB 21 `run_daily(days=...)` counts **0 = Sunday … 6 = Saturday**, so Saturday is `days=(6,)`.
- **Diet module (clean bulk):**
  - `nutricion.py` is pure. `objetivo_dia(fecha, peso, ajuste, cambio)` = TMB (Mifflin-St Jeor) × 1.35 + exercise kcal estimated from that day's plan sessions (`gasto_ejercicio`: fixed kcal per gym day, run km × weight, swim/bike minutes) + `superavit(peso)` (`SUPERAVIT_KCAL`, 0 once at `PESO_OBJETIVO`) + the per-user adjustment, rounded to 50. Protein is 2 g/kg, fat 0.9 g/kg, carbs the remainder. `nuevo_ajuste()` moves the adjustment ±100 kcal from the 14-day weight trend, clamped to [-300, 500].
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
  - `servicios._menus_semana()` reuses the variant that `compra.construir` picks for that day's Saturday–Friday week, using cached prices only. The daily reminder sends the menu as a second message.
  - Per-user data flow in `servicios`: weight = `db.ultimo_peso(chat_id)`, falling back to `ATHLETE["peso_inicial_kg"]` (`peso_actual`). The kcal adjustment is stored with its date in SQLite (`db.ajuste_kcal`/`guardar_ajuste_kcal`). It is recalculated **only** by the Saturday `aviso_compra` via `ajuste_semanal`, at most once per day. `/dieta` and `/compra` only read it.
  - `mercadona.actualizar()` derives which categories to fetch from `alimentos.categorias()`, which skips excluded foods. Adding a food with a new category ID is enough to get it cached.

## Conventions

- **Adding or renaming a command** touches several places in `bot.py`: the handler function, the `handlers` dict in `main()`, `set_my_commands` in `post_init`, and the `AYUDA` help text. Also update the command table in `README.md`. Put the logic in `servicios.py`; if the coach should be able to do it too, add a tool to `coach.HERRAMIENTAS` and `_ejecutar`.
- **Changing the weekly structure** means keeping these in sync: `_sesion_base()`, `tipos_dia()`, `carga_dia()`, `minutos_alternativa()`, `recomendar()`, `nutricion.gasto_ejercicio()`/`consejo()`, `bot.gimnasio` (weekday → gym group), the coach's `_contexto`/`INSTRUCCIONES`, the `plan.py` module docstring, and the README tables.
- **Notion select options must match the plan's strings**:
  - "Fase" options = `plan.FASES` values without the `"Fase N · "` prefix (see `fase_nombre_corto`).
  - "Tipo" options = values returned by `tipos_dia`.
  - "Día" options = `plan.DIAS`.

  `preparar()` doesn't migrate existing Notion databases, so schema changes only apply to newly created ones.
- In `servicios`, write to SQLite first, then call `notion_sync.sync.*`, and only use its `bool` result to append "Guardado también en Notion". Call `db.alta_usuario(chat_id, …)` before any per-user write.
- Replies use legacy `ParseMode.MARKDOWN` (`MD`): `*bold*`, `_italic_`, `` `code` ``. Unbalanced `*`/`_` in user notes or plan text will make Telegram reject the message.
- **Adding an `.env` setting** touches `config.py` (with a default), `.env.example`, and the README config table.
- **Adding a food** goes in `alimentos.CATALOGO` (`Alimento` dataclass):
  - Product-name matching uses `incluye` (all must appear), `alguna` (at least one) and `excluye`, all compared against `normalizar()` names (lowercase, accents stripped).
  - Set `gramos_unidad` for items sold by the unit and `duradero=True` for pantry items.
- **Pending work:** `.github/menu-fase2.md` has the agreed requirements for Phase 2. Per-meal dishes and grams (`menu.py`) and the Saturday weekly menu are done. Still pending: choosing recipes by cooking time based on training load.
- Gym is only edited in `gym.py`, run sessions in `plan._carrera`, swim/bike in `datos_semana`, and the athlete profile and reference half-marathon time in `config.py`.
- Never commit `.env`, `*.db` or `notion_dbs.json` (they are in `.gitignore`).
