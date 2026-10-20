"""Coach conversacional con Claude (API de Anthropic) y tool use.

Claude recibe el contexto del día (perfil, lo entrenado en 14 días según el Garmin, estado de
forma, sensaciones, consejo de hoy y dieta) y puede usar herramientas que llaman a `servicios`,
igual que los comandos: ver el consejo de carrera y bici, mandarlo al reloj, ver el historial,
registrar sensaciones, peso, grasa y tests, sincronizar el Garmin y cambiar platos del menú.

Es opcional y best-effort: sin `ANTHROPIC_API_KEY` o si la API falla, devuelve un mensaje
y el bot sigue funcionando (/carrera, /bici y /sensaciones funcionan con reglas, sin IA).
"""
import logging
from datetime import datetime, timedelta

import consejo
import db
import entrenador
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
_DEPORTE = {"type": "string", "enum": list(servicios.DEPORTES)}
_VACIO = {"type": "object", "properties": {}}

HERRAMIENTAS = [
    {"name": "ver_consejo",
     "description": "Qué entreno de carrera o de bici le conviene hoy. Lo calcula con reglas "
                    "a partir de lo que ha hecho (reloj), su estado de forma y sus sensaciones, "
                    "y lo deja listo para mandarlo al reloj.",
     "input_schema": {"type": "object", "properties": {"deporte": _DEPORTE},
                      "required": ["deporte"]}},
    {"name": "mandar_al_reloj",
     "description": "Manda al calendario del reloj el entreno aconsejado hoy para ese deporte "
                    "(llama antes a ver_consejo).",
     "input_schema": {"type": "object", "properties": {"deporte": _DEPORTE},
                      "required": ["deporte"]}},
    {"name": "ver_historial",
     "description": "Entrenos de los últimos 14 días día a día (carrera, bici, gimnasio con "
                    "ejercicios, pesos y progreso) con totales por semana.",
     "input_schema": _VACIO},
    {"name": "registrar_sensaciones",
     "description": "Anota cómo se encuentra hoy. Úsalo siempre que cuente cansancio, energía "
                    "o molestias, antes de dar el consejo del día.",
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
    {"name": "registrar_test",
     "description": "Registra un test o carrera de hoy y recalcula los ritmos.",
     "input_schema": {"type": "object", "properties": {
         "km": {"type": "number"},
         "tiempo": {"type": "string", "description": "mm:ss o h:mm:ss"}},
         "required": ["km", "tiempo"]}},
    {"name": "ver_ritmos",
     "description": "Ritmos de carrera y zonas de pulso de bici actuales del atleta.",
     "input_schema": _VACIO},
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
                    "de listar_platos, o 'normal' para volver al plato de siempre.",
     "input_schema": {"type": "object", "properties": {
         "dia": _DIA, "comida": {"type": "string", "enum": list(menu.COMIDAS)},
         "plato": {"type": "string"}}, "required": ["dia", "comida", "plato"]}},
    {"name": "ver_actividades",
     "description": "Últimas actividades del reloj Garmin con su análisis.",
     "input_schema": {"type": "object", "properties": {"n": {"type": "integer"}}}},
    {"name": "ver_bienestar",
     "description": "Training Readiness, HRV, sueño y Body Battery de los últimos 7 días.",
     "input_schema": _VACIO},
    {"name": "sincronizar_garmin",
     "description": "Baja ya las actividades nuevas del Garmin y devuelve su análisis.",
     "input_schema": _VACIO},
]


async def _ejecutar(chat_id: int, nombre: str, a: dict) -> str:
    if nombre == "ver_consejo":
        return servicios.consejo_texto(chat_id, a["deporte"])[0]
    if nombre == "mandar_al_reloj":
        return await servicios.mandar_reloj(chat_id, a["deporte"])
    if nombre == "ver_historial":
        return servicios.historial_texto(chat_id)
    if nombre == "registrar_sensaciones":
        return servicios.registrar_sensaciones(chat_id, a.get("energia"), a.get("molestias", ""),
                                               a.get("nota", ""))
    if nombre == "registrar_peso":
        return await servicios.registrar_metrica(chat_id, "peso", float(a["kg"]))
    if nombre == "registrar_grasa":
        return await servicios.registrar_metrica(chat_id, "grasa", float(a["porcentaje"]))
    if nombre == "registrar_test":
        return servicios.registrar_test(chat_id, float(a["km"]),
                                        servicios.parse_tiempo(a["tiempo"]))
    if nombre == "ver_ritmos":
        return servicios.ritmos_texto(chat_id)
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
    if nombre == "ver_actividades":
        return servicios.actividades_texto(chat_id, min(int(a.get("n") or 5), 10))
    if nombre == "ver_bienestar":
        return servicios.bienestar_texto(chat_id)
    if nombre == "sincronizar_garmin":
        return "\n\n".join(await servicios.sync_garmin(chat_id)) or "Sin actividades nuevas."
    raise servicios.ErrorUsuario(f"Herramienta desconocida: {nombre}")


def _contexto(chat_id: int) -> str:
    h = servicios.hoy()
    peso = servicios.peso_actual(chat_id)
    ref, fuente = servicios.ritmo(chat_id)
    u, fuente_u = servicios.umbral(chat_id)
    sens = db.sensaciones_desde(chat_id, h - timedelta(days=7))
    sens_txt = "\n".join(
        f"- {s['fecha']}: energía {s['energia'] or '?'}/5"
        + (f", molestias: {s['molestias']}" if s["molestias"] else "")
        + (f" ({s['nota']})" if s["nota"] else "") for s in sens[-8:]) or "- (sin registros)"
    o = servicios.objetivo(chat_id, h)
    e = servicios.estado(chat_id)
    r = consejo.resumen_14(e)
    acts = sorted((x for x in e.actividades if x["fecha"] >= (h - timedelta(days=13)).isoformat()),
                  key=lambda x: (x["fecha"], x.get("hora") or ""))
    lineas = [f"- {x['fecha']} {x['tipo']}: {x.get('nombre') or ''} "
              f"{(x.get('distancia_m') or 0) / 1000:.1f} km, {round(consejo.minutos(x))}'"
              + (f", FC {round(x['fc_media'])}" if x.get("fc_media") else "")
              + (" [dura]" if consejo.dura(x, e.umbral) else "") + (" [larga]" if consejo.larga(x) else "")
              + (" [pierna]" if consejo.pierna(x) else "") for x in acts]
    b = e.bienestar
    estado_txt = (entrenador.texto_bienestar(b).removeprefix("⌚ ") if b else "sin datos")
    consejos = [f"- {d}: {c['titulo']}" for d in servicios.DEPORTES
                if (c := db.consejo(chat_id, h, d))]
    return f"""Hoy es {servicios.nombre(h).lower()} {h.isoformat()} (hora {datetime.now(servicios.TZ):%H:%M}).

ATLETA
- {ATHLETE['nombre']}, {ATHLETE['edad']} años, {ATHLETE['altura_cm']} cm, {peso:g} kg (objetivo {PESO_OBJETIVO:g} kg).
- {ATHLETE['trabajo']}. Hace 4 comidas: desayuno, comida (táper L-X; el jueves la empresa le da pasta), merienda ligera y cena (el viernes cena fuera).
- Objetivo: {ATHLETE['objetivo']} Quiere mejorar en 10 km y media maratón (sin fecha de carrera) y en bici.
- Mejor marca: media maratón a {ATHLETE['marca_media']}. Ritmo de media actual: {plan.pace(ref)} /km ({fuente}).
- FC de umbral: {f'{u} ppm ({fuente_u})' if u else 'desconocida'}.
- Escribe su rutina de gimnasio (ejercicios, pesos, series) en Garmin Connect y la hace con el reloj. No hay plan semanal fijo.
- Toma creatina y whey. Come: pollo, salchichas de pollo, pavo, ternera picada, solomillo de cerdo; pescado (pota, salmón, merluza congelados) por la noche o el finde; arroz, pasta, patata, boniato, noodles, tortillas de fajita; legumbre de bote una vez por semana; kiwi, plátano y manzana; canónigos, tomate, zanahoria, cebolla y pimientos.

GARMIN FORERUNNER 265 · {'conectado' if servicios.garmin_activo(chat_id) else 'no conectado'}
Actividades de los últimos 14 días:
{chr(10).join(lineas) or '- ninguna'}
Km de carrera: {r['km_7']} en los últimos 7 días ({r['km_7_previos']} los 7 anteriores). Sesiones duras en 7 días: {r['duras_7']}. Días desde la última tirada larga: {r['dias_desde_larga'] if r['dias_desde_larga'] is not None else 'ninguna'}.
Estado de forma de hoy: {estado_txt}

CONSEJO YA DADO HOY
{chr(10).join(consejos) or '- todavía ninguno'}

SENSACIONES (últimos 7 días)
{sens_txt}

DIETA DE HOY: {o.kcal} kcal · P {o.proteina} g · G {o.grasa} g · HC {o.hidratos} g (ejercicio {o.ejercicio} kcal, superávit {nutricion.superavit(peso)} kcal)."""


INSTRUCCIONES = """Eres el entrenador personal y nutricionista de un atleta popular, dentro de un bot de Telegram. Respondes siempre en español de España, de tú, cercano y directo. Sé breve (menos de 120 palabras) salvo que te pida detalle.

Cómo trabajas:
- No hay plan semanal fijo. Cada día decides con él qué hacer en carrera y en bici a partir de lo que ha hecho (reloj), su estado de forma y sus sensaciones. Para eso usa ver_consejo: es un motor de reglas (80/20: casi todo suave, 1-2 sesiones duras por semana entre carrera y bici, una tirada larga cada 7-10 días, 48 h entre sesiones duras y nada duro tras pierna). Puedes matizarlo, pero no lo contradigas sin motivo.
- Si quiere el entreno en el reloj, usa mandar_al_reloj (después de ver_consejo).
- El gimnasio lo escribe él en Garmin Connect con sus pesos y series: no le programes la rutina. Puedes comentar su progreso con ver_historial (pesos y repeticiones frente a la vez anterior).
- Si cuenta cansancio, mal sueño, estrés, molestias o dolor: primero registrar_sensaciones y luego ver_consejo, que ya lo tiene en cuenta.
- Molestias en piernas → nada de impacto (bici suave o descanso). Fatiga general o enfermedad → descanso.
- Ante dolor agudo, punzante, con hinchazón o que dura más de una semana: descanso de esa zona y recomienda ir al fisio o al médico. No diagnostiques.
- Si te da un peso, % de grasa o el tiempo de un test, regístralo con su herramienta.
- Para cambiar comidas usa listar_platos y cambiar_plato. Para dudas de dieta, apóyate en ver_dieta y ver_menu y no inventes cantidades. Las kcal de ejercicio salen de la media de su reloj en 14 días.
- No inventes datos: consulta con las herramientas si no está en el contexto.
- Si una herramienta devuelve un error, explícalo en una frase y propone una alternativa.

Formato: Markdown de Telegram (antiguo). Solo *negrita* con un asterisco y _cursiva_ con guion bajo. Nada de **, #, tablas ni enlaces. Listas con "•"."""

def _texto(bloques) -> str:
    return "\n".join(b.text for b in bloques if b.type == "text").strip()


async def responder(chat_id: int, texto: str) -> str:
    if not activo():
        return ("🤖 El coach conversacional necesita una API key de Anthropic "
                "(`ANTHROPIC_API_KEY` en `.env`, de console.anthropic.com). "
                "La suscripción Claude Pro no sirve para esto.\n\n"
                "Mientras tanto, usa /carrera, /bici y /sensaciones.")
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
            respuesta = "He hecho varias consultas sin llegar a una respuesta; prueba con /carrera o /bici."
    except Exception as e:  # noqa: BLE001
        log.warning("Fallo al hablar con Claude: %s", e)
        return ("🤖 No he podido hablar con Claude ahora mismo. Inténtalo en un rato o usa "
                "/sensaciones y los comandos.")
    respuesta = respuesta or "👍"
    db.guardar_mensajes(chat_id, [("user", texto), ("assistant", respuesta)],
                        datetime.now(servicios.TZ).isoformat(timespec="seconds"))
    return respuesta


async def comentar_actividad(chat_id: int, analisis: str) -> str:
    """Comentario breve del coach sobre una actividad recién sincronizada ("" si no hay IA)."""
    if not activo() or db.sumar_uso_coach(chat_id, servicios.hoy()) > CLAUDE_MAX_MENSAJES_DIA:
        return ""
    try:
        r = await _api().messages.create(
            model=CLAUDE_MODEL, max_tokens=400,
            system=INSTRUCCIONES + "\n\n" + _contexto(chat_id),
            messages=[{"role": "user", "content":
                       "Acabo de terminar esta actividad (análisis automático del bot):\n\n"
                       f"{analisis}\n\nComéntala como mi entrenador en 2-4 frases: qué tal ha "
                       "ido frente a lo aconsejado (si lo hubo) y qué hago mañana (recuperación, comida). No "
                       "repitas los números que ya tengo."}])
        return "🧑‍🏫 " + _texto(r.content) if _texto(r.content) else ""
    except Exception as e:  # noqa: BLE001
        log.warning("Coach: no pude comentar la actividad: %s", e)
        return ""


async def comentar_consejo(chat_id: int, texto: str) -> str:
    """Matiz breve del coach al consejo de /carrera o /bici ("" si no hay IA)."""
    if not activo() or db.sumar_uso_coach(chat_id, servicios.hoy()) > CLAUDE_MAX_MENSAJES_DIA:
        return ""
    try:
        r = await _api().messages.create(
            model=CLAUDE_MODEL, max_tokens=300,
            system=INSTRUCCIONES + "\n\n" + _contexto(chat_id),
            messages=[{"role": "user", "content":
                       "El bot me ha dado este consejo para hoy (reglas automáticas):\n\n"
                       f"{texto}\n\nAñade en 1-2 frases un matiz útil como mi entrenador "
                       "(por qué encaja con lo que llevo o en qué fijarme). No repitas el "
                       "entreno ni los números."}])
        return "🧑‍🏫 " + _texto(r.content) if _texto(r.content) else ""
    except Exception as e:  # noqa: BLE001
        log.warning("Coach: no pude comentar el consejo: %s", e)
        return ""
