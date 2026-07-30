"""Motor de clasificación de severidad.

Es el único componente del sistema autorizado a decidir urgencia. Recibe una
`FichaClinica` y devuelve un nivel de severidad con su justificación y el tipo
de recurso asistencial que corresponde. No habla con el usuario, no llama al
modelo de lenguaje, no busca centros de salud, no diagnostica. Solo prioriza.

Lógica de priorización basada en la **estructura** del triaje de Manchester
(discriminadores generales + flowchart por motivo, cinco niveles con sus
tiempos). **No es una implementación validada del MTS**: los flowcharts del
Manchester Triage System son material con derechos de su grupo autor y no se
copiaron. Los discriminadores y umbrales de `ruleset.yaml` son propios y **no
tienen validación clínica**. Ver `app/REGLAS.md`.

--------------------------------------------------------------------------- #
Arquitectura: tres etapas, la urgencia solo sube
--------------------------------------------------------------------------- #

1. **Discriminadores generales** (+ bloque pediátrico). Aplican a cualquier
   motivo y fijan un PISO de urgencia.
2. **Flowchart del motivo de consulta.** Puede SUBIR la urgencia, nunca
   bajarla: el color final es `mas_urgente(piso, especifico)`.
3. **Piso por ignorancia.** Si falta algún campo clave, el color no puede
   quedar por debajo de amarillo. Los niveles bajos requieren evidencia
   positiva de benignidad, no ausencia de alarma.

La composición por `mas_urgente` es lo que hace **estructuralmente imposible**
que una regla específica mal escrita degrade a alguien que ya tenía una
bandera general. El bug de sub-triaje deja de depender del cuidado de quien
escribe el ruleset.

--------------------------------------------------------------------------- #
`None` no es `False`
--------------------------------------------------------------------------- #

Son tres estados distintos y se distinguen siempre: `is True` (tiene),
`is False` (confirmó que no), `is None` (no sabemos). Nunca se usa la
falsedad de Python sobre un valor clínico — hay un test que lo verifica
leyendo este archivo (`test_reglas.py::test_sin_falsedad_implicita`).

--------------------------------------------------------------------------- #
Propiedades garantizadas
--------------------------------------------------------------------------- #

- **Sincrónico.** El ruleset se carga una sola vez, al importar el módulo.
- **Puro.** Misma ficha → mismo resultado. Sin estado mutable, sin random,
  sin reloj.
- **Total.** Acepta cualquier `FichaClinica`, incluida una recién creada con
  todo en `None`. Nunca lanza, nunca devuelve `None`.
- **Rápido.** Menos de 1 ms por clasificación.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import yaml

from app.schema import Clasificacion, DiscriminadoresGenerales, FichaClinica

logger = logging.getLogger(__name__)

__all__ = [
    "DISCRIMINADORES_POR_MOTIVO",
    "MOTIVOS_SOPORTADOS",
    "ORDEN",
    "VERSION_RULESET",
    "campos_requeridos",
    "clasificar",
    "claves_desconocidas",
    "mas_urgente",
]


# --------------------------------------------------------------------------- #
# Escala
# --------------------------------------------------------------------------- #

#: Niveles ordenados de menor a mayor urgencia.
ORDEN: Final[tuple[str, ...]] = ("azul", "verde", "amarillo", "naranja", "rojo")


def mas_urgente(a: str, b: str) -> str:
    """El más urgente de dos colores. Es la operación que hace que la etapa 2
    no pueda degradar lo que decidió la etapa 1."""
    return max(a, b, key=ORDEN.index)


#: Ventana de atención recomendada por color, en minutos.
TIEMPO_MAXIMO_MIN: Final[Mapping[str, int]] = {
    "rojo": 0,
    "naranja": 10,
    "amarillo": 60,
    "verde": 120,
    "azul": 240,
}

#: Tipo de efector por color.
TIPO_RECURSO: Final[Mapping[str, str]] = {
    "rojo": "guardia_alta_complejidad",
    "naranja": "guardia",
    "amarillo": "centro_urgencias",
    "verde": "caps",
    "azul": "consulta_programada",
}

#: Cómo se nombra cada nivel en `motivo_clasificacion`.
NIVEL: Final[Mapping[str, str]] = {
    "rojo": "Emergencia, atención inmediata",
    "naranja": "Muy urgente, atención dentro de los 10 minutos",
    "amarillo": "Urgente, atención dentro de la hora",
    "verde": "Poco urgente, atención dentro de las 2 horas",
    "azul": "No urgente, atención dentro de las 4 horas",
}

#: Color de arranque de cada etapa, antes de que matchee ninguna regla.
COLOR_BASE: Final[str] = "azul"
#: Piso que imponen la ignorancia y los tres fallbacks. Nunca verde ni azul:
#: mandar a una consulta que no hacía falta es un costo aceptable; retener en
#: casa a alguien que necesitaba una guardia, no.
COLOR_PISO_MINIMO: Final[str] = "amarillo"

#: Grupos etarios de `aplica_a`.
GRUPO_CUALQUIERA: Final[str] = "cualquiera"
GRUPOS_VALIDOS: Final[frozenset[str]] = frozenset(
    {GRUPO_CUALQUIERA, "adulto", "pediatrico", "lactante"}
)

#: Discriminadores generales que definen riesgo vital, en orden de prioridad
#: para preguntar. Espeja `config.CAMPOS_CRITICOS_GENERALES`, pero el motor no
#: importa `config` para no acoplarse al orquestador.
CAMPOS_CRITICOS: Final[tuple[str, ...]] = (
    "respira_normalmente",
    "nivel_conciencia",
    "hemorragia_mayor",
    "riesgo_via_aerea",
)

#: Campos de la ficha que una condición puede nombrar además de los
#: discriminadores específicos declarados por cada flowchart.
CAMPOS_FICHA: Final[frozenset[str]] = frozenset(
    set(DiscriminadoresGenerales.model_fields)
    | {"edad", "es_para_tercero", "motivo_consulta"}
)


# --------------------------------------------------------------------------- #
# Representación del ruleset (inmutable)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Condicion:
    """Una condición atómica de una regla, ya normalizada."""

    campo: str
    clase: str  # "igual" | "en" | "rango"
    esperado: Any = None
    minimo: float | None = None
    maximo: float | None = None
    descripcion: str = ""


@dataclass(frozen=True, slots=True)
class Regla:
    """Una regla del ruleset. Todas sus condiciones se evalúan en AND."""

    id: str
    color: str
    disparador: str
    condiciones: tuple[Condicion, ...] = ()
    aplica_a: str = GRUPO_CUALQUIERA
    especialidad: str | None = None
    signos_alarma: tuple[str, ...] = ()
    por_defecto: bool = False


@dataclass(frozen=True, slots=True)
class Flowchart:
    """El bloque de un motivo de consulta: vocabulario + reglas."""

    slug: str
    descripcion: str
    reglas: tuple[Regla, ...]
    discriminadores: Mapping[str, str]
    campos_clave: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Ruleset:
    """El archivo entero, ya validado."""

    version: str
    generales: tuple[Regla, ...]
    pediatricos: tuple[Regla, ...]
    flowcharts: Mapping[str, Flowchart]
    signos_alarma_generales: tuple[str, ...]


class RulesetInvalido(ValueError):
    """El YAML no cumple el contrato. Se levanta al importar, nunca en runtime."""


# --------------------------------------------------------------------------- #
# Carga y validación del ruleset
# --------------------------------------------------------------------------- #

RUTA_RULESET: Final[Path] = Path(__file__).resolve().parent / "ruleset.yaml"


def _texto(valor: Any, contexto: str) -> str:
    if isinstance(valor, str) and valor.strip() != "":
        return valor.strip()
    raise RulesetInvalido(f"{contexto}: se esperaba un texto no vacío, vino {valor!r}")


def _numero(valor: Any, contexto: str) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise RulesetInvalido(f"{contexto}: se esperaba un número, vino {valor!r}")
    return float(valor)


def _armar_condicion(campo: str, spec: Any, contexto: str) -> Condicion:
    """Traduce una entrada de `condiciones:` del YAML a una `Condicion`."""
    if isinstance(spec, bool):
        return Condicion(
            campo=campo,
            clase="igual",
            esperado=spec,
            descripcion=f"tiene que ser exactamente {spec}",
        )

    if isinstance(spec, str):
        return Condicion(
            campo=campo,
            clase="igual",
            esperado=spec,
            descripcion=f"tiene que ser '{spec}'",
        )

    if isinstance(spec, list):
        opciones = tuple(_texto(o, contexto) for o in spec)
        return Condicion(
            campo=campo,
            clase="en",
            esperado=opciones,
            descripcion="tiene que ser alguno de " + ", ".join(opciones),
        )

    if isinstance(spec, dict):
        if "en" in spec:
            opciones = tuple(_texto(o, contexto) for o in spec["en"])
            return Condicion(
                campo=campo,
                clase="en",
                esperado=opciones,
                descripcion="tiene que ser alguno de " + ", ".join(opciones),
            )
        minimo = _numero(spec["min"], contexto) if "min" in spec else None
        maximo = _numero(spec["max"], contexto) if "max" in spec else None
        if minimo is None and maximo is None:
            raise RulesetInvalido(f"{contexto}: rango sin 'min' ni 'max'")
        partes = []
        if minimo is not None:
            partes.append(f">= {minimo:g}")
        if maximo is not None:
            partes.append(f"<= {maximo:g}")
        return Condicion(
            campo=campo,
            clase="rango",
            minimo=minimo,
            maximo=maximo,
            descripcion="tiene que ser " + " y ".join(partes),
        )

    raise RulesetInvalido(f"{contexto}: no sé interpretar la condición {spec!r}")


def _armar_regla(crudo: Any, contexto: str, campos_validos: frozenset[str]) -> Regla:
    if not isinstance(crudo, dict):
        raise RulesetInvalido(f"{contexto}: cada regla tiene que ser un mapa")

    rid = _texto(crudo.get("id"), f"{contexto}.id")
    color = _texto(crudo.get("color"), f"{rid}.color")
    if color not in ORDEN:
        raise RulesetInvalido(f"{rid}: color desconocido {color!r}")

    aplica_a = crudo.get("aplica_a", GRUPO_CUALQUIERA)
    if aplica_a not in GRUPOS_VALIDOS:
        raise RulesetInvalido(f"{rid}: aplica_a desconocido {aplica_a!r}")

    crudas = crudo.get("condiciones") or {}
    if not isinstance(crudas, dict):
        raise RulesetInvalido(f"{rid}: 'condiciones' tiene que ser un mapa")

    condiciones = []
    for campo, spec in crudas.items():
        if campo not in campos_validos:
            raise RulesetInvalido(
                f"{rid}: la condición nombra '{campo}', que no es un campo de la "
                f"ficha ni un discriminador declarado en este flowchart. "
                f"Declaralo en 'discriminadores:' o corregí el nombre."
            )
        condiciones.append(_armar_condicion(campo, spec, f"{rid}.{campo}"))

    signos = crudo.get("signos_alarma") or []
    if not isinstance(signos, list):
        raise RulesetInvalido(f"{rid}: 'signos_alarma' tiene que ser una lista")

    especialidad = crudo.get("especialidad")
    if especialidad is not None:
        especialidad = _texto(especialidad, f"{rid}.especialidad")

    return Regla(
        id=rid,
        color=color,
        disparador=_texto(crudo.get("disparador"), f"{rid}.disparador"),
        condiciones=tuple(condiciones),
        aplica_a=aplica_a,
        especialidad=especialidad,
        signos_alarma=tuple(_texto(s, f"{rid}.signos_alarma") for s in signos),
        por_defecto=bool(crudo.get("por_defecto", False)),
    )


def _validar_campo_clave(campo: str, contexto: str, declarados: Iterable[str]) -> str:
    """Un `campos_clave` tiene que ser un nombre punteado resoluble."""
    if campo.startswith("discriminadores_generales."):
        nombre = campo.split(".", 1)[1]
        if nombre not in DiscriminadoresGenerales.model_fields:
            raise RulesetInvalido(f"{contexto}: no existe el general '{nombre}'")
    elif campo.startswith("discriminadores_especificos."):
        nombre = campo.split(".", 1)[1]
        if nombre not in set(declarados):
            raise RulesetInvalido(
                f"{contexto}: '{nombre}' no está declarado en 'discriminadores:'"
            )
    elif campo not in {"edad", "es_para_tercero", "motivo_consulta"}:
        raise RulesetInvalido(f"{contexto}: campo clave desconocido '{campo}'")
    return campo


def cargar_ruleset(ruta: Path = RUTA_RULESET) -> Ruleset:
    """Lee y valida el YAML. Se llama una sola vez, al importar el módulo.

    Cualquier desalineación del archivo (un color inventado, una condición que
    nombra un discriminador no declarado, un id duplicado) revienta acá, al
    arrancar, y no en medio de una consulta.
    """
    crudo = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    if not isinstance(crudo, dict):
        raise RulesetInvalido(f"{ruta}: el archivo no es un mapa YAML")

    version = _texto(crudo.get("version"), "version")

    generales = tuple(
        _armar_regla(r, f"generales[{i}]", frozenset(CAMPOS_FICHA))
        for i, r in enumerate(crudo.get("generales") or [])
    )
    pediatricos = tuple(
        _armar_regla(r, f"pediatricos[{i}]", frozenset(CAMPOS_FICHA))
        for i, r in enumerate(crudo.get("pediatricos") or [])
    )

    flowcharts: dict[str, Flowchart] = {}
    for slug, bloque in (crudo.get("flowcharts") or {}).items():
        if not isinstance(bloque, dict):
            raise RulesetInvalido(f"flowcharts.{slug}: tiene que ser un mapa")

        discriminadores = bloque.get("discriminadores") or {}
        if not isinstance(discriminadores, dict):
            raise RulesetInvalido(f"{slug}.discriminadores: tiene que ser un mapa")
        for clave, desc in discriminadores.items():
            _texto(desc, f"{slug}.discriminadores.{clave}")

        reglas = tuple(
            _armar_regla(
                r,
                f"{slug}.reglas[{i}]",
                frozenset(CAMPOS_FICHA | set(discriminadores)),
            )
            for i, r in enumerate(bloque.get("reglas") or [])
        )
        if len(reglas) == 0:
            raise RulesetInvalido(f"{slug}: el flowchart no tiene reglas")

        campos_clave = tuple(
            _validar_campo_clave(c, f"{slug}.campos_clave", discriminadores)
            for c in (bloque.get("campos_clave") or [])
        )

        flowcharts[slug] = Flowchart(
            slug=slug,
            descripcion=_texto(bloque.get("descripcion", slug), f"{slug}.descripcion"),
            reglas=reglas,
            discriminadores=dict(discriminadores),
            campos_clave=campos_clave,
        )

    if len(flowcharts) == 0:
        raise RulesetInvalido(f"{ruta}: no hay ningún flowchart definido")

    # Los ids se muestran en la traza y en `regla_id`: tienen que ser únicos
    # para que "por qué salió naranja" tenga una sola respuesta posible.
    vistos: set[str] = set()
    todas = [*generales, *pediatricos, *(r for f in flowcharts.values() for r in f.reglas)]
    for regla in todas:
        if regla.id in vistos:
            raise RulesetInvalido(f"id de regla duplicado: {regla.id}")
        vistos.add(regla.id)

    signos = crudo.get("signos_alarma_generales") or []
    if not isinstance(signos, list):
        raise RulesetInvalido("signos_alarma_generales: tiene que ser una lista")

    return Ruleset(
        version=version,
        generales=generales,
        pediatricos=pediatricos,
        flowcharts=flowcharts,
        signos_alarma_generales=tuple(
            _texto(s, "signos_alarma_generales") for s in signos
        ),
    )


#: El ruleset vivo. Se carga al importar: no hay I/O en runtime.
RULESET: Final[Ruleset] = cargar_ruleset()

VERSION_RULESET: Final[str] = RULESET.version

#: Motivos de consulta con flowchart propio. Cualquier otro slug cae al
#: fallback de amarillo.
MOTIVOS_SOPORTADOS: Final[tuple[str, ...]] = tuple(RULESET.flowcharts)

#: **El motor es el dueño del vocabulario de `discriminadores_especificos`.**
#:
#: Mapea motivo → {clave: descripción coloquial}. La descripción no es
#: documentación decorativa: el backend de recolección la inyecta en el schema
#: de la tool y en el prompt de Gemma, para que el modelo sepa exactamente qué
#: clave escribir y qué significa. Un solo lugar define el vocabulario, dos
#: módulos lo consumen. Si el prompt y el ruleset se desalinean, la regla no
#: matchea nunca y nadie se entera: por eso existe `claves_desconocidas()`.
#:
#: Se construye desde el YAML, así que no puede quedar desactualizado respecto
#: de las reglas que lo usan.
DISCRIMINADORES_POR_MOTIVO: dict[str, dict[str, str]] = {
    slug: dict(fc.discriminadores) for slug, fc in RULESET.flowcharts.items()
}


# --------------------------------------------------------------------------- #
# Reglas sintéticas: fallbacks y pisos
# --------------------------------------------------------------------------- #
# No viven en el YAML porque no son conocimiento clínico revisable, son el
# comportamiento del motor ante la falta de información. Todas amarillo.

_SIN_MOTIVO = Regla(
    id="FALLBACK-SIN-MOTIVO",
    color=COLOR_PISO_MINIMO,
    disparador="todavía no sabemos bien qué es lo que te pasa",
    por_defecto=True,
)
_MOTIVO_DESCONOCIDO = Regla(
    id="FALLBACK-MOTIVO-DESCONOCIDO",
    color=COLOR_PISO_MINIMO,
    disparador="un motivo de consulta que este sistema no sabe evaluar",
    por_defecto=True,
)
_SIN_DATOS = Regla(
    id="FALLBACK-SIN-DATOS",
    color=COLOR_PISO_MINIMO,
    disparador="no hay información suficiente para evaluar la situación",
    por_defecto=True,
)
_PISO_IGNORANCIA = Regla(
    id="PISO-IGNORANCIA",
    color=COLOR_PISO_MINIMO,
    disparador="falta información clave para poder descartar algo urgente",
    por_defecto=True,
)
_ERROR_INTERNO = Regla(
    id="FALLBACK-ERROR-INTERNO",
    color=COLOR_PISO_MINIMO,
    disparador="no pudimos evaluar la información con seguridad",
    por_defecto=True,
)


# --------------------------------------------------------------------------- #
# Lectura de la ficha
# --------------------------------------------------------------------------- #


def _contexto(ficha: FichaClinica) -> dict[str, Any]:
    """Aplana la ficha en un dict `campo -> valor` para evaluar condiciones.

    Los discriminadores generales se escriben ENCIMA de los específicos a
    propósito: si el modelo mete un `dolor_eva` en el dict libre, no puede
    pisar el que está validado por Pydantic.
    """
    contexto: dict[str, Any] = dict(ficha.discriminadores_especificos)
    contexto.update(ficha.discriminadores_generales.model_dump())
    contexto["edad"] = ficha.edad
    contexto["es_para_tercero"] = ficha.es_para_tercero
    contexto["motivo_consulta"] = ficha.motivo_consulta
    return contexto


def _valor_campo(ficha: FichaClinica, campo: str) -> Any:
    """Resuelve un nombre punteado contra la ficha."""
    prefijo, _, nombre = campo.partition(".")
    if prefijo == "discriminadores_generales":
        return getattr(ficha.discriminadores_generales, nombre, None)
    if prefijo == "discriminadores_especificos":
        return ficha.discriminadores_especificos.get(nombre)
    return getattr(ficha, campo, None)


def _grupos_edad(edad: int | None) -> tuple[str, ...]:
    """Qué bloques etarios se evalúan.

    Si la edad es `None` se evalúan TODOS. Como la urgencia solo sube, evaluar
    de más es exactamente "aplicar los umbrales más conservadores de los dos
    conjuntos", sin tener que duplicar reglas.
    """
    if edad is None:
        return (GRUPO_CUALQUIERA, "adulto", "pediatrico", "lactante")
    if edad < 1:
        return (GRUPO_CUALQUIERA, "pediatrico", "lactante")
    if edad < 12:
        return (GRUPO_CUALQUIERA, "pediatrico")
    return (GRUPO_CUALQUIERA, "adulto")


def _ficha_vacia(ficha: FichaClinica) -> bool:
    """True si no hay literalmente nada con que evaluar."""
    if ficha.motivo_consulta is not None:
        return False
    if ficha.edad is not None:
        return False
    if len(ficha.discriminadores_especificos) > 0:
        return False
    valores = ficha.discriminadores_generales.model_dump().values()
    return all(v is None for v in valores)


# --------------------------------------------------------------------------- #
# Evaluación de condiciones
# --------------------------------------------------------------------------- #


def _es_numero(valor: Any) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _evaluar_condicion(cond: Condicion, contexto: Mapping[str, Any]) -> bool:
    """Una condición sola. Un `None` (no sabemos) NUNCA matchea."""
    actual = contexto.get(cond.campo)
    if actual is None:
        return False

    if cond.clase == "igual":
        if isinstance(cond.esperado, bool):
            # Identidad, no igualdad: en Python `True == 1`.
            return actual is cond.esperado
        return not isinstance(actual, bool) and actual == cond.esperado

    if cond.clase == "en":
        return not isinstance(actual, bool) and actual in cond.esperado

    # rango
    if _es_numero(actual) is False:
        return False
    if cond.minimo is not None and actual < cond.minimo:
        return False
    if cond.maximo is not None and actual > cond.maximo:
        return False
    return True


def _evaluar_regla(
    regla: Regla, contexto: Mapping[str, Any]
) -> tuple[bool, str]:
    """(matchea, explicación). La explicación nombra la primera condición que
    falló, que es lo que hace la traza útil para depurar el ruleset."""
    for cond in regla.condiciones:
        if _evaluar_condicion(cond, contexto) is False:
            actual = contexto.get(cond.campo)
            return False, f"{cond.campo}={actual!r}, {cond.descripcion}"
    if len(regla.condiciones) == 0:
        return True, "regla por defecto, siempre aplica"
    return True, "se cumplen todas las condiciones"


def _peso(regla: Regla) -> tuple[int, int, str]:
    """Criterio de desempate, deliberadamente independiente del orden del YAML:
    primero el color más urgente, después la regla más específica (más
    condiciones), y al final el id. Reordenar el archivo no cambia nada."""
    return (ORDEN.index(regla.color), len(regla.condiciones), regla.id)


def _evaluar_grupo(
    reglas: Sequence[Regla],
    contexto: Mapping[str, Any],
    grupos: tuple[str, ...],
    traza: list[str],
) -> Regla | None:
    """Evalúa TODAS las reglas y devuelve la más urgente que matcheó.

    Se evalúan todas (no se corta en la primera) justamente para que el orden
    del archivo no pueda cambiar el resultado. Toda regla evaluada entra en la
    traza, matchee o no.
    """
    ganadora: Regla | None = None
    for regla in reglas:
        if regla.aplica_a not in grupos:
            traza.append(f"{regla.id} n/a | no aplica a este grupo de edad")
            continue
        matchea, detalle = _evaluar_regla(regla, contexto)
        if matchea:
            traza.append(f"{regla.id} MATCH -> {regla.color} | {regla.disparador}")
            if ganadora is None or _peso(regla) > _peso(ganadora):
                ganadora = regla
        else:
            traza.append(f"{regla.id} no-match | {detalle}")
    return ganadora


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #


def campos_requeridos(ficha: FichaClinica) -> list[str]:
    """Qué falta para poder clasificar con confianza, ordenado por prioridad
    clínica (el más informativo primero). Lista vacía = alcanza para clasificar.

    El orquestador usa el primer elemento para decidir qué preguntar. Los
    nombres van en notación punteada: `"discriminadores_generales.inicio"`.

    Mientras esta lista no esté vacía, `clasificar()` no puede devolver un
    nivel por debajo de amarillo (piso por ignorancia, etapa 3).

    Nota de orden: acá `edad` va **antes** que `motivo_consulta`, al revés que
    `config.PRIORIDAD_CAMPOS`. No es un descuido: la edad cambia qué bloque de
    reglas se aplica (los umbrales pediátricos son otros), así que para el
    motor es más informativa. `config.PRIORIDAD_CAMPOS` ordena para
    *conversar*, donde preguntar primero qué pasa es más natural. Son dos
    listas con dos consumidores distintos.
    """
    return _campos_requeridos(ficha, RULESET)


def _campos_requeridos(ficha: FichaClinica, ruleset: Ruleset) -> list[str]:
    faltantes: list[str] = []
    generales = ficha.discriminadores_generales

    for campo in CAMPOS_CRITICOS:
        if getattr(generales, campo) is None:
            faltantes.append(f"discriminadores_generales.{campo}")

    if ficha.edad is None:
        # Consulta por un tercero y sin edad: es lo primero que hay que saber,
        # porque lo más probable es que sea por un chico.
        if ficha.es_para_tercero is True:
            faltantes.insert(0, "edad")
        else:
            faltantes.append("edad")

    if ficha.motivo_consulta is None:
        faltantes.append("motivo_consulta")

    flowchart = ruleset.flowcharts.get(ficha.motivo_consulta or "")
    if flowchart is not None:
        for campo in flowchart.campos_clave:
            if _valor_campo(ficha, campo) is None:
                faltantes.append(campo)

    return faltantes


def claves_desconocidas(ficha: FichaClinica) -> list[str]:
    """Claves en `discriminadores_especificos` que no están en el registro del
    motivo detectado.

    Si esto devuelve algo, hay desalineación entre el prompt y el ruleset: el
    modelo está escribiendo claves que ninguna regla mira, así que el dato se
    pierde en silencio. Es el punto de integración más frágil del sistema y
    esta función es la forma de verlo.
    """
    registro = DISCRIMINADORES_POR_MOTIVO.get(ficha.motivo_consulta or "", {})
    return sorted(c for c in ficha.discriminadores_especificos if c not in registro)


def clasificar(ficha: FichaClinica) -> Clasificacion:
    """Clasifica con la información disponible, completa o no.

    Nunca lanza excepciones. Nunca devuelve `None`.
    """
    try:
        return _clasificar(ficha, RULESET)
    except Exception as exc:  # noqa: BLE001 - el contrato es no propagar nada
        # Si el motor se rompe, la persona igual recibe una respuesta segura.
        # Se loguea el tipo, nunca el contenido de la ficha.
        logger.exception("reglas_error_interno tipo=%s", type(exc).__name__)
        return _armar_clasificacion(
            color=COLOR_PISO_MINIMO,
            ganadora=_ERROR_INTERNO,
            reglas_aportantes=(),
            traza=[
                f"{_ERROR_INTERNO.id} MATCH -> {_ERROR_INTERNO.color} | "
                f"error interno del motor ({type(exc).__name__})"
            ],
            por_defecto=True,
        )


# --------------------------------------------------------------------------- #
# Implementación de las tres etapas
# --------------------------------------------------------------------------- #


def _clasificar(ficha: FichaClinica, ruleset: Ruleset) -> Clasificacion:
    traza: list[str] = []
    contexto = _contexto(ficha)
    grupos = _grupos_edad(ficha.edad)

    traza.append(
        f"CONTEXTO | edad={ficha.edad} grupos={'/'.join(grupos)} "
        f"motivo={ficha.motivo_consulta!r} "
        f"especificos={len(ficha.discriminadores_especificos)}"
    )
    desconocidas = claves_desconocidas(ficha)
    if len(desconocidas) > 0:
        traza.append(
            "AVISO | claves específicas fuera del registro (ignoradas): "
            + ", ".join(desconocidas)
        )

    # --- Etapa 1: discriminadores generales. Fijan el PISO. ---------------- #
    traza.append("== etapa 1: discriminadores generales ==")
    regla_general = _evaluar_grupo(ruleset.generales, contexto, grupos, traza)

    traza.append("== etapa 1b: bloque pediátrico ==")
    regla_pediatrica = _evaluar_grupo(ruleset.pediatricos, contexto, grupos, traza)

    regla_piso = _mejor(regla_general, regla_pediatrica)
    piso = regla_piso.color if regla_piso is not None else COLOR_BASE

    # --- Etapa 2: flowchart del motivo. Solo puede SUBIR. ------------------ #
    traza.append("== etapa 2: flowchart del motivo ==")
    regla_especifica = _evaluar_flowchart(ficha, contexto, grupos, ruleset, traza)
    especifico = regla_especifica.color if regla_especifica is not None else COLOR_BASE

    color = mas_urgente(piso, especifico)
    traza.append(
        f"COMPOSICION | piso={piso} especifico={especifico} -> {color} "
        f"(la etapa 2 no puede bajar la etapa 1)"
    )

    # --- Etapa 3: piso por ignorancia. ------------------------------------- #
    faltantes = _campos_requeridos(ficha, ruleset)
    por_ignorancia = False
    if len(faltantes) > 0:
        subido = mas_urgente(color, COLOR_PISO_MINIMO)
        traza.append(
            f"PISO-IGNORANCIA MATCH -> {COLOR_PISO_MINIMO} | faltan campos: "
            + ", ".join(faltantes)
        )
        por_ignorancia = subido != color
        color = subido
    else:
        traza.append("PISO-IGNORANCIA no-match | no falta ningún campo clave")

    # --- Quién se queda con la explicación --------------------------------- #
    if por_ignorancia:
        ganadora = _PISO_IGNORANCIA
    elif regla_piso is not None and regla_piso.color == color:
        # Ante empate manda la bandera general: es la que habla de riesgo vital.
        ganadora = regla_piso
    elif regla_especifica is not None:
        ganadora = regla_especifica
    else:
        ganadora = regla_piso if regla_piso is not None else _SIN_DATOS

    # Una ficha sin nada adentro se reporta como tal, sea cual sea la regla
    # que técnicamente ganó.
    if _ficha_vacia(ficha):
        traza.append(
            f"{_SIN_DATOS.id} MATCH -> {_SIN_DATOS.color} | la ficha no tiene "
            f"ningún dato cargado"
        )
        ganadora = _SIN_DATOS
        color = mas_urgente(color, _SIN_DATOS.color)

    traza.append(f"RESULTADO | {color} por {ganadora.id}")

    return _armar_clasificacion(
        color=color,
        ganadora=ganadora,
        reglas_aportantes=(regla_especifica, regla_piso),
        traza=traza,
        por_defecto=ganadora.por_defecto,
        signos_base=ruleset.signos_alarma_generales,
        version=ruleset.version,
    )


def _mejor(a: Regla | None, b: Regla | None) -> Regla | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _peso(a) >= _peso(b) else b


def _evaluar_flowchart(
    ficha: FichaClinica,
    contexto: Mapping[str, Any],
    grupos: tuple[str, ...],
    ruleset: Ruleset,
    traza: list[str],
) -> Regla | None:
    """Etapa 2. Devuelve la regla del flowchart que ganó, o el fallback.

    `motivo_consulta` es `str | None` y no un `Literal`: el modelo puede
    devolver cualquier cosa. Es una decisión deliberada del backend (preferir
    un slug raro antes que perder el turno entero por un `ValidationError`), y
    deja al motor como único responsable de manejarlo. Nunca se asume que el
    slug está en el registro.
    """
    motivo = ficha.motivo_consulta
    if motivo is None:
        traza.append(
            f"{_SIN_MOTIVO.id} MATCH -> {_SIN_MOTIVO.color} | "
            f"no hay motivo de consulta cargado"
        )
        return _SIN_MOTIVO

    flowchart = ruleset.flowcharts.get(motivo)
    if flowchart is None:
        traza.append(
            f"{_MOTIVO_DESCONOCIDO.id} MATCH -> {_MOTIVO_DESCONOCIDO.color} | "
            f"el motivo {motivo!r} no tiene flowchart en el ruleset"
        )
        return _MOTIVO_DESCONOCIDO

    return _evaluar_grupo(flowchart.reglas, contexto, grupos, traza)


def _armar_clasificacion(
    *,
    color: str,
    ganadora: Regla,
    reglas_aportantes: tuple[Regla | None, ...],
    traza: list[str],
    por_defecto: bool,
    signos_base: tuple[str, ...] = (),
    version: str = VERSION_RULESET,
) -> Clasificacion:
    """Traduce el veredicto interno al contrato de salida."""
    motivo = NIVEL[color]
    if por_defecto:
        motivo += ", clasificado por precaución con información incompleta"

    especialidad = ganadora.especialidad
    if especialidad is None:
        for aportante in reglas_aportantes:
            if aportante is not None and aportante.especialidad is not None:
                especialidad = aportante.especialidad
                break

    signos: list[str] = []
    for origen in (ganadora, *reglas_aportantes):
        if origen is None:
            continue
        for signo in origen.signos_alarma:
            if signo not in signos:
                signos.append(signo)
    for signo in signos_base:
        if signo not in signos:
            signos.append(signo)

    return Clasificacion(
        color=color,
        tiempo_maximo_min=TIEMPO_MAXIMO_MIN[color],
        motivo_clasificacion=motivo,
        discriminador_disparador=ganadora.disparador,
        regla_id=ganadora.id,
        traza=list(traza),
        tipo_recurso_sugerido=TIPO_RECURSO[color],
        especialidad_sugerida=especialidad,
        signos_alarma_reconsulta=signos,
        version_ruleset=version,
        clasificacion_por_defecto=por_defecto,
    )


# --------------------------------------------------------------------------- #
# Punto de entrada alternativo, para tests
# --------------------------------------------------------------------------- #


def _con_reglas_reordenadas(ruleset: Ruleset, invertir: bool = True) -> Ruleset:
    """Devuelve el mismo ruleset con el orden de las reglas invertido.

    Existe para un único test: el resultado tiene que ser idéntico. Si algún
    día deja de serlo, es que alguien reintrodujo evaluación "primera que
    matchea" y el orden del YAML volvió a ser semántico.
    """
    paso = -1 if invertir else 1
    return replace(
        ruleset,
        generales=ruleset.generales[::paso],
        pediatricos=ruleset.pediatricos[::paso],
        flowcharts={
            slug: replace(fc, reglas=fc.reglas[::paso])
            for slug, fc in ruleset.flowcharts.items()
        },
    )
