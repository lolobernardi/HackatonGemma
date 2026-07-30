"""Tests de la máquina de estados del turno.

Se mockea `llamar_gemma` en todos los casos: acá no se prueba el modelo, se
prueba la lógica de decisión. Los tests son sincrónicos y usan `asyncio.run`
para no sumar `pytest-asyncio` a las dependencias.
"""

from __future__ import annotations

import asyncio

import pytest

from app import centros_db, config, gemma, orquestador, session
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


# Filas como las devuelve la base real: sin latitud/longitud (no hay
# geocodificación) y con el horario cargado sólo en algunos centros.
_CENTROS_DE_PRUEBA = [
    {
        "id": 1,
        "nombre": "Hospital General San Martín",
        "tipo": "hospital",
        "dependencia": "provincial",
        "ciudad": "Paraná",
        "provincia": "Entre Ríos",
        "direccion": "Pte. Juan Domingo Perón 450",
        "barrio": None,
        "telefono": "0343-4234545",
        "horario": None,
        "horario_informado": False,
        "latitud": None,
        "longitud": None,
        "distancia_km": None,
        "especialidades": ["Cardiología", "Clínica Médica"],
    },
    {
        "id": 2,
        "nombre": "CAPS Arturo Umberto Illia",
        "tipo": "CAPS",
        "dependencia": "municipal",
        "ciudad": "Paraná",
        "provincia": "Entre Ríos",
        "direccion": "Provincias Unidas 345",
        "barrio": None,
        "telefono": "0343-4201823",
        "horario": "7:00 a 19:00",
        "horario_informado": True,
        "latitud": None,
        "longitud": None,
        "distancia_km": None,
        "especialidades": ["Medicina General", "Pediatría"],
    },
]


@pytest.fixture(autouse=True)
def _sin_base_real(monkeypatch):
    """Aísla los tests de PostgreSQL.

    Sin esto los tests le pegan a la base de verdad y pasan o fallan según si
    alguien levantó `centros_salud_db`. La lógica de búsqueda se prueba aparte,
    en `test_recursos.py`.
    """
    monkeypatch.setattr(centros_db, "buscar", lambda **kwargs: list(_CENTROS_DE_PRUEBA))


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
    """Ficha que el motor de reglas considera completa.

    "Completa" significa que `reglas.campos_requeridos()` no devuelve nada, y
    eso incluye los discriminadores ESPECÍFICOS del flowchart, no sólo los
    generales. Con los generales solos el motor no baja de amarillo, que es
    justamente el comportamiento que hace falta poder distinguir en los tests.
    """
    from app import reglas

    generales = {
        "riesgo_via_aerea": False,
        "respira_normalmente": True,
        "nivel_conciencia": "alerta",
        "hemorragia_mayor": False,
        "dolor_eva": 2,
        "temperatura_c": 36.5,
        "inicio": "gradual",
        "tiempo_evolucion_horas": 48.0,
    }
    generales.update(extra)

    return CamposExtraidos(
        edad=54,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=DiscriminadoresGenerales(**generales),
        discriminadores_especificos={
            campo: False
            for campo in reglas.DISCRIMINADORES_POR_MOTIVO["dolor_toracico"]
        },
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
    assert "Quién te tiene que ver" in r.mensaje  # 3. especialista
    assert "Dónde podés ir" in r.mensaje  # 4. centros
    # Datos concretos del centro, que es lo que la persona necesita para ir.
    assert "Hospital General San Martín" in r.mensaje
    assert "Pte. Juan Domingo Perón 450" in r.mensaje
    assert "0343-4234545" in r.mensaje
    assert "Volvé a consultar" in r.mensaje  # 5. signos de alarma
    assert config.DISCLAIMER_FINAL in r.mensaje  # 6. disclaimer


def test_el_centro_sin_horario_lo_aclara_en_vez_de_omitirlo():
    """Un horario ausente no es lo mismo que estar cerrado.

    La base tiene el horario cargado en 6 de 86 centros, así que el caso es la
    norma, no la excepción.
    """
    state = session.crear_sesion()
    del state  # el mensaje se arma sin pasar por la sesión

    from app.recursos import ResultadoBusqueda, _a_recurso

    busqueda = ResultadoBusqueda(
        recursos=[_a_recurso(f) for f in _CENTROS_DE_PRUEBA],
        ciudad_persona="Oro Verde",
        ciudad_buscada="Paraná",
        especialidad="Cardiología",
        criterio="ciudad+especialidad+hospital",
    )
    mensaje = orquestador.armar_mensaje_resultado(
        orquestador.reglas.clasificar(FichaClinica()), busqueda
    )

    assert "Horario no informado" in mensaje       # el hospital no lo tiene
    assert "7:00 a 19:00" in mensaje               # el CAPS sí


def test_avisa_cuando_busco_en_otra_ciudad():
    """Derivar a otra ciudad se informa; mandar a alguien lejos sin avisar no."""
    from app.recursos import ResultadoBusqueda, _a_recurso

    busqueda = ResultadoBusqueda(
        recursos=[_a_recurso(_CENTROS_DE_PRUEBA[0])],
        ciudad_persona="Oro Verde",
        ciudad_buscada="Paraná",
        especialidad="Cardiología",
        criterio="ciudad+especialidad+hospital",
    )
    mensaje = orquestador.armar_mensaje_resultado(
        orquestador.reglas.clasificar(FichaClinica()), busqueda
    )

    assert "Oro Verde" in mensaje
    assert "Paraná" in mensaje


def test_el_placeholder_del_motor_de_reglas_no_llega_al_usuario():
    """El STUB no puede terminar en pantalla como explicación clínica.

    Antes se leía "Por qué te digo esto: STUB - motor de reglas no
    implementado", que no le dice nada a quien consulta y le muestra que el
    sistema está a medio hacer justo cuando le indica qué hacer con su salud.
    """
    from app.recursos import ResultadoBusqueda

    mensaje = orquestador.armar_mensaje_resultado(
        orquestador.reglas.clasificar(FichaClinica()),
        ResultadoBusqueda(ciudad_persona="Oro Verde"),
    )

    assert "STUB" not in mensaje.upper()
    assert "no implementado" not in mensaje.lower()


@pytest.mark.parametrize(
    "texto",
    [
        "STUB - motor de reglas no implementado",
        "stub",
        "TODO: implementar flowchart",
        "FIXME revisar",
        "placeholder",
        "sin implementar",
        "ninguno",
        "",
        None,
    ],
)
def test_se_reconoce_el_texto_interno(texto):
    assert orquestador.es_texto_interno(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "dolor torácico de inicio súbito",
        "fiebre alta en menor de 3 meses",
        "dolor_eva mayor a 7",
    ],
)
def test_una_razon_clinica_real_si_se_muestra(texto):
    """El filtro no puede tragarse las explicaciones legítimas."""
    assert not orquestador.es_texto_interno(texto)


def test_una_razon_real_del_motor_llega_al_usuario():
    """Cuando reglas.py se implemente, su explicación tiene que verse."""
    from app.recursos import ResultadoBusqueda
    from app.schema import Clasificacion

    clasificacion = Clasificacion(
        color="naranja",
        tiempo_maximo_min=10,
        motivo_clasificacion="dolor severo",
        discriminador_disparador="dolor muy fuerte",
        regla_id="TEST-01",
        traza=["TEST-01"],
        tipo_recurso_sugerido="guardia",
        version_ruleset="0.0.0",
    )
    mensaje = orquestador.armar_mensaje_resultado(
        clasificacion, ResultadoBusqueda(ciudad_persona="Oro Verde")
    )

    assert "Por qué te digo esto" in mensaje
    assert "dolor muy fuerte" in mensaje
    assert "dolor severo" in mensaje


def test_sin_razon_presentable_se_omite_la_seccion_entera():
    """Una explicación genérica no aporta y le corre hacia abajo lo importante."""
    from app.recursos import ResultadoBusqueda
    from app.schema import Clasificacion

    clasificacion = Clasificacion(
        color="amarillo",
        tiempo_maximo_min=60,
        motivo_clasificacion="STUB - sin implementar",
        discriminador_disparador="ninguno",
        regla_id="TEST-02",
        traza=["TEST-02"],
        tipo_recurso_sugerido="centro_urgencias",
        version_ruleset="0.0.0",
    )
    mensaje = orquestador.armar_mensaje_resultado(
        clasificacion, ResultadoBusqueda(ciudad_persona="Oro Verde")
    )

    assert "Por qué te digo esto" not in mensaje
    assert "por el conjunto de lo que me contaste" not in mensaje


def test_el_resultado_lleva_el_motivo_de_consulta(monkeypatch):
    """El popup lo muestra siempre, incluso con DEBUG_MODE apagado."""
    state = session.crear_sesion()
    _mockear(monkeypatch, _respuesta(campos=_ficha_completa(), confianza=0.9))

    r = asyncio.run(orquestador.procesar_turno(state, "listo"))

    assert r.resultado is not None
    assert r.resultado.motivo_consulta == "dolor_toracico"


def test_sin_centros_igual_entrega_el_triaje():
    """Si la base está caída, la persona igual recibe nivel y signos de alarma."""
    from app.recursos import ResultadoBusqueda

    mensaje = orquestador.armar_mensaje_resultado(
        orquestador.reglas.clasificar(FichaClinica()),
        ResultadoBusqueda(ciudad_persona="Oro Verde"),
    )

    assert config.DESCRIPCION_COLOR["amarillo"] in mensaje
    assert "Volvé a consultar" in mensaje
    assert config.DISCLAIMER_FINAL in mensaje


# --------------------------------------------------------------------------- #
# Límites
# --------------------------------------------------------------------------- #


def test_una_conversacion_que_no_llega_a_nada_igual_cierra(monkeypatch):
    """Nunca queda abierta, y nunca pasa del tope de preguntas.

    El cierre puede venir por dos vías —se agotaron las preguntas distintas o
    se llegó a `MAX_PREGUNTAS`— y cuál gana depende de cuántos campos falten.
    Lo que importa es la garantía: termina, clasifica y respeta el tope.
    """
    state = session.crear_sesion()

    campos = [
        CamposExtraidos(
            discriminadores_especificos={f"campo_{i}": True},
            confianza_extraccion=0.2,
        )
        for i in range(config.MAX_TURNOS + 2)
    ]
    _mockear(
        monkeypatch,
        *[_respuesta(campos=c, pregunta="¿Y qué más?", confianza=0.2) for c in campos],
    )

    for i in range(config.MAX_TURNOS):
        r = asyncio.run(orquestador.procesar_turno(state, f"dato {i}"))
        if r.tipo != "pregunta":
            break
    else:
        raise AssertionError("la conversación nunca cerró")

    assert r.tipo == "resultado"
    assert r.resultado is not None
    assert state.preguntas_aclaracion <= config.MAX_PREGUNTAS
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
# Alguien que no viene a consultar nada
# --------------------------------------------------------------------------- #
# Caso real: la persona dijo cuatro veces que estaba bien, contestó los cuatro
# discriminadores de riesgo vital en benigno, y el sistema cerró con amarillo
# "conviene que te vea un médico hoy" por el fallback de información
# incompleta. Un falso positivo, no una precaución.


def _sano() -> DiscriminadoresGenerales:
    return DiscriminadoresGenerales(
        respira_normalmente=True,
        nivel_conciencia="alerta",
        hemorragia_mayor=False,
        riesgo_via_aerea=False,
    )


def test_sin_sintomas_y_sin_banderas_no_se_triajea():
    ficha = FichaClinica(
        edad=24, sin_motivo_consulta=True, discriminadores_generales=_sano()
    )
    assert orquestador.sin_necesidad_de_triaje(ficha)


def test_decir_que_esta_bien_no_alcanza_sin_los_cuatro_discriminadores():
    """Alguien puede decir que está bien sin registrar que le falta el aire."""
    for campo, valor in (
        ("respira_normalmente", None),
        ("nivel_conciencia", None),
        ("hemorragia_mayor", None),
        ("riesgo_via_aerea", None),
    ):
        generales = _sano().model_copy(update={campo: valor})
        ficha = FichaClinica(
            edad=24, sin_motivo_consulta=True, discriminadores_generales=generales
        )
        assert not orquestador.sin_necesidad_de_triaje(ficha), campo


def test_un_discriminador_positivo_manda_sobre_el_estoy_bien():
    generales = _sano().model_copy(update={"respira_normalmente": False})
    ficha = FichaClinica(
        edad=24, sin_motivo_consulta=True, discriminadores_generales=generales
    )
    assert not orquestador.sin_necesidad_de_triaje(ficha)
    # Y sigue siendo bandera roja, que es el camino que tiene que ganar.
    assert orquestador.es_bandera_roja(ficha)


def test_si_menciono_un_motivo_ya_no_aplica():
    ficha = FichaClinica(
        edad=24,
        sin_motivo_consulta=True,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=_sano(),
    )
    assert not orquestador.sin_necesidad_de_triaje(ficha)


def test_sin_decirlo_explicitamente_no_aplica():
    """Un `None` es 'no sabemos', no 'no tiene nada'."""
    ficha = FichaClinica(edad=24, discriminadores_generales=_sano())
    assert not orquestador.sin_necesidad_de_triaje(ficha)


def test_el_turno_cierra_con_tipo_sin_motivo(monkeypatch):
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(
                edad=24,
                sin_motivo_consulta=True,
                discriminadores_generales=_sano(),
            ),
            confianza=0.9,
        ),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "estoy bien, no tengo nada"))

    assert r.tipo == "sin_motivo"
    assert r.resultado is None
    assert "No hace falta que consultes" in r.mensaje
    # Y no se le asigna ningún nivel de urgencia: no hay nada que priorizar.
    for descripcion in config.DESCRIPCION_COLOR.values():
        assert descripcion not in r.mensaje
    assert not session.existe(state.session_id)


def test_la_bandera_roja_gana_sobre_el_estoy_bien(monkeypatch):
    state = session.crear_sesion()
    generales = _sano().model_copy(update={"respira_normalmente": False})
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(
                edad=24, sin_motivo_consulta=True, discriminadores_generales=generales
            ),
            confianza=0.9,
        ),
    )

    r = asyncio.run(orquestador.procesar_turno(state, "estoy bien"))

    assert r.tipo == "derivacion_inmediata"


# --------------------------------------------------------------------------- #
# No repetir preguntas
# --------------------------------------------------------------------------- #


def test_no_se_repite_una_pregunta_ya_hecha():
    """Gemma se traba repitiendo lo mismo si un campo no se completa nunca."""
    state = session.crear_sesion()
    faltantes = ["discriminadores_generales.respira_normalmente"]

    primera = orquestador._elegir_pregunta(state, "¿Te pasa algo?", faltantes)
    segunda = orquestador._elegir_pregunta(state, "¿Te pasa algo?", faltantes)

    assert primera == "¿Te pasa algo?"
    assert segunda != primera


def test_la_comparacion_ignora_tildes_y_puntuacion():
    """'¿Estás bien?' y 'estas bien' son la misma pregunta."""
    state = session.crear_sesion()
    orquestador._elegir_pregunta(state, "¿Estás bien?", [])
    segunda = orquestador._elegir_pregunta(state, "Estas bien", [])
    assert segunda != "Estas bien"


def test_sin_preguntas_nuevas_devuelve_none():
    """Repetir no trae una respuesta distinta: el orquestador cierra."""
    state = session.crear_sesion()

    assert orquestador._elegir_pregunta(state, "¿Y?", []) == "¿Y?"
    # La genérica también se consume en la primera llamada.
    assert orquestador._elegir_pregunta(state, "¿Y?", []) is not None
    assert orquestador._elegir_pregunta(state, "¿Y?", []) is None


def test_una_conversacion_no_puede_repetir_preguntas_indefinidamente(monkeypatch):
    """Cota dura: ninguna pregunta se hace dos veces en toda la conversación."""
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        *[
            _respuesta(
                campos=CamposExtraidos(discriminadores_especificos={f"c{i}": True}),
                pregunta="¿Y qué más?",
                confianza=0.3,
            )
            for i in range(config.MAX_TURNOS + 2)
        ],
    )

    hechas: list[str] = []
    for i in range(config.MAX_TURNOS):
        r = asyncio.run(orquestador.procesar_turno(state, f"no sé {i}"))
        if r.tipo != "pregunta":
            break
        hechas.append(r.mensaje)

    assert len(hechas) == len(set(hechas)), "se repitió una pregunta"


def test_al_repetirse_pasa_al_fallback_del_campo_faltante():
    state = session.crear_sesion()
    faltantes = ["discriminadores_generales.respira_normalmente"]

    orquestador._elegir_pregunta(state, "¿Algo más?", faltantes)
    segunda = orquestador._elegir_pregunta(state, "¿Algo más?", faltantes)

    assert segunda == config.PREGUNTAS_FALLBACK["respira_normalmente"]


# --------------------------------------------------------------------------- #
# Conversación estancada
# --------------------------------------------------------------------------- #


def test_si_dos_turnos_no_aportan_nada_se_cierra_igual(monkeypatch):
    """No todos los datos existen: puede no haberse tomado la temperatura.

    Sin este corte el sistema reformula la misma pregunta hasta agotar el tope,
    que es la conversación que hace sentir que no se escuchó la respuesta.
    """
    state = session.crear_sesion()
    # Primer turno aporta; los siguientes no aportan nada nuevo.
    _mockear(
        monkeypatch,
        _respuesta(
            campos=CamposExtraidos(
                motivo_consulta="cefalea",
                discriminadores_generales=_sano(),
            ),
            pregunta="¿Qué edad tenés?",
            confianza=0.9,
        ),
        _respuesta(pregunta="¿Me decís tu edad?", confianza=0.9),
        _respuesta(pregunta="¿Cuántos años tenés?", confianza=0.9),
    )

    primera = asyncio.run(orquestador.procesar_turno(state, "me duele la cabeza"))
    assert primera.tipo == "pregunta"

    asyncio.run(orquestador.procesar_turno(state, "no sé"))
    tercera = asyncio.run(orquestador.procesar_turno(state, "no te puedo decir"))

    assert tercera.tipo == "resultado"


def test_un_turno_que_aporta_reinicia_el_contador(monkeypatch):
    """Si la conversación avanza, se sigue preguntando con normalidad."""
    state = session.crear_sesion()
    _mockear(
        monkeypatch,
        _respuesta(pregunta="¿Qué te pasa?", confianza=0.9),
        _respuesta(
            campos=CamposExtraidos(motivo_consulta="cefalea"),
            pregunta="¿Y respirás bien?",
            confianza=0.9,
        ),
        _respuesta(pregunta="¿Algo más?", confianza=0.9),
    )

    asyncio.run(orquestador.procesar_turno(state, "hola"))
    assert state.turnos_sin_avance == 1

    asyncio.run(orquestador.procesar_turno(state, "me duele la cabeza"))
    assert state.turnos_sin_avance == 0

    r = asyncio.run(orquestador.procesar_turno(state, "no sé"))
    assert r.tipo == "pregunta"


# --------------------------------------------------------------------------- #
# ficha_suficiente
# --------------------------------------------------------------------------- #


def test_con_los_generales_solos_todavia_falta_informacion():
    """Los discriminadores del flowchart también cuentan.

    Antes bastaba con los generales + motivo + edad. El resultado era que los
    específicos nunca se preguntaban, el motor no tenía evidencia de benignidad
    y **todo salía amarillo**. Ahora la ficha no se da por cerrada hasta que el
    motor deja de pedir campos.
    """
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
    assert not listo
    assert any(f.startswith("discriminadores_especificos.") for f in faltantes)


def test_una_ficha_realmente_completa_cierra():
    ficha = FichaClinica.model_validate(
        {
            **_ficha_completa().model_dump(exclude_none=True),
            "confianza_extraccion": 0.9,
        }
    )
    listo, faltantes = orquestador.ficha_suficiente(ficha)
    assert listo, f"todavía pide: {faltantes}"


def test_la_confianza_baja_bloquea_el_cierre():
    ficha = FichaClinica.model_validate(
        {
            **_ficha_completa().model_dump(exclude_none=True),
            "confianza_extraccion": 0.1,
        }
    )
    listo, _ = orquestador.ficha_suficiente(ficha)
    assert not listo


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
