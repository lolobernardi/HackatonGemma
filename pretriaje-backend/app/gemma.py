"""Cliente de Ollama y parseo de tool calls.

Toda la comunicación con el modelo pasa por acá. El resto del sistema no sabe
que existe HTTP, ni Ollama, ni el formato de tool calls: solo recibe un
`RespuestaGemma` o una `GemmaError`.

Regla de oro del módulo: **cualquier** cosa que salga mal —timeout, conexión
caída, JSON roto, tool call ausente, campo fuera de rango— termina en
`GemmaError`. Nunca se devuelve una respuesta a medias ni se inventa un valor
por defecto para un campo clínico.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from pydantic import ValidationError

from app import config
from app.schema import (
    CamposExtraidos,
    DiscriminadoresGenerales,
    FichaClinica,
    RespuestaGemma,
)

logger = logging.getLogger(__name__)

NOMBRE_TOOL = "actualizar_ficha"


class GemmaError(Exception):
    """Falla en la llamada al modelo o en el parseo de su respuesta.

    El orquestador la traduce a una respuesta `tipo: "error_seguro"`.
    """

    def __init__(self, causa: str, detalle: str = "") -> None:
        # `causa` es una etiqueta corta y estable, apta para loguear y para
        # métricas: timeout, conexion, http, json, sin_tool_call, validacion.
        self.causa = causa
        self.detalle = detalle
        super().__init__(f"{causa}: {detalle}" if detalle else causa)


# --------------------------------------------------------------------------- #
# Schema de la tool
# --------------------------------------------------------------------------- #

_DISCRIMINADORES_GENERALES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Discriminadores de riesgo vital. Solo lo que la persona dijo.",
    "properties": {
        "riesgo_via_aerea": {
            "type": ["boolean", "null"],
            "description": "True si algo obstruye la vía aérea o no puede tragar.",
        },
        "respira_normalmente": {
            "type": ["boolean", "null"],
            "description": "True si respira sin dificultad. False si le falta el aire.",
        },
        "nivel_conciencia": {
            "type": ["string", "null"],
            "enum": ["alerta", "somnoliento", "confuso", "no_responde", None],
            "description": "Estado de conciencia tal como lo describió la persona.",
        },
        "hemorragia_mayor": {
            "type": ["boolean", "null"],
            "description": "True si hay sangrado abundante o que no se detiene.",
        },
        "dolor_eva": {
            "type": ["integer", "null"],
            "minimum": 0,
            "maximum": 10,
            "description": (
                "Dolor de 0 a 10, SOLO si la persona dio un número o equivalente "
                "claro. No convertir adjetivos como 'mucho' en un número."
            ),
        },
        "temperatura_c": {
            "type": ["number", "null"],
            "description": "Temperatura en grados Celsius, solo si la midió.",
        },
        "inicio": {
            "type": ["string", "null"],
            "enum": ["subito", "gradual", None],
            "description": "Si empezó de golpe (subito) o de a poco (gradual).",
        },
        "tiempo_evolucion_horas": {
            "type": ["number", "null"],
            "description": "Hace cuántas horas empezó. 0.5 = media hora, 24 = un día.",
        },
    },
}

TOOL_ACTUALIZAR_FICHA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": NOMBRE_TOOL,
        "description": (
            "Registra los datos clínicos que la persona mencionó y, si hace "
            "falta, una única pregunta para seguir. Se llama SIEMPRE, en "
            "todos los turnos, aunque no se haya podido extraer nada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "edad": {
                    "type": ["integer", "null"],
                    "minimum": 0,
                    "maximum": 120,
                    "description": "Edad en años de la persona que tiene el síntoma.",
                },
                "es_para_tercero": {
                    "type": ["boolean", "null"],
                    "description": (
                        "True si consulta por otra persona (un hijo, un familiar)."
                    ),
                },
                "motivo_consulta": {
                    "type": ["string", "null"],
                    "enum": [*config.MOTIVOS_CONSULTA, None],
                    "description": "Motivo principal, elegido de la lista.",
                },
                "discriminadores_generales": _DISCRIMINADORES_GENERALES_SCHEMA,
                "discriminadores_especificos": {
                    "type": ["object", "null"],
                    "description": (
                        "Detalles propios del motivo de consulta, como pares "
                        "clave/valor. Ejemplo: {'irradia_brazo': true}."
                    ),
                    "additionalProperties": True,
                },
                "pregunta_aclaracion": {
                    "type": ["string", "null"],
                    "description": (
                        "UNA sola pregunta en lenguaje coloquial, sin jerga "
                        "médica. null si ya no falta información."
                    ),
                },
                "confianza_extraccion": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": (
                        "Qué tan seguro estás de lo que extrajiste, de 0.0 a 1.0."
                    ),
                },
            },
            "required": ["confianza_extraccion"],
        },
    },
}


# --------------------------------------------------------------------------- #
# Cliente HTTP
# --------------------------------------------------------------------------- #

_cliente: httpx.AsyncClient | None = None


def obtener_cliente() -> httpx.AsyncClient:
    """Cliente httpx compartido: reusa conexiones entre turnos."""
    global _cliente
    if _cliente is None or _cliente.is_closed:
        _cliente = httpx.AsyncClient(timeout=config.TIMEOUT_GEMMA_S)
    return _cliente


async def cerrar_cliente() -> None:
    """Cierra el cliente compartido. Se llama al apagar la app."""
    global _cliente
    if _cliente is not None and not _cliente.is_closed:
        await _cliente.aclose()
    _cliente = None


# --------------------------------------------------------------------------- #
# Llamada principal
# --------------------------------------------------------------------------- #


async def llamar_gemma(mensajes: list[dict]) -> RespuestaGemma:
    """Manda la conversación a Ollama y devuelve la tool call ya parseada.

    Levanta `GemmaError` ante cualquier problema. Nunca devuelve parcial.
    """
    payload = {
        "model": config.MODELO,
        "messages": mensajes,
        "tools": [TOOL_ACTUALIZAR_FICHA],
        "stream": False,
        "options": {"temperature": config.TEMPERATURA},
    }

    cliente = obtener_cliente()
    inicio = time.perf_counter()
    try:
        resp = await cliente.post(
            f"{config.OLLAMA_URL}/api/chat",
            json=payload,
            timeout=config.TIMEOUT_GEMMA_S,
        )
        resp.raise_for_status()
        cuerpo = resp.json()
    except httpx.TimeoutException as exc:
        raise GemmaError("timeout", f"{config.TIMEOUT_GEMMA_S}s") from exc
    except httpx.ConnectError as exc:
        raise GemmaError("conexion", config.OLLAMA_URL) from exc
    except httpx.HTTPStatusError as exc:
        raise GemmaError("http", str(exc.response.status_code)) from exc
    except httpx.HTTPError as exc:
        raise GemmaError("transporte", type(exc).__name__) from exc
    except json.JSONDecodeError as exc:
        raise GemmaError("json", "respuesta de Ollama no es JSON") from exc
    except ValueError as exc:
        # httpx levanta ValueError si el body no parsea como JSON.
        raise GemmaError("json", "respuesta de Ollama no es JSON") from exc

    latencia_ms = int((time.perf_counter() - inicio) * 1000)
    respuesta = parsear_respuesta(cuerpo)
    respuesta.latencia_ms = latencia_ms
    return respuesta


def parsear_respuesta(cuerpo: dict) -> RespuestaGemma:
    """Extrae y valida la tool call `actualizar_ficha` del body de Ollama."""
    if not isinstance(cuerpo, dict):
        raise GemmaError("json", "el body no es un objeto")

    mensaje = cuerpo.get("message")
    if not isinstance(mensaje, dict):
        raise GemmaError("json", "falta 'message' en la respuesta")

    tool_calls = mensaje.get("tool_calls") or []
    if not isinstance(tool_calls, list) or not tool_calls:
        # El modelo contestó en prosa en vez de llamar a la tool. No se intenta
        # rescatar el texto: sin ficha estructurada no hay turno válido.
        raise GemmaError("sin_tool_call", "el modelo no llamó a la función")

    argumentos = _extraer_argumentos(tool_calls)

    # `pregunta_aclaracion` y `confianza_extraccion` no son parte de la ficha:
    # se sacan antes de validar los campos clínicos.
    pregunta = argumentos.pop("pregunta_aclaracion", None)
    confianza = argumentos.pop("confianza_extraccion", None)

    try:
        campos = CamposExtraidos.model_validate(argumentos)
    except ValidationError as exc:
        raise GemmaError("validacion", _resumen_errores(exc)) from exc

    return RespuestaGemma(
        campos=campos,
        pregunta_aclaracion=_normalizar_pregunta(pregunta),
        confianza_extraccion=_normalizar_confianza(confianza),
    )


def _extraer_argumentos(tool_calls: list) -> dict:
    """Busca la tool call correcta y normaliza sus argumentos a dict.

    Ollama devuelve `arguments` como objeto, pero algunos modelos lo emiten
    como string JSON (estilo OpenAI). Se aceptan las dos formas.
    """
    candidata: dict | None = None
    for tc in tool_calls:
        funcion = tc.get("function") if isinstance(tc, dict) else None
        if not isinstance(funcion, dict):
            continue
        if funcion.get("name") == NOMBRE_TOOL:
            candidata = funcion
            break
        if candidata is None:
            candidata = funcion  # último recurso: la primera que haya

    if candidata is None:
        raise GemmaError("sin_tool_call", "tool_calls sin función válida")

    argumentos = candidata.get("arguments")
    if isinstance(argumentos, str):
        try:
            argumentos = json.loads(argumentos)
        except json.JSONDecodeError as exc:
            raise GemmaError("json", "arguments no es JSON válido") from exc
    if argumentos is None:
        argumentos = {}
    if not isinstance(argumentos, dict):
        raise GemmaError("json", "arguments no es un objeto")

    return dict(argumentos)


def _normalizar_pregunta(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = valor.strip()
    return limpio or None


def _normalizar_confianza(valor: Any) -> float:
    """Confianza ausente o inválida se trata como 0.0.

    Nunca se asume confianza alta: si el modelo no la reportó, el orquestador
    tiene que seguir preguntando, no cerrar la ficha.
    """
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return 0.0
    return max(0.0, min(1.0, float(valor)))


def _resumen_errores(exc: ValidationError) -> str:
    """Resumen de errores de validación sin exponer valores clínicos."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}"
        for err in exc.errors()[:5]
    )


# --------------------------------------------------------------------------- #
# Merge
# --------------------------------------------------------------------------- #


def merge_ficha(ficha_actual: FichaClinica, campos_nuevos: CamposExtraidos) -> FichaClinica:
    """Aplica los campos extraídos sobre la ficha, sin borrar nada.

    Invariante central del sistema: **un turno posterior nunca destruye
    información ya recolectada**. Un `None` significa "el modelo no habló de
    esto en este turno", jamás "olvidalo".
    """
    datos = ficha_actual.model_dump()

    for campo in ("edad", "es_para_tercero", "motivo_consulta"):
        valor = getattr(campos_nuevos, campo)
        if valor is not None:
            datos[campo] = valor

    # Slug fuera de la lista soportada: se lo manda al catch-all en vez de
    # pasarle basura al motor de reglas.
    if datos["motivo_consulta"] is not None and (
        datos["motivo_consulta"] not in config.MOTIVOS_CONSULTA
    ):
        logger.warning("motivo_desconocido normalizado=otro")
        datos["motivo_consulta"] = "otro"

    if campos_nuevos.discriminadores_generales is not None:
        dg = datos["discriminadores_generales"]
        nuevos_dg = campos_nuevos.discriminadores_generales.model_dump()
        for campo, valor in nuevos_dg.items():
            if valor is not None:
                dg[campo] = valor

    if campos_nuevos.discriminadores_especificos:
        for clave, valor in campos_nuevos.discriminadores_especificos.items():
            if valor is not None:
                datos["discriminadores_especificos"][clave] = valor

    return FichaClinica.model_validate(datos)


def campos_completados(anterior: FichaClinica, nueva: FichaClinica) -> list[str]:
    """Nombres de los campos que pasaron de vacíos a completos en este turno.

    Sirve para loguear qué se llenó sin loguear NUNCA con qué valor.
    """
    completados: list[str] = []
    for campo in ("edad", "motivo_consulta"):
        if getattr(anterior, campo) is None and getattr(nueva, campo) is not None:
            completados.append(campo)

    ant_dg = anterior.discriminadores_generales
    new_dg = nueva.discriminadores_generales
    for campo in DiscriminadoresGenerales.model_fields:
        if getattr(ant_dg, campo) is None and getattr(new_dg, campo) is not None:
            completados.append(campo)

    nuevas_claves = set(nueva.discriminadores_especificos) - set(
        anterior.discriminadores_especificos
    )
    completados.extend(sorted(nuevas_claves))
    return completados


# --------------------------------------------------------------------------- #
# Redacción opcional del mensaje final
# --------------------------------------------------------------------------- #

_PROMPT_REDACCION = """\
Reescribí el siguiente mensaje para que suene más natural y cálido, en español \
rioplatense.

Reglas absolutas:
- NO cambies el nivel de urgencia ni el motivo. Ya están decididos por otro \
componente y no se discuten.
- NO agregues información clínica nueva, ni diagnósticos, ni medicamentos.
- NO saques ninguna de las indicaciones de seguridad ni los signos de alarma.
- Mantené el mismo orden de las secciones.
- Devolvé solo el mensaje reescrito, sin comentarios tuyos.

Mensaje a reescribir:
"""


async def redactar_resultado(mensaje_plantilla: str) -> str:
    """Segunda pasada opcional por Gemma para pulir el mensaje final.

    Recibe el mensaje YA armado por la plantilla, con el color y el motivo ya
    decididos. Ante cualquier problema devuelve la plantilla intacta: nunca se
    bloquea la respuesta al usuario por un tema de estilo.
    """
    if not config.REDACCION_LLM:
        return mensaje_plantilla

    try:
        cliente = obtener_cliente()
        resp = await cliente.post(
            f"{config.OLLAMA_URL}/api/chat",
            json={
                "model": config.MODELO,
                "messages": [
                    {"role": "system", "content": _PROMPT_REDACCION},
                    {"role": "user", "content": mensaje_plantilla},
                ],
                "stream": False,
                "options": {"temperature": 0.3},
            },
            timeout=config.TIMEOUT_GEMMA_S,
        )
        resp.raise_for_status()
        texto = (resp.json().get("message") or {}).get("content", "")
    except Exception:
        logger.warning("redaccion_fallida usando_plantilla=true")
        return mensaje_plantilla

    texto = texto.strip() if isinstance(texto, str) else ""
    # Si el modelo devolvió algo sospechosamente corto, no lo usamos.
    if len(texto) < len(mensaje_plantilla) * 0.5:
        logger.warning("redaccion_descartada motivo=demasiado_corta")
        return mensaje_plantilla
    return texto
