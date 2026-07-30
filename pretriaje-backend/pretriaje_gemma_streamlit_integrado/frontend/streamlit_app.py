"""Interfaz conversacional Streamlit para el backend FastAPI de pre-triaje.

Ejecución desde la raíz del proyecto:
    python -m streamlit run frontend/streamlit_app.py

Toda la interfaz vive en la columna central: no se usa `st.sidebar`. El
resultado del triaje se presenta en un diálogo modal sobre la misma ventana.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

import streamlit as st

# Permite ejecutar el archivo desde la raíz con `streamlit run frontend/...`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from frontend.api_client import BackendClient, BackendError  # noqa: E402


BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
TIPOS_QUE_CIERRAN = {"resultado", "derivacion_inmediata", "sin_motivo"}

# Cuántos centros entran en el popup. El mensaje del chat trae la lista
# completa; acá van los dos primeros, que es lo que se puede leer de un vistazo
# en un modal sin que haya que scrollear para tomar la decisión.
CENTROS_EN_POPUP = 2

# Clave con la que se guarda el id de sesión en la URL. Es lo que permite
# limpiar la sesión que quedó colgada cuando se recarga la página.
PARAM_SESION = "sid"

# Localidades que se le ofrecen a la persona. Espejan `config.LOCALIDADES` del
# backend, que es donde están las coordenadas; acá sólo se necesitan los
# nombres, y el frontend no importa módulos del backend a propósito.
LOCALIDADES: tuple[str, ...] = (
    "Oro Verde",
    "Paraná",
    "San Benito",
    "Colonia Avellaneda",
    "Santa Fe",
    "Santo Tomé",
)

# Las únicas con centros cargados en la base.
CIUDADES_CON_COBERTURA: tuple[str, ...] = ("Paraná", "Santa Fe")

# Paleta del triaje. Dos criterios, y en ese orden:
#
# 1. El texto se tiene que leer. El nivel de urgencia es justo lo que no puede
#    quedar ilegible, así que todas las combinaciones superan 4.5:1 (WCAG AA
#    para texto normal). Por eso el amarillo y el naranja llevan texto oscuro:
#    sobre blanco daban 1.9 y 3.1.
# 2. El rojo y el naranja se tienen que distinguir de un vistazo. Hay naranjas
#    oscuros que pasan el contraste con texto blanco (#bf360c da 5.6), pero
#    quedan tan cerca del rojo que se confunden, y esa confusión en una escala
#    de triaje es peor que el problema que resuelve.
COLORES_TRIAJE: dict[str, dict[str, str]] = {
    "rojo": {"fondo": "#c62828", "texto": "#ffffff", "etiqueta": "ROJO"},
    "naranja": {"fondo": "#ef6c00", "texto": "#1a1a1a", "etiqueta": "NARANJA"},
    "amarillo": {"fondo": "#f9a825", "texto": "#1a1a1a", "etiqueta": "AMARILLO"},
    "verde": {"fondo": "#2e7d32", "texto": "#ffffff", "etiqueta": "VERDE"},
    "azul": {"fondo": "#1565c0", "texto": "#ffffff", "etiqueta": "AZUL"},
}

DESCRIPCION_COLOR: dict[str, str] = {
    "rojo": "Atención médica inmediata",
    "naranja": "Te tienen que ver en menos de 10 minutos",
    "amarillo": "Te tienen que ver hoy",
    "verde": "Consulta programada, no parece urgente",
    "azul": "Consulta común, sin apuro",
}

# Etiquetas de la ficha clínica. Los nombres de campo del ruleset son para el
# motor de reglas; el cuadro que lee una persona va en castellano.
ETIQUETAS_GENERALES: tuple[tuple[str, str, str], ...] = (
    ("respira_normalmente", "Respira con normalidad", ""),
    ("nivel_conciencia", "Estado de conciencia", ""),
    ("hemorragia_mayor", "Sangrado importante", ""),
    ("riesgo_via_aerea", "Dificultad para tragar o vía aérea", ""),
    ("dolor_eva", "Dolor (0 a 10)", ""),
    ("temperatura_c", "Temperatura", "°C"),
    ("inicio", "Cómo empezó", ""),
    ("tiempo_evolucion_horas", "Tiempo de evolución", "h"),
)

# Discriminadores específicos de cada flowchart (ver app/ruleset.yaml). Si el
# ruleset suma uno nuevo y no está acá, se muestra el nombre del campo con los
# guiones bajos convertidos en espacios: feo pero legible, nunca en blanco.
ETIQUETAS_ESPECIFICAS: dict[str, str] = {
    # Dolor torácico
    "dolor_opresivo": "Dolor opresivo, como un peso",
    "irradiacion_brazo_mandibula": "El dolor se corre al brazo o la mandíbula",
    "disnea_asociada": "Le falta el aire junto con el dolor",
    "sudoracion_profusa": "Sudoración abundante",
    "dolor_con_esfuerzo": "El dolor aparece al hacer esfuerzo",
    "antecedente_cardiaco": "Antecedentes del corazón",
    "dolor_aumenta_al_respirar": "El dolor aumenta al respirar",
    "dolor_reproducible_palpacion": "El dolor aparece al tocar la zona",
    # Dificultad respiratoria
    "dificultad_para_hablar": "Le cuesta hablar de corrido",
    "cianosis": "Labios o dedos azulados",
    "cuerpo_extrano": "Se atragantó con algo",
    "sibilancias": "Silbido al respirar",
    "antecedente_asma_epoc": "Antecedentes de asma o EPOC",
    "dolor_toracico_asociado": "Dolor de pecho asociado",
    "tos_productiva": "Tos con flema",
    "empeora_acostado": "Empeora al acostarse",
    "hinchazon_piernas": "Piernas hinchadas",
    # Fiebre
    "rigidez_nuca": "Rigidez en la nuca",
    "exantema_petequial": "Manchitas rojas que no desaparecen al apretar",
    "foco_respiratorio": "Síntomas respiratorios",
    "vomitos_persistentes": "Vómitos que no paran",
    "inmunocomprometido": "Defensas bajas",
    "convulsion_febril": "Convulsión con la fiebre",
    "decaimiento_marcado": "Decaimiento importante",
    "irritabilidad_inconsolable": "Llanto que no se calma",
    "rechazo_alimento": "No quiere comer ni tomar",
    "viaje_reciente": "Viaje reciente",
    # Dolor abdominal
    "abdomen_rigido": "Panza dura como una tabla",
    "vomito_con_sangre": "Vómito con sangre",
    "sangre_en_materia_fecal": "Sangre en la materia fecal",
    "dolor_fosa_iliaca_derecha": "Dolor abajo a la derecha",
    "embarazo_posible": "Posibilidad de embarazo",
    "sin_evacuar_ni_gases": "No evacúa ni elimina gases",
    "dolor_irradiado_espalda": "El dolor se corre a la espalda",
    "fiebre_asociada": "Fiebre asociada",
    # Herida y sangrado
    "sangrado_activo": "Sangrado activo",
    "sangrado_no_para_con_presion": "El sangrado no para haciendo presión",
    "herida_profunda": "Herida profunda",
    "herida_por_arma_o_mordedura": "Herida por arma o mordedura",
    "cuerpo_extrano_en_herida": "Quedó algo dentro de la herida",
    "signos_infeccion": "Signos de infección",
    "vacuna_antitetanica_vigente": "Antitetánica al día",
    "herida_en_cara_o_manos": "Herida en cara o manos",
    # Cefalea
    "deficit_neurologico": "Debilidad, hormigueo o dificultad para hablar",
    "peor_dolor_de_la_vida": "El peor dolor de cabeza de su vida",
    "vomitos_en_chorro": "Vómitos en chorro",
    "alteracion_visual": "Cambios en la visión",
    "fotofobia": "Molesta la luz",
    "cefalea_habitual": "Dolor de cabeza habitual en esta persona",
    "traumatismo_reciente": "Golpe reciente en la cabeza",
    # Lesión cutánea
    "hinchazon_labios_lengua": "Hinchazón de labios o lengua",
    "afecta_mucosas": "Afecta boca, ojos o genitales",
    "ampollas": "Ampollas",
    "lesion_que_avanza_rapido": "La lesión avanza rápido",
    "dolor_desproporcionado": "Duele mucho más de lo que aparenta",
    "lesion_extensa": "Lesión extensa",
    "mordedura_o_picadura": "Mordedura o picadura",
    "lesion_antigua_sin_cambios": "Lesión de larga data, sin cambios",
}

# El backend maneja slugs; a la persona se le muestra la versión en castellano
# llano, igual que hace el prompt ("no digas cefalea, decí dolor de cabeza").
MOTIVOS_LEGIBLES: dict[str, str] = {
    "dolor_toracico": "Dolor en el pecho",
    "dificultad_respiratoria": "Dificultad para respirar",
    "dolor_abdominal": "Dolor en la panza",
    "fiebre": "Fiebre",
    "cefalea": "Dolor de cabeza",
    "herida_sangrado": "Herida o sangrado",
    "lesion_cutanea": "Lesión en la piel",
    "otro": "Otro motivo",
}


st.set_page_config(
    page_title="Orientación sanitaria local",
    page_icon="🩺",
    layout="centered",
    # No se usa la barra lateral: toda la interfaz va en la columna central.
    initial_sidebar_state="collapsed",
)


@st.cache_resource
def obtener_cliente(base_url: str) -> BackendClient:
    """Reutiliza la configuración del cliente entre reejecuciones de Streamlit."""

    return BackendClient(base_url=base_url, timeout_s=120.0)


def _inicializar_estado() -> None:
    defaults: dict[str, Any] = {
        "backend_session_id": None,
        "messages": [],
        "conversation_closed": False,
        "latest_debug": None,
        "latest_response_type": None,
        "session_error": None,
        # Color de triaje vigente. Vive en el estado y no en una variable local
        # porque Streamlit reejecuta el script entero en cada interacción.
        "triaje_color": None,
        # Bloque `resultado` del último veredicto, para el popup.
        "resultado_final": None,
        # Se prende al llegar un veredicto y se consume al abrir el modal.
        "abrir_popup": False,
        # Localidad elegida por la persona. Decide en qué ciudad se le buscan
        # centros, así que se pregunta antes de empezar la conversación.
        "ciudad": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# --------------------------------------------------------------------------- #
# Ciclo de vida de la sesión
# --------------------------------------------------------------------------- #
# El estado de Streamlit vive en la conexión websocket, así que una recarga de
# la página lo pierde entero. Sin nada más, la sesión que ya existía en el
# backend queda huérfana: nadie la borra y sigue contando en `sesiones_activas`
# hasta que la barre el TTL, media hora después. Recargando tres veces se ven
# tres sesiones abiertas para una sola persona.
#
# La solución es que el id sobreviva a la recarga fuera del estado de Streamlit.
# Se guarda en la query string, que es lo único que persiste, y al arrancar se
# borra en el backend antes de crear la nueva.


def _recordar_sesion(session_id: str) -> None:
    st.query_params[PARAM_SESION] = session_id


def _olvidar_sesion() -> None:
    if PARAM_SESION in st.query_params:
        del st.query_params[PARAM_SESION]


def _limpiar_sesion_huerfana(cliente: BackendClient) -> None:
    """Borra la sesión que dejó colgada una recarga de la página.

    Sólo actúa cuando el estado de Streamlit está vacío pero la URL todavía
    tiene un id: esa combinación es exactamente el caso de la recarga.
    """
    if st.session_state.backend_session_id is not None:
        return  # la pestaña tiene su sesión viva, no hay nada que limpiar

    huerfana = st.query_params.get(PARAM_SESION)
    if not huerfana:
        return

    try:
        # Best-effort: si el backend se reinició o el TTL ya la barrió, da 404
        # y no hay nada que hacer. Lo que importa es no dejarla viva.
        cliente.borrar_sesion(huerfana)
    except BackendError:
        pass
    finally:
        _olvidar_sesion()


def _crear_sesion_backend(cliente: BackendClient) -> None:
    sesion = cliente.crear_sesion()
    st.session_state.backend_session_id = sesion["session_id"]
    st.session_state.messages = [
        {
            "role": "assistant",
            "content": sesion["mensaje"],
            "tipo": "bienvenida",
            "resultado": None,
        }
    ]
    st.session_state.conversation_closed = False
    st.session_state.latest_debug = None
    st.session_state.latest_response_type = None
    st.session_state.session_error = None
    st.session_state.triaje_color = None
    st.session_state.resultado_final = None
    st.session_state.abrir_popup = False
    _recordar_sesion(sesion["session_id"])


def _nueva_consulta(cliente: BackendClient) -> None:
    """Cierra la consulta en curso y arranca una limpia.

    Se borra la sesión anterior en el backend aunque la conversación no haya
    terminado: si no, cada 'Nueva consulta' a mitad de camino deja una sesión
    abierta con datos de salud adentro hasta que venza el TTL.
    """
    session_id = st.session_state.get("backend_session_id")
    if session_id:
        try:
            cliente.borrar_sesion(session_id)
        except BackendError:
            # El reinicio local no debe quedar bloqueado si el backend ya borró
            # la sesión o está reiniciándose.
            pass

    _olvidar_sesion()

    for key in (
        "backend_session_id",
        "messages",
        "conversation_closed",
        "latest_debug",
        "latest_response_type",
        "session_error",
        "triaje_color",
        "resultado_final",
        "abrir_popup",
    ):
        st.session_state.pop(key, None)
    st.rerun()


# --------------------------------------------------------------------------- #
# Presentación del resultado
# --------------------------------------------------------------------------- #


def _motivo_legible(resultado: dict[str, Any] | None) -> str | None:
    """Motivo de consulta en castellano llano, o None si no se registró."""
    if not resultado:
        return None
    slug = resultado.get("motivo_consulta")
    if not slug:
        return None
    return MOTIVOS_LEGIBLES.get(slug, str(slug).replace("_", " ").capitalize())


def _banner_color(color: str) -> str:
    """HTML del bloque de color. Se reusa en el popup y en la banda del pie."""
    estilo = COLORES_TRIAJE[color]
    return f"""
        <div style="
            background: {estilo['fondo']};
            color: {estilo['texto']};
            padding: 1rem 1.25rem;
            border-radius: 0.5rem;
            text-align: center;
            font-size: 1.1rem;
            line-height: 1.4;
        ">
            <strong style="letter-spacing: 0.08em;">NIVEL {estilo['etiqueta']}</strong>
            <br>{DESCRIPCION_COLOR.get(color, '')}
        </div>
    """


def _contenido_popup(resultado: dict[str, Any] | None, color: str) -> None:
    """Cuerpo del modal: color, motivo, especialista y centros."""
    st.markdown(_banner_color(color), unsafe_allow_html=True)
    st.write("")

    motivo = _motivo_legible(resultado)
    especialista = (resultado or {}).get("especialidad_sugerida")

    columnas = st.columns(2)
    with columnas[0]:
        st.markdown("**Motivo de la consulta**")
        st.write(motivo or "No quedó registrado")
    with columnas[1]:
        st.markdown("**Quién te tiene que ver**")
        st.write(especialista or "Un profesional de la salud")

    # Una bandera roja no trae centros: la indicación es llamar al 107, no
    # elegir a dónde ir. Ofrecer un listado ahí sería contradecir el mensaje.
    if color == "rojo" and not (resultado or {}).get("recursos"):
        st.error(
            "**Llamá al 107 ahora mismo.** Si no podés llamar, andá a la "
            "guardia más cercana o pedile a alguien que te lleve."
        )
        return

    centros = ((resultado or {}).get("recursos") or [])[:CENTROS_EN_POPUP]
    if not centros:
        st.warning(
            "No pude consultar el listado de centros. Acercate al centro de "
            "salud o la guardia que te quede más cerca."
        )
        return

    st.markdown("**Dónde podés ir**")

    ciudad_persona = (resultado or {}).get("ciudad_persona")
    ciudad_buscada = (resultado or {}).get("ciudad_buscada")
    if ciudad_persona and ciudad_buscada and ciudad_persona != ciudad_buscada:
        st.caption(
            f"En {ciudad_buscada}: en {ciudad_persona} no hay centros cargados."
        )

    for centro in centros:
        with st.container(border=True):
            st.markdown(f"**{centro.get('nombre')}** · {centro.get('tipo')}")
            ubicacion = ", ".join(
                p for p in (centro.get("direccion"), centro.get("ciudad")) if p
            )
            if ubicacion:
                st.write(ubicacion)
            if centro.get("telefono"):
                st.write(f"Tel: {centro['telefono']}")
            # No es lo mismo "no sabemos el horario" que "está cerrado".
            if centro.get("horario"):
                st.write(f"Horario: {centro['horario']}")
            else:
                st.caption("Horario no informado: conviene llamar antes de ir.")


@st.dialog("Resultado del pre-triaje", width="large")
def _dialogo_resultado(resultado: dict[str, Any] | None, color: str) -> None:
    _contenido_popup(resultado, color)
    st.caption(
        "Orientación automática, no un diagnóstico. La evaluación final "
        "siempre la hace un profesional de la salud."
    )
    if st.button("Entendido", type="primary", use_container_width=True):
        st.rerun()


def _render_banda_triaje(color: str | None) -> None:
    """Banda fija al pie con el color de severidad del triaje.

    Se muestra recién cuando hay un veredicto: durante la conversación no hay
    color todavía y mostrar uno provisional sería engañoso.

    El color nunca va solo: siempre acompañado del nombre del nivel y de qué
    significa en lenguaje llano. Un color aislado no le dice nada a la persona,
    y en un contexto clínico eso importa.
    """
    if not color:
        return

    estilo = COLORES_TRIAJE.get(color)
    if not estilo:
        return

    descripcion = DESCRIPCION_COLOR.get(color, "")

    st.markdown(
        f"""
        <div style="
            position: fixed;
            left: 0;
            bottom: 0;
            width: 100%;
            z-index: 999;
            background: {estilo['fondo']};
            color: {estilo['texto']};
            padding: 0.85rem 1.25rem;
            box-shadow: 0 -2px 12px rgba(0,0,0,0.25);
            text-align: center;
            font-size: 1.05rem;
            line-height: 1.4;
        ">
            <strong style="letter-spacing: 0.08em;">NIVEL {estilo['etiqueta']}</strong>
            &nbsp;—&nbsp;{descripcion}
        </div>
        <!-- Espaciador: sin esto la banda fija tapa el final de la conversación. -->
        <div style="height: 5rem;"></div>
        """,
        unsafe_allow_html=True,
    )


def _color_de_respuesta(respuesta: dict[str, Any]) -> str | None:
    """Color de triaje de una respuesta del backend, si lo hay.

    Una `derivacion_inmediata` no trae bloque `resultado` —el orquestador corta
    antes de llamar al motor de reglas—, pero clínicamente es lo más urgente
    que existe, así que se muestra como rojo.
    """
    if respuesta.get("tipo") == "derivacion_inmediata":
        return "rojo"

    # `sin_motivo` cierra sin color a propósito: no hay una consulta que
    # priorizar. Pintar la pantalla de azul sugeriría un nivel de urgencia
    # bajo, y lo correcto es que no haya ninguno.
    if respuesta.get("tipo") == "sin_motivo":
        return None

    resultado = respuesta.get("resultado")
    if isinstance(resultado, dict):
        color = resultado.get("color")
        if isinstance(color, str):
            return color
    return None


def _render_resultado_estructurado(resultado: dict[str, Any] | None) -> None:
    if not resultado:
        return

    with st.expander("Ver resultado estructurado del motor de reglas"):
        color = resultado.get("color", "sin dato")
        motivo = resultado.get("motivo_clasificacion", "sin dato")
        disparador = resultado.get("discriminador_disparador", "sin dato")

        st.write(f"**Nivel:** {str(color).upper()}")
        st.write(f"**Motivo:** {motivo}")
        st.write(f"**Dato que disparó la regla:** {disparador}")

        if "STUB" in str(motivo).upper():
            st.warning(
                "El motor de reglas todavía es un STUB y devuelve amarillo fijo. "
                "Esta salida no representa una clasificación clínica implementada. "
                "A la persona no se le muestra este texto: el mensaje del chat usa "
                "una explicación genérica."
            )

        especialidad = resultado.get("especialidad_sugerida")
        if especialidad:
            st.write(f"**Especialista sugerido:** {especialidad}")

        ciudad_persona = resultado.get("ciudad_persona")
        ciudad_buscada = resultado.get("ciudad_buscada")
        if ciudad_persona and ciudad_buscada and ciudad_persona != ciudad_buscada:
            st.info(
                f"La persona está en {ciudad_persona}, que no tiene centros "
                f"cargados en la base. Se buscó en {ciudad_buscada}."
            )

        criterio = resultado.get("criterio_busqueda")
        if criterio:
            st.caption(f"Criterio con el que se encontraron los centros: `{criterio}`")

        recursos = resultado.get("recursos") or []
        if recursos:
            st.write("**Centros devueltos por la base:**")
            st.dataframe(
                [
                    {
                        "Centro": r.get("nombre"),
                        "Tipo": r.get("tipo"),
                        "Ciudad": r.get("ciudad"),
                        "Dirección": r.get("direccion"),
                        "Teléfono": r.get("telefono"),
                        "Horario": r.get("horario") or "no informado",
                    }
                    for r in recursos
                ],
                use_container_width=True,
                hide_index=True,
            )


def _render_mensaje(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        _render_resultado_estructurado(message.get("resultado"))


# --------------------------------------------------------------------------- #
# Bloques técnicos (antes vivían en la barra lateral)
# --------------------------------------------------------------------------- #


def _render_estado_backend(cliente: BackendClient) -> bool:
    """Estado de los servicios, plegado para no competir con la conversación."""
    try:
        health = cliente.health()
    except BackendError as exc:
        st.error(str(exc))
        st.caption(f"Backend configurado: {BACKEND_URL}")
        return False

    centros_ok = bool(health.get("centros_db"))
    resumen = (
        f"Estado técnico · modelo `{health.get('modelo', 'desconocido')}` · "
        f"centros {'conectados' if centros_ok else 'NO disponibles'}"
    )

    with st.expander(resumen):
        st.write(f"**Modelo:** `{health.get('modelo', 'desconocido')}`")
        st.write(f"**Sesiones activas:** {health.get('sesiones_activas', '—')}")
        # La base de centros no bloquea el triaje, pero si está caída no hay
        # sugerencia de dónde ir y conviene que se note.
        if centros_ok:
            st.write("**Base de centros:** conectada")
        else:
            st.warning(
                "Base de centros no disponible. El triaje funciona igual, pero "
                "no va a poder sugerir centros de salud. Levantala con "
                "`centros_salud_db\\start_db.ps1`."
            )
        ciudad = health.get("ciudad_paciente")
        if ciudad:
            st.write(f"**Ubicación simulada:** {ciudad}")
        st.caption(f"API: {BACKEND_URL}")

    return True


def _render_ubicacion() -> None:
    """Le pregunta a la persona dónde está.

    De acá sale a qué ciudad se le buscan centros. Se pregunta explícitamente
    en vez de asumir una ubicación fija: mandar a alguien a una guardia de otra
    ciudad sin haberle preguntado dónde está no es una orientación útil.
    """
    opciones = list(LOCALIDADES)
    actual = st.session_state.get("ciudad")
    indice = opciones.index(actual) if actual in opciones else 0

    elegida = st.selectbox(
        "¿Dónde estás? Lo uso para buscarte centros cerca.",
        opciones,
        index=indice,
        key="selector_ciudad",
    )
    st.session_state.ciudad = elegida

    if elegida not in CIUDADES_CON_COBERTURA:
        st.caption(
            f"En {elegida} no hay centros cargados en la base. Te voy a buscar "
            "en la ciudad con cobertura más cercana."
        )


# Valores de enum tal como los guarda el backend -> cómo se leen en la ficha.
VALORES_LEGIBLES: dict[str, str] = {
    "alerta": "Alerta",
    "somnoliento": "Somnoliento",
    "confuso": "Confuso",
    "no_responde": "No responde",
    "subito": "Súbito, de golpe",
    "gradual": "Gradual, de a poco",
}


def _valor_legible(valor: Any) -> str:
    """Un valor de la ficha en castellano, no en notación de código."""
    if valor is None:
        # "No consultado" y "No" son cosas distintas y no se pueden confundir:
        # un campo sin preguntar no es una respuesta negativa.
        return "No consultado"
    if valor is True:
        return "Sí"
    if valor is False:
        return "No"
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    if isinstance(valor, str):
        return VALORES_LEGIBLES.get(valor, valor)
    return str(valor)


def _filas_ficha(ficha: dict[str, Any]) -> list[dict[str, str]]:
    """Convierte la ficha en filas legibles, agrupadas por sección."""
    generales = ficha.get("discriminadores_generales") or {}
    especificos = ficha.get("discriminadores_especificos") or {}
    filas: list[dict[str, str]] = []

    def agregar(seccion: str, etiqueta: str, valor: Any, unidad: str = "") -> None:
        texto = _valor_legible(valor)
        if unidad and valor is not None:
            texto = f"{texto} {unidad}"
        filas.append({"Sección": seccion, "Dato": etiqueta, "Respuesta": texto})

    agregar("Paciente", "Edad", ficha.get("edad"), "años")
    agregar("Paciente", "Consulta por otra persona", ficha.get("es_para_tercero"))
    agregar(
        "Paciente",
        "Motivo de consulta",
        MOTIVOS_LEGIBLES.get(
            ficha.get("motivo_consulta") or "", ficha.get("motivo_consulta")
        ),
    )

    for campo, etiqueta, unidad in ETIQUETAS_GENERALES:
        agregar("Evaluación inicial", etiqueta, generales.get(campo), unidad)

    for clave, valor in sorted(especificos.items()):
        agregar("Detalles del cuadro", _etiqueta_especifico(clave), valor)

    return filas


def _etiqueta_campo(clave: str) -> str:
    """Nombre de campo del ruleset -> texto legible por una persona.

    Mira las dos tablas: un faltante puede ser tanto un discriminador general
    (`temperatura_c`) como uno específico del flowchart.
    """
    clave = clave.rsplit(".", 1)[-1]
    for campo, etiqueta, _ in ETIQUETAS_GENERALES:
        if campo == clave:
            return etiqueta
    if clave in ETIQUETAS_ESPECIFICAS:
        return ETIQUETAS_ESPECIFICAS[clave]
    if clave in ("edad", "motivo_consulta", "es_para_tercero"):
        return {"edad": "Edad", "motivo_consulta": "Motivo de consulta",
                "es_para_tercero": "Consulta por otra persona"}[clave]
    return clave.replace("_", " ").capitalize()


# Alias histórico: el nombre viejo hablaba sólo de los específicos.
_etiqueta_especifico = _etiqueta_campo


def _tabla_markdown(filas: list[dict[str, str]]) -> str:
    """Tabla de texto, no un grid sobre canvas.

    `st.dataframe` dibuja sobre canvas: se ve bien pero el texto no queda en el
    documento, así que no se puede copiar, ni imprimir bien, ni leer con un
    lector de pantalla. Para una ficha que después lee un profesional, que sea
    texto importa más que el estilo del grid.
    """
    lineas = ["| Dato | Respuesta |", "| --- | --- |"]
    for fila in filas:
        dato = fila["Dato"].replace("|", "\\|")
        respuesta = fila["Respuesta"].replace("|", "\\|")
        lineas.append(f"| {dato} | {respuesta} |")
    return "\n".join(lineas)


def _render_ficha_clinica() -> None:
    """Ficha en formato de cuadro, para que la pueda leer un profesional."""
    debug = st.session_state.latest_debug
    if not debug or not isinstance(debug, dict):
        return

    ficha = debug.get("ficha")
    if not isinstance(ficha, dict):
        return

    with st.expander("Ficha clínica de la consulta"):
        st.caption(
            "Resumen de lo que respondió la persona, estructurado por Gemma. "
            "El motor determinístico decide la urgencia a partir de esto."
        )

        filas = _filas_ficha(ficha)
        for seccion in ("Paciente", "Evaluación inicial", "Detalles del cuadro"):
            de_la_seccion = [f for f in filas if f["Sección"] == seccion]
            if not de_la_seccion:
                continue
            st.markdown(f"##### {seccion}")
            st.markdown(_tabla_markdown(de_la_seccion))
            st.write("")

        relato = (ficha.get("relato_libre") or "").strip()
        if relato:
            st.markdown("##### Relato de la persona, en sus palabras")
            st.text(relato)

        columnas = st.columns(2)
        columnas[0].metric("Turno", debug.get("turno", "—"))
        confianza = debug.get("confianza")
        columnas[1].metric(
            "Confianza de extracción",
            f"{confianza:.0%}" if isinstance(confianza, (int, float)) else "—",
        )

        faltantes = debug.get("campos_faltantes") or []
        if faltantes:
            st.caption(
                "Todavía sin consultar: "
                + ", ".join(_etiqueta_campo(f) for f in faltantes)
            )


def _procesar_mensaje(cliente: BackendClient, texto: str) -> None:
    st.session_state.messages.append(
        {"role": "user", "content": texto, "tipo": "usuario", "resultado": None}
    )

    try:
        respuesta = cliente.enviar_mensaje(
            session_id=st.session_state.backend_session_id,
            texto=texto,
            ciudad=st.session_state.get("ciudad"),
        )
    except BackendError as exc:
        if exc.status_code == 404:
            st.session_state.conversation_closed = True
            mensaje = (
                "La sesión venció o el backend se reinició. Iniciá una nueva "
                "consulta para continuar."
            )
        else:
            mensaje = str(exc)

        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": mensaje,
                "tipo": "error_cliente",
                "resultado": None,
            }
        )
        st.session_state.session_error = mensaje
        return

    tipo = respuesta.get("tipo")
    st.session_state.latest_response_type = tipo
    st.session_state.latest_debug = respuesta.get("debug")
    st.session_state.session_error = None
    st.session_state.triaje_color = _color_de_respuesta(respuesta)
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": respuesta["mensaje"],
            "tipo": tipo,
            "resultado": respuesta.get("resultado"),
        }
    )

    if tipo in TIPOS_QUE_CIERRAN:
        st.session_state.conversation_closed = True
        st.session_state.resultado_final = respuesta.get("resultado")
        st.session_state.abrir_popup = True
        # El backend ya borró la sesión al cerrar: no hay nada que limpiar y el
        # id de la URL ya no apunta a nada.
        _olvidar_sesion()


# --------------------------------------------------------------------------- #
# Aplicación
# --------------------------------------------------------------------------- #

_inicializar_estado()
cliente = obtener_cliente(BACKEND_URL)

st.title("🩺 Orientación sanitaria local")
st.caption("Chat Streamlit conectado al backend FastAPI y a Gemma 4 mediante Ollama")

st.warning(
    "Prototipo de hackathon sin validación clínica. Usar solamente con casos "
    "sintéticos. No diagnostica ni reemplaza una consulta profesional."
)

backend_ok = _render_estado_backend(cliente)

# Ubicación: define en qué ciudad se le buscan centros, así que se pregunta
# antes de empezar. Se puede cambiar en cualquier momento de la conversación.
_render_ubicacion()

if backend_ok:
    # Antes de crear nada: si la página se recargó, hay una sesión colgada.
    _limpiar_sesion_huerfana(cliente)

if st.session_state.backend_session_id is None and backend_ok:
    try:
        _crear_sesion_backend(cliente)
    except BackendError as exc:
        st.session_state.session_error = str(exc)

if st.session_state.session_error and not st.session_state.messages:
    st.error(st.session_state.session_error)
    st.info(
        "Primero iniciá Ollama y el backend FastAPI. Después recargá esta página."
    )

for message in st.session_state.messages:
    _render_mensaje(message)

if st.session_state.conversation_closed:
    st.info("La conversación terminó. Usá “Nueva consulta” para comenzar otra.")
elif backend_ok and st.session_state.backend_session_id:
    texto_usuario = st.chat_input("Escribí tu respuesta…")
    if texto_usuario and texto_usuario.strip():
        with st.spinner("Gemma está estructurando el relato…"):
            _procesar_mensaje(cliente, texto_usuario.strip())
        st.rerun()

# Acciones, ahora en la columna central en vez de la barra lateral.
acciones = st.columns(2)
with acciones[0]:
    if st.button("Nueva consulta", use_container_width=True, type="primary"):
        _nueva_consulta(cliente)
with acciones[1]:
    if st.session_state.conversation_closed and st.session_state.triaje_color:
        if st.button("Ver resultado", use_container_width=True):
            st.session_state.abrir_popup = True
            st.rerun()

_render_ficha_clinica()

# La banda va última para que quede al pie de la página.
_render_banda_triaje(st.session_state.triaje_color)

# El popup se abre una sola vez por veredicto: la bandera se consume acá. Si no
# se consumiera, cerrar el modal dispara un rerun, la bandera seguiría prendida
# y el diálogo volvería a abrirse solo. Para reabrirlo está "Ver resultado".
if st.session_state.pop("abrir_popup", False) and st.session_state.triaje_color:
    _dialogo_resultado(
        st.session_state.resultado_final, st.session_state.triaje_color
    )
