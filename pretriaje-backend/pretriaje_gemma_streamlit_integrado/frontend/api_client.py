"""Cliente HTTP del frontend Streamlit.

El frontend nunca llama a Ollama ni a Gemma directamente. Toda la conversación
pasa por FastAPI, que conserva la sesión, invoca al modelo, hace el merge de la
ficha y aplica la máquina de estados.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class BackendError(RuntimeError):
    """Error controlado al comunicarse con el backend."""

    def __init__(self, mensaje: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(mensaje)


@dataclass(frozen=True, slots=True)
class BackendClient:
    """Cliente sin estado para la API FastAPI del proyecto."""

    base_url: str = "http://127.0.0.1:8000"
    timeout_s: float = 120.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def crear_sesion(self) -> dict[str, Any]:
        data = self._request("POST", "/sesion")
        self._require_keys(data, "session_id", "mensaje")
        return data

    def enviar_mensaje(
        self,
        *,
        session_id: str,
        texto: str,
        imagen_b64: str | None = None,
        ciudad: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/mensaje",
            json={
                "session_id": session_id,
                "texto": texto,
                "imagen_b64": imagen_b64,
                "ciudad": ciudad,
                "lat": lat,
                "lng": lng,
            },
        )
        self._require_keys(data, "tipo", "mensaje")
        return data

    def borrar_sesion(self, session_id: str) -> None:
        # DELETE es best-effort desde la UI: una sesión finalizada ya fue borrada
        # por el backend y puede responder 404 sin que sea un error para el usuario.
        try:
            self._request("DELETE", f"/sesion/{session_id}", expect_json=False)
        except BackendError as exc:
            if exc.status_code != 404:
                raise

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                timeout=self.timeout_s,
            )
        except httpx.ConnectError as exc:
            raise BackendError(
                "No se pudo conectar con el backend. Verificá que FastAPI esté "
                "corriendo en el puerto 8000."
            ) from exc
        except httpx.TimeoutException as exc:
            raise BackendError(
                "El backend tardó demasiado en responder. La primera carga de "
                "Gemma puede ser lenta; comprobá Ollama y volvé a intentar."
            ) from exc
        except httpx.HTTPError as exc:
            raise BackendError(
                f"Falló la comunicación con el backend ({type(exc).__name__})."
            ) from exc

        if response.is_error:
            detalle = self._extract_detail(response)
            raise BackendError(detalle, status_code=response.status_code)

        if not expect_json or response.status_code == 204:
            return {}

        try:
            data = response.json()
        except ValueError as exc:
            raise BackendError("El backend devolvió una respuesta que no es JSON.") from exc

        if not isinstance(data, dict):
            raise BackendError("El backend devolvió una estructura inesperada.")
        return data

    @staticmethod
    def _extract_detail(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            body = None

        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            if isinstance(detail, list):
                return "La solicitud no pasó la validación del backend."

        return f"El backend respondió HTTP {response.status_code}."

    @staticmethod
    def _require_keys(data: dict[str, Any], *keys: str) -> None:
        missing = [key for key in keys if key not in data]
        if missing:
            raise BackendError(
                "La respuesta del backend está incompleta: faltan " + ", ".join(missing)
            )
