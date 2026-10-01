# Copilot instructions

Telegram bot (python-telegram-bot 21 with `job-queue`) that serves a fixed 24-week half-marathon plan mixing gym, running, swimming and cycling. SQLite stores data, and Notion can optionally mirror it. Code identifiers, user-facing messages and docs are all in **Spanish**, so keep new code in Spanish too.

## Running

```bash
cp .env.example .env              # then set TELEGRAM_TOKEN (and optionally NOTION_*)
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```

There is no test suite, linter or CI. To smoke-test changes to the pure plan logic without Telegram, run:

```bash
.venv/bin/python -c "from datetime import date; import plan; print(plan.formatear_dia(date(2026, 10, 8)))"
.venv/bin/python -c "import plan; print(plan.resumen_semana(4))"
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

## Architecture

- **`config.py`** calls `load_dotenv()` at import time and exposes module-level constants (`PLAN_START`, `RACE_DATE`, `TIMEZONE`, `ATHLETE`, `GOAL_PACE_SEC`, …). `PLAN_START` must be a Monday because all week math depends on it.
- **`plan.py`** is pure and deterministic, with no I/O:
  - `WEEKS` has one dict per week (built with `_s(fase, descarga, calidad, larga, suave, bici, nata, nota)`). Its length *is* the plan length.
  - The week number comes from `(dia - PLAN_START).days // 7 + 1`. Gym variant is `"A"` on odd weeks and `"B"` on even weeks (`variante()`).
  - `_sesion_base(dia)` holds the fixed weekday → session mapping and returns `{fecha, semana, fase, descarga, fuera_de_plan, titulo, bloques: [(heading, text)], nota}`. `fuera_de_plan` is `"pre"`, `"post"` or `None`.
  - **Per-user changes** (`/mover`, `/saltar`, `/cambiar`, `/deshacer`) arrive as `plan.Cambio(origen, modo)`. `origen` is the same-week day whose base session is done; `modo` is `None|descanso|corta|bici|natacion`.
    - `sesiones_dia(dia, cambio)`, `tipos_dia(dia, cambio)`, `formatear_dia(dia, cambio)` and `resumen_semana(n, cambios)` apply them.
    - `mover`/`cambiar`/`deshacer` are pure. They return `{fecha: Cambio | None}` updates, where `None` deletes. Moving a skipped day recovers its session onto the target instead of swapping.
    - `avisos_semana` and `sugerir_recuperacion` check for bad combinations (`carga_dia`). Race day is locked (`es_dia_carrera`).
    - Changes live in the `cambios` table (`db.cambios_entre`/`guardar_cambios`). In `bot.py`, always fetch them with `_cambio(chat_id, d)` / `_cambios(chat_id, desde, hasta)` and pass them to plan/nutricion. Never call `sesiones_dia(d)` without them for user-facing output.
  - The **last week is race week** and is special-cased separately in `_titulo_carrera`, `_semana_carrera` and `tipos_dia`.
  - Running paces in `tabla_ritmos()` are derived from `GOAL_PACE_SEC`. Session text in `WEEKS` hard-codes paces as strings.
- **`gym.py`** contains routine dicts keyed by group (`"pierna" | "pecho" | "espalda" | "hombro"`) and then variant (`"A" | "B"`). `formatear()` renders them as Markdown.
- **`db.py`** is the SQLite **source of truth**. `conn()` is a context manager that commits on exit. `entrenos` has `UNIQUE(chat_id, fecha)` and is upserted. `metricas` keeps one row per chat/day, and peso/grasa are merged with `COALESCE`. Dates are stored as ISO strings.
- **`notion_sync.py`** is a **best-effort mirror**. The module-level singleton `sync = NotionSync()` is created at import. On `post_init`, `preparar()` auto-creates the "Entrenos" and "Métricas" databases under `NOTION_PARENT_PAGE_ID` and caches their IDs in `notion_dbs.json`. Writes go through `_upsert()`, which keys on the date. Every public method returns `bool` and swallows exceptions. Notion must never break the bot.
- **`bot.py`** contains the async command handlers. `main()` registers them from a `handlers` dict and schedules `aviso_diario` with `job_queue.run_daily` at `REMINDER_HOUR:REMINDER_MINUTE` in `TIMEZONE`. "Today" always comes from `_hoy()`, which is timezone-aware, not from `date.today()`.
  - It also schedules `aviso_compra` on Saturdays. PTB 21 `run_daily(days=...)` counts **0 = Sunday … 6 = Saturday**, so Saturday is `days=(6,)`.
- **Diet module (clean bulk):**
  - `nutricion.py` is pure. `objetivo_dia(fecha, peso, ajuste, cambio)` = TMB (Mifflin-St Jeor) × 1.35 + exercise kcal estimated from that day's plan sessions (parses minutes/km from `WEEKS` texts) + `SUPERAVIT_KCAL` + the per-user adjustment, rounded to 50. Protein is 2 g/kg, fat 0.9 g/kg, carbs the remainder. `nuevo_ajuste()` moves the adjustment ±100 kcal from the 14-day weight trend, clamped to [-300, 500].
  - `alimentos.py` is the food catalog: macros per 100 g as purchased, Mercadona category IDs, and include/`alguna`/exclude keywords for matching product names. Frozen fish (merluza, salmón, pota) is `duradero` like pantry items. `ALIMENTOS_EXCLUIDOS` (from `.env`) is applied with word-boundary matching.
  - `mercadona.py` is a **best-effort** client of the unofficial API (`tienda.mercadona.es/api`, warehouse `MERCADONA_WH`). `actualizar()` refreshes categories older than 6 days into SQLite (`mercadona_productos`/`mercadona_categorias`) and swallows network errors. `candidatos()` reads only from the cache. The API has no nutrition data.
  - `menu.py` is pure and is the **only source of gram amounts**. It is dish-based and personalised to the athlete's foods.
    - `PLATOS` holds `Plato`s: `nombre` (with `{p}` for the protein), a scaled `proteina`, scaled `hidratos` weights, fixed `fijos` grams, `secos` (nuts, cut first if fat overflows), `aceite`, `nota` and `fuera` (eaten out: it counts for macros as an estimate but is excluded from `Menu.gramos`, so it never reaches the shopping list).
    - `DESAYUNOS`/`MERIENDAS`/`SEMANA` map weekday to dish. Saturday lunch alternates with `COMIDA_SABADO` by plan week. Fruit rotates by `toordinal()`.
    - `_resolver` sizes components: each protein dish gets at least `PROTEINA_MIN_PLATO` g, carbs are split by `PESO_HIDRATOS` per meal and capped by `TOPE_HIDRATOS` (overflow goes to `DESBORDE_HIDRATOS`), and olive oil fills the remaining fat (min `AOVE_MINIMO`). Protein or fat excess is removed from carbs to keep kcal on target.
    - `VARIANTES` are budget swaps of the main protein, from most to least expensive.
    - HSN whey (`alimentos.WHEY`, `WHEY_GRAMOS`) is added to the merienda and counts for macros, but it isn't in `CATALOGO`, so it never reaches Mercadona. Use `con_suplementos()` when you need macros including whey. Creatine (`CREATINA_GRAMOS`) is only a reminder line.
  - `compra.py` sums the 7 daily menus into weekly grams (`construir`), picks products, and tries the budget variants in order. Fresh items cost whole packages, while durable ("despensa") items count only the prorated weekly cost. `formatear()` must stay under Telegram's 4096-char limit and strips Markdown characters from product names with `menu._md`.
  - `bot._menu()` reuses the variant that `compra.construir` picks for that day's Saturday–Friday week, using cached prices only. The daily reminder sends the menu as a second message.
  - Per-user data flow in `bot.py`: weight = `db.ultimo_peso(chat_id)`, falling back to `ATHLETE["peso_inicial_kg"]` (`_peso_actual`). The kcal adjustment is stored with its date in SQLite (`db.ajuste_kcal`/`guardar_ajuste_kcal`). It is recalculated **only** by the Saturday `aviso_compra` via `_ajuste_semanal`, at most once per day. `/dieta` and `/compra` only read it.
  - `mercadona.actualizar()` derives which categories to fetch from `alimentos.categorias()`, which skips excluded foods. Adding a food with a new category ID is enough to get it cached.

## Conventions

- **Adding or renaming a command** touches several places in `bot.py`: the handler function, the `handlers` dict in `main()`, `set_my_commands` in `post_init`, and the `AYUDA` help text. Also update the command table in `README.md`.
- **Changing the weekly structure** means keeping these in sync: `_sesion_base()`, `tipos_dia()`, `carga_dia()`, `minutos_alternativa()`, `nutricion.gasto_ejercicio()`, `_semana_carrera()`/`_titulo_carrera()`, the `plan.py` module docstring, and the README tables.
- **Notion select options must match the plan's strings**:
  - "Fase" options = `plan.FASES` values without the `"Fase N · "` prefix (see `fase_nombre_corto`).
  - "Tipo" options = values returned by `tipos_dia`.
  - "Día" options = `plan.DIAS`.

  `preparar()` doesn't migrate existing Notion databases, so schema changes only apply to newly created ones.
- In handlers, write to SQLite first, then call `notion_sync.sync.*`, and only use its `bool` result to append "Guardado también en Notion". Call `db.alta_usuario(chat_id, …)` before any per-user write.
- Replies use legacy `ParseMode.MARKDOWN` (`MD`): `*bold*`, `_italic_`, `` `code` ``. Unbalanced `*`/`_` in user notes or plan text will make Telegram reject the message.
- **Adding an `.env` setting** touches `config.py` (with a default), `.env.example`, and the README config table.
- **Adding a food** goes in `alimentos.CATALOGO` (`Alimento` dataclass):
  - Product-name matching uses `incluye` (all must appear), `alguna` (at least one) and `excluye`, all compared against `normalizar()` names (lowercase, accents stripped).
  - Set `gramos_unidad` for items sold by the unit and `duradero=True` for pantry items.
- **Pending work:** `.github/menu-fase2.md` has the agreed requirements for Phase 2. Per-meal dishes and grams (`menu.py`) are done. Still pending: cooking time based on training load, and sending the weekly menu with the Saturday list.
- Gym is only edited in `gym.py`, run/swim/bike sessions in `WEEKS`, and the athlete profile and goal pace in `config.py`.
- Never commit `.env`, `*.db` or `notion_dbs.json` (they are in `.gitignore`).
