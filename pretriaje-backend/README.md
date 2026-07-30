# Pre-triaje — Backend de recolección conversacional

> ⚠️ **Prototipo de hackathon. Sin validación clínica.** No usar con pacientes
> reales. No reemplaza la evaluación de un profesional de la salud. Ante una
> emergencia: **107** o **911**.

Backend en FastAPI que conversa con una persona sobre sus síntomas, arma una
ficha clínica estructurada turno a turno, y cuando tiene lo suficiente delega
en un motor de reglas de Manchester que asigna el nivel de urgencia.

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
| Motor de reglas de Manchester (`app/reglas.py`) | 🚧 **STUB — otra persona** |
| Búsqueda de centros de salud (`app/recursos.py`) | 🚧 **STUB — otra persona** |

Los dos stubs tienen la firma definitiva y un `TODO(equipo)` con el contrato
esperado. `reglas.clasificar()` devuelve **amarillo fijo a propósito**: el
fallback ante lo desconocido nunca debe ser un nivel bajo de urgencia.

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
pytest -q
```

No hace falta que Ollama esté corriendo: los tests mockean `llamar_gemma`.

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
  "mensaje": "Conviene que te vea un médico hoy, sin dejarlo pasar.\n\n**Por qué te digo esto:** …",
  "resultado": {
    "color": "amarillo",
    "motivo_clasificacion": "STUB - motor de reglas no implementado",
    "discriminador_disparador": "ninguno",
    "recursos": [
      {"nombre":"Hospital San Martín - Guardia","tipo":"guardia_hospitalaria","distancia_km":2.4,"ocupacion_estimada":"alta"}
    ]
  }
}
```

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
  reglas.py        🚧 STUB — motor de Manchester
  recursos.py      🚧 STUB — búsqueda de centros
tests/             58 tests, sin necesidad de Ollama
```

---

## Para quien implemente los stubs

Ambos módulos importan sus modelos de `app/schema.py`, así que no hace falta
tocar nada más. Lo único que no puede cambiar es la firma:

```python
# app/reglas.py
def clasificar(ficha: FichaClinica) -> Clasificacion: ...

# app/recursos.py
def buscar_recurso(color, especialidad, lat, lng) -> list[Recurso]: ...
```

Dos cosas a tener en cuenta en `reglas.py`:

- **La ficha puede llegar incompleta.** Si se agotaron las preguntas o los
  turnos, se clasifica con lo que haya. Campos en `None` son la norma.
- **`None` significa "no se sabe", nunca "no".** Ante la duda hay que subir la
  urgencia, no bajarla.

El `discriminador_disparador` que devuelvas se le muestra a la persona como la
razón de su clasificación, así que tiene que ser algo legible.
