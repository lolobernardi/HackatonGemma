"""Tests de la presentación del triaje en el frontend.

`streamlit_app.py` ejecuta la app en el cuerpo del módulo, así que no se puede
importar entero desde pytest. Se compila sólo el bloque de definiciones —hasta
el separador `# Aplicación`— que es donde viven la paleta y las funciones puras.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

RUTA_APP = Path(__file__).resolve().parents[1] / "frontend" / "streamlit_app.py"

# Contraste mínimo de WCAG 2.1 AA para texto normal.
CONTRASTE_MINIMO = 4.5


@pytest.fixture(scope="module")
def app():
    fuente = RUTA_APP.read_text(encoding="utf-8")
    corte = fuente.index("# Aplicación")
    modulo = types.ModuleType("streamlit_app_definiciones")
    modulo.__dict__["__file__"] = str(RUTA_APP)
    exec(compile(fuente[:corte], str(RUTA_APP), "exec"), modulo.__dict__)
    return modulo


def _luminancia(hexadecimal: str) -> float:
    h = hexadecimal.lstrip("#")
    canales = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    canales = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canales
    ]
    return 0.2126 * canales[0] + 0.7152 * canales[1] + 0.0722 * canales[2]


def _contraste(fondo: str, texto: str) -> float:
    a, b = _luminancia(fondo), _luminancia(texto)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


# --------------------------------------------------------------------------- #
# Paleta
# --------------------------------------------------------------------------- #


def test_todos_los_niveles_se_leen(app):
    """El nivel de urgencia es lo que no puede quedar ilegible."""
    for color, estilo in app.COLORES_TRIAJE.items():
        ratio = _contraste(estilo["fondo"], estilo["texto"])
        assert ratio >= CONTRASTE_MINIMO, (
            f"{color}: contraste {ratio:.2f}, por debajo de {CONTRASTE_MINIMO}"
        )


def test_el_rojo_y_el_naranja_se_distinguen(app):
    """Confundir los dos niveles más urgentes es peor que un contraste justo."""
    rojo = app.COLORES_TRIAJE["rojo"]["fondo"].lstrip("#")
    naranja = app.COLORES_TRIAJE["naranja"]["fondo"].lstrip("#")
    distancia = sum(
        abs(int(rojo[i : i + 2], 16) - int(naranja[i : i + 2], 16)) for i in (0, 2, 4)
    )
    assert distancia > 100, f"rojo y naranja demasiado parecidos (distancia {distancia})"


def test_cada_color_tiene_su_explicacion_en_lenguaje_llano(app):
    """El color nunca va solo: aislado no le dice nada a la persona."""
    for color in app.COLORES_TRIAJE:
        assert app.DESCRIPCION_COLOR.get(color), f"{color} sin descripción"


def test_la_paleta_cubre_los_cinco_niveles_de_manchester(app):
    assert set(app.COLORES_TRIAJE) == {"rojo", "naranja", "amarillo", "verde", "azul"}


# --------------------------------------------------------------------------- #
# De qué respuesta sale el color
# --------------------------------------------------------------------------- #


def test_el_color_sale_del_resultado(app):
    respuesta = {"tipo": "resultado", "resultado": {"color": "verde"}}
    assert app._color_de_respuesta(respuesta) == "verde"


def test_una_derivacion_inmediata_es_roja(app):
    """No trae bloque `resultado`, pero es lo más urgente que existe."""
    respuesta = {"tipo": "derivacion_inmediata", "mensaje": "Llamá al 107"}
    assert app._color_de_respuesta(respuesta) == "rojo"


@pytest.mark.parametrize(
    "respuesta",
    [
        {"tipo": "pregunta", "mensaje": "¿Qué edad tenés?"},
        {"tipo": "error_seguro", "mensaje": "Perdón, tuve un problema"},
        {"tipo": "resultado", "resultado": None},
    ],
)
def test_sin_veredicto_no_hay_color(app, respuesta):
    """Durante la conversación no hay color: mostrar uno sería engañoso."""
    assert app._color_de_respuesta(respuesta) is None


# --------------------------------------------------------------------------- #
# Motivo de consulta en el popup
# --------------------------------------------------------------------------- #


def test_el_motivo_se_muestra_en_castellano_llano(app):
    """El backend maneja slugs; a la persona se le habla sin jerga."""
    assert app._motivo_legible({"motivo_consulta": "cefalea"}) == "Dolor de cabeza"
    assert (
        app._motivo_legible({"motivo_consulta": "dificultad_respiratoria"})
        == "Dificultad para respirar"
    )


def test_todos_los_motivos_del_backend_tienen_traduccion(app):
    """Si el backend suma un motivo, no puede quedar mostrándose el slug."""
    from app.config import MOTIVOS_CONSULTA

    faltantes = [m for m in MOTIVOS_CONSULTA if m not in app.MOTIVOS_LEGIBLES]
    assert not faltantes, f"motivos sin texto legible: {faltantes}"


def test_un_motivo_desconocido_no_rompe(app):
    """Peor que un slug feo sería que explote la pantalla de resultado."""
    assert app._motivo_legible({"motivo_consulta": "algo_nuevo"}) == "Algo nuevo"


def test_sin_motivo_devuelve_none(app):
    assert app._motivo_legible({}) is None
    assert app._motivo_legible(None) is None


# --------------------------------------------------------------------------- #
# Popup
# --------------------------------------------------------------------------- #


def test_el_popup_muestra_como_maximo_dos_centros(app):
    assert app.CENTROS_EN_POPUP == 2


# --------------------------------------------------------------------------- #
# Ficha clínica legible
# --------------------------------------------------------------------------- #


def test_los_valores_no_salen_en_notacion_de_codigo(app):
    """Un profesional lee 'Sí' y 'No consultado', no 'true' y 'null'."""
    assert app._valor_legible(True) == "Sí"
    assert app._valor_legible(False) == "No"
    assert app._valor_legible(None) == "No consultado"
    assert app._valor_legible(38.0) == "38"
    assert app._valor_legible("alerta") == "Alerta"
    assert app._valor_legible("no_responde") == "No responde"


def test_todos_los_enums_del_schema_se_leen_en_castellano(app):
    """Si el schema suma un valor, no puede quedar mostrándose el slug."""
    from app.schema import DiscriminadoresGenerales

    esperados = {"alerta", "somnoliento", "confuso", "no_responde", "subito", "gradual"}
    faltantes = esperados - set(app.VALORES_LEGIBLES)
    assert not faltantes, f"valores sin traducir: {faltantes}"
    del DiscriminadoresGenerales


def test_el_none_se_distingue_del_no(app):
    """Que no se haya preguntado no es lo mismo que una respuesta negativa."""
    assert app._valor_legible(None) != app._valor_legible(False)


def test_la_ficha_se_arma_como_filas_agrupadas(app):
    ficha = {
        "edad": 54,
        "es_para_tercero": False,
        "motivo_consulta": "dolor_toracico",
        "discriminadores_generales": {
            "respira_normalmente": True,
            "nivel_conciencia": "alerta",
            "dolor_eva": 8,
            "temperatura_c": None,
        },
        "discriminadores_especificos": {"dolor_opresivo": True},
    }
    filas = app._filas_ficha(ficha)
    por_dato = {f["Dato"]: f["Respuesta"] for f in filas}

    assert por_dato["Edad"] == "54 años"
    assert por_dato["Motivo de consulta"] == "Dolor en el pecho"
    assert por_dato["Respira con normalidad"] == "Sí"
    assert por_dato["Dolor (0 a 10)"] == "8"
    assert por_dato["Temperatura"] == "No consultado"
    assert por_dato["Dolor opresivo, como un peso"] == "Sí"
    assert {f["Sección"] for f in filas} == {
        "Paciente",
        "Evaluación inicial",
        "Detalles del cuadro",
    }


def test_todos_los_discriminadores_del_ruleset_tienen_etiqueta(app):
    """Si el ruleset suma uno, no puede aparecer el nombre de campo crudo."""
    from app.reglas import DISCRIMINADORES_POR_MOTIVO

    del app  # el chequeo es contra las constantes del módulo, ya cargadas
    import types
    modulo = types.ModuleType("m")
    modulo.__dict__["__file__"] = str(RUTA_APP)
    fuente = RUTA_APP.read_text(encoding="utf-8")
    exec(compile(fuente[: fuente.index("# Aplicación")], str(RUTA_APP), "exec"), modulo.__dict__)

    todos = {c for campos in DISCRIMINADORES_POR_MOTIVO.values() for c in campos}
    generales = {c for c, _, _ in modulo.ETIQUETAS_GENERALES}
    faltantes = todos - set(modulo.ETIQUETAS_ESPECIFICAS) - generales
    assert not faltantes, f"discriminadores sin etiqueta legible: {sorted(faltantes)}"


def test_un_campo_sin_etiqueta_no_queda_en_blanco(app):
    """Feo pero legible es mejor que vacío."""
    assert app._etiqueta_campo("algo_nuevo_del_ruleset") == "Algo nuevo del ruleset"


def test_las_etiquetas_resuelven_tambien_los_campos_generales(app):
    """Un faltante puede ser general o específico; los dos tienen que leerse."""
    assert app._etiqueta_campo("discriminadores_generales.temperatura_c") == "Temperatura"
    assert app._etiqueta_campo("discriminadores_generales.inicio") == "Cómo empezó"
    assert app._etiqueta_campo("edad") == "Edad"


def test_la_ficha_es_texto_y_no_un_grid_sobre_canvas(app):
    """Un profesional tiene que poder copiarla e imprimirla."""
    filas = [
        {"Sección": "Paciente", "Dato": "Edad", "Respuesta": "54 años"},
        {"Sección": "Paciente", "Dato": "Motivo", "Respuesta": "Dolor en el pecho"},
    ]
    tabla = app._tabla_markdown(filas)
    assert "| Dato | Respuesta |" in tabla
    assert "| Edad | 54 años |" in tabla


def test_un_valor_con_pipe_no_rompe_la_tabla(app):
    filas = [{"Sección": "x", "Dato": "Raro", "Respuesta": "a | b"}]
    assert r"a \| b" in app._tabla_markdown(filas)


# --------------------------------------------------------------------------- #
# Ubicación
# --------------------------------------------------------------------------- #


def test_las_localidades_del_front_existen_en_el_backend(app):
    """Si no coinciden, el backend no sabría ubicar a la persona."""
    from app.config import LOCALIDADES

    faltantes = set(app.LOCALIDADES) - set(LOCALIDADES)
    assert not faltantes, f"localidades que el backend no conoce: {faltantes}"


def test_las_ciudades_con_cobertura_coinciden(app):
    from app.config import CIUDADES_CON_COBERTURA

    assert set(app.CIUDADES_CON_COBERTURA) == set(CIUDADES_CON_COBERTURA)


def test_la_interfaz_no_usa_la_barra_lateral():
    """Todo va en la columna central; `st.sidebar` no se toca.

    Se mira el árbol sintáctico y no el texto del archivo, para no confundir
    una mención en un comentario con una llamada real.
    """
    import ast

    arbol = ast.parse(RUTA_APP.read_text(encoding="utf-8"))
    usos = [
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.Attribute)
        and nodo.attr == "sidebar"
        and isinstance(nodo.value, ast.Name)
        and nodo.value.id == "st"
    ]
    assert not usos, f"quedaron {len(usos)} usos de st.sidebar"
