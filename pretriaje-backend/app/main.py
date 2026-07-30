"""Aplicación FastAPI: endpoints del backend de recolección conversacional.

Los endpoints son deliberadamente finitos. Toda la lógica del turno vive en
`orquestador.procesar_turno`; acá solo hay entrada/salida, ciclo de vida y el
chequeo de salud.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response, status

from app import config, gemma, orquestador, session
from app.schema import MensajeRequest, RespuestaAPI, SesionResponse

# Logging estructurado en formato clave=valor. NUNCA se loguea contenido
# clínico: solo session_id, turno, nombres de campos y latencias.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Arranca el barrido de sesiones inactivas y limpia todo al apagar."""
    tarea = asyncio.create_task(session.barrido_periodico())
    logger.info(
        "app_iniciada modelo=%s ollama=%s debug=%s ttl_min=%d",
        config.MODELO,
        config.OLLAMA_URL,
        config.DEBUG_MODE,
        config.SESION_TTL_MINUTOS,
    )
    try:
        yield
    finally:
        tarea.cancel()
        try:
            await tarea
        except asyncio.CancelledError:
            pass
        await gemma.cerrar_cliente()
        # Al apagar no queda ningún dato de salud en memoria.
        session.limpiar_todo()
        logger.info("app_detenida")


app = FastAPI(
    title="Pre-triaje - Backend de recolección conversacional",
    description=(
        "Prototipo de hackathon. Recolecta información clínica por chat para "
        "que un motor de reglas de Manchester asigne la urgencia. "
        "SIN validación clínica."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.post("/sesion", response_model=SesionResponse, status_code=status.HTTP_201_CREATED)
async def crear_sesion() -> SesionResponse:
    """Crea una sesión y devuelve el mensaje de bienvenida con el disclaimer."""
    state = session.crear_sesion()
    session.registrar_turno(state, "asistente", config.MENSAJE_BIENVENIDA)
    return SesionResponse(
        session_id=state.session_id,
        mensaje=config.MENSAJE_BIENVENIDA,
    )


@app.post("/mensaje", response_model=RespuestaAPI)
async def mensaje(req: MensajeRequest) -> RespuestaAPI:
    """Procesa un turno de la conversación."""
    state = session.obtener(req.session_id)  # 404 si no existe
    if state.cerrada:
        raise HTTPException(
            status_code=409,
            detail="La sesión ya está cerrada. Iniciá una nueva.",
        )

    return await orquestador.procesar_turno(
        state,
        texto=req.texto,
        imagen_b64=req.imagen_b64,
        lat=req.lat,
        lng=req.lng,
    )


@app.delete("/sesion/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def borrar_sesion(session_id: str) -> Response:
    """Borra la sesión y todos sus datos. Idempotente desde el punto de vista
    del cliente, pero devuelve 404 si nunca existió."""
    if not session.cerrar(session_id):
        raise HTTPException(status_code=404, detail="La sesión no existe.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/health")
async def health() -> dict:
    """Verifica que Ollama responda y que el modelo configurado esté bajado."""
    try:
        cliente = gemma.obtener_cliente()
        resp = await cliente.get(f"{config.OLLAMA_URL}/api/tags", timeout=5.0)
        resp.raise_for_status()
        modelos = [m.get("name", "") for m in resp.json().get("models", [])]
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama no responde en {config.OLLAMA_URL} ({type(exc).__name__}).",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="Ollama devolvió una respuesta inesperada."
        ) from exc

    if not _modelo_disponible(config.MODELO, modelos):
        raise HTTPException(
            status_code=503,
            detail=(
                f"El modelo '{config.MODELO}' no está disponible en Ollama. "
                f"Corré: ollama pull {config.MODELO}"
            ),
        )

    return {
        "estado": "ok",
        "modelo": config.MODELO,
        "ollama": config.OLLAMA_URL,
        "sesiones_activas": session.cantidad_sesiones(),
    }


def _modelo_disponible(modelo: str, disponibles: list[str]) -> bool:
    """Compara tolerando el tag: 'gemma4:e4b' matchea 'gemma4:e4b' y 'gemma4'."""
    base = modelo.split(":")[0]
    return any(d == modelo or d.split(":")[0] == base for d in disponibles)
