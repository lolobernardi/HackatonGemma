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
import unicodedata

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


def _generales_benignos(ficha: FichaClinica) -> bool:
    """Los cuatro discriminadores de riesgo vital, respondidos y en verde.

    Se exige respuesta explícita: un `None` es "no sabemos", nunca "no".
    """
    dg = ficha.discriminadores_generales
    return (
        dg.respira_normalmente is True
        and dg.nivel_conciencia == "alerta"
        and dg.hemorragia_mayor is False
        and dg.riesgo_via_aerea is False
    )


def sin_necesidad_de_triaje(ficha: FichaClinica, estancada: bool = False) -> bool:
    """True si la persona no vino a consultar nada y no hay riesgo vital.

    Dos maneras de llegar acá, y las dos exigen lo mismo de fondo: que no haya
    aparecido ningún motivo de consulta y que los **cuatro** discriminadores de
    riesgo vital estén respondidos y benignos.

    1. La persona lo dijo explícitamente y Gemma lo registró en
       `sin_motivo_consulta`.
    2. Nunca lo dijo con esas palabras, pero la conversación dejó de avanzar
       sin que apareciera ni un motivo ni un solo hallazgo.

    El segundo camino existe porque el primero depende de que el modelo ponga
    un campo, y no siempre lo pone. Sin él, la misma persona sana termina
    clasificada por el fallback de información incompleta, que es amarillo:
    "andá al médico dentro de la hora" a alguien que dijo cuatro veces que
    está bien. Eso no es precaución, es un falso positivo.

    Que los cuatro generales estén en verde es la red de seguridad de los dos
    caminos: alguien puede decir que está bien sin registrar que le falta el
    aire. Mientras no sepamos esos cuatro, se sigue preguntando.
    """
    if ficha.motivo_consulta:
        return False
    if not _generales_benignos(ficha):
        return False

    if ficha.sin_motivo_consulta is True:
        return True

    # Sin motivo, sin hallazgos y sin avance: no hay consulta que priorizar.
    return estancada and not ficha.discriminadores_especificos


def ficha_suficiente(ficha: FichaClinica) -> tuple[bool, list[str]]:
    """¿Alcanza lo recolectado para clasificar? Devuelve (listo, faltantes).

    Quién decide qué falta es el motor de reglas, no este módulo:
    `reglas.campos_requeridos` sabe qué discriminadores mira cada flowchart y
    los devuelve ordenados por prioridad clínica.

    Esto importa para el color que sale. El motor no baja de amarillo mientras
    le falte información (ver la etapa 3 en `REGLAS.md`: verde y azul son
    afirmaciones fuertes y exigen evidencia positiva de benignidad). Si sólo se
    preguntaran los discriminadores generales, como se hacía antes, los
    específicos del flowchart nunca se completarían y **todo terminaría en
    amarillo**, que es exactamente lo que pasaba.
    """
    faltantes = reglas.campos_requeridos(ficha)

    confianza = ficha.confianza_extraccion or 0.0
    listo = not faltantes and confianza >= config.CONFIANZA_MINIMA

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
    ciudad: str | None = None,
) -> RespuestaAPI:
    """Procesa un mensaje del usuario y devuelve la respuesta para el cliente."""
    state.turnos += 1
    turno = state.turnos

    # La ubicación se recuerda en la sesión: llega en cada mensaje pero recién
    # se usa al final, al buscar centros. Nunca se pisa un dato con un vacío.
    if ciudad:
        state.ciudad = ciudad
    if lat is not None and lng is not None:
        state.lat, state.lng = lat, lng

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

    nuevos = gemma.campos_completados(ficha_previa, state.ficha)

    # Hay datos que la persona no tiene: no se tomó la temperatura, no quiere
    # decir la edad. Si dos turnos seguidos no aportan ni un campo, insistir no
    # va a cambiarlo; sólo reformula la misma pregunta y da la sensación de que
    # no se la escuchó.
    state.turnos_sin_avance = 0 if nuevos else state.turnos_sin_avance + 1

    logger.info(
        "turno_procesado session_id=%s turno=%d campos_nuevos=%s sin_avance=%d "
        "confianza=%.2f latencia_ms=%s imagen=%s",
        state.session_id,
        turno,
        ",".join(nuevos) or "-",
        state.turnos_sin_avance,
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

    # (5) ¿La conversación dejó de avanzar? Se calcula antes de decidir nada.
    estancada = state.turnos_sin_avance >= config.MAX_TURNOS_SIN_AVANCE

    # (6) No vino a consultar nada y no hay riesgo vital: se cierra sin color.
    #     Va DESPUÉS de la bandera roja, nunca antes: si algo dio positivo, ese
    #     camino manda aunque la persona diga que está bien.
    if sin_necesidad_de_triaje(state.ficha, estancada):
        logger.info(
            "sin_motivo_consulta session_id=%s turno=%d", state.session_id, turno
        )
        mensaje = config.MENSAJE_SIN_MOTIVO
        salida = RespuestaAPI(
            tipo="sin_motivo",
            mensaje=mensaje,
            debug=_debug(state),
        )
        session.registrar_turno(state, "asistente", mensaje)
        session.cerrar(state.session_id)
        return salida

    # (7) ¿Alcanza lo que tenemos?
    listo, faltantes = ficha_suficiente(state.ficha)
    state.ficha.campos_faltantes = faltantes

    # (8) Falta información, quedan preguntas disponibles, la conversación
    #     avanza y todavía hay algo nuevo que preguntar.
    if not listo and not estancada and state.preguntas_aclaracion < config.MAX_PREGUNTAS:
        pregunta = _elegir_pregunta(state, respuesta.pregunta_aclaracion, faltantes)
        if pregunta is not None:
            state.preguntas_aclaracion += 1
            session.registrar_turno(state, "asistente", pregunta)
            return RespuestaAPI(
                tipo="pregunta",
                mensaje=pregunta,
                debug=_debug(state),
            )
        # Sin preguntas nuevas: repetir una ya hecha no aporta, se cierra.
        logger.info(
            "preguntas_agotadas session_id=%s turno=%d hechas=%d",
            state.session_id,
            turno,
            len(state.preguntas_hechas),
        )
        return await _finalizar(state, lat, lng, motivo_cierre="preguntas_agotadas")

    # (9) Clasificar y cerrar con lo que haya.
    if listo:
        motivo = "ficha_completa"
    elif estancada:
        logger.info(
            "conversacion_estancada session_id=%s turno=%d faltaban=%s",
            state.session_id,
            turno,
            ",".join(faltantes[:5]) or "-",
        )
        motivo = "sin_avance"
    else:
        motivo = "max_preguntas"
    return await _finalizar(state, lat, lng, motivo_cierre=motivo)


def _normalizar_pregunta(texto: str) -> str:
    """Forma canónica para comparar preguntas: sin tildes, signos ni espacios."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return "".join(c for c in sin_tildes.lower() if c.isalnum())


def _elegir_pregunta(
    state: SesionState,
    pregunta_modelo: str | None,
    faltantes: list[str],
) -> str | None:
    """La siguiente pregunta, o `None` si no queda ninguna nueva que hacer.

    Gemma se traba: si un campo no se completa nunca —porque la persona no
    tiene ese dato— vuelve a preguntar lo mismo turno tras turno. Se llegó a
    ver la misma pregunta tres veces seguidas, que además de inútil da la
    impresión de que no se escuchó la respuesta.

    Ante una repetición se prueba el fallback de cada campo faltante. Si todas
    las opciones ya se usaron, devuelve `None`: el orquestador cierra en vez de
    repetir, porque volver a preguntar lo mismo no va a traer una respuesta
    distinta.
    """
    candidatas: list[str] = []
    if pregunta_modelo:
        candidatas.append(pregunta_modelo)
    candidatas.extend(_pregunta_fallback([f]) for f in faltantes)
    candidatas.append(config.PREGUNTA_FALLBACK_GENERICA)

    for candidata in candidatas:
        canonica = _normalizar_pregunta(candidata)
        if canonica not in state.preguntas_hechas:
            state.preguntas_hechas.append(canonica)
            return candidata

    return None


def _pregunta_fallback(faltantes: list[str]) -> str:
    """Pregunta de reserva cuando el modelo no devolvió ninguna.

    `faltantes` ya viene ordenado por prioridad clínica, así que el primero es
    el que más conviene preguntar. Los nombres llegan con la ruta completa
    (`discriminadores_generales.dolor_eva`), que es como los devuelve el motor
    de reglas; acá se usa sólo el último tramo.
    """
    if not faltantes:
        return config.PREGUNTA_FALLBACK_GENERICA

    campo = faltantes[0].rsplit(".", 1)[-1]
    return config.PREGUNTAS_FALLBACK.get(campo, config.PREGUNTA_FALLBACK_GENERICA)


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
    busqueda = recursos.buscar_para_clasificacion(
        clasificacion,
        state.ficha,
        ciudad=state.ciudad,
        lat=lat if lat is not None else state.lat,
        lng=lng if lng is not None else state.lng,
    )
    centros = busqueda.recursos

    logger.info(
        "clasificacion session_id=%s turno=%d color=%s regla=%s por_defecto=%s "
        "cierre=%s centros=%d criterio=%s ruleset=%s",
        state.session_id,
        state.turnos,
        clasificacion.color,
        clasificacion.regla_id,
        clasificacion.clasificacion_por_defecto,
        motivo_cierre,
        len(centros),
        busqueda.criterio,
        clasificacion.version_ruleset,
    )

    mensaje = armar_mensaje_resultado(clasificacion, busqueda)
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
            motivo_consulta=state.ficha.motivo_consulta,
            especialidad_sugerida=busqueda.especialidad,
            ciudad_buscada=busqueda.ciudad_buscada,
            ciudad_persona=busqueda.ciudad_persona,
            criterio_busqueda=busqueda.criterio,
            tiempo_maximo_min=clasificacion.tiempo_maximo_min,
            tipo_recurso_sugerido=clasificacion.tipo_recurso_sugerido,
            signos_alarma_reconsulta=clasificacion.signos_alarma_reconsulta,
            regla_id=clasificacion.regla_id,
            version_ruleset=clasificacion.version_ruleset,
            clasificacion_por_defecto=clasificacion.clasificacion_por_defecto,
        ),
    )

    # Los datos de salud no se retienen más allá de la sesión.
    session.cerrar(state.session_id)
    return salida


def armar_mensaje_resultado(
    clasificacion: Clasificacion,
    busqueda: recursos.ResultadoBusqueda,
) -> str:
    """Plantilla determinística del mensaje final.

    El orden de las secciones es parte del diseño clínico: primero qué hacer,
    después por qué, después a quién ver y dónde, después cuándo volver, y al
    final el recordatorio de que esto no es un diagnóstico.
    """
    partes: list[str] = []

    # 1. El nivel, en lenguaje natural. El color nunca va solo.
    partes.append(
        config.DESCRIPCION_COLOR.get(
            clasificacion.color, "Conviene que te vea un profesional de la salud."
        )
    )

    # 2. Las razones, SOLO si el motor dio una concreta. Una explicación
    #    genérica ("por el conjunto de lo que me contaste") no aporta nada:
    #    ocupa lugar arriba de todo y le corre hacia abajo a la persona lo
    #    único que necesita, que es a dónde ir.
    razones = _texto_razones(clasificacion)
    if razones:
        partes.append("**Por qué te digo esto:** " + razones)

    # 3. A qué especialista le corresponde.
    if busqueda.especialidad:
        partes.append(f"**Quién te tiene que ver:** {busqueda.especialidad}.")

    # 4. Centros concretos, con los datos que la base tenga de cada uno.
    if busqueda.recursos:
        partes.append(_texto_centros(busqueda))
    else:
        partes.append(
            "No pude consultar el listado de centros de salud en este momento. "
            "Acercate al centro de salud o la guardia que te quede más cerca."
        )

    # 5. Signos de alarma. Los arma el motor de reglas: los generales del
    #    ruleset más los propios de la regla que ganó, así que son los que
    #    corresponden a ESTE caso y no una lista fija para todos.
    partes.append(_texto_signos_alarma(clasificacion))

    # 6. Disclaimer.
    partes.append(config.DISCLAIMER_FINAL)

    return "\n\n".join(partes)


def _texto_signos_alarma(clasificacion: Clasificacion) -> str:
    """Bloque de 'volvé a consultar si...' con los signos de este caso."""
    signos = [s.strip() for s in clasificacion.signos_alarma_reconsulta if s.strip()]
    if not signos:
        return config.SIGNOS_ALARMA

    lineas = [
        "Volvé a consultar o llamá al 107 si aparece cualquiera de estas cosas:"
    ]
    lineas.extend(f"• {s[0].upper() + s[1:]}." for s in signos)
    return "\n".join(lineas)


def _texto_centros(busqueda: recursos.ResultadoBusqueda) -> str:
    """Bloque 'Dónde podés ir', con los datos reales de la base."""
    encabezado = "**Dónde podés ir:**"
    # Que se buscó en otra ciudad se avisa, no se disimula.
    if busqueda.hubo_derivacion_de_ciudad:
        encabezado = (
            f"**Dónde podés ir** (en {busqueda.ciudad_buscada}, porque en "
            f"{busqueda.ciudad_persona} no hay centros cargados)**:**"
        )

    lineas = [encabezado]
    for c in busqueda.recursos:
        lineas.append(_linea_centro(c))
    return "\n".join(lineas)


def _linea_centro(c: Recurso) -> str:
    """Una línea por centro. Solo se nombra el dato que existe."""
    partes = [f"• **{c.nombre}** ({c.tipo})"]

    ubicacion = ", ".join(p for p in (c.direccion, c.ciudad) if p)
    if ubicacion:
        partes.append(f"  {ubicacion}")
    if c.telefono:
        partes.append(f"  Tel: {c.telefono}")
    if c.horario:
        partes.append(f"  Horario: {c.horario}")
    else:
        # No es lo mismo "no sabemos el horario" que "está cerrado".
        partes.append("  Horario no informado: conviene llamar antes de ir.")
    if c.distancia_km is not None:
        partes.append(f"  A {c.distancia_km:.1f} km".replace(".", ","))

    return "\n".join(partes)


# Marcas de texto de desarrollo. `motivo_clasificacion` y
# `discriminador_disparador` los escribe el motor de reglas y se le muestran tal
# cual a la persona, así que un placeholder del equipo termina en pantalla: el
# STUB actual hacía que se leyera "Por qué te digo esto: STUB - motor de reglas
# no implementado". Eso no le dice nada a quien consulta y encima le muestra que
# el sistema está a medio hacer justo cuando le está indicando qué hacer con su
# salud. El dato crudo se sigue viendo en el bloque estructurado y en el debug,
# que es donde le sirve al equipo.
_MARCADORES_INTERNOS: tuple[str, ...] = (
    "stub",
    "todo",
    "fixme",
    "no implementado",
    "sin implementar",
    "not implemented",
    "placeholder",
)

# Antes acá había una frase de relleno ("por el conjunto de lo que me
# contaste") para cuando el motor no daba una razón presentable. No aporta
# nada: la sección entera se omite y listo.
RAZON_GENERICA = ""


def es_texto_interno(texto: str | None) -> bool:
    """True si el texto es un placeholder del equipo y no se puede mostrar."""
    if not texto:
        return True
    bajo = texto.strip().lower()
    if not bajo or bajo == "ninguno":
        return True
    return any(marca in bajo for marca in _MARCADORES_INTERNOS)


def _texto_razones(clasificacion: Clasificacion) -> str:
    """Explicación del porqué, a partir de lo que devolvió el motor de reglas.

    Si el motor todavía no da una razón presentable, se responde con una frase
    genérica y honesta en vez de exponer el placeholder.
    """
    disparador = (clasificacion.discriminador_disparador or "").strip()
    motivo = (clasificacion.motivo_clasificacion or "").strip()

    disparador_ok = not es_texto_interno(disparador)
    motivo_ok = not es_texto_interno(motivo)

    if disparador_ok:
        texto = disparador.replace("_", " ")
        if motivo_ok:
            texto += f" ({motivo})"
        return texto
    if motivo_ok:
        return motivo
    return RAZON_GENERICA


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
