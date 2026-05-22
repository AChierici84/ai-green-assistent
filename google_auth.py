from typing import Any

import httpx
from fastapi import HTTPException


class GoogleAuthService:
    def __init__(
        self,
        *,
        google_client_ids: list[str],
        require_google_auth: bool,
        admin_users: set[str],
        data_storage: Any,
    ) -> None:
        self.google_client_ids = google_client_ids
        self.require_google_auth = require_google_auth
        self.admin_users = admin_users
        self.data_storage = data_storage

    def validate_google_token(self, id_token: str) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=8.0) as client:
                response = client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": id_token},
                )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Errore verifica token Google: {exc}")

        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Token Google non valido.")

        payload = response.json()
        audience = str(payload.get("aud") or "")

        if self.google_client_ids and audience not in self.google_client_ids:
            raise HTTPException(status_code=401, detail="Token Google con client_id non autorizzato.")

        return payload

    def get_google_user_from_authorization(
        self,
        authorization: str | None,
        require_auth: bool | None = None,
    ) -> dict[str, Any] | None:
        if require_auth is None:
            require_auth = self.require_google_auth

        if not authorization:
            if require_auth:
                raise HTTPException(status_code=401, detail="Authorization Bearer richiesta.")
            return None

        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise HTTPException(status_code=401, detail="Header Authorization non valido.")

        validated = self.validate_google_token(token.strip())
        return {
            "sub": validated.get("sub", ""),
            "email": validated.get("email", ""),
            "name": validated.get("name", ""),
            "picture": validated.get("picture", ""),
        }

    def is_admin_email(self, email: str) -> bool:
        normalized = str(email or "").strip().lower()
        return bool(normalized) and normalized in self.admin_users

    def require_admin_user(self, authorization: str | None) -> dict[str, Any]:
        user = self.get_google_user_from_authorization(authorization, require_auth=True)
        if not user:
            raise HTTPException(status_code=401, detail="Accedi con Google.")

        if not self.is_admin_email(str(user.get("email") or "")):
            raise HTTPException(status_code=403, detail="Accesso admin non autorizzato.")
        return user

    def register_google_user_if_needed(self, user: dict[str, Any]) -> tuple[bool, str]:
        return self.data_storage.register_google_user_if_needed(user)

    def list_registered_users_for_admin(self, limit: int = 300) -> list[dict[str, Any]]:
        return self.data_storage.list_registered_users_for_admin(limit)
