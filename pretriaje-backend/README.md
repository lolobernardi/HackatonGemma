# Pre-triaje — Backend de recolección conversacional

> ⚠️ **Prototipo de hackathon. Sin validación clínica.** No usar con pacientes
> reales. No reemplaza la evaluación de un profesional de la salud. Ante una
> emergencia: **107** o **911**.

Backend en FastAPI que conversa con una persona sobre sus síntomas, arma una
ficha clínica estructurada turno a turno, y cuando tiene lo suficiente delega
en un motor de reglas que asigna el nivel de urgencia.

> La priorización está **basada en la estructura del triaje de Manchester**
> —discriminadores generales más específicos por motivo, cinco niveles con sus
> tiempos— y **no es una implementación validada del MTS**. Los flowcharts del
> Manchester Triage System tienen derechos y no se copiaron: los
> discriminadores y umbrales son propios, y **no tienen validación clínica**.
> Ver [`app/REGLAS.md`](app/REGLAS.md).

El modelo de lenguaje es **Gemma 4 corriendo local vía Ollama**. Ningún dato
del paciente sale de la máquina, y el estado de sesión vive solo en memoria: se
borra al cerrar la conversación o a los 30 minutos de inactividad.

---

## Alcance de este módulo

Este repo cubre **solo la recolección conversacional**:

| Pieza | Estado |
|---|---|
| Sesiones en memoria, con borrado y barrido por inactividad | ✅ |
| Armado del prompt (system + ficha parcial + historial + imagen) | ✅ |
| Cliente Ollama, tool calling y parseo | ✅ |
| Merge incremental de la ficha | ✅ |
| Máquina de estados del turno (banderas rojas, límites, errores) | ✅ |
| Motor de clasificación de severidad (`app/reglas.py`) | ✅ — ver [`app/REGLAS.md`](app/REGLAS.md) |
| Búsqueda de centros de salud (`app/recursos.py`) | 🚧 **STUB — otra persona** |

El stub que queda tiene la firma definitiva y un `TODO(equipo)` con el contrato
esperado.

---

## Puesta en marcha

### 1. Ollama y el modelo

```bash
# Instalar Ollama desde https://ollama.com, después:
ollama pull gemma4:e4b
ollama serve          # si no arranca solo como servicio
```

> **Importante para la demo:** el modelo pesa ~9.6 GB. Si Ollama lo descarga de
> memoria entre requests, la primera llamada tarda **minutos** en cargarlo y el
> timeout de 25 s la corta (el usuario recibe un `error_seguro`). Antes de
> demostrar, dejalo caliente:
>
> ```bash
> # Linux/macOS
> OLLAMA_KEEP_ALIVE=-1 ollama serve
> # Windows PowerShell
> $env:OLLAMA_KEEP_ALIVE = "-1"; ollama serve
> ```
>
> y hacé una llamada de calentamiento (`ollama run gemma4:e4b "hola"`) antes de
> abrir el chat. Verificá con `ollama ps` que el modelo figure cargado.

### 2. El backend

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
cp .env.example .env            # y ajustar si hace falta

uvicorn app.main:app --reload --port 8000
```

Documentación interactiva en <http://localhost:8000/docs>.

### 3. Los tests

```bash
pytest -q            # todo
pytest -m subtriaje  # solo los casos críticos del motor de reglas
```

No hace falta que Ollama esté corriendo: los tests mockean `llamar_gemma`.

`pytest -m subtriaje` corre los casos clínicos donde se espera naranja o rojo.
**Ese es el que tiene que bloquear el build**: un falso negativo ahí es alguien
que se queda en casa cuando debería estar en una guardia. Los casos donde se
espera verde o azul salen como warning si el motor clasifica de más. Ver
[`app/REGLAS.md`](app/REGLAS.md#tests).

---

## Endpoints

### `GET /health`

Chequea que Ollama responda y que el modelo esté bajado. **503** si no.

```bash
curl http://localhost:8000/health
```

```json
{"estado":"ok","modelo":"gemma4:e4b","ollama":"http://localhost:11434","sesiones_activas":0}
```

### `POST /sesion`

Crea la sesión y devuelve el mensaje de bienvenida, que incluye el disclaimer,
el aviso de procesamiento local y borrado, y el número de emergencia.

```bash
curl -X POST http://localhost:8000/sesion
```

```json
{"session_id":"3f2a…","mensaje":"Hola. Te voy a hacer algunas preguntas cortas…"}
```

### `POST /mensaje`

Un turno de conversación. `imagen_b64` va en base64 **sin** el prefijo
`data:image/...;base64,` (si lo mandás igual, se limpia solo).

```bash
curl -X POST http://localhost:8000/mensaje \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "3f2a…",
    "texto": "me duele el pecho desde hace un rato",
    "imagen_b64": null,
    "lat": -31.73,
    "lng": -60.53
  }'
```

```json
{
  "tipo": "pregunta",
  "mensaje": "¿Estás respirando con normalidad o te cuesta tomar aire?",
  "debug": null,
  "resultado": null
}
```

Los cuatro valores de `tipo`:

| `tipo` | Cuándo | `resultado` |
|---|---|---|
| `pregunta` | Falta información y quedan preguntas disponibles | `null` |
| `resultado` | Ficha completa o preguntas agotadas → se clasificó | objeto |
| `derivacion_inmediata` | Bandera roja: riesgo vital. Se corta y se cierra | `null` |
| `error_seguro` | Falló el modelo. **La sesión queda abierta** para reintentar | `null` |

Con `tipo: "resultado"`:

```json
{
  "tipo": "resultado",
  "mensaje": "Necesitás que te vea un médico muy pronto, en menos de 10 minutos.\n\n**Por qué te digo esto:** …",
  "resultado": {
    "color": "naranja",
    "motivo_clasificacion": "Muy urgente, atención dentro de los 10 minutos",
    "discriminador_disparador": "dolor opresivo que se corre al brazo o a la mandíbula",
    "recursos": [
      {"nombre":"Hospital San Martín - Guardia","tipo":"guardia_hospitalaria","distancia_km":2.4,"ocupacion_estimada":"alta"}
    ]
  }
}
```

> El motor devuelve bastante más que eso —`traza`, `regla_id`,
> `tiempo_maximo_min`, `tipo_recurso_sugerido`, `signos_alarma_reconsulta`,
> `version_ruleset`— pero el modelo `Resultado` de la API todavía expone solo
> estos tres campos. Sumar la traza a la respuesta (o al bloque `debug`) es lo
> que permite mostrarle al jurado **por qué** salió naranja; está pendiente y
> es un cambio de `schema.Resultado` + `orquestador._finalizar`, no del motor.

### `DELETE /sesion/{session_id}`

Borra la sesión y todos sus datos. **204** si existía, **404** si no.

```bash
curl -X DELETE http://localhost:8000/sesion/3f2a…
```

---

## Cómo decide el orquestador

`app/orquestador.py:procesar_turno` — el orden de los pasos es parte del diseño
clínico, no una casualidad:

1. **Tope de turnos.** Si se pasó de `MAX_TURNOS`, se clasifica con lo que haya.
2. **Llamada a Gemma.** Si falla → `error_seguro`, sin cerrar la sesión.
3. **Merge** de los campos extraídos sobre la ficha.
4. **Bandera roja.** `nivel_conciencia == "no_responde"`, `respira_normalmente is
   False`, `riesgo_via_aerea is True` o `hemorragia_mayor is True` → se corta
   **acá mismo**, sin una pregunta más, con el 107 primero en el mensaje.
5. **¿Alcanza la ficha?** Discriminadores críticos + motivo + edad + confianza
   ≥ 0.6.
6. **Si no**, y quedan preguntas: se devuelve la de Gemma, o una de fallback
   (`config.PREGUNTAS_FALLBACK`) elegida por el primer campo faltante según
   prioridad clínica.
7. **Si sí**, o se agotaron las preguntas: `reglas.clasificar()` →
   `recursos.buscar_recurso()` → mensaje final → **se borra la sesión**.

`reglas.clasificar()` acepta la ficha esté como esté —incluso vacía— y nunca
lanza, así que el paso 7 no necesita ningún `try/except`. Los campos que
falten no bajan el color: el motor los cuenta y pone un piso en amarillo.

### Invariantes que no se tocan

- **El merge nunca borra.** Un `None` en un turno posterior significa "el modelo
  no habló de esto ahora", jamás "olvidalo". → `gemma.merge_ficha`
- **`None` ≠ `False`.** `None` es "no se sabe". La bandera roja solo dispara con
  un valor **afirmado** (`is False` / `is True`, no falsy).
- **Nunca una respuesta vacía.** Si Gemma falla, el usuario recibe igual las
  instrucciones de seguridad y el número de emergencia.
- **La clasificación no la escribe el modelo.** El mensaje final lo arma una
  plantilla en Python. La segunda pasada opcional por Gemma (`REDACCION_LLM=true`)
  solo reescribe estilo, recibe el color ya decidido, y ante cualquier problema
  cae de nuevo en la plantilla.

---

## Privacidad

- **Nada sale de la máquina.** El único destino de red es Ollama en localhost.
- **Sin base de datos.** El estado vive en un `dict` en memoria.
- **Borrado real.** `session.cerrar()` hace `pop` del dict: no hay soft-delete
  ni flag de archivado. Se borra al terminar, al llamar al `DELETE`, a los 30
  minutos de inactividad, y al apagar la app.
- **Los logs no llevan contenido clínico.** Se loguea `session_id`, número de
  turno, **nombres** de los campos que se completaron, confianza y latencia.
  Nunca el relato, nunca los valores. Ver cualquier `logger.info` del código.
- **No se piden datos identificatorios.** El system prompt lo prohíbe
  explícitamente y el mensaje de bienvenida se lo aclara a la persona.

---

## Configuración

| Variable | Default | Qué hace |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Dónde escucha Ollama |
| `MODELO` | `gemma4:e4b` | Modelo a usar |
| `DEBUG_MODE` | `false` | Expone la ficha en el campo `debug` |
| `MAX_PREGUNTAS` | `3` | Preguntas de aclaración antes de clasificar |
| `MAX_TURNOS` | `8` | Turnos antes de forzar el cierre |
| `HISTORIAL_TURNOS` | `6` | Turnos que se le reenvían al modelo |
| `SESION_TTL_MINUTOS` | `30` | Inactividad antes del borrado |
| `REDACCION_LLM` | `false` | Segunda pasada de estilo por Gemma |

**`DEBUG_MODE=true` sirve muchísimo para mostrarle al jurado cómo se fue
llenando la ficha turno a turno**, pero expone datos clínicos en la respuesta
HTTP. Apagado por defecto.

---

## Estructura

```
app/
  main.py          Endpoints FastAPI, lifespan, /health
  config.py        Env vars, constantes clínicas y textos que ve el usuario
  schema.py        Modelos Pydantic — el contrato entre todas las piezas
  session.py       Store en memoria + barrido de inactivas
  prompt.py        System prompt + armado por turno + prioridad de campos
  gemma.py         Cliente Ollama, schema de la tool, parseo, merge_ficha
  orquestador.py   Máquina de estados del turno
  reglas.py        Motor de clasificación de severidad  → REGLAS.md
  ruleset.yaml     Las reglas como datos, revisables sin leer Python
  recursos.py      🚧 STUB — búsqueda de centros
tests/             380 tests, sin necesidad de Ollama
```

---

## Para quien implemente el stub que queda

`recursos.py` importa sus modelos de `app/schema.py`, así que no hace falta
tocar nada más. Lo único que no puede cambiar es la firma:

```python
# app/recursos.py
def buscar_recurso(color, especialidad, lat, lng) -> list[Recurso]: ...
```

El motor de reglas ya le pasa `clasificacion.color`, y tiene además un
`tipo_recurso_sugerido` (`guardia_alta_complejidad`, `guardia`,
`centro_urgencias`, `caps`, `consulta_programada`) y una
`especialidad_sugerida` que hoy el orquestador no está reenviando: son un mejor
criterio de filtro que el color solo.

## Para quien toque el ruleset

Los umbrales viven en [`app/ruleset.yaml`](app/ruleset.yaml) y se pueden
revisar sin leer Python. Antes de tocarlos, leer
[`app/REGLAS.md`](app/REGLAS.md) — sobre todo la sección de limitaciones y la
tabla de revisión clínica, que **está vacía a propósito**.

```bash
pytest -m subtriaje    # el gate que tiene que bloquear el build
```
