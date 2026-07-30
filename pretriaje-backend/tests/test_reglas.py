"""Tests del motor de clasificación de severidad.

╔═══════════════════════════════════════════════════════════════════════════╗
║  LA ASIMETRÍA ES OBLIGATORIA                                              ║
║                                                                           ║
║  Un error de clasificación no es un error simétrico:                      ║
║                                                                           ║
║  · Clasificar de MENOS (sub-triaje) puede dejar en casa a alguien que     ║
║    necesitaba una guardia. Es el daño que este sistema tiene que evitar.  ║
║    Cualquier caso que salga por debajo de lo esperado FALLA el build.     ║
║                                                                           ║
║  · Clasificar de MÁS (sobre-triaje) manda a alguien a una consulta que    ║
║    quizás no necesitaba. Es molesto, no peligroso. Se registra como       ║
║    warning y no rompe nada.                                               ║
║                                                                           ║
║  El gate de CI es `pytest -m subtriaje`. La suite entera se corre igual,  ║
║  pero los sobre-triajes se leen en el resumen de warnings.                ║
╚═══════════════════════════════════════════════════════════════════════════╝

Los casos están escritos como viñetas clínicas legibles, con el `por_que` al
lado, para que alguien con formación en salud pueda revisarlos sin leer la
implementación.

⚠️  Los colores esperados NO están validados clínicamente. Son los que el
    equipo consideró razonables para un prototipo. Ver `app/REGLAS.md`.
"""

from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import pytest
from _pytest.outcomes import Failed

from app import config, reglas
from app.reglas import (
    DISCRIMINADORES_POR_MOTIVO,
    ORDEN,
    campos_requeridos,
    clasificar,
    claves_desconocidas,
    mas_urgente,
)
from app.schema import DiscriminadoresGenerales, FichaClinica

# --------------------------------------------------------------------------- #
# Helpers de construcción
# --------------------------------------------------------------------------- #

#: Los cuatro discriminadores de riesgo vital, todos respondidos y negativos.
#: Sin esto, el piso por ignorancia deja todo en amarillo y no se puede testear
#: nada por debajo.
VITALES_OK = {
    "respira_normalmente": True,
    "nivel_conciencia": "alerta",
    "hemorragia_mayor": False,
    "riesgo_via_aerea": False,
}


def f(
    *,
    edad: int | None = None,
    motivo: str | None = None,
    tercero: bool = False,
    esp: dict | None = None,
    vitales: bool = True,
    **generales,
) -> FichaClinica:
    """Arma una ficha. `vitales=False` deja los cuatro críticos en None."""
    campos = dict(VITALES_OK) if vitales else {}
    campos.update(generales)
    return FichaClinica(
        edad=edad,
        motivo_consulta=motivo,
        es_para_tercero=tercero,
        discriminadores_generales=DiscriminadoresGenerales(**campos),
        discriminadores_especificos=dict(esp or {}),
    )


@dataclass(frozen=True)
class Caso:
    nombre: str
    ficha: FichaClinica
    color_esperado: str
    por_que: str
    criticidad: str  # subtriaje | sobretriaje | intermedio


class SobretriajeWarning(UserWarning):
    """El motor clasificó de más. Tolerable, pero conviene mirarlo."""


def _verificar(caso: Caso, obtenido) -> None:
    i_obtenido = ORDEN.index(obtenido.color)
    i_esperado = ORDEN.index(caso.color_esperado)

    if i_obtenido < i_esperado:
        pytest.fail(
            f"SUB-TRIAJE en «{caso.nombre}»: se esperaba {caso.color_esperado} "
            f"y salió {obtenido.color} (menos urgente).\n"
            f"  razón clínica esperada: {caso.por_que}\n"
            f"  regla que ganó: {obtenido.regla_id} — "
            f"{obtenido.discriminador_disparador}\n"
            f"  traza:\n    " + "\n    ".join(obtenido.traza)
        )

    if i_obtenido > i_esperado:
        warnings.warn(
            f"SOBRE-TRIAJE en «{caso.nombre}»: se esperaba "
            f"{caso.color_esperado} y salió {obtenido.color}. "
            f"Ganó {obtenido.regla_id}.",
            SobretriajeWarning,
            stacklevel=2,
        )


# --------------------------------------------------------------------------- #
# Las viñetas
# --------------------------------------------------------------------------- #

CASOS: list[Caso] = [
    # ===================================================================== #
    # Riesgo vital inmediato: alcanza con un discriminador general
    # ===================================================================== #
    Caso(
        nombre="persona encontrada sin responder, no se sabe nada más",
        ficha=f(vitales=False, nivel_conciencia="no_responde"),
        color_esperado="rojo",
        por_que="una sola bandera general de riesgo vital alcanza, sin motivo ni edad",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="no está respirando bien, el resto sin datos",
        ficha=f(vitales=False, respira_normalmente=False),
        color_esperado="rojo",
        por_que="compromiso respiratorio afirmado",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="algo le obstruye la garganta",
        ficha=f(vitales=False, riesgo_via_aerea=True),
        color_esperado="rojo",
        por_que="vía aérea comprometida",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="sangrado abundante que no se detiene",
        ficha=f(vitales=False, hemorragia_mayor=True),
        color_esperado="rojo",
        por_que="hemorragia mayor afirmada",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="no responde, y el flowchart de piel daría azul",
        ficha=f(
            edad=30,
            motivo="lesion_cutanea",
            nivel_conciencia="no_responde",
            esp={"signos_infeccion": False, "lesion_extensa": False},
        ),
        color_esperado="rojo",
        por_que=(
            "la etapa 2 NO puede degradar la etapa 1: el flowchart benigno no "
            "puede tapar una bandera general roja"
        ),
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 70, desorientada, no sabe dónde está",
        ficha=f(edad=70, motivo="otro", nivel_conciencia="confuso"),
        color_esperado="naranja",
        por_que="alteración del sensorio afirmada",
        criticidad="subtriaje",
    ),
    # ===================================================================== #
    # Dolor torácico
    # ===================================================================== #
    Caso(
        nombre="varón 58, dolor opresivo irradiado al brazo, 20 minutos",
        ficha=f(
            edad=58,
            motivo="dolor_toracico",
            dolor_eva=7,
            inicio="subito",
            tiempo_evolucion_horas=0.3,
            esp={
                "dolor_opresivo": True,
                "irradiacion_brazo_mandibula": True,
                "disnea_asociada": False,
            },
        ),
        color_esperado="naranja",
        por_que="patrón coronario típico, tiempo de evolución corto",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 63, dolor de pecho con falta de aire",
        ficha=f(
            edad=63,
            motivo="dolor_toracico",
            dolor_eva=6,
            esp={"disnea_asociada": True, "dolor_opresivo": False},
        ),
        color_esperado="naranja",
        por_que="dolor torácico con disnea, aunque el dolor no sea opresivo",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 49, dolor opresivo y sudoración fría",
        ficha=f(
            edad=49,
            motivo="dolor_toracico",
            esp={
                "dolor_opresivo": True,
                "sudoracion_profusa": True,
                "disnea_asociada": False,
            },
        ),
        color_esperado="naranja",
        por_que="opresión más cortejo vegetativo",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 72 con stent previo, dolor opresivo",
        ficha=f(
            edad=72,
            motivo="dolor_toracico",
            esp={
                "dolor_opresivo": True,
                "antecedente_cardiaco": True,
                "disnea_asociada": False,
            },
        ),
        color_esperado="naranja",
        por_que="antecedente coronario cambia la probabilidad pre-test",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 55, dolor de pecho al subir escaleras",
        ficha=f(
            edad=55,
            motivo="dolor_toracico",
            dolor_eva=4,
            esp={
                "dolor_con_esfuerzo": True,
                "dolor_opresivo": False,
                "disnea_asociada": False,
            },
        ),
        color_esperado="amarillo",
        por_que="patrón de esfuerzo sin dolor en reposo: urgente pero no inmediato",
        criticidad="intermedio",
    ),
    Caso(
        nombre="varón 28, dolor que se reproduce al apretar el pecho",
        ficha=f(
            edad=28,
            motivo="dolor_toracico",
            dolor_eva=3,
            esp={
                "dolor_reproducible_palpacion": True,
                "dolor_opresivo": False,
                "disnea_asociada": False,
            },
        ),
        color_esperado="amarillo",
        por_que=(
            "aunque el cuadro parece de pared torácica, el flowchart de dolor "
            "de pecho tiene piso amarillo: no se manda a nadie a casa con un "
            "dolor de pecho en un prototipo"
        ),
        criticidad="intermedio",
    ),
    Caso(
        nombre="dolor de pecho sin ningún discriminador contestado",
        ficha=f(edad=50, motivo="dolor_toracico"),
        color_esperado="amarillo",
        por_que="piso del motivo más piso por ignorancia",
        criticidad="intermedio",
    ),
    # ===================================================================== #
    # Dificultad respiratoria
    # ===================================================================== #
    Caso(
        nombre="varón 66 con los labios azulados",
        ficha=f(edad=66, motivo="dificultad_respiratoria", esp={"cianosis": True}),
        color_esperado="rojo",
        por_que="cianosis es hipoxemia hasta que se demuestre lo contrario",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 40 que se atragantó comiendo",
        ficha=f(
            edad=40, motivo="dificultad_respiratoria", esp={"cuerpo_extrano": True}
        ),
        color_esperado="rojo",
        por_que="obstrucción de vía aérea por cuerpo extraño",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 34 que no puede terminar una frase",
        ficha=f(
            edad=34,
            motivo="dificultad_respiratoria",
            esp={"dificultad_para_hablar": True},
        ),
        color_esperado="naranja",
        por_que="disnea que interrumpe el habla indica trabajo respiratorio alto",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 22 asmática con el pecho silbando",
        ficha=f(
            edad=22,
            motivo="dificultad_respiratoria",
            esp={
                "sibilancias": True,
                "antecedente_asma_epoc": True,
                "dificultad_para_hablar": False,
            },
        ),
        color_esperado="naranja",
        por_que="crisis en paciente con obstrucción bronquial conocida",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 78, se ahoga acostado y tiene las piernas hinchadas",
        ficha=f(
            edad=78,
            motivo="dificultad_respiratoria",
            esp={
                "empeora_acostado": True,
                "hinchazon_piernas": True,
                "dificultad_para_hablar": False,
            },
        ),
        color_esperado="naranja",
        por_que="ortopnea con edemas: patrón de descompensación cardíaca",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 31, tos con catarro, habla de corrido",
        ficha=f(
            edad=31,
            motivo="dificultad_respiratoria",
            esp={
                "tos_productiva": True,
                "dificultad_para_hablar": False,
                "sibilancias": False,
            },
        ),
        color_esperado="amarillo",
        por_que="cuadro respiratorio bajo sin trabajo respiratorio",
        criticidad="intermedio",
    ),
    # ===================================================================== #
    # Fiebre
    # ===================================================================== #
    Caso(
        nombre="chico 8, fiebre y manchas moradas que no se borran al apretar",
        ficha=f(
            edad=8,
            motivo="fiebre",
            tercero=True,
            temperatura_c=39.2,
            esp={"exantema_petequial": True},
        ),
        color_esperado="rojo",
        por_que="petequias con fiebre: sospecha de sepsis meningocócica",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 24, fiebre y no puede bajar el mentón al pecho",
        ficha=f(
            edad=24,
            motivo="fiebre",
            temperatura_c=39.0,
            esp={"rigidez_nuca": True, "exantema_petequial": False},
        ),
        color_esperado="naranja",
        por_que="signo meníngeo con fiebre",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="bebé de meses con 39 de fiebre",
        ficha=f(edad=0, motivo="fiebre", tercero=True, temperatura_c=39.0),
        color_esperado="naranja",
        por_que=(
            "fiebre en menor de un año: no se puede distinguir un lactante "
            "pequeño de uno grande con la edad en años, así que se aplica el "
            "criterio del más chico"
        ),
        criticidad="subtriaje",
    ),
    Caso(
        nombre="chico 5 con 40,5 de fiebre",
        ficha=f(edad=5, motivo="fiebre", tercero=True, temperatura_c=40.5),
        color_esperado="naranja",
        por_que="umbral pediátrico de fiebre muy alta, más bajo que el de adulto",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 52 en quimioterapia, con fiebre",
        ficha=f(
            edad=52,
            motivo="fiebre",
            temperatura_c=38.2,
            esp={"inmunocomprometido": True},
        ),
        color_esperado="naranja",
        por_que="neutropenia febril: la fiebre baja no descarta nada",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="chico 3 que convulsionó con la fiebre",
        ficha=f(
            edad=3,
            motivo="fiebre",
            tercero=True,
            temperatura_c=39.4,
            esp={"convulsion_febril": True},
        ),
        color_esperado="naranja",
        por_que="convulsión durante el episodio febril",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 40 con 39 de fiebre y tos",
        ficha=f(
            edad=40,
            motivo="fiebre",
            temperatura_c=39.0,
            esp={
                "foco_respiratorio": True,
                "rigidez_nuca": False,
                "exantema_petequial": False,
            },
        ),
        color_esperado="amarillo",
        por_que=(
            "misma temperatura que el bebé del caso anterior, y sin embargo "
            "otro color: el umbral es sensible a la edad"
        ),
        criticidad="intermedio",
    ),
    Caso(
        nombre="chica 3 con 38,5 y mocos",
        ficha=f(
            edad=3,
            motivo="fiebre",
            tercero=True,
            temperatura_c=38.5,
            esp={
                "foco_respiratorio": True,
                "rigidez_nuca": False,
                "exantema_petequial": False,
            },
        ),
        color_esperado="amarillo",
        por_que="fiebre pediátrica con foco claro, sin signos de alarma",
        criticidad="intermedio",
    ),
    Caso(
        nombre="varón 35 con 37,9 y dolor de garganta",
        ficha=f(
            edad=35,
            motivo="fiebre",
            temperatura_c=37.9,
            esp={
                "foco_respiratorio": True,
                "rigidez_nuca": False,
                "exantema_petequial": False,
            },
        ),
        color_esperado="verde",
        por_que="febrícula con foco de vías aéreas altas y sin ningún alarma",
        criticidad="sobretriaje",
    ),
    # ===================================================================== #
    # Dolor abdominal
    # ===================================================================== #
    Caso(
        nombre="varón 45, panza dura como una tabla",
        ficha=f(
            edad=45,
            motivo="dolor_abdominal",
            dolor_eva=7,
            esp={"abdomen_rigido": True},
        ),
        color_esperado="naranja",
        por_que="abdomen en tabla: sospecha de irritación peritoneal",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="varón 60 que vomitó algo como borra de café",
        ficha=f(
            edad=60,
            motivo="dolor_abdominal",
            dolor_eva=5,
            esp={"vomito_con_sangre": True},
        ),
        color_esperado="naranja",
        por_que="hematemesis",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 27 con dolor de panza y posible embarazo",
        ficha=f(
            edad=27,
            motivo="dolor_abdominal",
            dolor_eva=6,
            esp={"embarazo_posible": True, "abdomen_rigido": False},
        ),
        color_esperado="naranja",
        por_que="hay que descartar embarazo ectópico antes que nada",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 19 con dolor abajo a la derecha",
        ficha=f(
            edad=19,
            motivo="dolor_abdominal",
            dolor_eva=6,
            esp={"dolor_fosa_iliaca_derecha": True, "abdomen_rigido": False},
        ),
        color_esperado="amarillo",
        por_que="localización compatible con apendicitis, sin signos de peritonitis",
        criticidad="intermedio",
    ),
    Caso(
        nombre="varón 30 con molestia leve de panza, sin nada más",
        ficha=f(edad=30, motivo="dolor_abdominal", dolor_eva=2),
        color_esperado="verde",
        por_que="dolor leve sin ningún discriminador de alarma positivo",
        criticidad="sobretriaje",
    ),
    # ===================================================================== #
    # Herida y sangrado
    # ===================================================================== #
    Caso(
        nombre="corte en el antebrazo que sigue sangrando con presión",
        ficha=f(
            edad=33,
            motivo="herida_sangrado",
            esp={"sangrado_activo": True, "sangrado_no_para_con_presion": True},
        ),
        color_esperado="rojo",
        por_que="sangrado no controlable con compresión directa",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mordedura de perro en la pierna",
        ficha=f(
            edad=41,
            motivo="herida_sangrado",
            esp={"herida_por_arma_o_mordedura": True, "sangrado_activo": False},
        ),
        color_esperado="naranja",
        por_que="herida contaminada, necesita evaluación y profilaxis",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="raspón en la rodilla que ya no sangra",
        ficha=f(
            edad=25,
            motivo="herida_sangrado",
            esp={
                "sangrado_activo": False,
                "herida_profunda": False,
                "signos_infeccion": False,
            },
        ),
        color_esperado="verde",
        por_que="herida superficial, con los tres discriminadores negados",
        criticidad="sobretriaje",
    ),
    # ===================================================================== #
    # Cefalea
    # ===================================================================== #
    Caso(
        nombre="varón 68, dolor de cabeza y se le desvía la cara",
        ficha=f(
            edad=68,
            motivo="cefalea",
            dolor_eva=6,
            esp={"deficit_neurologico": True},
        ),
        color_esperado="rojo",
        por_que="déficit neurológico focal: ventana de tiempo para ACV",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 44, el peor dolor de cabeza de su vida, empezó de golpe",
        ficha=f(
            edad=44,
            motivo="cefalea",
            dolor_eva=9,
            inicio="subito",
            esp={"peor_dolor_de_la_vida": True, "deficit_neurologico": False},
        ),
        color_esperado="naranja",
        por_que="cefalea en trueno: sospecha de hemorragia subaracnoidea",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mujer 36, la misma jaqueca de siempre, leve",
        ficha=f(
            edad=36,
            motivo="cefalea",
            dolor_eva=3,
            esp={
                "cefalea_habitual": True,
                "deficit_neurologico": False,
                "rigidez_nuca": False,
            },
        ),
        color_esperado="verde",
        por_que="patrón conocido, intensidad baja, sin ningún signo de alarma",
        criticidad="sobretriaje",
    ),
    # ===================================================================== #
    # Lesión cutánea (el motivo que aprovecha la foto)
    # ===================================================================== #
    Caso(
        nombre="se le hincharon los labios y la lengua después de comer",
        ficha=f(
            edad=29,
            motivo="lesion_cutanea",
            esp={"hinchazon_labios_lengua": True},
        ),
        color_esperado="rojo",
        por_que="angioedema con riesgo de compromiso de vía aérea",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="mancha en la pierna que se agranda hora a hora",
        ficha=f(
            edad=57,
            motivo="lesion_cutanea",
            esp={
                "lesion_que_avanza_rapido": True,
                "signos_infeccion": True,
                "lesion_extensa": False,
            },
        ),
        color_esperado="naranja",
        por_que="progresión rápida: sospecha de infección de partes blandas",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="lunar que tiene hace años y no cambió",
        ficha=f(
            edad=35,
            motivo="lesion_cutanea",
            esp={
                "lesion_antigua_sin_cambios": True,
                "signos_infeccion": False,
                "lesion_extensa": False,
            },
        ),
        color_esperado="azul",
        por_que="lesión estable: consulta programada con dermatología",
        criticidad="sobretriaje",
    ),
    Caso(
        nombre="manchita en el brazo, sin fiebre ni dolor",
        ficha=f(
            edad=44,
            motivo="lesion_cutanea",
            esp={"signos_infeccion": False, "lesion_extensa": False},
        ),
        color_esperado="azul",
        por_que="piso del motivo con los dos campos clave negados",
        criticidad="sobretriaje",
    ),
    # ===================================================================== #
    # Umbrales generales sensibles a la edad y a la temperatura
    # ===================================================================== #
    Caso(
        nombre="42 grados de fiebre en un adulto",
        ficha=f(edad=38, motivo="fiebre", temperatura_c=42.0),
        color_esperado="naranja",
        por_que="hipertermia extrema",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="persona mayor encontrada a 34 grados",
        ficha=f(edad=81, motivo="otro", temperatura_c=34.0),
        color_esperado="naranja",
        por_que="hipotermia: se prioriza igual que la hipertermia",
        criticidad="subtriaje",
    ),
    Caso(
        nombre="dolor 9 sobre 10 en cualquier parte",
        ficha=f(edad=47, motivo="otro", dolor_eva=9),
        color_esperado="naranja",
        por_que="el dolor severo es un discriminador general, no del flowchart",
        criticidad="subtriaje",
    ),
    # ===================================================================== #
    # Fallbacks
    # ===================================================================== #
    Caso(
        nombre="ficha completamente vacía",
        ficha=FichaClinica(),
        color_esperado="amarillo",
        por_que="ante la nada absoluta se peca de precavido, nunca de verde",
        criticidad="intermedio",
    ),
    Caso(
        nombre="el modelo inventó el slug 'gripe_fuerte'",
        ficha=f(edad=30, motivo="gripe_fuerte", temperatura_c=38.0),
        color_esperado="amarillo",
        por_que="motivo fuera del ruleset: fallback, sin excepciones",
        criticidad="intermedio",
    ),
    Caso(
        nombre="solo se sabe la edad",
        ficha=f(edad=45, vitales=False),
        color_esperado="amarillo",
        por_que="sin motivo de consulta no hay flowchart que evaluar",
        criticidad="intermedio",
    ),
    Caso(
        nombre="dolor de pecho con claves específicas que el ruleset no conoce",
        ficha=f(
            edad=60,
            motivo="dolor_toracico",
            esp={"disnea": True, "irradia_al_brazo": True, "opresivo": True},
        ),
        color_esperado="amarillo",
        por_que=(
            "el modelo escribió claves fuera del vocabulario: los datos se "
            "pierden, el sistema no explota, y el color no baja"
        ),
        criticidad="intermedio",
    ),
]


def _param(caso: Caso):
    return pytest.param(
        caso, id=caso.nombre, marks=getattr(pytest.mark, caso.criticidad)
    )


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS])
def test_vinetas_clinicas(caso: Caso) -> None:
    _verificar(caso, clasificar(caso.ficha))


def test_el_arnes_distingue_sub_de_sobre_triaje() -> None:
    """El arnés de la asimetría, testeado a sí mismo.

    Si `_verificar` se rompiera y dejara pasar todo, las 50 viñetas seguirían
    en verde y la suite no protegería nada. Este test es el que impide que eso
    pase inadvertido.
    """

    @dataclass(frozen=True)
    class Falsa:
        color: str
        regla_id: str = "TEST"
        discriminador_disparador: str = "-"
        traza: tuple[str, ...] = ()

    caso = Caso(
        nombre="control", ficha=FichaClinica(), color_esperado="naranja",
        por_que="-", criticidad="subtriaje",
    )

    # Menos urgente que lo esperado: tiene que FALLAR.
    with pytest.raises(Failed):
        _verificar(caso, Falsa("amarillo"))

    # Más urgente: warning, no falla.
    with pytest.warns(SobretriajeWarning):
        _verificar(caso, Falsa("rojo"))

    # Exacto: ni falla ni avisa.
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _verificar(caso, Falsa("naranja"))


def test_hay_suficientes_casos() -> None:
    """Mínimo 30 viñetas, con las tres criticidades cubiertas."""
    assert len(CASOS) >= 30
    criticidades = {c.criticidad for c in CASOS}
    assert criticidades == {"subtriaje", "sobretriaje", "intermedio"}
    # El grupo crítico tiene que ser el más grande: es el que bloquea el build.
    criticos = [c for c in CASOS if c.criticidad == "subtriaje"]
    assert len(criticos) >= len(CASOS) / 2


# --------------------------------------------------------------------------- #
# Totalidad: nunca lanza, nunca devuelve None
# --------------------------------------------------------------------------- #

FICHAS_HOSTILES = [
    FichaClinica(),
    FichaClinica(motivo_consulta=""),
    FichaClinica(motivo_consulta="   "),
    FichaClinica(motivo_consulta="Dolor Toracico"),  # mayúsculas: no matchea
    FichaClinica(motivo_consulta="otro"),
    FichaClinica(edad=0),
    FichaClinica(edad=120),
    FichaClinica(es_para_tercero=True),
    # El dict libre puede traer cualquier tipo: strings donde se espera bool,
    # números donde se espera texto, listas anidadas, claves vacías.
    FichaClinica(
        motivo_consulta="dolor_toracico",
        discriminadores_especificos={
            "dolor_opresivo": "si",
            "disnea_asociada": 1,
            "irradiacion_brazo_mandibula": 0,
            "": None,
            "clave_inventada": "cualquier cosa",
        },
    ),
    FichaClinica(
        motivo_consulta="fiebre",
        discriminadores_especificos={"rigidez_nuca": None, "foco_respiratorio": None},
    ),
    FichaClinica(
        motivo_consulta="lesion_cutanea",
        discriminadores_especificos={f"clave_{i}": True for i in range(200)},
    ),
]


@pytest.mark.parametrize("ficha", FICHAS_HOSTILES, ids=range(len(FICHAS_HOSTILES)))
def test_nunca_lanza_ni_devuelve_none(ficha: FichaClinica) -> None:
    """Si el orquestador necesitara un try/except alrededor, el motor está mal."""
    resultado = clasificar(ficha)
    assert resultado is not None
    assert resultado.color in ORDEN
    assert campos_requeridos(ficha) is not None
    assert claves_desconocidas(ficha) is not None


def test_valores_de_tipo_raro_no_matchean_como_true() -> None:
    """`"si"` y `1` no son `True`. Un bool mal tipado no puede disparar reglas."""
    ficha = FichaClinica(
        edad=30,
        motivo_consulta="dolor_toracico",
        discriminadores_generales=DiscriminadoresGenerales(**VITALES_OK),
        discriminadores_especificos={"disnea_asociada": 1, "dolor_opresivo": "si"},
    )
    resultado = clasificar(ficha)
    assert resultado.regla_id not in {"DT-01", "DT-02", "DT-03", "DT-04"}


def test_ficha_vacia_es_amarillo_por_defecto() -> None:
    resultado = clasificar(FichaClinica())
    assert resultado.color == "amarillo"
    assert resultado.clasificacion_por_defecto is True
    assert resultado.regla_id == "FALLBACK-SIN-DATOS"
    assert len(resultado.traza) > 0


def test_los_tres_fallbacks_tienen_su_id() -> None:
    sin_motivo = clasificar(
        FichaClinica(edad=40, discriminadores_generales=DiscriminadoresGenerales(**VITALES_OK))
    )
    assert sin_motivo.regla_id == "FALLBACK-SIN-MOTIVO"

    desconocido = clasificar(FichaClinica(edad=40, motivo_consulta="gripe_fuerte"))
    assert desconocido.regla_id == "FALLBACK-MOTIVO-DESCONOCIDO"

    sin_datos = clasificar(FichaClinica())
    assert sin_datos.regla_id == "FALLBACK-SIN-DATOS"

    for resultado in (sin_motivo, desconocido, sin_datos):
        assert resultado.clasificacion_por_defecto is True
        assert resultado.color not in {"verde", "azul"}
        assert any("FALLBACK" in linea for linea in resultado.traza)


# --------------------------------------------------------------------------- #
# La propiedad estructural: la etapa 2 no puede degradar la etapa 1
# --------------------------------------------------------------------------- #


def test_mas_urgente_es_un_maximo() -> None:
    for a in ORDEN:
        for b in ORDEN:
            resultado = mas_urgente(a, b)
            assert resultado in (a, b)
            assert ORDEN.index(resultado) >= max(ORDEN.index(a), ORDEN.index(b))
    assert mas_urgente("verde", "rojo") == "rojo"
    assert mas_urgente("rojo", "azul") == "rojo"


@pytest.mark.subtriaje
@pytest.mark.parametrize("motivo", list(DISCRIMINADORES_POR_MOTIVO))
def test_bandera_roja_gana_sobre_cualquier_flowchart(motivo: str) -> None:
    """Ningún flowchart, con ninguna combinación de discriminadores negados,
    puede bajar el color de alguien que no responde."""
    negados = {clave: False for clave in DISCRIMINADORES_POR_MOTIVO[motivo]}
    ficha = FichaClinica(
        edad=30,
        motivo_consulta=motivo,
        discriminadores_generales=DiscriminadoresGenerales(
            nivel_conciencia="no_responde",
            respira_normalmente=True,
            hemorragia_mayor=False,
            riesgo_via_aerea=False,
            dolor_eva=0,
            temperatura_c=36.5,
        ),
        discriminadores_especificos=negados,
    )
    assert clasificar(ficha).color == "rojo"


@pytest.mark.subtriaje
@pytest.mark.parametrize("caso", [_param(c) for c in CASOS if c.criticidad != "subtriaje"])
def test_agregar_una_bandera_general_nunca_baja_el_color(caso: Caso) -> None:
    """Tomar cualquier caso y sumarle dolor severo solo puede subir el color."""
    original = clasificar(caso.ficha)
    generales = caso.ficha.discriminadores_generales.model_copy(
        update={"dolor_eva": 9}
    )
    agravado = clasificar(caso.ficha.model_copy(update={"discriminadores_generales": generales}))
    assert ORDEN.index(agravado.color) >= ORDEN.index(original.color)


# --------------------------------------------------------------------------- #
# Independencia del orden del YAML
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS])
def test_orden_del_ruleset_no_cambia_el_resultado(caso: Caso) -> None:
    """Con las reglas del YAML al revés tiene que salir exactamente lo mismo.

    Si esto falla, alguien reintrodujo evaluación "la primera que matchea" y
    el orden del archivo volvió a ser semántico.
    """
    normal = clasificar(caso.ficha)
    invertido = reglas._clasificar(
        caso.ficha, reglas._con_reglas_reordenadas(reglas.RULESET)
    )
    assert invertido.color == normal.color
    assert invertido.regla_id == normal.regla_id
    assert invertido.discriminador_disparador == normal.discriminador_disparador


# --------------------------------------------------------------------------- #
# Pureza, sincronía y velocidad
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS])
def test_pureza(caso: Caso) -> None:
    """Misma ficha, mismo resultado, siempre."""
    assert clasificar(caso.ficha).model_dump() == clasificar(caso.ficha).model_dump()


def test_funciones_publicas_son_sincronicas() -> None:
    import inspect

    for fn in (clasificar, campos_requeridos, claves_desconocidas, mas_urgente):
        assert not inspect.iscoroutinefunction(fn)


def test_clasificar_es_rapido() -> None:
    """Menos de 1 ms: cientos de tests tienen que correr en un segundo, sin
    levantar Ollama ni el servidor."""
    fichas = [c.ficha for c in CASOS]
    inicio = time.perf_counter()
    for _ in range(20):
        for ficha in fichas:
            clasificar(ficha)
    promedio_ms = (time.perf_counter() - inicio) * 1000 / (20 * len(fichas))
    assert promedio_ms < 1.0, f"{promedio_ms:.3f} ms por clasificación"


# --------------------------------------------------------------------------- #
# `None` no es `False`
# --------------------------------------------------------------------------- #

FUENTE_REGLAS = Path(reglas.__file__).read_text(encoding="utf-8")

# `if not X` sobre un valor clínico trata "no sé" igual que "no tiene". Se
# permite solo sobre `isinstance`, que valida la ESTRUCTURA del ruleset y no
# un dato de la persona.
FALSEDAD_IMPLICITA = re.compile(r"\bif\s+not\s+(?!isinstance\b)(\w+)")


def test_sin_falsedad_implicita() -> None:
    encontrados = FALSEDAD_IMPLICITA.findall(FUENTE_REGLAS)
    assert encontrados == [], (
        "Hay `if not <valor>` en reglas.py: eso trata None ('no sé') igual que "
        f"False ('no tiene'). Usá `is None` / `is True` / `is False`. "
        f"Nombres encontrados: {encontrados}"
    )


def test_sin_comparaciones_de_igualdad_con_booleanos() -> None:
    assert re.search(r"==\s*(True|False)\b", FUENTE_REGLAS) is None
    assert re.search(r"!=\s*None\b", FUENTE_REGLAS) is None


def test_sin_reloj_ni_random() -> None:
    """La pureza también se verifica leyendo el módulo."""
    for prohibido in ("datetime.now", "time.time", "random.", "uuid"):
        assert prohibido not in FUENTE_REGLAS


def test_un_none_no_matchea_como_false() -> None:
    """DT-06 pide `dolor_opresivo: false`. Con el campo sin contestar no puede
    matchear: son tres estados, no dos."""
    base = {"dolor_reproducible_palpacion": True}
    sin_saber = clasificar(f(edad=28, motivo="dolor_toracico", esp=base))
    negado = clasificar(
        f(
            edad=28,
            motivo="dolor_toracico",
            esp={**base, "dolor_opresivo": False, "disnea_asociada": False},
        )
    )
    assert "DT-06" not in sin_saber.regla_id
    assert any("DT-06 no-match" in linea for linea in sin_saber.traza)
    assert any("DT-06 MATCH" in linea for linea in negado.traza)


def test_un_none_no_baja_el_color() -> None:
    """Para cada campo crítico: no saberlo nunca da menos urgencia que saber
    que está bien."""
    for campo in ("respira_normalmente", "hemorragia_mayor", "riesgo_via_aerea"):
        completa = f(edad=30, motivo="fiebre", temperatura_c=38.0)
        parcial = f(
            edad=30, motivo="fiebre", temperatura_c=38.0, **{campo: None}
        )
        assert ORDEN.index(clasificar(parcial).color) >= ORDEN.index(
            clasificar(completa).color
        )


# --------------------------------------------------------------------------- #
# Umbrales sensibles a la edad
# --------------------------------------------------------------------------- #


def test_misma_fiebre_distinta_edad_distinto_color() -> None:
    """39 de fiebre en un adulto y en un bebé no son lo mismo."""
    adulto = clasificar(f(edad=40, motivo="fiebre", temperatura_c=39.0))
    bebe = clasificar(f(edad=0, motivo="fiebre", tercero=True, temperatura_c=39.0))
    assert adulto.color != bebe.color
    assert ORDEN.index(bebe.color) > ORDEN.index(adulto.color)


def test_edad_desconocida_aplica_el_umbral_mas_conservador() -> None:
    """Sin edad se evalúan todos los bloques etarios. Como la urgencia solo
    sube, eso es exactamente "el umbral más conservador de los dos"."""
    sin_edad = clasificar(f(motivo="fiebre", temperatura_c=39.0))
    adulto = clasificar(f(edad=40, motivo="fiebre", temperatura_c=39.0))
    bebe = clasificar(f(edad=0, motivo="fiebre", temperatura_c=39.0))
    assert ORDEN.index(sin_edad.color) >= ORDEN.index(adulto.color)
    assert ORDEN.index(sin_edad.color) >= ORDEN.index(bebe.color)


def test_edad_faltante_es_prioritaria() -> None:
    faltantes = campos_requeridos(f(motivo="fiebre", temperatura_c=38.0))
    assert "edad" in faltantes
    # Con los vitales contestados, la edad queda primera.
    assert faltantes[0] == "edad"


def test_consulta_por_tercero_pone_la_edad_al_frente() -> None:
    faltantes = campos_requeridos(FichaClinica(es_para_tercero=True))
    assert faltantes[0] == "edad"


# --------------------------------------------------------------------------- #
# campos_requeridos
# --------------------------------------------------------------------------- #


def test_campos_requeridos_de_una_ficha_vacia() -> None:
    faltantes = campos_requeridos(FichaClinica())
    assert faltantes[0] == "discriminadores_generales.respira_normalmente"
    assert "motivo_consulta" in faltantes
    assert "edad" in faltantes


def test_campos_requeridos_usa_notacion_punteada() -> None:
    for campo in campos_requeridos(FichaClinica(motivo_consulta="dolor_toracico")):
        if "." in campo:
            prefijo, nombre = campo.split(".", 1)
            assert prefijo in {
                "discriminadores_generales",
                "discriminadores_especificos",
            }
            assert nombre != ""
        else:
            assert campo in {"edad", "motivo_consulta", "es_para_tercero"}


def test_ficha_completa_no_requiere_nada() -> None:
    ficha = f(
        edad=35,
        motivo="fiebre",
        temperatura_c=37.5,
        esp={"foco_respiratorio": True, "rigidez_nuca": False},
    )
    assert campos_requeridos(ficha) == []


def test_piso_por_ignorancia_impide_bajar_de_amarillo() -> None:
    """Mientras falte un campo clave, verde y azul son inalcanzables."""
    incompleta = f(
        edad=35, motivo="fiebre", esp={"foco_respiratorio": True}
    )  # falta temperatura_c
    assert campos_requeridos(incompleta) != []
    resultado = clasificar(incompleta)
    assert ORDEN.index(resultado.color) >= ORDEN.index("amarillo")
    assert any("PISO-IGNORANCIA MATCH" in linea for linea in resultado.traza)


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS if c.criticidad == "sobretriaje"])
def test_los_verdes_y_azules_tienen_toda_la_informacion(caso: Caso) -> None:
    """Un nivel bajo requiere evidencia positiva de benignidad, no ausencia de
    alarma: si falta algo, no puede salir verde ni azul."""
    assert campos_requeridos(caso.ficha) == []


# --------------------------------------------------------------------------- #
# Contrato de salida
# --------------------------------------------------------------------------- #

TABLA_NIVELES = {
    "rojo": (0, "guardia_alta_complejidad"),
    "naranja": (10, "guardia"),
    "amarillo": (60, "centro_urgencias"),
    "verde": (120, "caps"),
    "azul": (240, "consulta_programada"),
}


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS])
def test_contrato_de_salida(caso: Caso) -> None:
    resultado = clasificar(caso.ficha)
    tiempo, recurso = TABLA_NIVELES[resultado.color]

    assert resultado.tiempo_maximo_min == tiempo
    assert resultado.tipo_recurso_sugerido == recurso
    assert resultado.version_ruleset == reglas.VERSION_RULESET
    assert resultado.regla_id != ""
    assert resultado.motivo_clasificacion != ""

    # El disparador se le muestra a una persona: nada de nombres de campo ni
    # de expresiones del ruleset.
    disparador = resultado.discriminador_disparador
    assert disparador != ""
    assert "==" not in disparador
    assert "_" not in disparador

    # Sin traza el proyecto pierde su diferencial: nunca puede venir vacía.
    assert len(resultado.traza) > 0
    assert any(resultado.regla_id in linea for linea in resultado.traza)


@pytest.mark.parametrize("caso", [_param(c) for c in CASOS])
def test_toda_clasificacion_trae_signos_de_alarma(caso: Caso) -> None:
    resultado = clasificar(caso.ficha)
    assert len(resultado.signos_alarma_reconsulta) > 0
    assert len(set(resultado.signos_alarma_reconsulta)) == len(
        resultado.signos_alarma_reconsulta
    )


def test_la_traza_muestra_las_tres_etapas() -> None:
    resultado = clasificar(
        f(
            edad=58,
            motivo="dolor_toracico",
            dolor_eva=7,
            esp={
                "dolor_opresivo": True,
                "irradiacion_brazo_mandibula": True,
                "disnea_asociada": False,
            },
        )
    )
    texto = "\n".join(resultado.traza)
    assert "etapa 1" in texto
    assert "etapa 1b" in texto
    assert "etapa 2" in texto
    assert "COMPOSICION" in texto
    assert "PISO-IGNORANCIA" in texto
    # Toda regla evaluada aparece, matchee o no.
    assert "GEN-ROJO-01 no-match" in texto
    assert "DT-01 MATCH" in texto


# --------------------------------------------------------------------------- #
# El registro de discriminadores: el punto de integración frágil
# --------------------------------------------------------------------------- #


def test_el_registro_cubre_los_motivos_del_backend() -> None:
    """`config.MOTIVOS_CONSULTA` es el enum que se le impone a Gemma. Todo
    slug de esa lista tiene que tener flowchart, o el sistema clasifica por
    fallback sin que nadie se entere."""
    assert set(config.MOTIVOS_CONSULTA) <= set(DISCRIMINADORES_POR_MOTIVO)


def test_las_descripciones_son_coloquiales() -> None:
    """No son documentación decorativa: se inyectan en el prompt de Gemma."""
    for motivo, registro in DISCRIMINADORES_POR_MOTIVO.items():
        for clave, descripcion in registro.items():
            assert clave == clave.lower()
            assert " " not in clave
            assert len(descripcion) > 10, f"{motivo}.{clave}"
            # La descripción tiene que explicar la clave, no repetirla con
            # espacios. Si Gemma solo ve el nombre del campo traducido, no
            # sabe qué está afirmando cuando lo pone en true.
            assert descripcion.lower() != clave.replace("_", " "), (
                f"{motivo}.{clave} no explica nada"
            )
            # Sin jerga médica: el prompt se la muestra al modelo para que
            # hable como la persona, no como un manual.
            for jerga in ("disnea", "cefalea", "exantema", "pirexia", "astenia"):
                assert jerga not in descripcion.lower(), f"{motivo}.{clave}"


def test_claves_desconocidas_reporta_la_desalineacion() -> None:
    ficha = FichaClinica(
        motivo_consulta="dolor_toracico",
        discriminadores_especificos={
            "disnea": True,  # el nombre correcto es disnea_asociada
            "dolor_opresivo": True,
        },
    )
    assert claves_desconocidas(ficha) == ["disnea"]
    # Y no revienta nada: la clave simplemente no participa.
    assert clasificar(ficha) is not None


def test_claves_desconocidas_con_motivo_invalido() -> None:
    ficha = FichaClinica(
        motivo_consulta="gripe_fuerte",
        discriminadores_especificos={"fiebre_alta": True},
    )
    assert claves_desconocidas(ficha) == ["fiebre_alta"]
    assert claves_desconocidas(FichaClinica()) == []


def test_toda_condicion_del_ruleset_usa_una_clave_declarada() -> None:
    """Es lo que valida `cargar_ruleset` al importar, verificado desde afuera:
    ninguna regla puede mirar una clave que el prompt no le va a mandar."""
    campos_ficha = reglas.CAMPOS_FICHA
    for slug, flowchart in reglas.RULESET.flowcharts.items():
        declarados = set(flowchart.discriminadores)
        for regla in flowchart.reglas:
            for cond in regla.condiciones:
                assert cond.campo in declarados or cond.campo in campos_ficha, (
                    f"{regla.id} mira '{cond.campo}', que no está declarado en "
                    f"los discriminadores de {slug}"
                )


def test_ids_de_regla_unicos() -> None:
    todas = [
        *reglas.RULESET.generales,
        *reglas.RULESET.pediatricos,
        *(r for fc in reglas.RULESET.flowcharts.values() for r in fc.reglas),
    ]
    ids = [r.id for r in todas]
    assert len(ids) == len(set(ids))


def test_el_ruleset_tiene_version() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", reglas.VERSION_RULESET)


def test_los_tres_motivos_prioritarios_estan_implementados() -> None:
    """Con estos tres el sistema se demuestra de punta a punta."""
    for motivo in ("dolor_toracico", "dificultad_respiratoria", "fiebre"):
        flowchart = reglas.RULESET.flowcharts[motivo]
        assert len(flowchart.reglas) >= 4
        assert len(flowchart.discriminadores) >= 5
    # La variante pediátrica de fiebre.
    ids_fiebre = {r.id for r in reglas.RULESET.flowcharts["fiebre"].reglas}
    assert any(rid.startswith("FB-PED") for rid in ids_fiebre)
    assert len(reglas.RULESET.pediatricos) > 0
