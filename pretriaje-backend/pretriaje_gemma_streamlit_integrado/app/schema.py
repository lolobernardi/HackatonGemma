"""Modelos Pydantic del módulo de recolección conversacional.

Este archivo es el contrato entre todas las piezas del sistema:
- lo que Gemma tiene que devolver (`CamposExtraidos`),
- lo que se va acumulando en la sesión (`FichaClinica`),
- lo que el motor de reglas y el buscador de recursos devuelven
  (`Clasificacion`, `Recurso`) — implementados por otras personas del equipo,
- lo que ve el cliente HTTP (`MensajeRequest`, `RespuestaAPI`, ...).

Regla de diseño transversal: **todo campo clínico es opcional y arranca en
`None`**. La ficha nace vacía y se llena de a poco, turno a turno. Un `None`
significa "no lo sé", nunca "no". Esa distinción es la que le permite al motor
de reglas saber si le falta información o si tiene un dato negativo real.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Alias de tipos
# --------------------------------------------------------------------------- #

NivelConciencia = Literal["alerta", "somnoliento", "confuso", "no_responde"]
Inicio = Literal["subito", "gradual"]
ColorTriaje = Literal["rojo", "naranja", "amarillo", "verde", "azul"]
# `sin_motivo` cierra la conversación SIN color de triaje: la persona no vino a
# consultar nada y ningún discriminador de riesgo vital dio positivo. No es un
# nivel de urgencia bajo, es la ausencia de una consulta que priorizar.
TipoRespuesta = Literal[
    "pregunta",
    "resultado",
    "derivacion_inmediata",
    "sin_motivo",
    "error_seguro",
]

# Tipo de efector al que conviene derivar. Lo decide el motor de reglas, no la
# plantilla de mensajes: es conocimiento clínico y vive en un solo lugar.
TipoRecurso = Literal[
    "guardia_alta_complejidad",
    "guardia",
    "centro_urgencias",
    "caps",
    "consulta_programada",
]

# Valor que puede tomar un discriminador específico de un flowchart.
ValorDiscriminador = bool | int | float | str | None


# --------------------------------------------------------------------------- #
# Ficha clínica
# --------------------------------------------------------------------------- #


class DiscriminadoresGenerales(BaseModel):
    """Discriminadores que definen riesgo vital, aplicables a cualquier motivo.

    En Manchester estos se evalúan siempre, antes que los específicos del
    flowchart. Por eso el orquestador los prioriza al preguntar y el chequeo
    de bandera roja mira únicamente los cuatro primeros.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Riesgo vital inmediato (los que disparan derivación sin más preguntas) ---
    riesgo_via_aerea: bool | None = None
    respira_normalmente: bool | None = None
    nivel_conciencia: NivelConciencia | None = None
    hemorragia_mayor: bool | None = None

    # --- Modificadores generales ---
    dolor_eva: int | None = Field(default=None, ge=0, le=10)
    temperatura_c: float | None = Field(default=None, ge=25.0, le=45.0)
    inicio: Inicio | None = None
    tiempo_evolucion_horas: float | None = Field(default=None, ge=0)


class FichaClinica(BaseModel):
    """Estado clínico acumulado de una sesión.

    Se construye por merge sucesivo: cada turno solo puede agregar o corregir
    campos, nunca borrarlos (ver `gemma.merge_ficha`).
    """

    model_config = ConfigDict(extra="forbid")

    edad: int | None = Field(default=None, ge=0, le=120)
    # "mi hijo tiene fiebre" -> es_para_tercero=True. Cambia los umbrales
    # pediátricos que aplica el motor de reglas.
    es_para_tercero: bool = False
    # Slug del flowchart de Manchester. Los valores válidos viven en
    # `config.MOTIVOS_CONSULTA` y se le imponen a Gemma vía enum en el schema
    # de la tool; acá queda como str para no romper si el modelo inventa uno.
    motivo_consulta: str | None = None

    # True si la persona dice explícitamente que NO tiene ningún síntoma.
    #
    # Es distinto de `motivo_consulta is None`, que significa "todavía no
    # sabemos qué le pasa". Sin esta distinción el sistema no puede cerrar una
    # conversación con alguien que no viene a consultar nada: se le seguían
    # haciendo preguntas hasta agotar el tope y se cerraba con el fallback por
    # información incompleta, que es amarillo. Decirle "andá al médico dentro
    # de la hora" a alguien sano no es precaución, es un falso positivo.
    sin_motivo_consulta: bool | None = None

    discriminadores_generales: DiscriminadoresGenerales = Field(
        default_factory=DiscriminadoresGenerales
    )
    # Discriminadores propios del flowchart elegido (ej. "irradia_brazo": True).
    discriminadores_especificos: dict[str, ValorDiscriminador] = Field(
        default_factory=dict
    )

    # Acumulado textual de lo que dijo la persona. Nunca se loguea.
    relato_libre: str = ""
    # Autorreportada por el modelo. Es una señal, no una garantía.
    confianza_extraccion: float | None = Field(default=None, ge=0.0, le=1.0)
    campos_faltantes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Contrato con Gemma
# --------------------------------------------------------------------------- #


class CamposExtraidos(BaseModel):
    """Payload de la tool call `actualizar_ficha`.

    Espeja `FichaClinica` pero con **todo** opcional (incluido
    `es_para_tercero`, que en la ficha tiene default `False`): el modelo solo
    reporta lo que efectivamente pudo extraer del mensaje. Lo que no dice,
    no se toca.
    """

    model_config = ConfigDict(extra="ignore")

    edad: int | None = Field(default=None, ge=0, le=120)
    es_para_tercero: bool | None = None
    motivo_consulta: str | None = None
    sin_motivo_consulta: bool | None = None
    discriminadores_generales: DiscriminadoresGenerales | None = None
    discriminadores_especificos: dict[str, ValorDiscriminador] | None = None
    campos_faltantes: list[str] | None = None


class RespuestaGemma(BaseModel):
    """Lo que el cliente de Ollama le devuelve al orquestador."""

    model_config = ConfigDict(extra="forbid")

    campos: CamposExtraidos = Field(default_factory=CamposExtraidos)
    # UNA sola pregunta, en lenguaje coloquial. None si el modelo no pidió nada.
    pregunta_aclaracion: str | None = None
    confianza_extraccion: float = Field(default=0.0, ge=0.0, le=1.0)
    # Latencia de la llamada, para logging. No es parte del contrato clínico.
    latencia_ms: int | None = None


# --------------------------------------------------------------------------- #
# Salidas de los módulos de otras personas del equipo
# --------------------------------------------------------------------------- #


class Clasificacion(BaseModel):
    """Veredicto del motor de reglas (ver `reglas.py` y `REGLAS.md`).

    Es la única pieza del sistema autorizada a decidir urgencia. El modelo de
    lenguaje no opina sobre esto, y la plantilla de mensajes tampoco: todo lo
    que sigue de la clasificación (cuánto puede esperar, a qué tipo de efector
    conviene ir, qué signos de alarma mirar) sale de acá, porque es
    conocimiento clínico y duplicarlo en dos módulos es cómo se desincronizan.
    """

    model_config = ConfigDict(extra="forbid")

    color: ColorTriaje
    # Ventana de atención recomendada. 0 / 10 / 60 / 120 / 240 según el color.
    tiempo_maximo_min: int
    motivo_clasificacion: str
    # Qué discriminador concreto disparó el color. Es lo que se le explica a
    # la persona en el mensaje final: el "por qué" de la clasificación. Tiene
    # que ser legible por una persona, no una expresión del ruleset.
    discriminador_disparador: str

    # --- Trazabilidad ------------------------------------------------------ #
    # Identificador de la regla que fijó ESTE color (ej. "DT-02").
    regla_id: str
    # Todas las reglas evaluadas, en orden, matcheen o no. Permite mostrar en
    # vivo por qué salió el color que salió. Nunca viene vacía.
    traza: list[str] = Field(default_factory=list)

    # --- Derivación -------------------------------------------------------- #
    tipo_recurso_sugerido: TipoRecurso
    especialidad_sugerida: str | None = None
    signos_alarma_reconsulta: list[str] = Field(default_factory=list)

    # --- Metadatos --------------------------------------------------------- #
    # Versión del ruleset con el que se clasificó. Cuando un profesional revise
    # y ajuste umbrales, cambia, y las clasificaciones viejas quedan trazables
    # a la versión con la que se hicieron.
    version_ruleset: str
    # True si la respuesta salió de un fallback y no de una regla clínica
    # positiva. El orquestador lo usa para ser más explícito con la persona
    # sobre la incertidumbre.
    clasificacion_por_defecto: bool = False


class Recurso(BaseModel):
    """Centro de salud sugerido (ver `recursos.py`).

    Espeja una fila de la base `centros_salud`. Casi todo es opcional a
    propósito: la base tiene huecos reales y es preferible mostrar un centro
    con datos incompletos que no mostrarlo. Concretamente hoy:

    - `distancia_km`: None siempre, no hay direcciones geocodificadas (0/86).
    - `horario`: cargado en 6 de 86 centros; `horario_informado` distingue
      "cerrado a esta hora" de "no sabemos el horario".
    - `ocupacion_estimada`: no existe en la base. Queda para cuando haya una
      fuente real; inventarla sería desinformar sobre dónde ir.
    """

    model_config = ConfigDict(extra="forbid")

    nombre: str
    tipo: str
    ciudad: str | None = None
    direccion: str | None = None
    telefono: str | None = None
    horario: str | None = None
    horario_informado: bool = False
    especialidades: list[str] = Field(default_factory=list)
    distancia_km: float | None = None
    ocupacion_estimada: str | None = None


class Resultado(BaseModel):
    """Bloque `resultado` de la respuesta cuando `tipo == "resultado"`."""

    model_config = ConfigDict(extra="forbid")

    color: ColorTriaje
    motivo_clasificacion: str
    discriminador_disparador: str
    recursos: list[Recurso] = Field(default_factory=list)

    # Lo que decidió el motor de reglas. Viaja al frontend para el popup y para
    # que un profesional pueda auditar por qué salió este color.
    tiempo_maximo_min: int | None = None
    tipo_recurso_sugerido: str | None = None
    signos_alarma_reconsulta: list[str] = Field(default_factory=list)
    regla_id: str | None = None
    version_ruleset: str | None = None
    clasificacion_por_defecto: bool = False

    # Motivo por el que la persona consultó, tal como lo estructuró Gemma.
    # Va en el resultado y no sólo en `debug` porque el popup del frontend lo
    # muestra siempre, y `debug` depende de que DEBUG_MODE esté prendido.
    motivo_consulta: str | None = None

    # Cómo se buscaron los centros. Viaja hasta el frontend porque la persona
    # tiene derecho a saber por qué le tocaron esos y no otros: a qué
    # especialista se lo deriva, y si se buscó en otra ciudad que la suya.
    especialidad_sugerida: str | None = None
    ciudad_persona: str | None = None
    ciudad_buscada: str | None = None
    criterio_busqueda: str | None = None


# --------------------------------------------------------------------------- #
# API HTTP
# --------------------------------------------------------------------------- #


class SesionResponse(BaseModel):
    """Respuesta de `POST /sesion`."""

    session_id: str
    mensaje: str


class MensajeRequest(BaseModel):
    """Body de `POST /mensaje`."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    texto: str = Field(min_length=1, max_length=4000)
    # Base64 **sin** el prefijo `data:image/...;base64,` (formato Ollama).
    imagen_b64: str | None = None
    # Ubicación de la persona. `ciudad` es la localidad que eligió en la UI;
    # lat/lng son las coordenadas si compartió su ubicación real. Con
    # cualquiera de las dos alcanza para elegir a qué ciudad buscarle centros.
    ciudad: str | None = Field(default=None, max_length=120)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


class DebugInfo(BaseModel):
    """Bloque de introspección. Solo se emite si `DEBUG_MODE` está prendido."""

    model_config = ConfigDict(extra="forbid")

    turno: int
    campos_faltantes: list[str] = Field(default_factory=list)
    confianza: float | None = None
    ficha: dict[str, Any] = Field(default_factory=dict)


class RespuestaAPI(BaseModel):
    """Respuesta de `POST /mensaje`."""

    model_config = ConfigDict(extra="forbid")

    tipo: TipoRespuesta
    mensaje: str
    debug: DebugInfo | None = None
    resultado: Resultado | None = None


# --------------------------------------------------------------------------- #
# Estado de sesión
# --------------------------------------------------------------------------- #


class TurnoHistorial(BaseModel):
    """Una entrada del historial conversacional."""

    model_config = ConfigDict(extra="forbid")

    rol: Literal["usuario", "asistente"]
    texto: str


class SesionState(BaseModel):
    """Estado completo de una sesión. Vive solo en memoria y se borra al cerrar."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    ficha: FichaClinica = Field(default_factory=FichaClinica)
    # Ubicación declarada por la persona. Se guarda en la sesión porque la
    # manda en cada mensaje pero sólo se usa al final, al buscar centros.
    ciudad: str | None = None
    lat: float | None = None
    lng: float | None = None
    turnos: int = 0
    preguntas_aclaracion: int = 0
    # Preguntas ya hechas, en forma canónica, para no repetirlas. Ver
    # `orquestador._elegir_pregunta`.
    preguntas_hechas: list[str] = Field(default_factory=list)
    # Turnos seguidos en los que la ficha no ganó ningún campo. Ver
    # `orquestador.procesar_turno`.
    turnos_sin_avance: int = 0
    historial: list[TurnoHistorial] = Field(default_factory=list)
    cerrada: bool = False
    creada_en: datetime
    # Marca para el barrido de inactividad.
    ultimo_acceso: datetime
