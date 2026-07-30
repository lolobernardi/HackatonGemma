"""Tests del parseo de tool calls y del merge de la ficha."""

from __future__ import annotations

import json

import pytest

from app.gemma import (
    NOMBRE_TOOL,
    TOOL_ACTUALIZAR_FICHA,
    GemmaError,
    merge_ficha,
    parsear_respuesta,
)
from app.schema import CamposExtraidos, DiscriminadoresGenerales, FichaClinica


def _body(argumentos, nombre: str = NOMBRE_TOOL) -> dict:
    """Body típico de `POST /api/chat` de Ollama con una tool call."""
    return {
        "model": "gemma4:e4b",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": nombre, "arguments": argumentos}}],
        },
        "done": True,
    }


# --------------------------------------------------------------------------- #
# Parseo feliz
# --------------------------------------------------------------------------- #


def test_parsea_un_tool_call_bien_formado():
    body = _body(
        {
            "edad": 54,
            "motivo_consulta": "dolor_toracico",
            "discriminadores_generales": {
                "respira_normalmente": True,
                "nivel_conciencia": "alerta",
                "dolor_eva": 8,
                "inicio": "subito",
                "tiempo_evolucion_horas": 0.5,
            },
            "discriminadores_especificos": {"irradia_brazo": True},
            "pregunta_aclaracion": "¿Hay algún sangrado?",
            "confianza_extraccion": 0.85,
        }
    )

    r = parsear_respuesta(body)

    assert r.campos.edad == 54
    assert r.campos.motivo_consulta == "dolor_toracico"
    assert r.campos.discriminadores_generales.dolor_eva == 8
    assert r.campos.discriminadores_especificos == {"irradia_brazo": True}
    assert r.pregunta_aclaracion == "¿Hay algún sangrado?"
    assert r.confianza_extraccion == 0.85


def test_acepta_arguments_como_string_json():
    # Algunos modelos emiten `arguments` en formato OpenAI (string JSON).
    body = _body(json.dumps({"edad": 30, "confianza_extraccion": 0.5}))
    r = parsear_respuesta(body)
    assert r.campos.edad == 30


def test_tool_call_vacio_es_valido_y_no_completa_nada():
    # "me siento mal": no hay nada para extraer, pero el turno es válido.
    r = parsear_respuesta(_body({"confianza_extraccion": 0.1}))
    assert r.campos.edad is None
    assert r.campos.motivo_consulta is None
    assert r.confianza_extraccion == 0.1


def test_pregunta_vacia_se_normaliza_a_none():
    r = parsear_respuesta(_body({"pregunta_aclaracion": "   ", "confianza_extraccion": 1}))
    assert r.pregunta_aclaracion is None


def test_confianza_ausente_se_asume_cero():
    # Nunca se asume confianza alta: ante la duda, se sigue preguntando.
    assert parsear_respuesta(_body({"edad": 20})).confianza_extraccion == 0.0


def test_confianza_fuera_de_rango_se_clampea():
    assert parsear_respuesta(_body({"confianza_extraccion": 7.5})).confianza_extraccion == 1.0
    assert parsear_respuesta(_body({"confianza_extraccion": -2})).confianza_extraccion == 0.0


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #


def test_json_roto_en_arguments_levanta_gemma_error():
    body = _body('{"edad": 54, "motivo_consulta":')  # string JSON truncado
    with pytest.raises(GemmaError) as exc:
        parsear_respuesta(body)
    assert exc.value.causa == "json"


def test_respuesta_sin_message_levanta_gemma_error():
    with pytest.raises(GemmaError) as exc:
        parsear_respuesta({"done": True})
    assert exc.value.causa == "json"


def test_sin_tool_call_levanta_gemma_error():
    # El modelo contestó en prosa en vez de llamar a la función.
    body = {"message": {"role": "assistant", "content": "Hola, contame más."}}
    with pytest.raises(GemmaError) as exc:
        parsear_respuesta(body)
    assert exc.value.causa == "sin_tool_call"


def test_campo_fuera_de_rango_levanta_gemma_error():
    # dolor_eva=44 es una alucinación: mejor perder el turno que ensuciar la ficha.
    body = _body(
        {"discriminadores_generales": {"dolor_eva": 44}, "confianza_extraccion": 0.9}
    )
    with pytest.raises(GemmaError) as exc:
        parsear_respuesta(body)
    assert exc.value.causa == "validacion"


def test_enum_invalido_levanta_gemma_error():
    body = _body(
        {
            "discriminadores_generales": {"nivel_conciencia": "medio_dormido"},
            "confianza_extraccion": 0.9,
        }
    )
    with pytest.raises(GemmaError) as exc:
        parsear_respuesta(body)
    assert exc.value.causa == "validacion"


# --------------------------------------------------------------------------- #
# Schema de la tool
# --------------------------------------------------------------------------- #


def test_el_schema_de_la_tool_espeja_la_ficha():
    props = TOOL_ACTUALIZAR_FICHA["function"]["parameters"]["properties"]
    for campo in ("edad", "es_para_tercero", "motivo_consulta"):
        assert campo in props
    assert "pregunta_aclaracion" in props
    assert "confianza_extraccion" in props

    generales = props["discriminadores_generales"]["properties"]
    for campo in DiscriminadoresGenerales.model_fields:
        assert campo in generales, f"falta {campo} en el schema de la tool"


# --------------------------------------------------------------------------- #
# merge_ficha
# --------------------------------------------------------------------------- #


def test_merge_completa_campos_vacios():
    ficha = merge_ficha(
        FichaClinica(),
        CamposExtraidos(edad=54, motivo_consulta="dolor_toracico"),
    )
    assert ficha.edad == 54
    assert ficha.motivo_consulta == "dolor_toracico"


def test_merge_no_borra_datos_previos_con_none():
    previa = FichaClinica(
        edad=54,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=DiscriminadoresGenerales(
            dolor_eva=8, respira_normalmente=True
        ),
    )
    # Turno donde el modelo solo captó el inicio: todo lo demás viene en None.
    nueva = merge_ficha(
        previa,
        CamposExtraidos(
            discriminadores_generales=DiscriminadoresGenerales(inicio="subito")
        ),
    )

    assert nueva.edad == 54
    assert nueva.motivo_consulta == "dolor_toracico"
    assert nueva.discriminadores_generales.dolor_eva == 8
    assert nueva.discriminadores_generales.respira_normalmente is True
    assert nueva.discriminadores_generales.inicio == "subito"


def test_merge_con_campos_totalmente_vacios_no_cambia_nada():
    previa = FichaClinica(edad=30, motivo_consulta="fiebre")
    assert merge_ficha(previa, CamposExtraidos()).model_dump() == previa.model_dump()


def test_merge_permite_corregir_un_valor_existente():
    previa = FichaClinica(edad=30)
    # "perdón, 31" -> un valor no nulo sí pisa el anterior.
    assert merge_ficha(previa, CamposExtraidos(edad=31)).edad == 31


def test_merge_preserva_false_como_dato():
    previa = FichaClinica(
        discriminadores_generales=DiscriminadoresGenerales(hemorragia_mayor=False)
    )
    nueva = merge_ficha(previa, CamposExtraidos(edad=20))
    assert nueva.discriminadores_generales.hemorragia_mayor is False


def test_merge_acumula_discriminadores_especificos():
    previa = FichaClinica(discriminadores_especificos={"irradia_brazo": True})
    nueva = merge_ficha(
        previa, CamposExtraidos(discriminadores_especificos={"sudoracion": True})
    )
    assert nueva.discriminadores_especificos == {
        "irradia_brazo": True,
        "sudoracion": True,
    }


def test_merge_normaliza_motivo_desconocido_a_otro():
    # Si el modelo ignora el enum, no le pasamos basura al motor de reglas.
    nueva = merge_ficha(FichaClinica(), CamposExtraidos(motivo_consulta="apendicitis"))
    assert nueva.motivo_consulta == "otro"


def test_merge_no_muta_la_ficha_original():
    previa = FichaClinica(edad=30)
    merge_ficha(previa, CamposExtraidos(edad=99, motivo_consulta="fiebre"))
    assert previa.edad == 30
    assert previa.motivo_consulta is None
