import json
from datetime import datetime
from typing import Any
from uuid import uuid4

import httpx


class GoogleDriveStorageService:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = timeout_seconds

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {access_token}",
        }

    def _parse_drive_error(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            message = str(payload.get("error", {}).get("message") or "").strip()
            if message:
                return message
        except Exception:
            pass
        return (response.text or "Errore Google Drive").strip()[:300]

    def _request(
        self,
        client: httpx.Client,
        *,
        method: str,
        url: str,
        access_token: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = self._auth_headers(access_token)
        if extra_headers:
            headers.update(extra_headers)

        response = client.request(
            method=method,
            url=url,
            params=params,
            json=json_body,
            content=content,
            headers=headers,
            timeout=self.timeout_seconds,
        )

        if response.status_code >= 400:
            message = self._parse_drive_error(response)
            raise RuntimeError(f"Google Drive API {response.status_code}: {message}")

        try:
            return response.json()
        except Exception:
            return {}

    @staticmethod
    def _escape_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _find_or_create_folder(
        self,
        client: httpx.Client,
        *,
        access_token: str,
        name: str,
        parent_id: str | None = None,
    ) -> str:
        escaped_name = self._escape_query_value(name)
        query_parts = [
            "mimeType='application/vnd.google-apps.folder'",
            f"name='{escaped_name}'",
            "trashed=false",
        ]
        if parent_id:
            query_parts.append(f"'{parent_id}' in parents")

        list_payload = self._request(
            client,
            method="GET",
            url="https://www.googleapis.com/drive/v3/files",
            access_token=access_token,
            params={
                "q": " and ".join(query_parts),
                "spaces": "drive",
                "fields": "files(id,name)",
                "pageSize": 1,
            },
        )

        files = list_payload.get("files") or []
        if files:
            return str(files[0].get("id") or "").strip()

        body: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]

        created = self._request(
            client,
            method="POST",
            url="https://www.googleapis.com/drive/v3/files",
            access_token=access_token,
            params={"fields": "id"},
            json_body=body,
        )

        folder_id = str(created.get("id") or "").strip()
        if not folder_id:
            raise RuntimeError("Google Drive: cartella non creata.")
        return folder_id

    def _build_multipart_upload_body(
        self,
        *,
        metadata: dict[str, Any],
        file_content: bytes,
        content_type: str,
        boundary: str,
    ) -> bytes:
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        parts = [
            f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{metadata_json}\r\n",
            f"--{boundary}\r\n"
            f"Content-Type: {content_type}\r\n\r\n",
        ]

        body = "".join(parts).encode("utf-8")
        body += file_content
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")
        return body

    def upload_user_plant_photo(
        self,
        *,
        access_token: str,
        user_id: str,
        plant_id: int,
        filename: str,
        content_type: str,
        file_content: bytes,
    ) -> dict[str, str]:
        token = str(access_token or "").strip()
        if not token:
            raise RuntimeError("Access token Google mancante.")

        with httpx.Client() as client:
            root_folder_id = self._find_or_create_folder(
                client,
                access_token=token,
                name="Clorofilla",
            )
            photos_folder_id = self._find_or_create_folder(
                client,
                access_token=token,
                name="user-plants",
                parent_id=root_folder_id,
            )

            safe_filename = str(filename or "").strip() or "plant-photo.jpg"
            unique_name = (
                f"plant_{plant_id}_user_{user_id[:12]}_"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}_{safe_filename}"
            )

            metadata = {
                "name": unique_name,
                "parents": [photos_folder_id],
            }
            boundary = f"drive-upload-{uuid4().hex}"
            payload = self._build_multipart_upload_body(
                metadata=metadata,
                file_content=file_content,
                content_type=content_type,
                boundary=boundary,
            )

            created = self._request(
                client,
                method="POST",
                url="https://www.googleapis.com/upload/drive/v3/files",
                access_token=token,
                params={
                    "uploadType": "multipart",
                    "fields": "id,name,webViewLink,webContentLink",
                },
                content=payload,
                extra_headers={
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
            )

            file_id = str(created.get("id") or "").strip()
            if not file_id:
                raise RuntimeError("Google Drive: file caricato ma ID non disponibile.")

            self._request(
                client,
                method="POST",
                url=f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
                access_token=token,
                params={"fields": "id"},
                json_body={"role": "reader", "type": "anyone"},
            )

            public_url = f"https://drive.google.com/uc?export=view&id={file_id}"
            return {
                "provider": "google_drive",
                "file_id": file_id,
                "photo_url": public_url,
            }
