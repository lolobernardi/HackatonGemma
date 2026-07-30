"""Constantes, variables de entorno y textos fijos del sistema.

Todo lo que sea un mensaje que ve el usuario final vive acá, no incrustado en
la lógica. En un prototipo clínico los textos se revisan y se ajustan mucho;
conviene tenerlos juntos y en un solo lugar.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Carga de .env
# --------------------------------------------------------------------------- #
# Loader minimalista a propósito: no queremos sumar `python-dotenv` al set de
# dependencias. Las variables ya presentes en el entorno tienen prioridad.


def _cargar_dotenv(ruta: Path) -> None:
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


_cargar_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env_bool(clave: str, default: bool) -> bool:
    return os.getenv(clave, str(default)).strip().lower() in {"1", "true", "yes", "si"}


def _env_int(clave: str, default: int) -> int:
    try:
        return int(os.getenv(clave, default))
    except (TypeError, ValueError):
        return default


def _env_float(clave: str, default: float) -> float:
    try:
        return float(os.getenv(clave, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Ollama / modelo
# --------------------------------------------------------------------------- #

OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODELO: str = os.getenv("MODELO", "gemma4:e2b-it-qat")

# Extracción, no creatividad: temperatura casi nula.
TEMPERATURA: float = 0.1
TIMEOUT_GEMMA_S: float = _env_float("TIMEOUT_GEMMA_S", 90.0)
OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "10m")
NUM_CTX: int = _env_int("NUM_CTX", 4096)

# Los tags de Gemma con capacidad de "thinking" (por ejemplo `gemma4:12b`)
# razonan en voz alta por defecto y agotan la salida antes de emitir la tool
# call: el turno termina siempre en `sin_tool_call`. Apagarlo es lo que hace
# que la extracción estructurada funcione, y de paso es mucho más rápido.
# Los tags sin thinking rechazan el parámetro; ver el fallback en `gemma.py`.
GEMMA_THINK: bool = _env_bool("GEMMA_THINK", False)

# --------------------------------------------------------------------------- #
# Comportamiento del orquestador
# --------------------------------------------------------------------------- #

DEBUG_MODE: bool = _env_bool("DEBUG_MODE", False)
MAX_PREGUNTAS: int = _env_int("MAX_PREGUNTAS", 3)
MAX_TURNOS: int = _env_int("MAX_TURNOS", 8)

# Turnos seguidos sin que la ficha gane ni un campo antes de cerrar igual.
#
# Hay datos que la persona simplemente no tiene: no se tomó la temperatura, no
# quiere decir la edad. Sin este corte, el sistema le insiste hasta agotar
# MAX_PREGUNTAS reformulando la misma pregunta, que es la conversación que hace
# sentir que no se la escuchó. Se cierra y se clasifica con lo que haya, que es
# el camino conservador que el motor ya sabe manejar.
MAX_TURNOS_SIN_AVANCE: int = _env_int("MAX_TURNOS_SIN_AVANCE", 2)

# Cuántos turnos del historial se le reenvían a Gemma en cada llamada.
HISTORIAL_TURNOS: int = _env_int("HISTORIAL_TURNOS", 6)

# Confianza mínima autorreportada para dar la ficha por buena.
CONFIANZA_MINIMA: float = 0.6

# Inactividad tras la cual se borra una sesión, y cada cuánto corre el barrido.
SESION_TTL_MINUTOS: int = _env_int("SESION_TTL_MINUTOS", 30)
BARRIDO_INTERVALO_S: int = 60

# Segunda llamada opcional a Gemma para redactar el mensaje final más lindo.
# Apagada por defecto: la plantilla determinística siempre alcanza.
REDACCION_LLM: bool = _env_bool("REDACCION_LLM", False)

# --------------------------------------------------------------------------- #
# Base de centros de salud
# --------------------------------------------------------------------------- #
# La levanta el proyecto hermano `centros_salud_db` con `.\start_db.ps1`.

CENTROS_DB_URL: str = os.getenv(
    "CENTROS_DB_URL",
    "postgresql+psycopg2://postgres@127.0.0.1:5438/centros_salud",
)
CENTROS_DB_TIMEOUT_S: int = _env_int("CENTROS_DB_TIMEOUT_S", 5)

# Cuántos centros se le muestran a la persona. Más de tres no ayuda a decidir.
MAX_CENTROS_SUGERIDOS: int = _env_int("MAX_CENTROS_SUGERIDOS", 3)

# Ciudad por defecto si la persona no elige ninguna. El frontend le pregunta
# la ubicación, así que en la práctica este valor casi nunca se usa.
CIUDAD_PACIENTE: str = os.getenv("CIUDAD_PACIENTE", "Oro Verde")

# Localidades que el frontend ofrece, con sus coordenadas. Sirven para dos
# cosas: mostrar el desplegable y, cuando la persona comparte su ubicación real
# o elige una localidad sin cobertura, calcular cuál es la ciudad con centros
# cargados más cercana.
#
# Las coordenadas son las del centro de cada localidad, redondeadas. Alcanzan
# de sobra para decidir entre Paraná y Santa Fe, que están a 25 km una de otra.
LOCALIDADES: dict[str, tuple[float, float]] = {
    "Oro Verde": (-31.8333, -60.5167),
    "Paraná": (-31.7333, -60.5238),
    "San Benito": (-31.7667, -60.4500),
    "Colonia Avellaneda": (-31.7500, -60.4667),
    "Santa Fe": (-31.6333, -60.7000),
    "Santo Tomé": (-31.6667, -60.7667),
}

# Ciudades que la base realmente tiene cargadas. Cualquier otra localidad se
# resuelve buscando la más cercana de estas por distancia real.
CIUDADES_CON_COBERTURA: tuple[str, ...] = ("Paraná", "Santa Fe")

# Hasta qué distancia se considera razonable derivar a otra ciudad. Más lejos
# que esto, se ofrece igual pero el mensaje lo aclara con más énfasis.
DISTANCIA_MAXIMA_DERIVACION_KM: float = 60.0

# --------------------------------------------------------------------------- #
# Dominio clínico
# --------------------------------------------------------------------------- #

# Slugs de flowchart soportados en el prototipo. Se le imponen a Gemma como
# enum dentro del schema de la tool `actualizar_ficha`.
MOTIVOS_CONSULTA: tuple[str, ...] = (
    "dolor_toracico",
    "dificultad_respiratoria",
    "dolor_abdominal",
    "fiebre",
    "cefalea",
    "herida_sangrado",
    "lesion_cutanea",
    "otro",
)

# --------------------------------------------------------------------------- #
# Traducción del vocabulario del motor de reglas al de la base
# --------------------------------------------------------------------------- #
# A qué especialista derivar lo decide `reglas.py`, no este archivo: es
# conocimiento clínico y vive en un solo lugar (ver `app/REGLAS.md`). Acá sólo
# se traduce el slug que emite el ruleset al nombre exacto que usa la tabla
# `especialidades` de la base.
#
# Un `None` significa "el ruleset la pide pero la base no la tiene": son
# especialidades que ningún efector de Paraná ni Santa Fe declara. En ese caso
# se busca por la de respaldo en vez de devolver cero centros.

ESPECIALIDAD_DB: dict[str, str | None] = {
    "cardiologia": "Cardiología",
    "cirugia_general": "Cirugía",
    "gastroenterologia": "Gastroenterología",
    "ginecologia": "Ginecología",
    "pediatria": "Pediatría",
    # Sin cobertura en la base (0 centros la declaran).
    "dermatologia": None,
    "infectologia": None,
    "neumonologia": None,
    "neurologia": None,
}

# Especialidad de respaldo cuando la específica no existe o no da resultados.
ESPECIALIDAD_FALLBACK: str = "Medicina General"

# Si el motor no sugiere especialista y la consulta es por un menor, igual
# conviene un pediatra.
ESPECIALIDAD_PEDIATRICA: str = "Pediatría"
EDAD_PEDIATRICA_MAX: int = 14

# Con urgencia alta y sin especialista sugerido, lo que hace falta es un
# efector con guardia, no medicina general.
ESPECIALIDAD_URGENCIA: str = "Emergencias"
TIPOS_RECURSO_DE_URGENCIA: tuple[str, ...] = ("guardia_alta_complejidad", "guardia")

# --------------------------------------------------------------------------- #
# Tipo de efector sugerido -> tipos que existen en la base
# --------------------------------------------------------------------------- #
# `reglas.py` devuelve `tipo_recurso_sugerido` con su propio vocabulario
# (guardia_alta_complejidad, guardia, centro_urgencias, caps,
# consulta_programada). La base tiene otros: hospital, CAPS, centro de salud,
# CIC, SAMCO, centro de especialidades.
#
# Se listan en orden de preferencia y `recursos.py` los prueba uno por uno. La
# regla de fondo: no mandar un azul a una guardia de alta complejidad, porque
# satura el sistema, y no mandar un naranja a un CAPS, porque no lo resuelve.

TIPOS_POR_RECURSO: dict[str, tuple[str, ...]] = {
    "guardia_alta_complejidad": ("hospital",),
    "guardia": ("hospital",),
    "centro_urgencias": ("hospital", "CAPS", "centro de salud"),
    "caps": ("CAPS", "centro de salud", "CIC", "SAMCO"),
    "consulta_programada": (
        "centro de salud",
        "CAPS",
        "centro de especialidades",
        "CIC",
    ),
}

# Discriminadores generales que definen riesgo vital. Son los únicos que mira
# `orquestador.es_bandera_roja` y los que exige `ficha_suficiente`.
CAMPOS_CRITICOS_GENERALES: tuple[str, ...] = (
    "respira_normalmente",
    "nivel_conciencia",
    "hemorragia_mayor",
    "riesgo_via_aerea",
)

# Orden de prioridad para preguntar. Los generales van SIEMPRE antes que
# `motivo_consulta` y `edad`: primero se descarta riesgo vital.
PRIORIDAD_CAMPOS: tuple[str, ...] = (
    "respira_normalmente",
    "nivel_conciencia",
    "hemorragia_mayor",
    "riesgo_via_aerea",
    "motivo_consulta",
    "edad",
    "inicio",
    "dolor_eva",
    "tiempo_evolucion_horas",
    "temperatura_c",
)

# Preguntas de fallback, por si Gemma no devuelve `pregunta_aclaracion`.
# Lenguaje coloquial, sin jerga: las lee la persona tal cual están.
PREGUNTAS_FALLBACK: dict[str, str] = {
    "respira_normalmente": "¿Estás respirando con normalidad o te cuesta tomar aire?",
    "nivel_conciencia": (
        "¿Estás despierto y podés seguir la conversación sin problema, o te "
        "sentís muy adormecido o confundido?"
    ),
    "hemorragia_mayor": "¿Hay algún sangrado que no pare o que sea abundante?",
    "riesgo_via_aerea": (
        "¿Sentís que algo te obstruye la garganta o que te cuesta tragar?"
    ),
    "motivo_consulta": "Contame un poco más: ¿qué es lo que más te molesta ahora?",
    "edad": "¿Qué edad tenés?",
    "inicio": "¿Empezó de golpe o fue apareciendo de a poco?",
    "dolor_eva": (
        "Si tuvieras que ponerle un número al dolor del 0 al 10, donde 10 es "
        "el peor dolor que te imaginás, ¿cuánto sería?"
    ),
    "tiempo_evolucion_horas": "¿Hace cuánto tiempo que te viene pasando esto?",
    "temperatura_c": "¿Te tomaste la temperatura? ¿Cuánto te dio?",
}

# Pregunta genérica de último recurso, si el campo faltante no está en el dict.
PREGUNTA_FALLBACK_GENERICA = (
    "Contame un poco más sobre lo que estás sintiendo, así te puedo orientar mejor."
)

# --------------------------------------------------------------------------- #
# Textos fijos que ve el usuario
# --------------------------------------------------------------------------- #

MENSAJE_BIENVENIDA = (
    "Hola. Te voy a hacer algunas preguntas cortas sobre lo que te está pasando "
    "para orientarte sobre a dónde conviene que vayas.\n\n"
    "Antes de empezar, tres cosas importantes:\n\n"
    "• Esto **no reemplaza la consulta con un profesional de la salud**. No hago "
    "diagnósticos ni te voy a indicar medicamentos.\n"
    "• Lo que me cuentes **se procesa localmente, en esta computadora**, y se "
    "borra cuando termina la conversación. No se guarda ni se envía a ningún "
    "lado. No me des tu nombre, DNI ni dirección: no los necesito.\n"
    "• Si esto es una **emergencia** —dolor fuerte en el pecho, dificultad para "
    "respirar, sangrado que no para, pérdida de conocimiento— **no sigas acá**: "
    "llamá al **107** o al **911** ahora mismo.\n\n"
    "Si estás de acuerdo y querés seguir, contame con tus palabras qué te pasa."
)

# Gemma falló: el usuario NUNCA puede quedarse sin instrucción de seguridad.
MENSAJE_ERROR_SEGURO = (
    "Perdón, tuve un problema técnico y no pude procesar lo que me contaste. "
    "Si tenés dolor en el pecho, dificultad para respirar, sangrado importante, "
    "o si la persona está desvanecida o confundida, llamá al 107 ahora mismo o "
    "andá a la guardia más cercana. Si no es ese el caso, podés escribirme de nuevo."
)

# Bandera roja: el número de emergencia primero, sin rodeos y sin más preguntas.
MENSAJE_DERIVACION_INMEDIATA = (
    "**Llamá al 107 ahora mismo.** Si no podés llamar, andá a la guardia más "
    "cercana o pedile a alguien que te lleve.\n\n"
    "Por lo que me contaste, esto necesita atención médica urgente y no puede "
    "esperar. No sigas escribiendo acá.\n\n"
    "Mientras llega la ayuda: quedate acompañado, no manejes y no tomes ningún "
    "medicamento por tu cuenta."
)

# Signos de alarma genéricos, para el cierre de todo resultado.
SIGNOS_ALARMA = (
    "Volvé a consultar o llamá al 107 si aparece cualquiera de estas cosas, "
    "aunque hoy te haya salido un nivel bajo:\n"
    "• Te cuesta respirar o te falta el aire.\n"
    "• El dolor se hace mucho más fuerte o cambia de lugar.\n"
    "• Te sentís muy adormecido, confundido o te desmayás.\n"
    "• Aparece un sangrado que no para.\n"
    "• Tenés fiebre alta que no baja, o la persona es un bebé menor de 3 meses."
)

# La persona no vino a consultar ningún síntoma y los cuatro discriminadores de
# riesgo vital dieron benignos. No se le asigna color: no hay nada que
# priorizar. Igual se cierra con red de seguridad, porque puede volver.
MENSAJE_SIN_MOTIVO = (
    "Por lo que me contaste, no tenés ningún síntoma ahora y no encontré "
    "señales de alarma. **No hace falta que consultes por esto.**\n\n"
    "Si en algún momento aparece alguna de estas cosas, volvé a escribirme o "
    "llamá al 107:\n"
    "• Te cuesta respirar o te falta el aire.\n"
    "• Dolor fuerte en el pecho o en la panza.\n"
    "• Te sentís muy adormecido, confundido o te desmayás.\n"
    "• Aparece un sangrado que no para.\n"
    "• Fiebre alta que no baja.\n\n"
    "Y si querés hacerte un control de rutina, pedí un turno en el centro de "
    "salud que te quede más cerca: para eso no hace falta una guardia."
)

DISCLAIMER_FINAL = (
    "Esto es una orientación automática, no un diagnóstico. La evaluación final "
    "siempre la hace un profesional de la salud."
)

# Cómo se le nombra cada color a la persona. Nunca se muestra el color solo.
DESCRIPCION_COLOR: dict[str, str] = {
    "rojo": "Necesitás atención médica inmediata.",
    "naranja": "Necesitás que te vea un médico muy pronto, en menos de 10 minutos.",
    "amarillo": "Conviene que te vea un médico hoy, sin dejarlo pasar.",
    "verde": "Podés esperar a una consulta programada, no parece urgente.",
    "azul": "Se puede resolver en una consulta común, sin apuro.",
}
