"""Tests de la máquina de estados del turno.

Se mockea `llamar_gemma` en todos los casos: acá no se prueba el modelo, se
prueba la lógica de decisión. Los tests son sincrónicos y usan `asyncio.run`
para no sumar `pytest-asyncio` a las dependencias.
"""

from __future__ import annotations

import asyncio

import pytest

from app import config, gemma, orquestador, session
from app.gemma import GemmaError
from app.schema import (
    CamposExtraidos,
    DiscriminadoresGenerales,
    FichaClinica,
    RespuestaGemma,
)


@pytest.fixture(autouse=True)
def _store_limpio():
    """Cada test arranca con el store de sesiones vacío."""
    session.limpiar_todo()
    yield
    session.limpiar_todo()


def _respuesta(
    campos: CamposExtraidos | None = None,
    pregunta: str | None = None,
    confianza: float = 0.9,
) -> RespuestaGemma:
    return RespuestaGemma(
        campos=campos or CamposExtraidos(),
        pregunta_aclaracion=pregunta,
        confianza_extraccion=confianza,
        latencia_ms=42,
    )


def _mockear(monkeypatch, *respuestas):
    """Encola respuestas de Gemma; la última se repite si hacen falta más."""
    cola = list(respuestas)

    async def fake(mensajes):
        r = cola.pop(0) if len(cola) > 1 else cola[0]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(gemma, "llamar_gemma", fake)


def _ficha_completa(**extra) -> CamposExtraidos:
    return CamposExtraidos(
        edad=54,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=DiscriminadoresGenerales(
            riesgo_via_aerea=False,
            respira_normalmente=True,
            nivel_conciencia="alerta",
            hemorragia_mayor=False,
            **extra,
        ),
    )


# --------------------------------------------------------------------------- #
# Bandera roja
# --------------------------------------------------------------------------- #


def test_bandera_roja_en_el_primer_mensaje_corta_sin_preguntar(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(
                discriminadores_generales=DiscriminadoresGenerales(
                    respira_normalmente=False
                )
            ),
            # Aunque el modelo traiga una pregunta, no se la usa.
            pregunta="¿Qué edad tenés?",
            confianza=0.4,
        ),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "no puedo respirar"))

    assert r.tipo == "derivacion_inmediata"
    assert "107" in r.mensaje
    # Lo importante: NO se hizo ninguna pregunta de aclaración.
    assert state.preguntas_aclaracion == 0
    assert "¿Qué edad tenés?" not in r.mensaje
    # Y la ficha estaba incompleta: la bandera roja manda sobre todo lo demás.
    assert state.ficha.edad is None


def test_bandera_roja_cierra_y_borra_la_sesion(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(
                discriminadores_generales=DiscriminadoresGenerales(
                    nivel_conciencia="no_responde"
                )
            )
        ),
    )

    asyncio.run(orquestador.procesar_turno(state, "no reacciona"))
    assert not session.existe(state.session_id)


@pytest.mark.parametrize(
    "dg",
    [
        DiscriminadoresGenerales(nivel_conciencia="no_responde"),
        DiscriminadoresGenerales(respira_normalmente=False),
        DiscriminadoresGenerales(riesgo_via_aerea=True),
        DiscriminadoresGenerales(hemorragia_mayor=True),
    ],
)
def test_cada_criterio_dispara_bandera_roja(dg):
    assert orquestador.es_bandera_roja(FichaClinica(discriminadores_generales=dg))


@pytest.mark.parametrize(
    "dg",
    [
        DiscriminadoresGenerales(),  # todo None: no se sabe, no es bandera roja
        DiscriminadoresGenerales(respira_normalmente=True, hemorragia_mayor=False),
        DiscriminadoresGenerales(nivel_conciencia="somnoliento", dolor_eva=10),
    ],
)
def test_sin_riesgo_vital_afirmado_no_hay_bandera_roja(dg):
    assert not orquestador.es_bandera_roja(FichaClinica(discriminadores_generales=dg))


# --------------------------------------------------------------------------- #
# Ficha incompleta -> pregunta
# --------------------------------------------------------------------------- #


def test_ficha_incompleta_devuelve_pregunta(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(motivo_consulta="dolor_toracico"),
            pregunta="¿Estás respirando con normalidad?",
            confianza=0.5,
        ),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "me duele el pecho"))

    assert r.tipo == "pregunta"
    assert r.mensaje == "¿Estás respirando con normalidad?"
    assert r.resultado is None
    assert state.preguntas_aclaracion == 1
    # La sesión sigue viva esperando la respuesta.
    assert session.existe(state.session_id)


def test_confianza_baja_no_cierra_aunque_esten_todos_los_campos(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(campos=_ficha_completa(), confianza=0.3))

    r = asyncio.run(orquestador.procesar_turno(state, "algo raro"))
    assert r.tipo == "pregunta"


def test_usa_pregunta_de_fallback_si_gemma_no_trajo_ninguna(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(campos=CamposExtraidos(edad=30), pregunta=None, confianza=0.5),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "me siento mal"))

    assert r.tipo == "pregunta"
    # El primer faltante por prioridad es un discriminador general.
    assert r.mensaje == config.PREGUNTAS_FALLBACK["respira_normalmente"]


# --------------------------------------------------------------------------- #
# Ficha completa -> resultado
# --------------------------------------------------------------------------- #


def test_ficha_completa_clasifica_y_cierra(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(campos=_ficha_completa(), confianza=0.9))

    r = asyncio.run(orquestador.procesar_turno(state, "me duele el pecho, tengo 54"))

    assert r.tipo == "resultado"
    assert r.resultado is not None
    assert r.resultado.color == "amarillo"  # stub del motor de reglas
    assert r.resultado.recursos
    assert not session.existe(state.session_id)


def test_el_mensaje_de_resultado_tiene_todas_las_secciones(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(campos=_ficha_completa(), confianza=0.9))

    r = asyncio.run(orquestador.procesar_turno(state, "listo", lat=-31.73, lng=-60.53))

    assert config.DESCRIPCION_COLOR["amarillo"] in r.mensaje  # 1. nivel
    assert "Por qué te digo esto" in r.mensaje  # 2. razones
    assert "Dónde podés ir" in r.mensaje  # 3. centros
    assert "km" in r.mensaje
    assert "Volvé a consultar" in r.mensaje  # 4. signos de alarma
    assert config.DISCLAIMER_FINAL in r.mensaje  # 5. disclaimer


# --------------------------------------------------------------------------- #
# Límites
# --------------------------------------------------------------------------- #


def test_al_agotar_las_preguntas_clasifica_igual(monkeypatch):
    state = session.crear_sesion()
    # Gemma nunca completa nada y siempre pregunta.
    _mockear(monkeypatch, _respuesta(pregunta="¿Y qué más?", confianza=0.2))

    for i in range(config.MAX_PREGUNTAS):
        r = asyncio.run(orquestador.procesar_turno(state, f"no sé {i}"))
        assert r.tipo == "pregunta"

    # El siguiente turno ya no pregunta: clasifica con lo que haya.
    r = asyncio.run(orquestador.procesar_turno(state, "no sé"))
    assert r.tipo == "resultado"
    assert r.resultado is not None
    assert state.preguntas_aclaracion == config.MAX_PREGUNTAS
    assert not session.existe(state.session_id)


def test_al_superar_el_tope_de_turnos_fuerza_el_cierre(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(pregunta="¿Y?", confianza=0.1))

    state.turnos = config.MAX_TURNOS
    r = asyncio.run(orquestador.procesar_turno(state, "otra cosa más"))

    assert r.tipo == "resultado"
    assert not session.existe(state.session_id)


# --------------------------------------------------------------------------- #
# Errores del modelo
# --------------------------------------------------------------------------- #


def test_gemma_error_devuelve_error_seguro_y_no_cierra(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, GemmaError("timeout", "25.0s"))

    r = asyncio.run(orquestador.procesar_turno(state, "me duele el pecho"))

    assert r.tipo == "error_seguro"
    assert r.mensaje == config.MENSAJE_ERROR_SEGURO
    assert "107" in r.mensaje
    # La sesión queda abierta para que la persona pueda reintentar.
    assert session.existe(state.session_id)
    assert not state.cerrada


def test_se_puede_reintentar_despues_de_un_error(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        GemmaError("conexion", "localhost"),
        _respuesta(campos=_ficha_completa(), confianza=0.9),
    )

    primera = asyncio.run(orquestador.procesar_turno(state, "hola"))
    segunda = asyncio.run(orquestador.procesar_turno(state, "me duele el pecho"))

    assert primera.tipo == "error_seguro"
    assert segunda.tipo == "resultado"


# --------------------------------------------------------------------------- #
# Acumulación entre turnos
# --------------------------------------------------------------------------- #


def test_la_ficha_se_acumula_y_no_se_pierde_entre_turnos(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(campos=CamposExtraidos(edad=54), pregunta="¿Qué te pasa?", confianza=0.5),
        # Segundo turno: el modelo no vuelve a mencionar la edad.
        _respuesta(
            campos=CamposExtraidos(motivo_consulta="dolor_toracico"),
            pregunta="¿Respirás bien?",
            confianza=0.5,
        ),
    )

    asyncio.run(orquestador.procesar_turno(state, "tengo 54"))
    asyncio.run(orquestador.procesar_turno(state, "me duele el pecho"))

    assert state.ficha.edad == 54  # no se borró
    assert state.ficha.motivo_consulta == "dolor_toracico"


def test_el_relato_libre_acumula_los_mensajes(monkeypatch):
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(pregunta="¿Y?", confianza=0.2))

    asyncio.run(orquestador.procesar_turno(state, "me duele el pecho"))
    asyncio.run(orquestador.procesar_turno(state, "desde ayer"))

    assert "me duele el pecho" in state.ficha.relato_libre
    assert "desde ayer" in state.ficha.relato_libre


# --------------------------------------------------------------------------- #
# ficha_suficiente
# --------------------------------------------------------------------------- #


def test_ficha_suficiente_exige_criticos_motivo_edad_y_confianza():
    ficha = FichaClinica(
        edad=54,
        motivo_consulta="dolor_toracico",
        confianza_extraccion=0.9,
        discriminadores_generales=DiscriminadoresGenerales(
            riesgo_via_aerea=False,
            respira_normalmente=True,
            nivel_conciencia="alerta",
            hemorragia_mayor=False,
        ),
    )
    listo, faltantes = orquestador.ficha_suficiente(ficha)
    assert listo
    # Los no críticos pueden seguir faltando sin bloquear el cierre.
    assert "dolor_eva" in faltantes


def test_ficha_vacia_no_es_suficiente():
    listo, faltantes = orquestador.ficha_suficiente(FichaClinica())
    assert not listo
    assert faltantes


# --------------------------------------------------------------------------- #
# Debug
# --------------------------------------------------------------------------- #


def test_el_bloque_debug_esta_apagado_por_defecto(monkeypatch):
    monkeypatch.setattr(config, "DEBUG_MODE", False)
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(pregunta="¿Y?", confianza=0.2))

    r = asyncio.run(orquestador.procesar_turno(state, "hola"))
    assert r.debug is None


def test_con_debug_prendido_se_expone_la_ficha(monkeypatch):
    monkeypatch.setattr(config, "DEBUG_MODE", True)
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(campos=CamposExtraidos(edad=54), pregunta="¿Y?", confianza=0.45),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "tengo 54"))

    assert r.debug is not None
    assert r.debug.turno == 1
    assert r.debug.confianza == 0.45
    assert r.debug.ficha["edad"] == 54
    assert "respira_normalmente" in r.debug.campos_faltantes
