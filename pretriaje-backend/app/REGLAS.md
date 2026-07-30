# Motor de clasificación de severidad — `app/reglas.py`

> ⚠️ **Sin validación clínica.** Los umbrales de `ruleset.yaml` son razonables
> para un prototipo de hackathon y nada más. Ver [Limitaciones](#limitaciones).

Recibe una `FichaClinica` y devuelve un nivel de severidad con su justificación
y el tipo de recurso asistencial que corresponde.

**Es el único componente del sistema que decide urgencia.** El modelo de
lenguaje tiene prohibido opinar sobre eso (ver `prompt.SYSTEM_PROMPT`, punto 2
de las prohibiciones). Si una clasificación está mal, está mal acá.

No habla con el usuario, no llama al modelo, no busca centros de salud, no
redacta mensajes, no diagnostica. Solo prioriza.

---

## Contrato

```python
from app.reglas import campos_requeridos, clasificar

campos_requeridos(ficha) -> list[str]   # qué falta, por prioridad clínica
clasificar(ficha)        -> Clasificacion
```

Ambas son **sincrónicas**, **puras** (misma ficha → mismo resultado, sin
estado mutable, sin reloj, sin random), **totales** (aceptan cualquier
`FichaClinica`, incluida una vacía; nunca lanzan, nunca devuelven `None`) y
**rápidas** (< 1 ms; el ruleset se carga una sola vez al importar, no hay I/O
en runtime).

Si el orquestador necesitara envolver `clasificar()` en un `try/except`, el
motor estaría mal hecho.

### Funciones auxiliares

| | |
|---|---|
| `claves_desconocidas(ficha)` | claves de `discriminadores_especificos` que ninguna regla mira. Si devuelve algo, hay desalineación entre el prompt y el ruleset |
| `mas_urgente(a, b)` | el más urgente de dos colores |
| `DISCRIMINADORES_POR_MOTIVO` | el vocabulario (ver abajo) |
| `MOTIVOS_SOPORTADOS`, `VERSION_RULESET`, `ORDEN` | metadatos |

---

## Arquitectura: tres etapas, la urgencia solo sube

```
ORDEN = ("azul", "verde", "amarillo", "naranja", "rojo")

etapa 1   discriminadores generales (+ bloque pediátrico)  →  PISO
etapa 2   flowchart del motivo de consulta                 →  puede SUBIR
          color = mas_urgente(piso, especifico)
etapa 3   piso por ignorancia: si falta algo, mínimo amarillo
```

La composición por `mas_urgente` hace **estructuralmente imposible** que una
regla específica mal escrita degrade a alguien que ya tenía una bandera
general. El bug de sub-triaje deja de depender del cuidado de quien escribe el
ruleset: no hay ninguna rama del código por la que una regla del flowchart
pueda bajar un color.

Es la propiedad más importante del módulo, y está testeada como propiedad, no
solo con ejemplos: `test_bandera_roja_gana_sobre_cualquier_flowchart` niega
*todos* los discriminadores de *cada* flowchart y verifica que un
`nivel_conciencia="no_responde"` siga saliendo rojo.

### La etapa 3, en detalle

Verde y azul son afirmaciones fuertes ("podés esperar hasta 4 horas") y
requieren evidencia positiva de benignidad, no ausencia de alarma. Mientras
`campos_requeridos()` devuelva algo, el color no puede bajar de amarillo.

Amarillo ante la duda manda a alguien a una consulta que quizás no
necesitaba: costo aceptable. Verde ante la duda puede retener en casa a
alguien que necesitaba una guardia: costo inaceptable.

---

## `None` no es `False`

Son **tres** estados y se distinguen siempre:

```python
if g.hemorragia_mayor is True:      # tiene hemorragia
if g.hemorragia_mayor is False:     # confirmó que no
if g.hemorragia_mayor is None:      # no sabemos
```

En el ruleset, una condición `campo: true` matchea **solo** con `True`. Un
`None` no matchea nunca, y tampoco matchean un `1` o un `"si"` que el modelo
haya escrito mal tipados.

Un `None` en un discriminador crítico hace que el campo aparezca en
`campos_requeridos()`. Si igual se clasifica sin él (porque se agotó el
presupuesto de preguntas), ese desconocimiento **no baja el color**: de eso se
encarga la etapa 3.

Hay un test de lint que lee el módulo y falla si aparece cualquier
`if not <algo>` que no sea sobre `isinstance`:
`test_sin_falsedad_implicita`.

---

## El registro de discriminadores: el punto de integración frágil

`ficha.discriminadores_especificos` es un `dict` libre y nada en el schema
valida sus claves. Si Gemma escribe `"disnea": true` y la regla espera
`"disnea_asociada"`, la regla no matchea nunca, el sistema no explota, y nadie
se entera hasta que alguien mira una clasificación rara.

Por eso **el motor es el dueño del vocabulario**:

```python
from app.reglas import DISCRIMINADORES_POR_MOTIVO

DISCRIMINADORES_POR_MOTIVO["dolor_toracico"]["disnea_asociada"]
# 'le falta el aire junto con el dolor'
```

Sale directamente de la sección `discriminadores:` de cada flowchart en
`ruleset.yaml`, así que no puede desactualizarse respecto de las reglas que lo
usan (`cargar_ruleset` falla al importar si una condición nombra una clave no
declarada).

La descripción coloquial **no es documentación decorativa**: se inyecta en el
schema de la tool `actualizar_ficha` y en el prompt de Gemma, para que el
modelo sepa exactamente qué clave escribir y qué significa. Un solo lugar
define el vocabulario, dos módulos lo consumen.

Para detectar la desalineación en vivo, durante la demo o depurando:

```python
claves_desconocidas(ficha)   # ['disnea']  ← el prompt y el ruleset no coinciden
```

---

## Reglas como datos

El ruleset vive en [`ruleset.yaml`](ruleset.yaml), no en if/else. Alguien con
formación clínica puede revisarlo sin leer Python, se puede mostrar en el
pitch, y agregar motivos no requiere tocar la lógica de evaluación.

```yaml
- id: DT-02
  condiciones:
    disnea_asociada: true
  color: naranja
  disparador: "dolor de pecho con falta de aire"
  especialidad: cardiologia
```

Semántica del evaluador:

- Las condiciones de una regla se evalúan en **AND**.
- `campo: true` matchea solo con `True` exacto; `campo: false`, solo con
  `False`. Un `None` no matchea.
- `{min: N}` / `{max: N}` para numéricos; un `None` no matchea.
- Se evalúan **todas** las reglas y se toma **la más urgente que matcheó**, no
  la primera. **El orden del archivo no cambia el resultado**, y hay un test
  que corre las 50 viñetas con el YAML invertido y exige resultados idénticos
  (`test_orden_del_ruleset_no_cambia_el_resultado`). Ante empate de color gana
  la regla con más condiciones, y si sigue el empate, el id mayor: todo
  determinístico y todo independiente del orden.
- **Toda regla evaluada entra en la traza**, matchee o no, con su resultado.

La regla `*-DEFAULT` de cada flowchart fija el **piso del motivo**: el nivel
mínimo que le corresponde a esa consulta aunque no haya ningún signo de alarma.
Por eso dolor torácico nunca baja de amarillo (ni siquiera con un patrón claro
de pared torácica) y una lesión de piel puede llegar a azul.

### Motivos implementados

Los ocho: `dolor_toracico`, `dificultad_respiratoria`, `fiebre` (con variante
pediátrica), `dolor_abdominal`, `herida_sangrado`, `cefalea`,
`lesion_cutanea`, y `otro` → fallback de amarillo.

---

## Trazabilidad

`traza` y `regla_id` son el "verificable" del proyecto: permiten demostrar en
vivo, frente al jurado, por qué salió naranja.

```
CONTEXTO | edad=58 grupos=cualquiera/adulto motivo='dolor_toracico' especificos=3
== etapa 1: discriminadores generales ==
GEN-ROJO-01 no-match | nivel_conciencia='alerta', tiene que ser 'no_responde'
GEN-NAR-03 no-match | dolor_eva=7, tiene que ser >= 8
GEN-AMA-01 MATCH -> amarillo | dolor moderado
== etapa 1b: bloque pediátrico ==
PED-LAC-NAR-01 n/a | no aplica a este grupo de edad
== etapa 2: flowchart del motivo ==
DT-01 MATCH -> naranja | dolor opresivo que se corre al brazo o a la mandíbula
DT-02 no-match | disnea_asociada=False, tiene que ser exactamente True
COMPOSICION | piso=amarillo especifico=naranja -> naranja (la etapa 2 no puede bajar la etapa 1)
PISO-IGNORANCIA no-match | no falta ningún campo clave
RESULTADO | naranja por DT-01
```

Toda `Clasificacion` devuelta trae la traza no vacía. Está testeado.

---

## Umbrales sensibles a la edad

El mismo dato no significa lo mismo según a quién. El bloque `pediatricos:` del
YAML tiene su propio piso, con reglas para `edad < 12` y otras más estrictas
para `edad < 1`.

**Si `edad is None` se evalúan todos los grupos etarios.** Como la urgencia
solo sube, evaluar de más *es* aplicar el umbral más conservador de los dos
conjuntos, sin duplicar reglas. Por eso `edad` va con prioridad alta en
`campos_requeridos()`.

> **Limitación conocida.** `FichaClinica.edad` es un entero de años (Pydantic
> rechaza `0.2`), así que "lactante" es `edad == 0`, o sea de 0 a 11 meses. No
> se puede distinguir un recién nacido de un bebé de 10 meses, y el ruleset
> trata a todo el grupo con el criterio del más chico. Si alguien cambia
> `edad` a `float`, conviene partir el bloque en `< 0.25` y `< 1`.

---

## Fallbacks — nunca verde, nunca azul

| Situación | Color | `regla_id` |
|---|---|---|
| `motivo_consulta` es `None` | amarillo | `FALLBACK-SIN-MOTIVO` |
| `motivo_consulta` no está en el ruleset | amarillo | `FALLBACK-MOTIVO-DESCONOCIDO` |
| Ficha con todo en `None` | amarillo | `FALLBACK-SIN-DATOS` |
| Falta algún campo clave | ≥ amarillo | `PISO-IGNORANCIA` |
| El motor se rompió | amarillo | `FALLBACK-ERROR-INTERNO` |

En todos, `clasificacion_por_defecto = True` y la traza lo dice explícitamente.
El orquestador puede usar ese flag para ser más explícito con la persona sobre
la incertidumbre.

`motivo_consulta` es `str | None` y no un `Literal`: el modelo *puede* devolver
cualquier cosa. Es una decisión deliberada del backend (preferir un slug raro
antes que perder el turno entero por un `ValidationError`), y deja al motor
como único responsable de manejarlo. Nunca se asume que el slug está en el
registro.

---

## Tabla de niveles

| Color | Nivel | `tiempo_maximo_min` | `tipo_recurso_sugerido` |
|---|---|---|---|
| Rojo | Emergencia | 0 | `guardia_alta_complejidad` |
| Naranja | Muy urgente | 10 | `guardia` |
| Amarillo | Urgente | 60 | `centro_urgencias` |
| Verde | Poco urgente | 120 | `caps` |
| Azul | No urgente | 240 | `consulta_programada` |

En rojo, el número de emergencia lo antepone el orquestador
(`config.MENSAJE_DERIVACION_INMEDIATA`); el motor solo devuelve el color y el
tipo de recurso.

---

## Nota sobre `campos_requeridos()` y `config.PRIORIDAD_CAMPOS`

Son dos listas parecidas con dos consumidores distintos, y **difieren a
propósito en un punto**: acá `edad` va antes que `motivo_consulta`, y en
`config.PRIORIDAD_CAMPOS` va después.

- El motor ordena por **cuánto cambia la clasificación**: la edad determina qué
  bloque de reglas se aplica.
- `config.PRIORIDAD_CAMPOS` ordena para **conversar**: preguntar primero qué le
  pasa a la persona es más natural que arrancar pidiéndole la edad.

Los cuatro discriminadores de riesgo vital van primeros en las dos.

`campos_requeridos()` usa notación punteada
(`"discriminadores_generales.inicio"`); `config.PREGUNTAS_FALLBACK` se indexa
por el nombre pelado. Si alguien conecta las dos, hay que sacarle el prefijo:
`campo.rpartition(".")[2]`.

---

## Tests

```bash
pytest tests/test_reglas.py -q     # 322 tests, ~1,5 s, sin Ollama
pytest -m subtriaje                # el gate que tiene que bloquear el build
```

**La asimetría es obligatoria.** Un error de clasificación no es simétrico:

| Marca | Se espera | Si sale menos urgente |
|---|---|---|
| `subtriaje` | naranja o rojo | **falla el build.** Un falso negativo acá es alguien que se queda en casa cuando debería estar en una guardia |
| `sobretriaje` | verde o azul | warning. Manda a alguien a una consulta que no necesitaba: molesto, no peligroso |
| `intermedio` | amarillo | mismo criterio |

50 viñetas clínicas legibles, cada una con su `por_que` al lado, para que
alguien con formación en salud pueda revisarlas sin leer la implementación.
Arriba de eso, tests de propiedad: pureza, totalidad ante fichas hostiles,
independencia del orden del YAML, la no-degradación de la etapa 2, el lint de
`None` vs `False`, y el propio arnés de la asimetría testeado a sí mismo.

---

## Limitaciones

### Sobre el Manchester

La lógica está **basada en la estructura** del triaje de Manchester
—discriminadores generales más específicos por motivo, cinco niveles con sus
tiempos— y **no es una implementación validada del MTS**.

Los flowcharts del Manchester Triage System son material publicado por el
Manchester Triage Group y tienen derechos: **no se copiaron**. Los
discriminadores, los umbrales y los textos de `ruleset.yaml` son propios.

La forma correcta de describirlo, en el README y en el pitch:

> *"Lógica de priorización basada en la estructura del triaje de Manchester.
> No es una implementación validada del MTS."*

### Sobre la validación clínica

**Los umbrales de `ruleset.yaml` no están validados clínicamente.** Son
razonables para un prototipo y nada más. Fueron escritos por el equipo de
desarrollo, sin revisión profesional.

| Revisó | Rol | Fecha | Versión |
|---|---|---|---|
| — | — | — | — |

*(Nadie todavía. Si alguien del equipo tiene formación en salud, que revise el
ruleset y complete la fila. Si no hay nadie, esta tabla se deja vacía y se dice
abiertamente: en un hackathon de salud el jurado lo va a notar igual, y la
honestidad sobre las limitaciones suma más que el disimulo.)*

`version_ruleset` existe justamente para esto: cuando un profesional revise y
ajuste umbrales, la versión cambia y las clasificaciones viejas quedan
trazables a la versión con la que se hicieron.

### Otras

- El prototipo **no modela embarazo** como campo de la ficha. Cambia varios
  umbrales; hoy solo se captura como discriminador específico de
  `dolor_abdominal` (`embarazo_posible`).
- Los umbrales pediátricos están limitados por la resolución de `edad` (ver
  arriba).
- El motor prioriza; **no diagnostica**. Un `especialidad_sugerida:
  "cardiologia"` significa "a esto lo mira un cardiólogo", no "esto es
  cardíaco".
