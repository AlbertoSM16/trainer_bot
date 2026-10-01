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
```

## Architecture

- **`config.py`** calls `load_dotenv()` at import time and exposes module-level constants (`PLAN_START`, `RACE_DATE`, `TIMEZONE`, `ATHLETE`, `GOAL_PACE_SEC`, …). `PLAN_START` must be a Monday because all week math depends on it.
- **`plan.py`** is pure and deterministic, with no I/O:
  - `WEEKS` has one dict per week (built with `_s(fase, descarga, calidad, larga, suave, bici, nata, nota)`). Its length *is* the plan length.
  - The week number comes from `(dia - PLAN_START).days // 7 + 1`. Gym variant is `"A"` on odd weeks and `"B"` on even weeks (`variante()`).
  - `sesiones_dia(dia)` holds the fixed weekday → session mapping and returns `{fecha, semana, fase, descarga, fuera_de_plan, titulo, bloques: [(heading, text)], nota}`. `fuera_de_plan` is `"pre"`, `"post"` or `None`.
  - The **last week is race week** and is special-cased separately in `_titulo_carrera`, `_semana_carrera` and `tipos_dia`.
  - Running paces in `tabla_ritmos()` are derived from `GOAL_PACE_SEC`. Session text in `WEEKS` hard-codes paces as strings.
- **`gym.py`** contains routine dicts keyed by group (`"pierna" | "pecho" | "espalda" | "hombro"`) and then variant (`"A" | "B"`). `formatear()` renders them as Markdown.
- **`db.py`** is the SQLite **source of truth**. `conn()` is a context manager that commits on exit. `entrenos` has `UNIQUE(chat_id, fecha)` and is upserted. `metricas` keeps one row per chat/day, and peso/grasa are merged with `COALESCE`. Dates are stored as ISO strings.
- **`notion_sync.py`** is a **best-effort mirror**. The module-level singleton `sync = NotionSync()` is created at import. On `post_init`, `preparar()` auto-creates the "Entrenos" and "Métricas" databases under `NOTION_PARENT_PAGE_ID` and caches their IDs in `notion_dbs.json`. Writes go through `_upsert()`, which keys on the date. Every public method returns `bool` and swallows exceptions. Notion must never break the bot.
- **`bot.py`** contains the async command handlers. `main()` registers them from a `handlers` dict and schedules `aviso_diario` with `job_queue.run_daily` at `REMINDER_HOUR:REMINDER_MINUTE` in `TIMEZONE`. "Today" always comes from `_hoy()`, which is timezone-aware, not from `date.today()`.

## Conventions

- **Adding or renaming a command** touches several places in `bot.py`: the handler function, the `handlers` dict in `main()`, `set_my_commands` in `post_init`, and the `AYUDA` help text. Also update the command table in `README.md`.
- **Changing the weekly structure** means keeping these in sync: `sesiones_dia()`, `tipos_dia()`, `_semana_carrera()`/`_titulo_carrera()`, the `plan.py` module docstring, and the README tables.
- **Notion select options must match the plan's strings**:
  - "Fase" options = `plan.FASES` values without the `"Fase N · "` prefix (see `fase_nombre_corto`).
  - "Tipo" options = values returned by `tipos_dia`.
  - "Día" options = `plan.DIAS`.

  `preparar()` doesn't migrate existing Notion databases, so schema changes only apply to newly created ones.
- In handlers, write to SQLite first, then call `notion_sync.sync.*`, and only use its `bool` result to append "Guardado también en Notion". Call `db.alta_usuario(chat_id, …)` before any per-user write.
- Replies use legacy `ParseMode.MARKDOWN` (`MD`): `*bold*`, `_italic_`, `` `code` ``. Unbalanced `*`/`_` in user notes or plan text will make Telegram reject the message.
- Gym is only edited in `gym.py`, run/swim/bike sessions in `WEEKS`, and the athlete profile and goal pace in `config.py`.
- Never commit `.env`, `*.db` or `notion_dbs.json` (they are in `.gitignore`).
