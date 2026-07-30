# Análisis de la integración

## Lo que ya estaba bien separado

- FastAPI expone sesiones y mensajes.
- El orquestador concentra la máquina de estados.
- Gemma usa function calling para devolver campos estructurados.
- La ficha se acumula en memoria y se elimina al cerrar.
- El motor de reglas y los recursos tienen contratos independientes.

## Conexión elegida

Streamlit actúa como cliente HTTP de FastAPI. No se importan directamente
`orquestador`, `session` ni `gemma` desde la UI porque eso duplicaría el ciclo de
vida de la aplicación y rompería la separación entre frontend y backend.

## Contrato utilizado

- `POST /sesion` → `{session_id, mensaje}`
- `POST /mensaje` → `{tipo, mensaje, debug, resultado}`
- `DELETE /sesion/{id}` → elimina la sesión
- `GET /health` → verifica backend, Ollama y modelo

## Flujo de sesión

- Streamlit guarda únicamente el `session_id`, el historial visible y la última
  respuesta estructurada.
- FastAPI conserva la ficha clínica y el historial real del modelo.
- Si el backend clasifica o detecta bandera roja, elimina la sesión y la UI
  bloquea la caja de texto.
- Si ocurre `error_seguro`, la sesión permanece abierta y el usuario puede
  reintentar.

## Correcciones de integración incluidas

1. Modelo por defecto cambiado a `gemma4:e2b-it-qat`.
2. `/health` exige el tag exacto y no confunde E2B con E4B.
3. Timeout configurable y elevado para la primera carga local.
4. `keep_alive` enviado a Ollama para reducir recargas del modelo.
5. Streamlit añadido a dependencias.
6. Scripts PowerShell separados para backend y frontend.
