"""Coach conversacional con Claude (API de Anthropic) y tool use.

Claude recibe el contexto del día (perfil, plan, sensaciones y dieta) y puede usar herramientas
que llaman a `servicios`, igual que los comandos: adaptar la semana según las sensaciones,
registrar peso, grasa, entrenos y tests, y cambiar platos del menú.

Es opcional y best-effort: sin `ANTHROPIC_API_KEY` o si la API falla, devuelve un mensaje
y el bot sigue funcionando (/sensaciones tiene reglas sin IA).
"""
import logging
from datetime import datetime, timedelta

import db
import menu
import nutricion
import plan
import servicios
from config import (ANTHROPIC_API_KEY, ATHLETE, CLAUDE_MAX_MENSAJES_DIA, CLAUDE_MODEL,
                    PESO_OBJETIVO)

log = logging.getLogger(__name__)

MAX_VUELTAS = 8
HISTORIAL = 20

_cliente = None


def activo() -> bool:
    return bool(ANTHROPIC_API_KEY)


def _api():
    global _cliente
    if _cliente is None:
        import anthropic
        _cliente = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _cliente


_DIA = {"type": "string",
        "description": "hoy, mañana, un día de esta semana (lunes…domingo) o AAAA-MM-DD"}

HERRAMIENTAS = [
    {"name": "ver_dia", "description": "Sesión de entrenamiento completa de un día (con cambios).",
     "input_schema": {"type": "object", "properties": {"dia": _DIA}, "required": ["dia"]}},
    {"name": "ver_semana", "description": "Resumen de la semana actual del plan con los cambios.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "cambiar_sesion",
     "description": "Cambia la sesión de un día (de hoy en adelante, semana actual). Modos: "
                    "descanso (no entrena), corta (mitad de volumen, misma intensidad), "
                    "suave (misma duración, intensidad baja: carrera en Z2, gym -30-40 % carga), "
                    "bici o natacion (alternativa sin impacto), normal (deshace el modo).",
     "input_schema": {"type": "object", "properties": {
         "dia": _DIA,
         "modo": {"type": "string",
                  "enum": ["descanso", "corta", "suave", "bici", "natacion", "normal"]}},
         "required": ["dia", "modo"]}},
    {"name": "mover_sesion",
     "description": "Intercambia las sesiones de dos días de la misma semana. Si el día origen "
                    "estaba saltado, recupera su sesión en el día destino.",
     "input_schema": {"type": "object", "properties": {"desde": _DIA, "hasta": _DIA},
                      "required": ["desde", "hasta"]}},
    {"name": "deshacer_cambios",
     "description": "Vuelve a la sesión original de un día, o de toda la semana con dia='semana'.",
     "input_schema": {"type": "object", "properties": {"dia": {"type": "string"}},
                      "required": ["dia"]}},
    {"name": "registrar_sensaciones",
     "description": "Anota cómo se encuentra hoy. Úsalo siempre que cuente cansancio, energía "
                    "o molestias, antes de adaptar el plan.",
     "input_schema": {"type": "object", "properties": {
         "energia": {"type": "integer", "minimum": 1, "maximum": 5,
                     "description": "1 = fundido, 3 = normal, 5 = a tope"},
         "molestias": {"type": "string", "description": "Zona y tipo de molestia, o vacío"},
         "nota": {"type": "string"}}, "required": ["energia"]}},
    {"name": "registrar_peso", "description": "Registra el peso de hoy en kg.",
     "input_schema": {"type": "object", "properties": {"kg": {"type": "number"}},
                      "required": ["kg"]}},
    {"name": "registrar_grasa", "description": "Registra el % de grasa corporal de hoy.",
     "input_schema": {"type": "object", "properties": {"porcentaje": {"type": "number"}},
                      "required": ["porcentaje"]}},
    {"name": "marcar_hecho", "description": "Marca como completado el entreno de hoy.",
     "input_schema": {"type": "object", "properties": {"nota": {"type": "string"}}}},
    {"name": "registrar_test",
     "description": "Registra un test o carrera de hoy y recalcula los ritmos.",
     "input_schema": {"type": "object", "properties": {
         "km": {"type": "number"},
         "tiempo": {"type": "string", "description": "mm:ss o h:mm:ss"}},
         "required": ["km", "tiempo"]}},
    {"name": "ver_ritmos", "description": "Tabla de ritmos y zonas actuales del atleta.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ver_dieta", "description": "Kcal y macros objetivo de un día.",
     "input_schema": {"type": "object", "properties": {"dia": _DIA}, "required": ["dia"]}},
    {"name": "ver_menu", "description": "Menú de un día con platos y gramos.",
     "input_schema": {"type": "object", "properties": {"dia": _DIA}, "required": ["dia"]}},
    {"name": "listar_platos",
     "description": "Platos disponibles (con su clave) para una comida.",
     "input_schema": {"type": "object", "properties": {
         "comida": {"type": "string", "enum": list(menu.COMIDAS)}}, "required": ["comida"]}},
    {"name": "cambiar_plato",
     "description": "Cambia el plato de una comida de un día (hoy a 7 días vista). Usa la clave "
                    "de listar_platos, o 'normal' para volver al plato del plan.",
     "input_schema": {"type": "object", "properties": {
         "dia": _DIA, "comida": {"type": "string", "enum": list(menu.COMIDAS)},
         "plato": {"type": "string"}}, "required": ["dia", "comida", "plato"]}},
]


async def _ejecutar(chat_id: int, nombre: str, a: dict) -> str:
    if nombre == "ver_dia":
        d = servicios.parse_dia(a.get("dia", "hoy"))
        if d is None:
            raise servicios.ErrorUsuario("Día no válido.")
        return servicios.formatear_dia(chat_id, d)
    if nombre == "ver_semana":
        return servicios.resumen_semana(chat_id, plan.semana_indice(servicios.hoy()))
    if nombre == "cambiar_sesion":
        if a["modo"] == "descanso":
            return servicios.saltar(chat_id, a["dia"])
        return servicios.cambiar(chat_id, a["modo"], a["dia"])
    if nombre == "mover_sesion":
        return servicios.mover(chat_id, a["desde"], a["hasta"])
    if nombre == "deshacer_cambios":
        return servicios.deshacer(chat_id, a.get("dia"))
    if nombre == "registrar_sensaciones":
        return servicios.registrar_sensaciones(chat_id, a.get("energia"), a.get("molestias", ""),
                                               a.get("nota", ""))
    if nombre == "registrar_peso":
        return await servicios.registrar_metrica(chat_id, "peso", float(a["kg"]))
    if nombre == "registrar_grasa":
        return await servicios.registrar_metrica(chat_id, "grasa", float(a["porcentaje"]))
    if nombre == "marcar_hecho":
        return await servicios.marcar_hecho(chat_id, a.get("nota", ""))
    if nombre == "registrar_test":
        return servicios.registrar_test(chat_id, float(a["km"]),
                                        servicios.parse_tiempo(a["tiempo"]))
    if nombre == "ver_ritmos":
        return plan.tabla_ritmos(*servicios.ritmo(chat_id))
    if nombre == "ver_dieta":
        d = servicios.parse_dia(a.get("dia", "hoy")) or servicios.hoy()
        return servicios.dieta_texto(chat_id, d)
    if nombre == "ver_menu":
        d = servicios.parse_dia(a.get("dia", "hoy")) or servicios.hoy()
        return servicios.menu_texto(chat_id, d)
    if nombre == "listar_platos":
        return servicios.platos_disponibles(a["comida"])
    if nombre == "cambiar_plato":
        return servicios.cambiar_plato(chat_id, a.get("dia"), a["comida"], a["plato"])
    raise servicios.ErrorUsuario(f"Herramienta desconocida: {nombre}")


def _contexto(chat_id: int) -> str:
    h = servicios.hoy()
    wn = plan.semana_indice(h)
    peso = servicios.peso_actual(chat_id)
    ref, fuente = servicios.ritmo(chat_id)
    sens = db.sensaciones_desde(chat_id, h - timedelta(days=7))
    sens_txt = "\n".join(
        f"- {s['fecha']}: energía {s['energia'] or '?'}/5"
        + (f", molestias: {s['molestias']}" if s["molestias"] else "")
        + (f" ({s['nota']})" if s["nota"] else "") for s in sens[-8:]) or "- (sin registros)"
    hechos = db.entrenos_entre(chat_id, servicios.lunes(h), h)
    hechos_txt = ", ".join(f"{r['fecha']} {r['titulo']}" for r in hechos) or "ninguno"
    o = servicios.objetivo(chat_id, h)
    semana = (servicios.resumen_semana(chat_id, wn) if wn >= 1 else
              f"El plan empieza el {plan.PLAN_START.isoformat()}.")
    test, km = plan.proximo_test(h)
    return f"""Hoy es {servicios.nombre(h).lower()} {h.isoformat()} (hora {datetime.now(servicios.TZ):%H:%M}).

ATLETA
- {ATHLETE['nombre']}, {ATHLETE['edad']} años, {ATHLETE['altura_cm']} cm, {peso:g} kg (objetivo {PESO_OBJETIVO:g} kg).
- {ATHLETE['trabajo']}. Hace 4 comidas: desayuno, comida (táper L-X; el jueves la empresa le da pasta), merienda ligera y cena (el viernes cena fuera).
- Objetivo: {ATHLETE['objetivo']}
- Mejor marca: media maratón a {ATHLETE['marca_media']}. Ritmo de media actual estimado: {plan.pace(ref)} /km ({fuente}).
- Toma creatina y whey. Come: pollo, salchichas de pollo, pavo, ternera picada, solomillo de cerdo; pescado (pota, salmón, merluza congelados) por la noche o el finde; arroz, pasta, patata, boniato, noodles, tortillas de fajita; legumbre de bote una vez por semana; kiwi, plátano y manzana; canónigos, tomate, zanahoria, cebolla y pimientos.

PLAN (cíclico, bloques de 8 semanas: 3 de carga + 1 de descarga, dos veces; test de 5/10 km el sábado de la semana 8)
- L pecho+tríceps · M espalda+bíceps · X pierna · J natación · V descanso flexible · S carrera (la única de la semana, la sesión clave) + hombro y core · D bici.
- Próximo test: {km} km el {test.isoformat()}.

SEMANA ACTUAL
{semana}

Entrenos marcados como hechos esta semana: {hechos_txt}

SENSACIONES (últimos 7 días)
{sens_txt}

DIETA DE HOY: {o.kcal} kcal · P {o.proteina} g · G {o.grasa} g · HC {o.hidratos} g (superávit {nutricion.superavit(peso)} kcal)."""


INSTRUCCIONES = """Eres el entrenador personal y nutricionista de un atleta popular, dentro de un bot de Telegram. Respondes siempre en español de España, de tú, cercano y directo. Sé breve (menos de 120 palabras) salvo que te pida detalle.

Cómo actuar:
- Si cuenta cansancio, mal sueño, estrés, molestias o dolor: primero registrar_sensaciones y después adapta la sesión con cambiar_sesion o mover_sesion SIN pedir confirmación. Luego explica en 1-2 frases qué has cambiado y por qué.
- Molestias en piernas → sesiones sin impacto (natación o bici suave) o descanso. Molestias en hombro o espalda → bici o carrera; evita gimnasio de tren superior y natación. Fatiga general o enfermedad → descanso.
- La carrera del sábado es la sesión más importante: si se pierde, intenta recuperarla el viernes (descanso flexible) con mover_sesion. Deja al menos 48 h entre pierna y carrera.
- Ante dolor agudo, punzante, con hinchazón o que dura más de una semana: descanso de esa zona y recomienda ir al fisio o al médico. No diagnostiques.
- Solo se pueden cambiar días de hoy en adelante dentro de la semana actual (lunes a domingo).
- Si te da un peso, % de grasa, un entreno hecho o el tiempo de un test, regístralo con su herramienta.
- Para cambiar comidas usa listar_platos y cambiar_plato. Para dudas de dieta, apóyate en ver_dieta y ver_menu y no inventes cantidades.
- No inventes datos del plan: consulta con las herramientas si no está en el contexto.
- Si una herramienta devuelve un error, explícalo en una frase y propone una alternativa.

Formato: Markdown de Telegram (antiguo). Solo *negrita* con un asterisco y _cursiva_ con guion bajo. Nada de **, #, tablas ni enlaces. Listas con "•"."""


def _texto(bloques) -> str:
    return "\n".join(b.text for b in bloques if b.type == "text").strip()


async def responder(chat_id: int, texto: str) -> str:
    if not activo():
        return ("🤖 El coach conversacional necesita una API key de Anthropic "
                "(`ANTHROPIC_API_KEY` en `.env`, de console.anthropic.com). "
                "La suscripción Claude Pro no sirve para esto.\n\n"
                "Mientras tanto, usa /sensaciones para adaptar el día de hoy.")
    h = servicios.hoy()
    if db.sumar_uso_coach(chat_id, h) > CLAUDE_MAX_MENSAJES_DIA:
        return (f"🤖 Has llegado al límite de {CLAUDE_MAX_MENSAJES_DIA} mensajes de hoy "
                "(`CLAUDE_MAX_MENSAJES_DIA`). Los comandos siguen funcionando.")

    historial = db.historial(chat_id, HISTORIAL)
    while historial and historial[0]["role"] != "user":
        historial.pop(0)
    mensajes = historial + [{"role": "user", "content": texto}]
    sistema = INSTRUCCIONES + "\n\n" + _contexto(chat_id)
    try:
        respuesta = ""
        for _ in range(MAX_VUELTAS):
            r = await _api().messages.create(
                model=CLAUDE_MODEL, max_tokens=1024, system=sistema,
                tools=HERRAMIENTAS, messages=mensajes)
            if r.stop_reason != "tool_use":
                respuesta = _texto(r.content)
                break
            mensajes.append({"role": "assistant", "content": r.content})
            resultados = []
            for b in r.content:
                if b.type != "tool_use":
                    continue
                try:
                    salida, error = await _ejecutar(chat_id, b.name, b.input), False
                except servicios.ErrorUsuario as e:
                    salida, error = str(e), True
                except Exception as e:  # noqa: BLE001
                    log.exception("Error en la herramienta %s", b.name)
                    salida, error = f"Error interno: {e}", True
                log.info("Coach %s · %s(%s) -> %s", chat_id, b.name, b.input,
                         "error" if error else "ok")
                resultados.append({"type": "tool_result", "tool_use_id": b.id,
                                   "content": salida, "is_error": error})
            mensajes.append({"role": "user", "content": resultados})
        else:
            respuesta = "He hecho varios cambios; revisa /semana para ver cómo ha quedado."
    except Exception as e:  # noqa: BLE001
        log.warning("Fallo al hablar con Claude: %s", e)
        return ("🤖 No he podido hablar con Claude ahora mismo. Inténtalo en un rato o usa "
                "/sensaciones y los comandos.")
    respuesta = respuesta or "👍"
    db.guardar_mensajes(chat_id, [("user", texto), ("assistant", respuesta)],
                        datetime.now(servicios.TZ).isoformat(timespec="seconds"))
    return respuesta
