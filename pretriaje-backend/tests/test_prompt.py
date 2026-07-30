"""Tests del armado del prompt."""

from __future__ import annotations

from datetime import datetime, timezone

from app import config
from app.prompt import SYSTEM_PROMPT, armar_mensajes, detectar_faltantes
from app.schema import (
    DiscriminadoresGenerales,
    FichaClinica,
    SesionState,
    TurnoHistorial,
)


def _sesion(ficha: FichaClinica | None = None, historial=None) -> SesionState:
    ahora = datetime.now(timezone.utc)
    return SesionState(
        session_id="test-session",
        ficha=ficha or FichaClinica(),
        historial=historial or [],
        creada_en=ahora,
        ultimo_acceso=ahora,
    )


# --------------------------------------------------------------------------- #
# Estructura de los mensajes
# --------------------------------------------------------------------------- #


def test_primer_mensaje_es_el_system_prompt():
    mensajes = armar_mensajes(_sesion(), "me duele la cabeza")
    assert mensajes[0]["role"] == "system"
    assert mensajes[0]["content"] == SYSTEM_PROMPT


def test_el_ultimo_mensaje_es_el_del_usuario():
    mensajes = armar_mensajes(_sesion(), "me duele la cabeza")
    assert mensajes[-1] == {"role": "user", "content": "me duele la cabeza"}


def test_incluye_la_ficha_parcial_en_el_segundo_bloque_system():
    ficha = FichaClinica(
        edad=54,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=DiscriminadoresGenerales(dolor_eva=7),
    )
    mensajes = armar_mensajes(_sesion(ficha), "sigue igual")

    contexto = mensajes[1]
    assert contexto["role"] == "system"
    assert "Ficha actual:" in contexto["content"]
    # Los datos ya recolectados viajan en el bloque dinámico.
    assert '"edad": 54' in contexto["content"]
    assert "dolor_toracico" in contexto["content"]
    assert '"dolor_eva": 7' in contexto["content"]


def test_los_campos_faltantes_aparecen_en_el_contexto():
    ficha = FichaClinica(edad=30, motivo_consulta="fiebre")
    contenido = armar_mensajes(_sesion(ficha), "hola")[1]["content"]

    assert "Campos prioritarios faltantes" in contenido
    for campo in config.CAMPOS_CRITICOS_GENERALES:
        assert campo in contenido


def test_ficha_completa_no_pide_mas_preguntas():
    ficha = FichaClinica(
        edad=30,
        motivo_consulta="fiebre",
        discriminadores_generales=DiscriminadoresGenerales(
            riesgo_via_aerea=False,
            respira_normalmente=True,
            nivel_conciencia="alerta",
            hemorragia_mayor=False,
            dolor_eva=3,
            temperatura_c=38.5,
            inicio="gradual",
            tiempo_evolucion_horas=12,
        ),
    )
    contenido = armar_mensajes(_sesion(ficha), "hola")[1]["content"]
    assert "No faltan campos prioritarios" in contenido


def test_el_relato_libre_no_se_duplica_en_el_contexto():
    # El relato ya viaja en el historial; repetirlo solo gasta contexto.
    ficha = FichaClinica(relato_libre="me duele mucho el pecho desde ayer")
    contenido = armar_mensajes(_sesion(ficha), "hola")[1]["content"]
    assert "desde ayer" not in contenido


# --------------------------------------------------------------------------- #
# Historial
# --------------------------------------------------------------------------- #


def test_historial_se_recorta_a_n_turnos_y_mapea_roles():
    historial = [
        TurnoHistorial(rol="usuario" if i % 2 == 0 else "asistente", texto=f"t{i}")
        for i in range(10)
    ]
    mensajes = armar_mensajes(_sesion(historial=historial), "nuevo")

    # 2 bloques system + N del historial + 1 mensaje nuevo
    del_historial = mensajes[2:-1]
    assert len(del_historial) == config.HISTORIAL_TURNOS
    assert del_historial[0]["content"] == f"t{10 - config.HISTORIAL_TURNOS}"
    assert {m["role"] for m in del_historial} <= {"user", "assistant"}


# --------------------------------------------------------------------------- #
# Imagen
# --------------------------------------------------------------------------- #


def test_la_imagen_va_en_el_mensaje_nuevo_del_usuario():
    mensajes = armar_mensajes(_sesion(), "mirá esto", imagen_b64="iVBORw0KGgo=")
    ultimo = mensajes[-1]

    assert ultimo["role"] == "user"
    assert ultimo["images"] == ["iVBORw0KGgo="]
    # Ningún otro mensaje lleva imágenes.
    assert all("images" not in m for m in mensajes[:-1])


def test_sin_imagen_no_se_agrega_la_clave():
    assert "images" not in armar_mensajes(_sesion(), "hola")[-1]


def test_se_saca_el_prefijo_data_uri():
    mensajes = armar_mensajes(
        _sesion(), "mirá", imagen_b64="data:image/png;base64,iVBORw0KGgo="
    )
    assert mensajes[-1]["images"] == ["iVBORw0KGgo="]


# --------------------------------------------------------------------------- #
# Prioridad de campos
# --------------------------------------------------------------------------- #


def test_los_generales_se_preguntan_antes_que_los_especificos():
    faltantes = detectar_faltantes(FichaClinica())
    assert faltantes[: len(config.CAMPOS_CRITICOS_GENERALES)] == list(
        config.CAMPOS_CRITICOS_GENERALES
    )
    assert faltantes.index("respira_normalmente") < faltantes.index("dolor_eva")


def test_los_campos_ya_cargados_no_figuran_como_faltantes():
    ficha = FichaClinica(
        edad=40,
        discriminadores_generales=DiscriminadoresGenerales(respira_normalmente=True),
    )
    faltantes = detectar_faltantes(ficha)
    assert "edad" not in faltantes
    assert "respira_normalmente" not in faltantes


def test_un_false_cuenta_como_dato_no_como_faltante():
    # False es información clínica ("no tiene hemorragia"), no ausencia.
    ficha = FichaClinica(
        discriminadores_generales=DiscriminadoresGenerales(hemorragia_mayor=False)
    )
    assert "hemorragia_mayor" not in detectar_faltantes(ficha)


# --------------------------------------------------------------------------- #
# System prompt: las prohibiciones tienen que estar
# --------------------------------------------------------------------------- #


def test_el_system_prompt_prohibe_diagnosticar_y_medicar():
    texto = SYSTEM_PROMPT.lower()
    assert "no diagnosticás" in texto
    assert "no recomendás medicamentos" in texto
    assert "no inventás datos" in texto
    assert "null" in texto
