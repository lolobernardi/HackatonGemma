"""Tests de la búsqueda de centros de salud.

No tocan PostgreSQL: se mockea `centros_db.buscar` y se verifica QUÉ filtros se
le pidieron y en qué orden. Lo que se prueba es la política de búsqueda (cómo
traduce el vocabulario del motor de reglas, cómo resuelve la ubicación, cómo
relaja los filtros), no el SQL.
"""

from __future__ import annotations

import pytest

from app import centros_db, config, recursos
from app.schema import Clasificacion, FichaClinica


def _clasificacion(
    color: str = "amarillo",
    tipo_recurso: str = "centro_urgencias",
    especialidad: str | None = None,
) -> Clasificacion:
    return Clasificacion(
        color=color,
        tiempo_maximo_min=60,
        motivo_clasificacion="prueba",
        discriminador_disparador="prueba",
        regla_id="TEST-01",
        traza=["TEST-01"],
        tipo_recurso_sugerido=tipo_recurso,
        especialidad_sugerida=especialidad,
        version_ruleset="0.0.0",
    )


def _fila(nombre: str = "Centro X", tipo: str = "CAPS", **extra) -> dict:
    base = {
        "id": 1,
        "nombre": nombre,
        "tipo": tipo,
        "dependencia": "municipal",
        "ciudad": "Paraná",
        "provincia": "Entre Ríos",
        "direccion": "Alguna calle 123",
        "barrio": None,
        "telefono": "0343-0000000",
        "horario": None,
        "horario_informado": False,
        "latitud": None,
        "longitud": None,
        "distancia_km": None,
        "especialidades": [],
    }
    base.update(extra)
    return base


@pytest.fixture
def espia(monkeypatch):
    """Registra cada llamada a la base y devuelve lo que se le configure."""

    llamadas: list[dict] = []
    respuestas: dict[str, list] = {"valor": []}

    def fake(**kwargs):
        llamadas.append(kwargs)
        idx = len(llamadas) - 1
        cola = respuestas["valor"]
        return cola[idx] if idx < len(cola) else []

    monkeypatch.setattr(centros_db, "buscar", fake)
    return llamadas, respuestas


# --------------------------------------------------------------------------- #
# Ubicación
# --------------------------------------------------------------------------- #


def test_una_ciudad_con_cobertura_se_usa_tal_cual():
    assert recursos.resolver_ciudad("Paraná") == ("Paraná", 0.0)
    assert recursos.resolver_ciudad("Santa Fe") == ("Santa Fe", 0.0)


def test_oro_verde_va_a_parana_por_distancia_real():
    """Oro Verde no tiene centros cargados; Paraná es la cobertura más cercana."""
    ciudad, distancia = recursos.resolver_ciudad("Oro Verde")
    assert ciudad == "Paraná"
    # ~11 km en línea recta.
    assert 5 < distancia < 20


def test_santo_tome_va_a_santa_fe_y_no_a_parana():
    """La derivación se calcula, no está cableada: cada localidad va a la suya."""
    ciudad, _ = recursos.resolver_ciudad("Santo Tomé")
    assert ciudad == "Santa Fe"


def test_las_coordenadas_compartidas_mandan_sobre_la_localidad():
    """Si la persona comparte su ubicación real, esa es la que vale."""
    # Coordenadas de Santa Fe capital, con la localidad puesta en Oro Verde.
    ciudad, _ = recursos.resolver_ciudad("Oro Verde", lat=-31.6333, lng=-60.7000)
    assert ciudad == "Santa Fe"


def test_sin_ubicacion_utilizable_se_busca_sin_filtro():
    """Mejor ofrecer centros de cualquier ciudad que no ofrecer ninguno."""
    assert recursos.resolver_ciudad("Ushuaia") == (None, None)
    assert recursos.resolver_ciudad(None) == (None, None)


def test_la_distancia_es_simetrica_y_cero_consigo_misma():
    parana = config.LOCALIDADES["Paraná"]
    santa_fe = config.LOCALIDADES["Santa Fe"]
    assert recursos.distancia_km(parana, parana) == pytest.approx(0.0)
    assert recursos.distancia_km(parana, santa_fe) == pytest.approx(
        recursos.distancia_km(santa_fe, parana)
    )
    # Paraná y Santa Fe están a ~25 km.
    assert 15 < recursos.distancia_km(parana, santa_fe) < 40


# --------------------------------------------------------------------------- #
# Especialista: lo decide el motor, acá sólo se traduce
# --------------------------------------------------------------------------- #


def test_se_traduce_el_slug_del_ruleset_al_nombre_de_la_base():
    assert recursos.resolver_especialidad("cardiologia") == "Cardiología"
    assert recursos.resolver_especialidad("pediatria") == "Pediatría"
    assert recursos.resolver_especialidad("cirugia_general") == "Cirugía"


def test_una_especialidad_sin_cobertura_cae_en_la_de_respaldo():
    """Dermatología, neumonología, neurología e infectología: 0 centros."""
    for slug in ("dermatologia", "neumonologia", "neurologia", "infectologia"):
        assert recursos.resolver_especialidad(slug) == config.ESPECIALIDAD_FALLBACK


def test_todas_las_especialidades_del_ruleset_estan_traducidas():
    """Si el ruleset suma una, no puede quedar sin entrada en el mapa."""
    import re
    from pathlib import Path

    ruleset = Path(__file__).resolve().parents[1] / "app" / "ruleset.yaml"
    slugs = set(re.findall(r"^\s+especialidad:\s*(\S+)", ruleset.read_text("utf-8"), re.M))
    faltantes = slugs - set(config.ESPECIALIDAD_DB)
    assert not faltantes, f"especialidades del ruleset sin traducir: {faltantes}"


def test_sin_especialista_del_motor_un_menor_va_a_pediatria():
    assert recursos.resolver_especialidad(None, edad=8) == "Pediatría"


def test_sin_especialista_ni_edad_pediatrica_va_al_respaldo():
    assert recursos.resolver_especialidad(None, edad=40) == config.ESPECIALIDAD_FALLBACK


# --------------------------------------------------------------------------- #
# Tipo de efector: lo decide el motor
# --------------------------------------------------------------------------- #


def test_una_guardia_de_alta_complejidad_busca_en_hospital(espia):
    llamadas, respuestas = espia
    respuestas["valor"] = [[_fila(tipo="hospital")]]

    recursos.buscar_para_clasificacion(
        _clasificacion(color="rojo", tipo_recurso="guardia_alta_complejidad"),
        FichaClinica(edad=50),
    )

    assert llamadas[0]["tipo"] == "hospital"


def test_una_consulta_programada_no_va_al_hospital(espia):
    """No mandar un azul a una guardia de alta complejidad: satura el sistema."""
    llamadas, respuestas = espia
    respuestas["valor"] = [[_fila()]]

    recursos.buscar_para_clasificacion(
        _clasificacion(color="azul", tipo_recurso="consulta_programada"),
        FichaClinica(edad=30),
    )

    assert "hospital" not in [c.get("tipo") for c in llamadas]


def test_todos_los_tipos_del_motor_tienen_traduccion():
    from app.reglas import TIPO_RECURSO

    faltantes = set(TIPO_RECURSO.values()) - set(config.TIPOS_POR_RECURSO)
    assert not faltantes, f"tipos de recurso sin traducir: {faltantes}"


# --------------------------------------------------------------------------- #
# Cascada
# --------------------------------------------------------------------------- #


def test_el_primer_intento_usa_los_tres_criterios(espia):
    """ciudad + especialista + horario, que es lo que pidió el equipo."""
    llamadas, respuestas = espia
    respuestas["valor"] = [[_fila()]]

    recursos.buscar_para_clasificacion(
        _clasificacion(especialidad="pediatria"),
        FichaClinica(edad=8),
        ciudad="Oro Verde",
    )

    primera = llamadas[0]
    assert primera["ciudad"] == "Paraná"
    assert primera["especialidad"] == "Pediatría"
    assert primera["hora"] is not None


def test_si_no_hay_nada_relaja_los_filtros(espia):
    """La cobertura es despareja: Cardiología está en 2 centros de 86."""
    llamadas, respuestas = espia
    respuestas["valor"] = [[], [], [], [], [], [], [_fila()]]

    r = recursos.buscar_para_clasificacion(
        _clasificacion(especialidad="cardiologia"), FichaClinica(edad=50)
    )

    assert r.recursos
    assert llamadas[0]["especialidad"] == "Cardiología"
    assert llamadas[0]["hora"] is not None
    assert llamadas[-1].get("hora") is None


def test_se_queda_con_el_primer_intento_que_da_resultados(espia):
    llamadas, respuestas = espia
    respuestas["valor"] = [[_fila(nombre="El primero")]]

    r = recursos.buscar_para_clasificacion(_clasificacion(), FichaClinica(edad=30))

    assert len(llamadas) == 1
    assert r.recursos[0].nombre == "El primero"


def test_sin_resultados_devuelve_vacio_y_no_explota(espia):
    _, respuestas = espia
    respuestas["valor"] = []

    r = recursos.buscar_para_clasificacion(_clasificacion(), FichaClinica(edad=30))

    assert r.recursos == []
    assert r.criterio == "sin_resultados"


# --------------------------------------------------------------------------- #
# Rastro y conversión
# --------------------------------------------------------------------------- #


def test_la_derivacion_de_ciudad_queda_registrada(espia):
    _, respuestas = espia
    respuestas["valor"] = [[_fila()]]

    r = recursos.buscar_para_clasificacion(
        _clasificacion(), FichaClinica(edad=30), ciudad="Oro Verde"
    )

    assert r.ciudad_persona == "Oro Verde"
    assert r.ciudad_buscada == "Paraná"
    assert r.hubo_derivacion_de_ciudad
    assert r.distancia_a_ciudad_km is not None


def test_no_se_inventa_la_ocupacion_ni_la_distancia(espia):
    """La base no tiene esos datos; inventarlos sería desinformar."""
    _, respuestas = espia
    respuestas["valor"] = [[_fila()]]

    r = recursos.buscar_para_clasificacion(_clasificacion(), FichaClinica(edad=30))

    assert r.recursos[0].ocupacion_estimada is None
    assert r.recursos[0].distancia_km is None


# --------------------------------------------------------------------------- #
# Degradación
# --------------------------------------------------------------------------- #


def test_si_la_base_falla_no_se_propaga(monkeypatch):
    """Sin centros la persona igual tiene que recibir su nivel de urgencia."""
    from sqlalchemy.exc import OperationalError

    def explota(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("base caída"))

    monkeypatch.setattr(centros_db, "_obtener_engine", explota)

    assert centros_db.buscar(ciudad="Paraná") == []
    assert centros_db.disponible() is False
