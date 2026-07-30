"""Máquina de estados del turno.

Este es el núcleo del módulo: recibe lo que escribió la persona y decide si
sigue preguntando, si corta por riesgo vital, o si delega en el motor de
reglas y cierra.

El orden de los pasos NO es negociable. En particular, el chequeo de bandera
roja va después del merge y antes de cualquier decisión de seguir preguntando:
si alguien dice que no respira, no se le hace una pregunta más.
"""

from __future__ import annotations

import logging

from app import config, gemma, prompt, recursos, reglas, session
from app.schema import (
    Clasificacion,
    DebugInfo,
    FichaClinica,
    Recurso,
    RespuestaAPI,
    Resultado,
    SesionState,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Predicados de decisión
# --------------------------------------------------------------------------- #


def es_bandera_roja(ficha: FichaClinica) -> bool:
    """True si hay riesgo vital inmediato y hay que cortar la conversación.

    Ojo con los `is` explícitos: solo dispara con un negativo/positivo
    **afirmado**. Un `None` (no se sabe) no es bandera roja acá — de eso se
    encarga el motor de reglas, que sí evalúa la incertidumbre.
    """
    dg = ficha.discriminadores_generales
    return (
        dg.nivel_conciencia == "no_responde"
        or dg.respira_normalmente is False
        or dg.riesgo_via_aerea is True
        or dg.hemorragia_mayor is True
    )


def ficha_suficiente(ficha: FichaClinica) -> tuple[bool, list[str]]:
    """¿Alcanza lo recolectado para clasificar? Devuelve (listo, faltantes).

    Criterio: todos los discriminadores generales críticos con valor, más
    motivo de consulta, edad, y confianza de extracción por encima del umbral.
    """
    faltantes = prompt.detectar_faltantes(ficha)

    criticos = set(config.CAMPOS_CRITICOS_GENERALES) | {"motivo_consulta", "edad"}
    pendientes_criticos = [c for c in faltantes if c in criticos]

    confianza = ficha.confianza_extraccion or 0.0
    listo = not pendientes_criticos and confianza >= config.CONFIANZA_MINIMA

    return listo, faltantes


# --------------------------------------------------------------------------- #
# Turno
# --------------------------------------------------------------------------- #


async def procesar_turno(
    state: SesionState,
    texto: str,
    imagen_b64: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
) -> RespuestaAPI:
    """Procesa un mensaje del usuario y devuelve la respuesta para el cliente."""
    state.turnos += 1
    turno = state.turnos

    # (1) Tope de turnos: se corta y se clasifica con lo que haya.
    if turno > config.MAX_TURNOS:
        logger.info(
            "turno_limite session_id=%s turno=%d motivo=max_turnos",
            state.session_id,
            turno,
        )
        return await _finalizar(state, lat, lng, motivo_cierre="max_turnos")

    # (2) Llamada al modelo.
    mensajes = prompt.armar_mensajes(state, texto, imagen_b64)
    try:
        respuesta = await gemma.llamar_gemma(mensajes)
    except gemma.GemmaError as exc:
        # La sesión NO se cierra: la persona tiene que poder reintentar.
        logger.warning(
            "gemma_error session_id=%s turno=%d causa=%s",
            state.session_id,
            turno,
            exc.causa,
        )
        return RespuestaAPI(
            tipo="error_seguro",
            mensaje=config.MENSAJE_ERROR_SEGURO,
            debug=_debug(state),
        )

    # (3) Merge. El relato se acumula pero nunca se loguea.
    ficha_previa = state.ficha
    state.ficha = gemma.merge_ficha(ficha_previa, respuesta.campos)
    state.ficha.relato_libre = (state.ficha.relato_libre + "\n" + texto).strip()
    state.ficha.confianza_extraccion = respuesta.confianza_extraccion
    session.registrar_turno(state, "usuario", texto)

    logger.info(
        "turno_procesado session_id=%s turno=%d campos_nuevos=%s "
        "confianza=%.2f latencia_ms=%s imagen=%s",
        state.session_id,
        turno,
        ",".join(gemma.campos_completados(ficha_previa, state.ficha)) or "-",
        respuesta.confianza_extraccion,
        respuesta.latencia_ms,
        bool(imagen_b64),
    )

    # (4) Bandera roja: se corta acá mismo, sin una pregunta más.
    if es_bandera_roja(state.ficha):
        logger.info(
            "bandera_roja session_id=%s turno=%d", state.session_id, turno
        )
        mensaje = config.MENSAJE_DERIVACION_INMEDIATA
        salida = RespuestaAPI(
            tipo="derivacion_inmediata",
            mensaje=mensaje,
            debug=_debug(state),
        )
        session.registrar_turno(state, "asistente", mensaje)
        session.cerrar(state.session_id)
        return salida

    # (5) ¿Alcanza lo que tenemos?
    listo, faltantes = ficha_suficiente(state.ficha)
    state.ficha.campos_faltantes = faltantes

    # (6) Falta información y todavía quedan preguntas disponibles.
    if not listo and state.preguntas_aclaracion < config.MAX_PREGUNTAS:
        state.preguntas_aclaracion += 1
        pregunta = respuesta.pregunta_aclaracion or _pregunta_fallback(faltantes)
        session.registrar_turno(state, "asistente", pregunta)
        return RespuestaAPI(
            tipo="pregunta",
            mensaje=pregunta,
            debug=_debug(state),
        )

    # (7) Ficha lista, o se agotaron las preguntas: clasificar y cerrar.
    motivo = "ficha_completa" if listo else "max_preguntas"
    return await _finalizar(state, lat, lng, motivo_cierre=motivo)


def _pregunta_fallback(faltantes: list[str]) -> str:
    """Pregunta de reserva cuando el modelo no devolvió ninguna.

    `faltantes` ya viene ordenado por prioridad clínica, así que el primero
    es el que más conviene preguntar.
    """
    if faltantes:
        return config.PREGUNTAS_FALLBACK.get(
            faltantes[0], config.PREGUNTA_FALLBACK_GENERICA
        )
    return config.PREGUNTA_FALLBACK_GENERICA


# --------------------------------------------------------------------------- #
# Cierre y armado del resultado
# --------------------------------------------------------------------------- #


async def _finalizar(
    state: SesionState,
    lat: float | None,
    lng: float | None,
    motivo_cierre: str,
) -> RespuestaAPI:
    """Delega en el motor de reglas, arma el mensaje final y borra la sesión."""
    clasificacion = reglas.clasificar(state.ficha)
    centros = recursos.buscar_recurso(
        color=clasificacion.color,
        especialidad=state.ficha.motivo_consulta,
        lat=lat,
        lng=lng,
    )

    logger.info(
        "clasificacion session_id=%s turno=%d color=%s disparador=%s cierre=%s",
        state.session_id,
        state.turnos,
        clasificacion.color,
        clasificacion.discriminador_disparador,
        motivo_cierre,
    )

    mensaje = armar_mensaje_resultado(clasificacion, centros)
    # Segunda pasada opcional por Gemma, con color y motivo ya decididos.
    # Si falla o está apagada, vuelve la plantilla intacta.
    mensaje = await gemma.redactar_resultado(mensaje)

    salida = RespuestaAPI(
        tipo="resultado",
        mensaje=mensaje,
        debug=_debug(state),
        resultado=Resultado(
            color=clasificacion.color,
            motivo_clasificacion=clasificacion.motivo_clasificacion,
            discriminador_disparador=clasificacion.discriminador_disparador,
            recursos=centros,
        ),
    )

    # Los datos de salud no se retienen más allá de la sesión.
    session.cerrar(state.session_id)
    return salida


def armar_mensaje_resultado(
    clasificacion: Clasificacion,
    centros: list[Recurso],
) -> str:
    """Plantilla determinística del mensaje final.

    El orden de las secciones es parte del diseño clínico: primero qué hacer,
    después por qué, después dónde, después cuándo volver, y al final el
    recordatorio de que esto no es un diagnóstico.
    """
    partes: list[str] = []

    # 1. El nivel, en lenguaje natural. El color nunca va solo.
    partes.append(
        config.DESCRIPCION_COLOR.get(
            clasificacion.color, "Conviene que te vea un profesional de la salud."
        )
    )

    # 2. Las razones: qué de lo que contó llevó a esta clasificación.
    partes.append("**Por qué te digo esto:** " + _texto_razones(clasificacion))

    # 3. Centros sugeridos, con distancia.
    if centros:
        lineas = ["**Dónde podés ir:**"]
        for c in centros:
            distancia = f"a {c.distancia_km:.1f} km".replace(".", ",")
            lineas.append(
                f"• {c.nombre} ({c.tipo.replace('_', ' ')}) — {distancia}, "
                f"ocupación {c.ocupacion_estimada}."
            )
        partes.append("\n".join(lineas))

    # 4. Signos de alarma para reconsultar.
    partes.append(config.SIGNOS_ALARMA)

    # 5. Disclaimer.
    partes.append(config.DISCLAIMER_FINAL)

    return "\n\n".join(partes)


def _texto_razones(clasificacion: Clasificacion) -> str:
    """Explicación del porqué, a partir de lo que devolvió el motor de reglas."""
    disparador = (clasificacion.discriminador_disparador or "").strip()
    motivo = (clasificacion.motivo_clasificacion or "").strip()

    if disparador and disparador != "ninguno":
        texto = disparador.replace("_", " ")
        if motivo:
            texto += f" ({motivo})"
        return texto
    if motivo:
        return motivo
    return "por el conjunto de lo que me contaste."


# --------------------------------------------------------------------------- #
# Debug
# --------------------------------------------------------------------------- #


def _debug(state: SesionState) -> DebugInfo | None:
    """Bloque de introspección para la demo. Apagado por defecto.

    Incluye la ficha completa, así que solo se emite con `DEBUG_MODE=true`.
    """
    if not config.DEBUG_MODE:
        return None
    return DebugInfo(
        turno=state.turnos,
        campos_faltantes=prompt.detectar_faltantes(state.ficha),
        confianza=state.ficha.confianza_extraccion,
        ficha=state.ficha.model_dump(mode="json"),
    )
