import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import importlib
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, unquote
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import chromadb
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from openai import OpenAI
from pydantic import BaseModel, Field

load_dotenv()


def _default_asset_path(filename: str) -> str:
    if os.getenv("SPACE_ID") and Path("/data").exists():
        return str(Path("/data") / "greenassistent-assets" / filename)
    return str(Path("data") / filename)


INDEX_PATH = os.getenv("PLANCLEF_INDEX_PATH", _default_asset_path("planclef.faiss"))
CACHE_PATH = os.getenv("PLANCLEF_CACHE_PATH", _default_asset_path("planclef_cache.pt"))
MODEL_NAME = os.getenv("PLANCLEF_MODEL_NAME", "ViT-B-32")
LEAFSNAP_INDEX_PATH = os.getenv("LEAFSNAP_INDEX_PATH", _default_asset_path("leafsnap.faiss"))
LEAFSNAP_CACHE_PATH = os.getenv("LEAFSNAP_CACHE_PATH", _default_asset_path("leafsnap_cache.pt"))
RAG_DB_PATH = os.getenv("RAG_DB_PATH", _default_asset_path("plant_rag"))
WIKI_USER_AGENT = os.getenv(
    "WIKI_USER_AGENT",
    "clorofilla/1.0 (contact: local-dev)",
)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _default_plants_db_path() -> str:
    # On Hugging Face Spaces with persistent storage enabled, /data survives restarts.
    if os.getenv("SPACE_ID") and Path("/data").exists():
        return "/data/plants.db"
    return "data/plants.db"


def _default_user_plants_db_path() -> str:
    # Keep user-saved plants in a dedicated sqlite file to avoid coupling with plants catalog growth.
    if os.getenv("SPACE_ID") and Path("/data").exists():
        return "/data/user_plants.db"
    return "data/user_plants.db"


PLANTS_SQLITE_PATH = os.getenv("PLANTS_SQLITE_PATH", _default_plants_db_path())
USER_PLANTS_SQLITE_PATH = os.getenv("USER_PLANTS_SQLITE_PATH", _default_user_plants_db_path())
MY_SQL_CONNECTION_STRING = os.getenv("MY_SQL", "").strip()
MYSQL_USER = os.getenv("MYSQL_USER", "").strip()
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "").strip() or os.getenv("DB_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "").strip()
MYSQL_HOST = os.getenv("MYSQL_HOST", "localhost").strip() or "localhost"
MYSQL_PORT_RAW = os.getenv("MYSQL_PORT", "").strip()
MYSQL_ENABLED_RAW = os.getenv("MYSQL_ENABLED")
MYSQL_ENABLED = (MYSQL_ENABLED_RAW or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MYSQL_USE_UNIX_SOCKET = os.getenv("MYSQL_USE_UNIX_SOCKET", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
MYSQL_UNIX_SOCKET = os.getenv("MYSQL_UNIX_SOCKET", "").strip()
MYSQL_COMPONENTS_PRESENT = bool(MYSQL_USER and MYSQL_DATABASE)


class _MySQLResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, lastrowid: int = 0):
        self._rows = rows or []
        self.lastrowid = int(lastrowid or 0)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _MySQLCompatConnection:
    def __init__(self, config: str | dict[str, Any]):
        pymysql_mod, dict_cursor = _load_pymysql()
        if pymysql_mod is None or dict_cursor is None:
            raise RuntimeError("Configurazione MySQL impostata ma pymysql non disponibile. Installa pymysql.")

        params = _parse_mysql_dsn(config) if isinstance(config, str) else config
        connect_kwargs: dict[str, Any] = {
            "user": params["user"],
            "password": params["password"],
            "database": params["database"],
            "charset": "utf8mb4",
            "autocommit": False,
            "cursorclass": dict_cursor,
        }
        if params.get("unix_socket"):
            connect_kwargs["unix_socket"] = params["unix_socket"]
        else:
            connect_kwargs["host"] = params["host"]
            connect_kwargs["port"] = params["port"]

        self._conn = pymysql_mod.connect(**connect_kwargs)

    def execute(self, query: str, params: tuple | list | None = None):
        converted = _to_mysql_query(query)
        with self._conn.cursor() as cur:
            cur.execute(converted, tuple(params or ()))
            rows = cur.fetchall() if cur.description else []
            return _MySQLResult(rows=rows, lastrowid=cur.lastrowid or 0)

    def executemany(self, query: str, params_seq: list[tuple] | tuple):
        converted = _to_mysql_query(query)
        with self._conn.cursor() as cur:
            cur.executemany(converted, params_seq)
            return _MySQLResult(rows=[], lastrowid=cur.lastrowid or 0)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()


def _parse_mysql_dsn(dsn: str) -> dict[str, Any]:
    parsed = urlparse(dsn)
    if parsed.scheme not in {"mysql", "mysql+pymysql"}:
        raise RuntimeError(
            "MY_SQL non valido: usa mysql://user:pass@host:3306/database oppure "
            "mysql://user:pass@localhost/database?unix_socket=/percorso/mysql.sock"
        )

    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = (parsed.path or "").lstrip("/")
    query = parse_qs(parsed.query)
    socket_values = query.get("unix_socket") or query.get("socket") or []
    unix_socket = unquote(socket_values[0]).strip() if socket_values else ""
    if not user or not database:
        raise RuntimeError("MY_SQL non valido: user e database sono obbligatori")

    params: dict[str, Any] = {
        "user": user,
        "password": password,
        "database": database,
    }
    if unix_socket:
        params["unix_socket"] = unix_socket
    else:
        params["host"] = parsed.hostname or "localhost"
        params["port"] = int(parsed.port or 3306)

    return params


def _load_pymysql():
    try:
        pymysql_mod = importlib.import_module("pymysql")
        cursors_mod = importlib.import_module("pymysql.cursors")
        dict_cursor = getattr(cursors_mod, "DictCursor", None)
        return pymysql_mod, dict_cursor
    except Exception:
        return None, None


def _to_mysql_query(query: str) -> str:
    converted = query.replace("?", "%s")
    converted = converted.replace("INSERT OR IGNORE", "INSERT IGNORE")
    return converted


def _is_mysql_enabled() -> bool:
    # If explicitly configured, honor MYSQL_ENABLED strictly.
    if MYSQL_ENABLED_RAW is not None:
        return MYSQL_ENABLED
    # Backward compatibility for legacy environments.
    return bool(MY_SQL_CONNECTION_STRING or MYSQL_COMPONENTS_PRESENT)


def _parse_mysql_components_from_env() -> dict[str, Any] | None:
    if not MYSQL_COMPONENTS_PRESENT:
        return None

    if not MYSQL_USER or not MYSQL_DATABASE:
        raise RuntimeError(
            "Configurazione MySQL incompleta: MYSQL_USER e MYSQL_DATABASE sono obbligatori."
        )

    params: dict[str, Any] = {
        "user": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "database": MYSQL_DATABASE,
    }

    if MYSQL_USE_UNIX_SOCKET:
        if not MYSQL_UNIX_SOCKET:
            raise RuntimeError(
                "MYSQL_USE_UNIX_SOCKET=1 ma MYSQL_UNIX_SOCKET non e impostata."
            )
        params["unix_socket"] = MYSQL_UNIX_SOCKET
        return params

    try:
        port = int(MYSQL_PORT_RAW or 3306)
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT non valido: usa un numero intero") from exc

    params["host"] = MYSQL_HOST
    params["port"] = port
    return params


def _is_mysql_conn(conn: Any) -> bool:
    return isinstance(conn, _MySQLCompatConnection)

# Cloudinary configuration (optional - photo upload disabled if not set)
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")
if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

GOOGLE_CLIENT_IDS = [
    value.strip()
    for value in os.getenv("GOOGLE_CLIENT_ID", "").split(",")
    if value.strip()
]
REQUIRE_GOOGLE_AUTH = os.getenv("REQUIRE_GOOGLE_AUTH", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ADMIN_USERS = {
    value.strip().lower()
    for value in os.getenv("ADMIN_USERS", "").split(",")
    if value.strip()
}
PWA_DIST_DIR = Path(os.getenv("PWA_DIST_DIR", "pwa-app/dist"))
PLANT_CARD_CACHE_ENABLED = os.getenv("PLANT_CARD_CACHE_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

index: Any = None
rag_collection: Any = None


logger = logging.getLogger("ai_green_assistant.api")


species_build_jobs: dict[str, dict[str, Any]] = {}
species_build_jobs_lock = threading.Lock()


def configure_logging() -> None:
    """Configure logging for all ai_green_assistant modules."""
    # Configure the parent logger so all child loggers inherit the handlers
    root_logger = logging.getLogger("ai_green_assistant")
    
    if root_logger.handlers:
        return

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / os.getenv("LOG_FILE", "api.log")

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
        utc=False,
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    root_logger.setLevel(log_level)
    root_logger.propagate = True
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


configure_logging()


def _truncate(value: Any, max_len: int = 500) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _log_api(endpoint: str, event: str, payload: dict[str, Any]) -> None:
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(payload)
    logger.info("%s | %s | %s", endpoint, event, serialized)


def _response_payload_for_log(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status_code": getattr(response, "status_code", None),
        "content_type": getattr(response, "media_type", None) or getattr(response, "headers", {}).get("content-type", ""),
    }

    body = getattr(response, "body", None)
    if not isinstance(body, (bytes, bytearray)) or not body:
        return payload

    text = body.decode("utf-8", errors="replace")
    content_type = str(payload["content_type"] or "").lower()
    if "application/json" in content_type:
        try:
            payload["body"] = json.loads(text)
        except Exception:
            payload["body"] = _truncate(text)
        return payload

    if content_type.startswith("text/") or "xml" in content_type or "javascript" in content_type:
        payload["body"] = _truncate(text)

    return payload


def _serve_pwa_index() -> HTMLResponse:
    pwa_index = PWA_DIST_DIR / "index.html"
    if pwa_index.exists():
        return HTMLResponse(content=pwa_index.read_text(encoding="utf-8"))

    raise HTTPException(status_code=503, detail="Frontend non disponibile.")


def _serve_landing_page() -> HTMLResponse:
    landing_page = Path(__file__).with_name("ui.html")
    if landing_page.exists():
        return HTMLResponse(content=landing_page.read_text(encoding="utf-8"))

    raise HTTPException(status_code=503, detail="Landing page non disponibile.")


def _serve_pwa_file(filename: str, media_type: str | None = None) -> FileResponse:
    path = PWA_DIST_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"File statico non trovato: {filename}")
    return FileResponse(path=str(path), media_type=media_type)


def _format_datetime_display(value: Any) -> Any:
    raw_value = str(value or "").strip()
    if not raw_value:
        return value

    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return parsed.strftime("%d/%m/%Y %H:%M:%S")


def _normalize_image_path(raw_path: str) -> str:
    """Normalize image path to be relative to data/images."""
    normalized = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if normalized.lower().startswith("data/"):
        normalized = normalized[5:]
    if normalized.lower().startswith("images/"):
        normalized = normalized[7:]
    return normalized


# ---------------------------------------------------------------------------
# GPT-4o vision fallback helpers
# ---------------------------------------------------------------------------

FAISS_CONFIDENCE_THRESHOLD = float(os.getenv("FAISS_CONFIDENCE_THRESHOLD", "0.82"))
FAISS_AMBIGUITY_MARGIN = float(os.getenv("FAISS_AMBIGUITY_MARGIN", "0.015"))
RRF_AMBIGUITY_MARGIN = float(os.getenv("RRF_AMBIGUITY_MARGIN", "0.0025"))
FORCE_OPENAI_FALLBACK = os.getenv("FORCE_OPENAI_FALLBACK", "0").strip().lower() in {
    "1", "true", "yes", "on"
}


def _should_trigger_gpt_fallback(top_score: float, results: list[tuple[str, float, list]]) -> tuple[bool, str]:
    """Decide whether GPT vision fallback should run.

    Triggers on low FAISS confidence, explicit force flag, or very ambiguous top-vs-second gap.
    """
    if FORCE_OPENAI_FALLBACK:
        return True, "forced_by_env"

    if top_score < FAISS_CONFIDENCE_THRESHOLD:
        return True, "low_top_score"

    if len(results) < 2:
        return False, "single_result"

    top_result_score = float(results[0][1])
    second_result_score = float(results[1][1])
    gap = max(0.0, top_result_score - second_result_score)
    rrf_like = top_result_score <= 0.1 and second_result_score <= 0.1

    if rrf_like and gap < RRF_AMBIGUITY_MARGIN:
        return True, "ambiguous_rrf_gap"

    if (not rrf_like) and gap < FAISS_AMBIGUITY_MARGIN:
        return True, "ambiguous_similarity_gap"

    return False, "high_confidence"


def _gpt_vision_identify_plant(
    image_path: str,
    api_key: str,
    candidate_species: list[str] | None = None,
) -> tuple[str | None, str]:
    """Ask GPT-4o to identify the plant species from an image.

    Returns (scientific binomial name or None, diagnostic reason).
    """
    import base64
    suffix = Path(image_path).suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}
    mime = mime_map.get(suffix, "image/jpeg")
    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("utf-8")
        client = OpenAI(api_key=api_key)
        model_name = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")
        resp = client.chat.completions.create(
            model=model_name,
            max_tokens=80,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                        },
                        {
                            "type": "text",
                            "text": (
                                "Identify the plant species in this image. "
                                "Reply with ONLY the scientific Latin binomial name (Genus species). "
                                "If you cannot identify it, reply exactly: unknown"
                            ),
                        },
                    ],
                }
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        logger.info(f"GPT vision raw output: {raw[:200] if raw else '<empty>'}")
        if not raw or raw.lower().startswith("unknown"):
            # Second pass: constrain the choice to top FAISS candidates.
            if candidate_species:
                candidates_text = "\n".join(f"- {name}" for name in candidate_species[:12])
                resp2 = client.chat.completions.create(
                    model=model_name,
                    max_tokens=80,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
                                },
                                {
                                    "type": "text",
                                    "text": (
                                        "Choose the best matching species from this candidate list. "
                                        "Reply with ONLY one exact binomial from the list, or 'unknown'.\n\n"
                                        f"Candidates:\n{candidates_text}"
                                    ),
                                },
                            ],
                        }
                    ],
                )
                raw2 = (resp2.choices[0].message.content or "").strip()
                logger.info(f"GPT vision candidate-mode output: {raw2[:200] if raw2 else '<empty>'}")
                cleaned2 = raw2.replace("*", " ").replace("`", " ").replace("_", " ")
                match2 = re.search(r"\b([A-Z][a-z\-]+)\s+([a-z][a-z\-]+)\b", cleaned2)
                if match2:
                    picked = f"{match2.group(1)} {match2.group(2)}"
                    # Accept only if it is one of the provided candidates.
                    if any(picked.lower() == c.lower() for c in candidate_species):
                        return picked, "ok_candidate_mode"
            return None, "model returned unknown or empty"

        cleaned = raw.replace("*", " ").replace("`", " ").replace("_", " ")
        match = re.search(r"\b([A-Z][a-z\-]+)\s+([a-z][a-z\-]+)\b", cleaned)
        if not match:
            return None, f"no binomial found in model output: {raw[:120]}"
        return f"{match.group(1)} {match.group(2)}", "ok"
    except Exception as exc:
        logger.warning(f"GPT vision fallback failed: {exc}")
        return None, f"exception: {type(exc).__name__}: {exc}"


def _insert_draft_plant_if_missing(species_name: str, api_key: str) -> bool:
    """Insert a minimal plant record (indexed=0) if the species is not in plants.db.

    Returns True if a new record was inserted, False if it already existed.
    """
    with get_plants_db_connection() as conn:
        row = conn.execute(
            "SELECT id FROM plants WHERE lower(species_name) = lower(?) LIMIT 1",
            (species_name.strip(),),
        ).fetchone()
        if row is not None:
            return False

    # Keep a minimal draft payload; enrichment is handled by async build job.
    profile: dict[str, Any] = {}
    now_iso = datetime.utcnow().isoformat()
    with get_plants_db_connection() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO plants (
                species_name, indexed, annaffiatura_gg, annaffiatura_time, luce, temperatura,
                umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione, updated_at
            ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                species_name,
                profile.get("annaffiatura_gg"),
                profile.get("annaffiatura_time"),
                profile.get("luce"),
                profile.get("temperatura"),
                profile.get("umidita"),
                profile.get("altezza_media"),
                profile.get("pulizia"),
                profile.get("terriccio"),
                profile.get("concimazione"),
                profile.get("prevenzione"),
                now_iso,
            ),
        )
        conn.commit()
    logger.info(f"Draft plant inserted: '{species_name}' (indexed=0)")
    return True


def _species_build_status(species_name: str) -> dict[str, Any]:
    key = species_name.strip().lower()
    with species_build_jobs_lock:
        payload = species_build_jobs.get(key)
        if payload:
            return dict(payload)

    profile = get_plant_profile_from_db(species_name)
    if profile and profile.get("indexed"):
        return {
            "species": profile.get("species_name") or species_name,
            "status": "completed",
            "started_at": None,
            "finished_at": profile.get("updated_at"),
            "error": None,
            "result": {"indexed": True},
        }

    return {
        "species": species_name,
        "status": "not_started",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "result": None,
    }


def _set_species_build_job(species_name: str, **updates: Any) -> None:
    key = species_name.strip().lower()
    with species_build_jobs_lock:
        current = species_build_jobs.get(key, {"species": species_name})
        current.update(updates)
        species_build_jobs[key] = current


def _run_species_build_job(species_name: str) -> None:
    _set_species_build_job(
        species_name,
        status="running",
        started_at=datetime.utcnow().isoformat(),
        finished_at=None,
        error=None,
    )
    try:
        from add_species_to_faiss import add_to_faiss, fetch_wiki_image_urls, resolve_title

        langs = tuple(x.strip().lower() for x in os.getenv("WIKI_LANGS", "it,en").split(",") if x.strip())
        max_images = max(4, int(os.getenv("RAG_BUILD_MAX_IMAGES", "8")))
        lang, resolved_title = resolve_title(species_name, "", langs)
        image_urls = fetch_wiki_image_urls(resolved_title, lang, max_images=max_images)
        if not image_urls:
            logger.warning(
                f"No image URLs found for '{species_name}' on {lang}:{resolved_title}. "
                "Continuing build with textual ingestion only."
            )

        add_result = add_to_faiss(
            species_name,
            image_urls,
            lang=lang,
            resolved_title=resolved_title,
            model_name=MODEL_NAME,
            index_path=Path(INDEX_PATH),
            cache_path=Path(CACHE_PATH),
        )

        hf_synced = False
        hf_error = None
        if os.getenv("AUTO_SYNC_HF_ASSETS", "1").strip().lower() in {"1", "true", "yes", "on"}:
            try:
                from upload_hf_assets import DEFAULT_REPO_ID, upload_assets

                hf_token = os.getenv("HF_TOKEN", "").strip() or None
                uploaded = upload_assets(
                    repo_id=os.getenv("HF_ASSETS_DATASET_REPO", DEFAULT_REPO_ID),
                    private=False,
                    include_plants_db=True,
                    skip_missing=True,
                    token=hf_token,
                )
                hf_synced = uploaded > 0
            except Exception as exc:
                hf_error = str(exc)
                logger.warning(f"HF sync failed for '{species_name}': {exc}")

        # Force lazy reload of in-memory search/rag handles after asset update.
        global index, rag_collection
        index = None
        rag_collection = None

        _set_species_build_job(
            species_name,
            status="completed",
            finished_at=datetime.utcnow().isoformat(),
            error=None,
            result={
                "species": species_name,
                "add_result": add_result,
                "hf_synced": hf_synced,
                "hf_error": hf_error,
            },
        )
        logger.info(f"Species build completed for '{species_name}'")
    except Exception as exc:
        _set_species_build_job(
            species_name,
            status="failed",
            finished_at=datetime.utcnow().isoformat(),
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.exception(f"Species build failed for '{species_name}': {exc}")


def _ensure_species_build_job(species_name: str) -> dict[str, Any]:
    status = _species_build_status(species_name)
    if status.get("status") in {"queued", "running", "completed"}:
        return status

    _set_species_build_job(
        species_name,
        species=species_name,
        status="queued",
        started_at=None,
        finished_at=None,
        error=None,
        result=None,
    )

    thread = threading.Thread(
        target=_run_species_build_job,
        args=(species_name,),
        daemon=True,
        name=f"species-build-{species_name[:24]}",
    )
    thread.start()
    return _species_build_status(species_name)


def _species_to_folder_name(species_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(species_name or "").lower()).strip("_")
    return normalized


def _get_species_preview_image_url(species_name: str) -> str:
    image_paths = _get_species_images_from_db(species_name)
    for raw_path in image_paths:
        if isinstance(raw_path, str) and raw_path.startswith(("http://", "https://")):
            return raw_path

        normalized_path = _normalize_image_path(str(raw_path or ""))
        if not normalized_path:
            continue

        local_path = Path("data") / "images" / normalized_path
        if local_path.exists():
            return f"/images/{normalized_path}"

    # Backward compatibility: read from legacy RAG metadata if DB is empty.
    try:
        collection = get_rag_collection()
        res = collection.get(
            where={"species_name": {"$eq": species_name}},
            limit=1,
        )
        metadatas = res.get("metadatas", []) if res else []
        metadata = metadatas[0] if metadatas else {}
        image_paths_json = metadata.get("image_paths", "[]") if metadata else "[]"

        try:
            image_paths = json.loads(image_paths_json)
        except (json.JSONDecodeError, TypeError):
            image_paths = []

        for raw_path in image_paths:
            if isinstance(raw_path, str) and raw_path.startswith(("http://", "https://")):
                return raw_path

            normalized_path = _normalize_image_path(str(raw_path or ""))
            if not normalized_path:
                continue

            local_path = Path("data") / "images" / normalized_path
            if local_path.exists():
                return f"/images/{normalized_path}"
    except Exception:
        pass

    folder_name = _species_to_folder_name(species_name)
    if not folder_name:
        return ""

    image_dir = Path("data") / "images" / folder_name
    if not image_dir.exists() or not image_dir.is_dir():
        return ""

    candidates = sorted(
        [
            path
            for path in image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
    )
    if not candidates:
        return ""

    return f"/images/{folder_name}/{candidates[0].name}"


def get_rag_collection():
    """Get or initialize the ChromaDB collection for plant RAG."""
    global rag_collection
    if rag_collection is None:
        try:
            client = chromadb.PersistentClient(path=RAG_DB_PATH)
            rag_collection = client.get_collection(
                name="plants",
            )
        except Exception as e:
            raise RuntimeError(f"Impossibile caricare il database RAG delle piante: {e}")
    return rag_collection


def ensure_plant_cards_cache_table(conn: Any) -> None:
    if _is_mysql_conn(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plant_cards_cache (
                species_name VARCHAR(255) NOT NULL,
                lang VARCHAR(10) NOT NULL,
                title TEXT NOT NULL,
                common_name TEXT,
                summary TEXT NOT NULL,
                markdown TEXT NOT NULL,
                images_json TEXT NOT NULL,
                source VARCHAR(64) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                PRIMARY KEY (species_name, lang)
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_plant_cards_cache_updated_at ON plant_cards_cache(updated_at)"
            )
        except Exception:
            pass
        conn.commit()
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plant_cards_cache (
            species_name TEXT NOT NULL,
            lang TEXT NOT NULL,
            title TEXT NOT NULL,
            common_name TEXT,
            summary TEXT NOT NULL,
            markdown TEXT NOT NULL,
            images_json TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (species_name, lang)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_plant_cards_cache_updated_at ON plant_cards_cache(updated_at)"
    )
    conn.commit()


def get_cached_plant_card(name: str, lang: str) -> dict[str, Any] | None:
    if not PLANT_CARD_CACHE_ENABLED:
        return None

    species_name = (name or "").strip()
    lang_code = (lang or "it").strip().lower()
    if not species_name:
        return None

    with get_plants_db_connection() as conn:
        ensure_plant_cards_cache_table(conn)
        row = conn.execute(
            (
                "SELECT title, common_name, summary, markdown, images_json, source, updated_at "
                "FROM plant_cards_cache "
                "WHERE lower(species_name) = lower(?) AND lower(lang) = lower(?) "
                "LIMIT 1"
            ),
            (species_name, lang_code),
        ).fetchone()

    if row is None:
        return None

    images: list[str] = []
    raw_images = row["images_json"] if "images_json" in row.keys() else "[]"
    try:
        parsed = json.loads(raw_images or "[]")
        if isinstance(parsed, list):
            images = [str(item) for item in parsed if str(item).strip()]
    except Exception:
        images = []

    return {
        "title": row["title"],
        "common_name": row["common_name"] or "",
        "markdown": row["markdown"],
        "summary": row["summary"],
        "images": images,
        "source": row["source"],
        "cache_updated_at": row["updated_at"],
    }


def upsert_cached_plant_card(name: str, lang: str, payload: dict[str, Any]) -> None:
    if not PLANT_CARD_CACHE_ENABLED:
        return

    species_name = (name or "").strip()
    lang_code = (lang or "it").strip().lower()
    if not species_name:
        return

    title = str(payload.get("title") or species_name)
    common_name = str(payload.get("common_name") or "")
    summary = str(payload.get("summary") or "")
    markdown = str(payload.get("markdown") or "")
    source = str(payload.get("source") or "rag")
    images = payload.get("images")
    images_json = json.dumps(images if isinstance(images, list) else [], ensure_ascii=False)
    updated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    with get_plants_db_connection() as conn:
        ensure_plant_cards_cache_table(conn)
        if _is_mysql_conn(conn):
            conn.execute(
                (
                    "INSERT INTO plant_cards_cache "
                    "(species_name, lang, title, common_name, summary, markdown, images_json, source, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON DUPLICATE KEY UPDATE "
                    "title=VALUES(title), "
                    "common_name=VALUES(common_name), "
                    "summary=VALUES(summary), "
                    "markdown=VALUES(markdown), "
                    "images_json=VALUES(images_json), "
                    "source=VALUES(source), "
                    "updated_at=VALUES(updated_at)"
                ),
                (
                    species_name,
                    lang_code,
                    title,
                    common_name,
                    summary,
                    markdown,
                    images_json,
                    source,
                    updated_at,
                ),
            )
        else:
            conn.execute(
                (
                    "INSERT INTO plant_cards_cache "
                    "(species_name, lang, title, common_name, summary, markdown, images_json, source, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(species_name, lang) DO UPDATE SET "
                    "title=excluded.title, "
                    "common_name=excluded.common_name, "
                    "summary=excluded.summary, "
                    "markdown=excluded.markdown, "
                    "images_json=excluded.images_json, "
                    "source=excluded.source, "
                    "updated_at=excluded.updated_at"
                ),
                (
                    species_name,
                    lang_code,
                    title,
                    common_name,
                    summary,
                    markdown,
                    images_json,
                    source,
                    updated_at,
                ),
            )
        conn.commit()


PLANT_PROFILE_FIELDS = (
    "species_name",
    "indexed",
    "annaffiatura_gg",
    "annaffiatura_time",
    "luce",
    "temperatura",
    "umidita",
    "altezza_media",
    "pulizia",
    "terriccio",
    "concimazione",
    "prevenzione",
    "updated_at",
)


def get_plants_db_connection() -> Any:
    if _is_mysql_enabled():
        mysql_params = _parse_mysql_components_from_env()
        return _MySQLCompatConnection(mysql_params or MY_SQL_CONNECTION_STRING)

    db_path = Path(PLANTS_SQLITE_PATH)
    if not db_path.exists():
        bundled_db = Path("data") / "plants.db"
        if bundled_db.exists() and bundled_db.resolve() != db_path.resolve():
            db_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(bundled_db, db_path)

    if not db_path.exists():
        raise HTTPException(status_code=503, detail="Database plants.db non disponibile.")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Quick sanity check to fail fast on non-SQLite files.
        conn.execute("PRAGMA schema_version").fetchone()
    except sqlite3.DatabaseError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Il file configurato in PLANTS_SQLITE_PATH non e un database SQLite valido: {db_path}. "
                f"Dettaglio: {exc}. "
                "Se usi MySQL, imposta MYSQL_ENABLED=1."
            ),
        ) from exc

    try:
        conn.execute("ALTER TABLE plants ADD COLUMN image_paths TEXT")
        conn.commit()
    except Exception:
        pass
    return conn


def _get_species_images_from_db(species_name: str) -> list[str]:
    query = "SELECT image_paths FROM plants WHERE lower(species_name) = lower(?) LIMIT 1"
    with get_plants_db_connection() as conn:
        row = conn.execute(query, (species_name.strip(),)).fetchone()

    if row is None:
        return []

    raw = row["image_paths"] if "image_paths" in row.keys() else None
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []

    if not isinstance(parsed, list):
        return []

    return [str(v).strip() for v in parsed if str(v).strip()]


def _sqlite_table_exists(conn: Any, table_name: str) -> bool:
    if _is_mysql_conn(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ? LIMIT 1",
            (table_name,),
        ).fetchone()
        return row is not None

    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    ).fetchone()
    return row is not None


def _migrate_user_plants_if_needed(user_conn: sqlite3.Connection) -> None:
    if _is_mysql_conn(user_conn):
        return

    user_db_path = Path(USER_PLANTS_SQLITE_PATH)
    plants_db_path = Path(PLANTS_SQLITE_PATH)

    try:
        if user_db_path.resolve() == plants_db_path.resolve():
            return
    except Exception:
        if str(user_db_path) == str(plants_db_path):
            return

    if not plants_db_path.exists():
        return

    if not _sqlite_table_exists(user_conn, "user_plants"):
        return

    dest_count = user_conn.execute("SELECT COUNT(1) AS c FROM user_plants").fetchone()["c"]
    if int(dest_count or 0) > 0:
        return

    src_conn = sqlite3.connect(plants_db_path)
    src_conn.row_factory = sqlite3.Row
    try:
        if not _sqlite_table_exists(src_conn, "user_plants"):
            return

        src_columns = {
            row["name"] for row in src_conn.execute("PRAGMA table_info(user_plants)").fetchall()
        }
        if "user_photo_url" in src_columns:
            rows = src_conn.execute(
                "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at FROM user_plants"
            ).fetchall()
        else:
            rows = src_conn.execute(
                "SELECT id, plant_name, user_given_name, user_id, user_email, NULL AS user_photo_url, created_at FROM user_plants"
            ).fetchall()

        if not rows:
            return

        user_conn.executemany(
            (
                "INSERT OR IGNORE INTO user_plants "
                "(id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)"
            ),
            [
                (
                    row["id"],
                    row["plant_name"],
                    row["user_given_name"],
                    row["user_id"],
                    row["user_email"],
                    row["user_photo_url"],
                    row["created_at"],
                )
                for row in rows
            ],
        )
        user_conn.commit()
    finally:
        src_conn.close()


def get_user_plants_db_connection() -> sqlite3.Connection:
    if _is_mysql_enabled():
        mysql_params = _parse_mysql_components_from_env()
        conn = _MySQLCompatConnection(mysql_params or MY_SQL_CONNECTION_STRING)
        ensure_user_plants_table(conn)
        ensure_registered_users_table(conn)
        ensure_recognition_logs_table(conn)
        return conn

    db_path = Path(USER_PLANTS_SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_user_plants_table(conn)
    ensure_registered_users_table(conn)
    ensure_recognition_logs_table(conn)
    _migrate_user_plants_if_needed(conn)
    return conn


def get_plant_profile_from_db(name: str) -> dict[str, Any] | None:
    query = (
        "SELECT species_name, indexed, annaffiatura_gg, annaffiatura_time, luce, temperatura, "
        "umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione, updated_at "
        "FROM plants WHERE lower(species_name) = lower(?) LIMIT 1"
    )

    with get_plants_db_connection() as conn:
        row = conn.execute(query, (name.strip(),)).fetchone()

    if row is None:
        return None

    payload = {field: row[field] for field in PLANT_PROFILE_FIELDS}
    payload["indexed"] = bool(payload["indexed"])
    payload["updated_at"] = _format_datetime_display(payload["updated_at"])
    return payload


def ensure_user_plants_table(conn: sqlite3.Connection) -> None:
    if _is_mysql_conn(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plants (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                plant_name VARCHAR(255) NOT NULL,
                user_given_name VARCHAR(255) NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                user_email VARCHAR(255) NULL,
                user_photo_url TEXT NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plant_photos (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                plant_id BIGINT NOT NULL,
                photo_url TEXT NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                FOREIGN KEY (plant_id) REFERENCES user_plants(id) ON DELETE CASCADE
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_user_plant_photos_plant_id ON user_plant_photos(plant_id)"
            )
        except Exception:
            pass
        conn.commit()
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_name TEXT NOT NULL,
            user_given_name TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_email TEXT,
            user_photo_url TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    # Add user_photo_url column to existing databases (migration)
    try:
        conn.execute("ALTER TABLE user_plants ADD COLUMN user_photo_url TEXT")
        conn.commit()
    except Exception:
        pass  # Column already exists

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_plant_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL,
            photo_url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (plant_id) REFERENCES user_plants(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_plant_photos_plant_id ON user_plant_photos(plant_id)"
    )
    conn.commit()


def ensure_registered_users_table(conn: sqlite3.Connection) -> None:
    if _is_mysql_conn(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registered_users (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                google_sub VARCHAR(255) NOT NULL UNIQUE,
                email VARCHAR(255) NOT NULL,
                registered_at VARCHAR(40) NOT NULL
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_registered_users_email ON registered_users(email)"
            )
        except Exception:
            pass
        conn.commit()
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registered_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            google_sub TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            registered_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_registered_users_email ON registered_users(email)"
    )
    conn.commit()


def ensure_recognition_logs_table(conn: sqlite3.Connection) -> None:
    if _is_mysql_conn(conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recognition_logs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(255) NOT NULL,
                user_email VARCHAR(255) NULL,
                user_type VARCHAR(16) NOT NULL,
                chosen_species VARCHAR(255) NOT NULL,
                image_url TEXT NULL,
                used_openai TINYINT(1) NOT NULL DEFAULT 0,
                recognition_ms INT NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_created_at ON recognition_logs(created_at)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_species ON recognition_logs(chosen_species)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_user_id ON recognition_logs(user_id)"
            )
        except Exception:
            pass
        conn.commit()
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS recognition_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_email TEXT,
            user_type TEXT NOT NULL,
            chosen_species TEXT NOT NULL,
            image_url TEXT,
            used_openai INTEGER NOT NULL DEFAULT 0,
            recognition_ms INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    # Migration: add recognition_ms to existing databases.
    try:
        conn.execute("ALTER TABLE recognition_logs ADD COLUMN recognition_ms INTEGER")
        conn.commit()
    except Exception:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recognition_logs_created_at ON recognition_logs(created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recognition_logs_species ON recognition_logs(chosen_species)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_recognition_logs_user_id ON recognition_logs(user_id)"
    )
    conn.commit()


def create_recognition_log(
    chosen_species: str,
    used_openai: bool,
    image_url: str | None,
    recognition_ms: int | None,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    species_clean = str(chosen_species or "").strip()
    if not species_clean:
        raise HTTPException(status_code=400, detail="Specie scelta obbligatoria.")

    user_id = str((user or {}).get("sub") or "").strip() or "guest"
    user_email = str((user or {}).get("email") or "").strip() or None
    user_type = "user" if user and user_id != "guest" else "guest"
    image_url_clean = str(image_url or "").strip() or None
    recognition_ms_value = None if recognition_ms is None else max(0, int(recognition_ms))
    created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    with get_user_plants_db_connection() as conn:
        ensure_recognition_logs_table(conn)
        cursor = conn.execute(
            (
                "INSERT INTO recognition_logs "
                "(user_id, user_email, user_type, chosen_species, image_url, used_openai, recognition_ms, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                user_id,
                user_email,
                user_type,
                species_clean,
                image_url_clean,
                1 if used_openai else 0,
                recognition_ms_value,
                created_at,
            ),
        )
        conn.commit()

    return {
        "id": int(cursor.lastrowid),
        "user_id": user_id,
        "user_email": user_email,
        "user_type": user_type,
        "chosen_species": species_clean,
        "image_url": image_url_clean,
        "used_openai": bool(used_openai),
        "recognition_ms": recognition_ms_value,
        "created_at": created_at,
    }


def get_recognition_admin_aggregates(conn: sqlite3.Connection, chart_days: int = 30) -> dict[str, Any]:
    ensure_recognition_logs_table(conn)

    safe_days = int(chart_days) if chart_days in (7, 30, 90) else 30
    window_start = (datetime.utcnow() - timedelta(days=safe_days - 1)).strftime("%Y-%m-%d") + "T00:00:00Z"

    totals = conn.execute(
        """
        SELECT
            COUNT(1) AS total,
            SUM(CASE WHEN user_type = 'guest' THEN 1 ELSE 0 END) AS guest_total,
            SUM(CASE WHEN user_type = 'user' THEN 1 ELSE 0 END) AS user_total,
            SUM(CASE WHEN used_openai = 1 THEN 1 ELSE 0 END) AS openai_total,
            SUM(CASE WHEN image_url IS NOT NULL AND trim(image_url) <> '' THEN 1 ELSE 0 END) AS with_image_total,
            COUNT(recognition_ms) AS timed_total,
            AVG(recognition_ms * 1.0) AS avg_recognition_ms
        FROM recognition_logs
        WHERE created_at >= ?
        """
        ,
        (window_start,),
    ).fetchone()

    top_species_rows = conn.execute(
        """
        SELECT chosen_species, COUNT(1) AS count
        FROM recognition_logs
        WHERE created_at >= ?
        GROUP BY chosen_species
        ORDER BY count DESC, chosen_species ASC
        LIMIT 8
        """
        ,
        (window_start,),
    ).fetchall()

    daily_rows = conn.execute(
        """
        SELECT
            substr(created_at, 1, 10) AS day,
            COUNT(1) AS total,
            SUM(CASE WHEN used_openai = 1 THEN 1 ELSE 0 END) AS openai
        FROM recognition_logs
        WHERE created_at >= ?
        GROUP BY substr(created_at, 1, 10)
        ORDER BY day DESC
        LIMIT ?
        """
        ,
        (window_start, safe_days),
    ).fetchall()

    daily_series = [
        {
            "day": str(row["day"] or ""),
            "total": int(row["total"] or 0),
            "openai": int(row["openai"] or 0),
        }
        for row in reversed(daily_rows)
    ]

    top_species = [
        {
            "species": str(row["chosen_species"] or ""),
            "count": int(row["count"] or 0),
        }
        for row in top_species_rows
    ]

    return {
        "chart_days": safe_days,
        "total": int((totals["total"] or 0) if totals else 0),
        "guest_total": int((totals["guest_total"] or 0) if totals else 0),
        "user_total": int((totals["user_total"] or 0) if totals else 0),
        "openai_total": int((totals["openai_total"] or 0) if totals else 0),
        "with_image_total": int((totals["with_image_total"] or 0) if totals else 0),
        "avg_recognition_ms": (
            float(totals["avg_recognition_ms"])
            if totals and int(totals["timed_total"] or 0) > 0 and totals["avg_recognition_ms"] is not None
            else None
        ),
        "top_species": top_species,
        "daily_series": daily_series,
    }


def register_google_user_if_needed(user: dict[str, Any]) -> tuple[bool, str]:
    google_sub = str(user.get("sub") or "").strip()
    email = str(user.get("email") or "").strip()
    if not google_sub or not email:
        return False, ""

    with get_user_plants_db_connection() as conn:
        ensure_registered_users_table(conn)
        existing = conn.execute(
            "SELECT registered_at FROM registered_users WHERE google_sub = ? LIMIT 1",
            (google_sub,),
        ).fetchone()
        if existing:
            return False, str(existing["registered_at"] or "")

        registered_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        conn.execute(
            (
                "INSERT INTO registered_users "
                "(google_sub, email, registered_at) VALUES (?, ?, ?)"
            ),
            (google_sub, email, registered_at),
        )
        conn.commit()
        return True, registered_at


def list_registered_users_for_admin(limit: int = 300) -> list[dict[str, Any]]:
    max_limit = max(1, min(int(limit), 1000))
    with get_user_plants_db_connection() as conn:
        ensure_registered_users_table(conn)
        rows = conn.execute(
            (
                "SELECT email, registered_at "
                "FROM registered_users "
                "ORDER BY registered_at DESC "
                "LIMIT ?"
            ),
            (max_limit,),
        ).fetchall()
        return [
            {
                "email": str(row["email"] or ""),
                "registered_at": str(row["registered_at"] or ""),
                "registered_at_display": _format_datetime_display(row["registered_at"]),
            }
            for row in rows
        ]


def _is_admin_email(email: str) -> bool:
    normalized = str(email or "").strip().lower()
    return bool(normalized) and normalized in ADMIN_USERS


def _require_admin_user(authorization: str | None) -> dict[str, Any]:
    user = _get_google_user_from_authorization(authorization, require_auth=True)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google.")

    if not _is_admin_email(str(user.get("email") or "")):
        raise HTTPException(status_code=403, detail="Accesso admin non autorizzato.")
    return user


def _get_user_plant_photo_urls(conn: sqlite3.Connection, plant_id: int, fallback_url: str | None) -> list[str]:
    rows = conn.execute(
        "SELECT photo_url FROM user_plant_photos WHERE plant_id = ? ORDER BY id DESC",
        (plant_id,),
    ).fetchall()
    urls = [str(r["photo_url"] or "").strip() for r in rows if str(r["photo_url"] or "").strip()]
    if urls:
        return urls

    fallback = str(fallback_url or "").strip()
    return [fallback] if fallback else []


def _user_plant_row_to_payload(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    plant_id = int(row["id"])
    fallback_photo = row["user_photo_url"] if "user_photo_url" in row.keys() else None
    photo_urls = _get_user_plant_photo_urls(conn, plant_id, fallback_photo)
    return {
        "id": plant_id,
        "plant_name": row["plant_name"],
        "user_given_name": row["user_given_name"],
        "user": row["user_email"] or row["user_id"],
        "user_photo_url": (photo_urls[0] if photo_urls else None),
        "user_photos": photo_urls,
        "created_at_iso": row["created_at"],
        "created_at": _format_datetime_display(row["created_at"]),
    }


def create_user_plant(plant_name: str, user_given_name: str, user: dict[str, Any]) -> dict[str, Any]:
    plant_name_clean = plant_name.strip()
    user_given_name_clean = user_given_name.strip()
    user_id = str(user.get("sub") or "").strip()
    user_email = str(user.get("email") or "").strip()
    created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    if not plant_name_clean:
        raise HTTPException(status_code=400, detail="Nome pianta obbligatorio.")
    if not user_given_name_clean:
        raise HTTPException(status_code=400, detail="Nome scelto dall'utente obbligatorio.")
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente Google non valido.")

    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        cursor = conn.execute(
            (
                "INSERT INTO user_plants "
                "(plant_name, user_given_name, user_id, user_email, created_at) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            (plant_name_clean, user_given_name_clean, user_id, user_email, created_at),
        )
        conn.commit()

        row = conn.execute(
            (
                "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                "FROM user_plants WHERE id = ?"
            ),
            (cursor.lastrowid,),
        ).fetchone()
        return _user_plant_row_to_payload(conn, row)


def list_user_plants(user: dict[str, Any]) -> list[dict[str, Any]]:
    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente Google non valido.")

    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        rows = conn.execute(
            (
                "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                "FROM user_plants WHERE user_id = ? ORDER BY id DESC"
            ),
            (user_id,),
        ).fetchall()
        return [_user_plant_row_to_payload(conn, row) for row in rows]


def delete_user_plant_by_id(user: dict[str, Any], plant_id: int) -> bool:
    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente Google non valido.")

    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        existing = conn.execute(
            "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
            (plant_id, user_id),
        ).fetchone()

        if existing is None:
            return False

        conn.execute(
            "DELETE FROM user_plant_photos WHERE plant_id = ?",
            (plant_id,),
        )

        conn.execute(
            "DELETE FROM user_plants WHERE id = ? AND user_id = ?",
            (plant_id, user_id),
        )
        conn.commit()

    return True


def update_user_plant_created_at_by_id(user: dict[str, Any], plant_id: int, created_at_iso: str) -> dict[str, Any] | None:
    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente Google non valido.")

    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        existing = conn.execute(
            "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
            (plant_id, user_id),
        ).fetchone()

        if existing is None:
            return None

        conn.execute(
            "UPDATE user_plants SET created_at = ? WHERE id = ? AND user_id = ?",
            (created_at_iso, plant_id, user_id),
        )
        conn.commit()

        row = conn.execute(
            (
                "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                "FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1"
            ),
            (plant_id, user_id),
        ).fetchone()
        if row is None:
            return None
        return _user_plant_row_to_payload(conn, row)


def _build_profile_context(profile: dict[str, Any] | None) -> str:
    if not profile:
        return ""

    labels = {
        "species_name": "Specie",
        "indexed": "Presente in RAG",
        "annaffiatura_gg": "Annaffiatura ogni giorni",
        "annaffiatura_time": "Momento annaffiatura",
        "luce": "Luce",
        "temperatura": "Temperatura",
        "umidita": "Umidita",
        "altezza_media": "Altezza media",
        "pulizia": "Pulizia",
        "terriccio": "Terriccio",
        "concimazione": "Concimazione",
        "prevenzione": "Prevenzione",
        "updated_at": "Ultimo aggiornamento",
    }

    lines = []
    for field in PLANT_PROFILE_FIELDS:
        value = profile.get(field)
        if value is None or value == "":
            continue
        if field == "indexed":
            value = "si" if value else "no"
        lines.append(f"- {labels[field]}: {value}")

    if not lines:
        return ""

    return "Dati strutturati estratti da plants.db:\n" + "\n".join(lines)

app = FastAPI(title="PlantCLEF Image Search API")

cors_origins_raw = os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
cors_origins = [origin.strip() for origin in cors_origins_raw.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve PWA static assets generated by Vite build.
app.mount(
    "/assets",
    StaticFiles(directory=str(PWA_DIST_DIR / "assets"), check_dir=False),
    name="pwa-assets",
)
app.mount(
    "/icons",
    StaticFiles(directory=str(PWA_DIST_DIR / "icons"), check_dir=False),
    name="pwa-icons",
)


def get_search_backend_status():
    checks: dict[str, str] = {}
    for module_name in ("torch", "faiss", "open_clip"):
        try:
            __import__(module_name)
            checks[module_name] = "ok"
        except Exception as e:
            checks[module_name] = f"{type(e).__name__}: {e}"

    files = {
        "index_exists": os.path.exists(INDEX_PATH),
        "cache_exists": os.path.exists(CACHE_PATH),
        "index_path": INDEX_PATH,
        "cache_path": CACHE_PATH,
    }

    native_ok = all(value == "ok" for value in checks.values())
    ready = native_ok and files["index_exists"] and files["cache_exists"]
    return {"ready": ready, "modules": checks, "files": files}


def get_catalog_and_faiss_stats() -> dict[str, Any]:
    species_db_total = 0
    species_rag_total = 0
    catalog_ok = False
    catalog_error = ""

    try:
        with get_plants_db_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT lower(species_name)) AS c FROM plants"
            ).fetchone()
            species_db_total = int((row["c"] if row else 0) or 0)

            row_rag = conn.execute(
                "SELECT COUNT(DISTINCT lower(species_name)) AS c FROM plants WHERE indexed = 1"
            ).fetchone()
            species_rag_total = int((row_rag["c"] if row_rag else 0) or 0)
            catalog_ok = True
    except Exception as exc:
        catalog_error = f"{type(exc).__name__}: {exc}"

    faiss_ok = False
    faiss_error = ""
    plantclef_images_total = 0
    plantclef_species_total = 0
    leafsnap_images_total = 0
    leafsnap_species_total = 0

    try:
        loaded_index = get_index()
        plantclef_labels = list(getattr(loaded_index, "plantclef_labels", []) or [])
        leafsnap_labels = list(getattr(loaded_index, "leafsnap_labels", []) or [])

        plantclef_images_total = len(plantclef_labels)
        plantclef_species_total = len({str(v).strip().lower() for v in plantclef_labels if str(v).strip()})
        leafsnap_images_total = len(leafsnap_labels)
        leafsnap_species_total = len({str(v).strip().lower() for v in leafsnap_labels if str(v).strip()})
        faiss_ok = True
    except Exception as exc:
        faiss_error = f"{type(exc).__name__}: {exc}"

    return {
        "catalog": {
            "ok": catalog_ok,
            "error": catalog_error,
            "species_db_total": species_db_total,
            "species_rag_total": species_rag_total,
        },
        "faiss": {
            "ok": faiss_ok,
            "error": faiss_error,
            "plantclef": {
                "images_total": plantclef_images_total,
                "species_total": plantclef_species_total,
            },
            "leafsnap": {
                "images_total": leafsnap_images_total,
                "species_total": leafsnap_species_total,
            },
        },
    }


def get_public_app_config() -> dict[str, Any]:
    return {
        "google_client_id": GOOGLE_CLIENT_IDS[0] if GOOGLE_CLIENT_IDS else "",
        "require_google_auth": REQUIRE_GOOGLE_AUTH,
    }


@app.get("/app-config")
def app_config():
    return JSONResponse(content=get_public_app_config())


class PlantChatRequest(BaseModel):
    plant_name: str = Field(..., min_length=2, description="Nome comune o scientifico della pianta")
    question: str = Field(..., min_length=3, description="Domanda sulla cura della pianta")
    lang: str = Field("it", description="Lingua Wikipedia da usare per il contesto")


class SaveUserPlantRequest(BaseModel):
    plant_name: str = Field(..., min_length=2, description="Nome della specie trovata")
    user_given_name: str = Field(..., min_length=1, max_length=80, description="Nome scelto dall'utente")


class UpdateFirstWateringDateRequest(BaseModel):
    first_watering_date: str = Field(
        ...,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Data prima innaffiatura in formato YYYY-MM-DD",
    )


class GoogleAuthRequest(BaseModel):
    id_token: str = Field(..., min_length=20, description="Google ID token")


class RecognitionLogRequest(BaseModel):
    chosen_species: str = Field(..., min_length=2, max_length=120, description="Specie selezionata")
    used_openai: bool = Field(default=False, description="True se nel riconoscimento e stato usato OpenAI")
    image_url: str | None = Field(default=None, max_length=1200, description="URL immagine se salvata")
    recognition_ms: int | None = Field(default=None, ge=0, le=300000, description="Durata riconoscimento in ms")


def _validate_google_token(id_token: str) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=8.0) as client:
            response = client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Errore verifica token Google: {e}")

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Token Google non valido.")

    payload = response.json()
    audience = str(payload.get("aud") or "")

    if GOOGLE_CLIENT_IDS and audience not in GOOGLE_CLIENT_IDS:
        raise HTTPException(status_code=401, detail="Token Google con client_id non autorizzato.")

    return payload


def _get_google_user_from_authorization(
    authorization: str | None,
    require_auth: bool | None = None,
) -> dict[str, Any] | None:
    if require_auth is None:
        require_auth = REQUIRE_GOOGLE_AUTH

    if not authorization:
        if require_auth:
            raise HTTPException(status_code=401, detail="Authorization Bearer richiesta.")
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Header Authorization non valido.")

    validated = _validate_google_token(token.strip())
    return {
        "sub": validated.get("sub", ""),
        "email": validated.get("email", ""),
        "name": validated.get("name", ""),
        "picture": validated.get("picture", ""),
    }


def fetch_wikipedia_text_context(name: str, lang: str):
    base = f"https://{lang}.wikipedia.org"
    wiki_headers = {
        "User-Agent": WIKI_USER_AGENT,
        "Accept": "application/json",
    }

    with httpx.Client(timeout=10.0, headers=wiki_headers, follow_redirects=True) as client:
        search_resp = client.get(
            f"{base}/w/api.php",
            params={
                "action": "opensearch",
                "search": name,
                "limit": 1,
                "format": "json",
            },
        )
        titles = []
        if search_resp.status_code == 200:
            search_data = search_resp.json()
            titles = search_data[1]

        if not titles:
            query_resp = client.get(
                f"{base}/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": name,
                    "srlimit": 1,
                    "format": "json",
                },
            )
            if query_resp.status_code == 200:
                query_data = query_resp.json()
                items = query_data.get("query", {}).get("search", [])
                if items:
                    titles = [items[0].get("title", "")]

        if not titles:
            raise HTTPException(status_code=404, detail=f"Nessuna pagina Wikipedia trovata per '{name}'.")

        page_title = titles[0]
        safe_title = page_title.replace(" ", "_")

        summary_resp = client.get(f"{base}/api/rest_v1/page/summary/{safe_title}")
        summary_resp.raise_for_status()
        summary = summary_resp.json()

        long_resp = client.get(
            f"{base}/w/api.php",
            params={
                "action": "query",
                "prop": "extracts",
                "titles": page_title,
                "explaintext": 1,
                "redirects": 1,
                "format": "json",
            },
        )
        long_text = ""
        if long_resp.status_code == 200:
            long_data = long_resp.json()
            pages = long_data.get("query", {}).get("pages", {})
            if isinstance(pages, dict) and pages:
                first_page = next(iter(pages.values()))
                long_text = (first_page.get("extract") or "").strip()

    title = summary.get("title", page_title)
    extract = summary.get("extract", "Nessuna descrizione disponibile.")
    page_url = summary.get("content_urls", {}).get("desktop", {}).get("page", f"{base}/wiki/{safe_title}")

    extended_text = ""
    if long_text:
        if long_text.startswith(extract):
            extended_text = long_text[len(extract):].strip()
        else:
            extended_text = long_text

    thumbnail = summary.get("thumbnail", {}).get("source", "")

    return {
        "title": title,
        "summary": extract,
        "extended_text": extended_text,
        "wikipedia_url": page_url,
        "thumbnail": thumbnail,
    }


def get_index():
    global index
    if index is None:
        try:
            from plentclef import PlentClefIndex

            leafsnap_aliases: dict[str, str] = {}
            try:
                with get_plants_db_connection() as _conn:
                    rows = _conn.execute(
                        "SELECT leafsnap_label, db_species_name FROM leafsnap_aliases"
                    ).fetchall()
                    normalized_rows = [
                        (
                            r["leafsnap_label"] if hasattr(r, "keys") else r[0],
                            r["db_species_name"] if hasattr(r, "keys") else r[1],
                        )
                        for r in rows
                    ]
                    leafsnap_aliases = {
                        str(left): str(right) for left, right in normalized_rows if left and right
                    }
            except Exception:
                pass  # table may not exist yet; aliases simply won't be applied

            index = PlentClefIndex(
                model_name=MODEL_NAME,
                index_path=INDEX_PATH,
                index_cache=CACHE_PATH,
                leafsnap_index_path=LEAFSNAP_INDEX_PATH,
                leafsnap_cache_path=LEAFSNAP_CACHE_PATH,
                leafsnap_aliases=leafsnap_aliases,
            )
        except Exception as e:
            cause = f"{type(e).__name__}: {e}"
            raise RuntimeError(
                "Impossibile inizializzare il motore di ricerca immagini. "
                "Probabile blocco di sicurezza su librerie native (es. torch/faiss). "
                f"Dettaglio: {cause}."
            ) from e
    return index


@app.post("/search")
async def search_similar(
    file: UploadFile = File(..., description="Immagine della pianta da ricercare"),
    k: int = Query(default=5, ge=1, le=50, description="Numero di risultati da restituire"),
    authorization: str | None = Header(default=None),
):
    started_at = datetime.utcnow()
    _get_google_user_from_authorization(authorization, require_auth=False)
    _log_api(
        "/search",
        "input",
        {
            "filename": file.filename,
            "content_type": file.content_type,
            "k": k,
        },
    )

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Il file caricato non è un'immagine valida.")

    suffix = os.path.splitext(file.filename or "")[1] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        loaded_index = get_index()
        # Pass debug=True to enable detailed logging of FAISS scoring
        debug_candidates = max(
            k,
            min(500, int(os.getenv("SEARCH_DEBUG_CANDIDATES", "50"))),
        )
        results, top_planclef_score = loaded_index.search(
            tmp_path,
            loaded_index.plantclef_labels,
            k=k,
            debug=True,
            search_k=debug_candidates,
            return_scores=True,
        )

        # GPT-4o vision fallback when FAISS confidence is low
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        gpt_species: str | None = None
        gpt_job_status: dict[str, Any] | None = None
        gpt_fallback_attempted = False
        gpt_fallback_reason = "not_attempted"
        should_trigger_gpt, gpt_trigger_basis = _should_trigger_gpt_fallback(top_planclef_score, results)
        if should_trigger_gpt and api_key:
            gpt_fallback_attempted = True
            logger.info(
                "Activating GPT-4o vision fallback: "
                f"basis={gpt_trigger_basis}, top_planclef_score={top_planclef_score:.4f}, "
                f"threshold={FAISS_CONFIDENCE_THRESHOLD}"
            )
            fallback_candidates = [species for species, _, _ in results[:12]]
            gpt_species, gpt_fallback_reason = _gpt_vision_identify_plant(
                tmp_path,
                api_key,
                candidate_species=fallback_candidates,
            )
            if gpt_species:
                logger.info(f"GPT-4o identified: '{gpt_species}'")
                _insert_draft_plant_if_missing(gpt_species, api_key)
                gpt_job_status = _ensure_species_build_job(gpt_species)
                # Prepend GPT result at score 1.0, avoid duplicates
                results = [(gpt_species, 1.0, [])] + [
                    r for r in results if r[0].lower() != gpt_species.lower()
                ]
                results = results[:k]
            else:
                logger.info(f"GPT fallback attempted but no species accepted: {gpt_fallback_reason}")
        elif should_trigger_gpt:
            gpt_fallback_reason = "OPENAI_API_KEY missing"

    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # Determine is_draft for each result (indexed=0 in plants.db)
    draft_species: set[str] = set()
    try:
        species_names = [r[0] for r in results]
        with get_plants_db_connection() as conn:
            placeholders = ",".join("?" * len(species_names))
            rows = conn.execute(
                f"SELECT species_name, indexed FROM plants WHERE lower(species_name) IN ({placeholders})",
                [n.lower() for n in species_names],
            ).fetchall()
            indexed_map = {row["species_name"].lower(): bool(row["indexed"]) for row in rows}
        for name in species_names:
            if not indexed_map.get(name.lower(), True):
                draft_species.add(name.lower())
    except Exception as exc:
        logger.warning(f"Could not determine draft status for results: {exc}")

    _log_api(
        "/search",
        "results",
        {
            "k": k,
            "top_planclef_score": top_planclef_score if 'top_planclef_score' in dir() else None,
            "gpt_fallback_attempted": gpt_fallback_attempted if 'gpt_fallback_attempted' in dir() else False,
            "gpt_fallback_used": gpt_species is not None if 'gpt_species' in dir() else False,
            "gpt_fallback_reason": gpt_fallback_reason if 'gpt_fallback_reason' in dir() else "not_attempted",
            "gpt_trigger_basis": gpt_trigger_basis if 'gpt_trigger_basis' in dir() else "not_evaluated",
            "gpt_job_status": gpt_job_status if 'gpt_job_status' in dir() else None,
            "species_found": [species for species, _, _ in results],
            "scores": [float(score) for _, score, _ in results],
            "draft_species": list(draft_species),
        },
    )

    return JSONResponse(
        content={
            "results": [
                {
                    "species": species,
                    "score": float(score),
                    "is_draft": species.lower() in draft_species,
                    "build_status": _species_build_status(species),
                }
                for species, score, _ in results
            ],
            "gpt_fallback_used": gpt_species is not None if 'gpt_species' in dir() else False,
            "recognition_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
        }
    )


@app.middleware("http")
async def log_requests(request, call_next):
    request_id = uuid4().hex[:8]
    started_at = datetime.utcnow()
    _log_api(
        request.url.path,
        "request",
        {
            "request_id": request_id,
            "method": request.method,
            "query": str(request.url.query or ""),
        },
    )

    try:
        response = await call_next(request)
    except Exception as exc:
        _log_api(
            request.url.path,
            "error",
            {
                "request_id": request_id,
                "elapsed_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise

    _log_api(
        request.url.path,
        "response",
        {
            "request_id": request_id,
            "elapsed_ms": int((datetime.utcnow() - started_at).total_seconds() * 1000),
            **_response_payload_for_log(response),
        },
    )
    return response


@app.post("/auth/google")
def auth_google(payload: GoogleAuthRequest):
    validated = _validate_google_token(payload.id_token)

    user = {
        "sub": validated.get("sub", ""),
        "email": validated.get("email", ""),
        "name": validated.get("name", ""),
        "picture": validated.get("picture", ""),
    }
    is_new_user, registered_at = register_google_user_if_needed(user)
    is_admin = _is_admin_email(str(user.get("email") or ""))

    return JSONResponse(
        content={
            "ok": True,
            "user": user,
            "is_admin": is_admin,
            "is_new_user": is_new_user,
            "registered_at": registered_at,
            "expires_at": validated.get("exp", ""),
            "aud": validated.get("aud", ""),
        }
    )


@app.get("/admin/console")
def get_admin_console(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    chart_days: int = Query(default=30, ge=7, le=90),
):
    admin_user = _require_admin_user(authorization)
    users = list_registered_users_for_admin(limit=limit)
    inventory = get_catalog_and_faiss_stats()
    with get_user_plants_db_connection() as conn:
        ensure_recognition_logs_table(conn)
        total_registered = conn.execute("SELECT COUNT(1) AS c FROM registered_users").fetchone()["c"]
        total_saved_plants = conn.execute("SELECT COUNT(1) AS c FROM user_plants").fetchone()["c"]
        total_external_user_images = conn.execute(
            "SELECT COUNT(1) AS c FROM user_plant_photos WHERE photo_url IS NOT NULL AND trim(photo_url) <> ''"
        ).fetchone()["c"]
        recognition = get_recognition_admin_aggregates(conn, chart_days=chart_days)

    return JSONResponse(
        content={
            "ok": True,
            "admin_email": admin_user.get("email", ""),
            "stats": {
                "registered_users_total": int(total_registered or 0),
                "saved_plants_total": int(total_saved_plants or 0),
                "external_user_images_total": int(total_external_user_images or 0),
            },
            "recognition": {
                "chart_days": recognition["chart_days"],
                "total": recognition["total"],
                "guest_total": recognition["guest_total"],
                "user_total": recognition["user_total"],
                "openai_total": recognition["openai_total"],
                "with_image_total": recognition["with_image_total"],
                "avg_recognition_ms": recognition["avg_recognition_ms"],
            },
            "charts": {
                "top_species": recognition["top_species"],
                "daily_series": recognition["daily_series"],
            },
            "inventory": inventory,
            "users": users,
        }
    )


@app.post("/recognitions/log")
def log_recognition(payload: RecognitionLogRequest, authorization: str | None = Header(default=None)):
    user = _get_google_user_from_authorization(authorization, require_auth=False)
    created = create_recognition_log(
        chosen_species=payload.chosen_species,
        used_openai=bool(payload.used_openai),
        image_url=payload.image_url,
        recognition_ms=payload.recognition_ms,
        user=user,
    )

    return JSONResponse(content={"saved": created})


@app.post("/user/plants")
def save_user_plant(payload: SaveUserPlantRequest, authorization: str | None = Header(default=None)):
    user = _get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per salvare una pianta.")

    saved = create_user_plant(
        plant_name=payload.plant_name,
        user_given_name=payload.user_given_name,
        user=user,
    )

    _log_api(
        "/user/plants",
        "saved",
        {
            "plant_name": saved["plant_name"],
            "user_given_name": saved["user_given_name"],
            "user": saved["user"],
        },
    )

    return JSONResponse(content={"saved": saved})


@app.get("/user/plants")
def get_user_plants(authorization: str | None = Header(default=None)):
    user = _get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per vedere le tue piante.")

    items = list_user_plants(user)
    return JSONResponse(content={"items": items})


@app.delete("/user/plants/{plant_id}")
def delete_user_plant(plant_id: int, authorization: str | None = Header(default=None)):
    user = _get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per eliminare una pianta.")

    deleted = delete_user_plant_by_id(user=user, plant_id=plant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pianta salvata non trovata.")

    _log_api("/user/plants/{plant_id}", "deleted", {"plant_id": plant_id})
    return JSONResponse(content={"deleted": True, "id": plant_id})


@app.patch("/user/plants/{plant_id}/first-watering-date")
def update_user_plant_first_watering_date(
    plant_id: int,
    payload: UpdateFirstWateringDateRequest,
    authorization: str | None = Header(default=None),
):
    user = _get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per aggiornare la data.")

    created_at_iso = f"{payload.first_watering_date}T00:00:00Z"
    updated = update_user_plant_created_at_by_id(user=user, plant_id=plant_id, created_at_iso=created_at_iso)
    if updated is None:
        raise HTTPException(status_code=404, detail="Pianta salvata non trovata.")

    _log_api(
        "/user/plants/{plant_id}/first-watering-date",
        "updated",
        {"plant_id": plant_id, "created_at_iso": updated["created_at_iso"]},
    )

    return JSONResponse(content={"updated": updated})


@app.post("/user/plants/{plant_id}/photo")
async def upload_user_plant_photo(
    plant_id: int,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    """Upload a user photo for a saved plant, store it on Cloudinary."""
    user = _get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per caricare una foto.")

    if not (CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET):
        raise HTTPException(status_code=503, detail="Servizio foto non configurato.")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Il file caricato non è un'immagine valida.")

    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente non valido.")

    # Verify the plant belongs to this user
    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        row = conn.execute(
            "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
            (plant_id, user_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Pianta non trovata.")

    suffix = os.path.splitext(file.filename or "")[1] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        result = cloudinary.uploader.upload(
            tmp_path,
            folder="clorofilla/user-plants",
            public_id=f"plant_{plant_id}_user_{user_id[:12]}_{uuid4().hex[:10]}",
            overwrite=False,
            resource_type="image",
            transformation=[{"width": 1200, "crop": "limit", "quality": "auto:good"}],
        )
        photo_url = result.get("secure_url", "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore upload foto: {e}")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # Save URL to DB
    with get_user_plants_db_connection() as conn:
        ensure_user_plants_table(conn)
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        conn.execute(
            "INSERT INTO user_plant_photos (plant_id, photo_url, created_at) VALUES (?, ?, ?)",
            (plant_id, photo_url, created_at),
        )
        conn.execute(
            "UPDATE user_plants SET user_photo_url = ? WHERE id = ? AND user_id = ?",
            (photo_url, plant_id, user_id),
        )
        conn.commit()
        updated_row = conn.execute(
            "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
            "FROM user_plants WHERE id = ?",
            (plant_id,),
        ).fetchone()
        updated_payload = _user_plant_row_to_payload(conn, updated_row)

    _log_api("/user/plants/{plant_id}/photo", "uploaded", {"plant_id": plant_id})
    return JSONResponse(content={"updated": updated_payload})


@app.get("/health")
def health():
    status = get_search_backend_status()
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "search_backend_ready": status["ready"],
    }


@app.get("/search/status")
def search_status():
    return get_search_backend_status()


@app.get("/sw.js")
def pwa_sw_js():
    return _serve_pwa_file("sw.js", media_type="application/javascript")


@app.get("/registerSW.js")
def pwa_register_sw_js():
    return _serve_pwa_file("registerSW.js", media_type="application/javascript")


@app.get("/manifest.webmanifest")
def pwa_manifest():
    return _serve_pwa_file("manifest.webmanifest", media_type="application/manifest+json")


@app.get("/favicon.ico")
def pwa_favicon():
    try:
        return _serve_pwa_file("favicon.ico", media_type="image/x-icon")
    except HTTPException:
        return _serve_pwa_file("icons/favicon.svg", media_type="image/svg+xml")


@app.get("/species/previews")
def species_previews(
    names: list[str] = Query(default=[], description="Nomi specie da risolvere per anteprima immagine"),
    authorization: str | None = Header(default=None),
):
    _get_google_user_from_authorization(authorization, require_auth=False)
    if not names:
        return JSONResponse(content={"previews": {}})

    previews = {name: _get_species_preview_image_url(name) for name in names}
    return JSONResponse(content={"previews": previews})


@app.get("/species/common-names")
def species_common_names(
    names: list[str] = Query(default=[], description="Nomi specie di cui ottenere il nome comune"),
    authorization: str | None = Header(default=None),
):
    _get_google_user_from_authorization(authorization, require_auth=False)
    if not names:
        return JSONResponse(content={"common_names": {}})

    try:
        collection = get_rag_collection()
    except Exception:
        return JSONResponse(content={"common_names": {}})

    result_map: dict[str, str] = {}
    for name in names:
        try:
            res = collection.get(
                where={"species_name": {"$eq": name}},
                limit=1,
            )
            metadatas = res.get("metadatas", []) if res else []
            meta = metadatas[0] if metadatas else {}
            result_map[name] = meta.get("common_name", "") or ""
        except Exception:
            result_map[name] = ""

    return JSONResponse(content={"common_names": result_map})


@app.get("/species/{name}/build-status")
def species_build_status(name: str, authorization: str | None = Header(default=None)):
    _get_google_user_from_authorization(authorization, require_auth=False)
    status = _species_build_status(name)
    profile = get_plant_profile_from_db(name)
    ready = bool(profile and profile.get("indexed"))
    return JSONResponse(content={"species": name, "ready": ready, "status": status})


@app.get("/", response_class=HTMLResponse)
def ui():
    return _serve_landing_page()


@app.get("/app", response_class=HTMLResponse)
@app.get("/app/", response_class=HTMLResponse)
def pwa_app():
    return _serve_pwa_index()



@app.get("/images/{full_path:path}")
def get_image(full_path: str):
    """Serve local plant images from the RAG data directory."""
    try:
        normalized_path = _normalize_image_path(full_path)
        file_path = Path("data") / "images" / normalized_path
        file_path = file_path.resolve()
        
        # Security check: ensure the path is within data/images
        data_images_path = (Path("data") / "images").resolve()
        if not str(file_path).startswith(str(data_images_path)):
            raise HTTPException(status_code=403, detail="Accesso negato.")
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Immagine non trovata.")
        
        return FileResponse(file_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nel caricamento immagine: {e}")


@app.get("/plant/{name}")
def plant_info(
    name: str,
    lang: str = Query(default="it", description="Codice lingua Wikipedia (es. it, en, fr)"),
    refresh_cache: bool = Query(default=False, description="Forza rigenerazione cache scheda"),
    authorization: str | None = Header(default=None),
):
    """Recupera informazioni su una pianta dalla RAG con riassunto OpenAI."""
    _get_google_user_from_authorization(authorization, require_auth=False)
    _log_api("/plant/{name}", "input", {"name": name, "lang": lang, "refresh_cache": refresh_cache})

    normalized_name = (name or "").strip()
    normalized_lang = (lang or "it").strip().lower()

    if not refresh_cache:
        cached_payload = get_cached_plant_card(normalized_name, normalized_lang)
        if cached_payload is not None:
            cached_payload["build_status"] = _species_build_status(cached_payload.get("title") or normalized_name)
            _log_api(
                "/plant/{name}",
                "cache_hit",
                {
                    "title": cached_payload.get("title", normalized_name),
                    "source": cached_payload.get("source", "rag"),
                    "cache_updated_at": cached_payload.get("cache_updated_at", ""),
                },
            )
            return JSONResponse(content=cached_payload)

    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    try:
        retrieval_mode = "rag"
        collection = get_rag_collection()
        results = collection.get(
            where={"species_name": {"$eq": normalized_name}},
            limit=20,
        )

        if not results or not results.get("documents"):
            wiki_data = None
            try:
                retrieval_mode = "wikipedia_fallback"
                wiki_data = fetch_wikipedia_text_context(normalized_name, normalized_lang)
            except Exception:
                if normalized_lang != "en":
                    try:
                        retrieval_mode = "wikipedia_fallback_en"
                        wiki_data = fetch_wikipedia_text_context(normalized_name, "en")
                    except Exception:
                        wiki_data = None

            if wiki_data is not None:
                title = wiki_data["title"]
                extract = wiki_data["summary"]
                common_name = ""
                thumbnail = (wiki_data.get("thumbnail") or "").strip()
                image_paths = [thumbnail] if thumbnail else []
                rag_used = False
            else:
                db_profile = get_plant_profile_from_db(normalized_name)
                if db_profile is not None:
                    retrieval_mode = "db_draft"
                    rag_used = False
                    title = db_profile.get("species_name") or normalized_name
                    common_name = ""
                    image_paths = _get_species_images_from_db(title)
                    if not db_profile.get("indexed"):
                        _ensure_species_build_job(title)
                    if db_profile.get("indexed"):
                        extract = (
                            "Scheda non ancora disponibile dalla base conoscenza RAG. "
                            "Stiamo completando i contenuti per questa specie."
                        )
                    else:
                        extract = (
                            "Scheda in costruzione. Questa specie e stata riconosciuta, "
                            "ma i contenuti descrittivi sono ancora in preparazione."
                        )
                else:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Pianta '{normalized_name}' non trovata nella RAG, in Wikipedia o nel database locale.",
                    )
        else:
            retrieval_mode = "rag"
            rag_used = True
            metadatas = results.get("metadatas", [])
            first_meta = metadatas[0] if metadatas else {}

            title = first_meta.get("species_name", normalized_name)
            common_name = first_meta.get("common_name", "")
            image_paths = _get_species_images_from_db(normalized_name)
            if not image_paths:
                image_paths_json = first_meta.get("image_paths", "[]")
                try:
                    image_paths = json.loads(image_paths_json)
                except (json.JSONDecodeError, TypeError):
                    image_paths = []

            documents = results.get("documents", [])
            combined_text = "\n\n".join(documents[:10])
            if len(combined_text) > 6000:
                combined_text = combined_text[:6000] + "\n..."

            if api_key:
                try:
                    client = OpenAI(api_key=api_key)
                    completion = client.chat.completions.create(
                        model=OPENAI_MODEL,
                        temperature=0.3,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Sei un botanico esperto. Genera un riassunto conciso e affascinante "
                                    "della pianta in base al testo fornito. Includi: descrizione, habitat, "
                                    "caratteristiche distintive e usi. Rispondi in italiano."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"Crea un riassunto affascinante della pianta '{title}'.\n\n"
                                    f"Testo di riferimento:\n{combined_text}"
                                ),
                            },
                        ],
                    )
                    extract = completion.choices[0].message.content or ""
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"Errore nella generazione del riassunto: {e}")
            else:
                # Fallback local summary to avoid hard failure when key is missing.
                extract = _truncate(re.sub(r"\s+", " ", combined_text), 1200)

        _log_api(
            "/plant/{name}",
            "retrieval",
            {
                "mode": retrieval_mode,
                "rag_used": rag_used,
                "documents_found": len(results.get("documents", [])) if results else 0,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore nel recupero informazioni pianta: {e}")

    images: list[str] = []
    data_dir = Path("data")

    for img_path in image_paths[:3]:
        normalized_img_path = _normalize_image_path(img_path)
        local_path = data_dir / "images" / normalized_img_path
        if local_path.exists():
            images.append(f"/images/{normalized_img_path}")
        elif str(img_path).startswith("http"):
            images.append(img_path)

    md_lines = [f"# {title}\n"]

    if common_name:
        md_lines.append(f"**Nome comune:** {common_name}\n")

    if images:
        img_tags = "".join(
            f'<img src="{url}" alt="{title}" width="280" style="margin:4px;border-radius:8px"/>'
            for url in images
        )
        md_lines.append(img_tags + "\n")

    md_lines.append(extract + "\n")

    if rag_used:
        source_info = "Fonte: Database RAG"
    elif retrieval_mode.startswith("wikipedia"):
        source_info = "Fonte: Wikipedia"
    else:
        source_info = "Fonte: Database locale"
    md_lines.append(f"\n---\n{source_info}")

    markdown = "\n".join(md_lines)

    payload = {
        "title": title,
        "common_name": common_name,
        "markdown": markdown,
        "summary": extract,
        "images": images,
        "source": "rag" if rag_used else ("wikipedia" if retrieval_mode.startswith("wikipedia") else "db_draft"),
        "build_status": _species_build_status(title),
    }

    if payload["source"] in {"rag", "wikipedia"}:
        try:
            upsert_cached_plant_card(normalized_name, normalized_lang, payload)
        except Exception as cache_exc:
            logger.warning(f"Impossibile aggiornare cache scheda per '{normalized_name}': {cache_exc}")

    _log_api(
        "/plant/{name}",
        "output",
        {
            "title": payload["title"],
            "source": payload["source"],
            "images_count": len(payload["images"]),
            "summary_preview": _truncate(payload["summary"]),
        },
    )

    return JSONResponse(content=payload)


@app.get("/plant/{name}/profile")
def plant_profile(name: str, authorization: str | None = Header(default=None)):
    _get_google_user_from_authorization(authorization, require_auth=False)
    _log_api("/plant/{name}/profile", "input", {"name": name})

    try:
        profile = get_plant_profile_from_db(name)
    except HTTPException:
        raise
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Errore accesso plants.db: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore accesso database piante: {type(e).__name__}: {e}")

    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profilo DB non trovato per '{name}'.")

    _log_api(
        "/plant/{name}/profile",
        "output",
        {
            "species_name": profile["species_name"],
            "indexed": profile["indexed"],
            "updated_at": profile["updated_at"],
        },
    )

    return JSONResponse(content=profile)


@app.post("/chat/plant-care")
def plant_care_chat(payload: PlantChatRequest, authorization: str | None = Header(default=None)):
    _get_google_user_from_authorization(authorization)
    _log_api(
        "/chat/plant-care",
        "input",
        {
            "plant_name": payload.plant_name,
            "question": _truncate(payload.question, 300),
            "lang": payload.lang,
        },
    )

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY non configurata. Imposta la variabile ambiente e riprova.",
        )

    try:
        retrieval_mode = "rag"
        profile = get_plant_profile_from_db(payload.plant_name)
        # Try to get context from RAG first
        collection = get_rag_collection()
        results = collection.get(
            where={"species_name": {"$eq": payload.plant_name}},
            limit=15,  # Get multiple chunks for comprehensive context
        )
        
        if results and results.get("documents"):
            # Use RAG context
            documents = results.get("documents", [])
            context_text = "\n\n".join(documents)
            if len(context_text) > 8000:
                context_text = context_text[:8000] + "\n..."
            
            metadatas = results.get("metadatas", [])
            plant_title = metadatas[0].get("species_name", payload.plant_name) if metadatas else payload.plant_name
            common_name = metadatas[0].get("common_name", "") if metadatas else ""
            source_info = "RAG"
            source_url = ""
        else:
            # Fallback to Wikipedia if not found in RAG
            retrieval_mode = "wikipedia_fallback"
            wiki_data = fetch_wikipedia_text_context(payload.plant_name, payload.lang)
            context_text = (wiki_data.get("summary", "") + "\n\n" + wiki_data.get("extended_text", "")).strip()
            if len(context_text) > 8000:
                context_text = context_text[:8000] + "\n..."
            plant_title = wiki_data["title"]
            common_name = ""
            source_info = "Wikipedia"
            source_url = wiki_data.get("wikipedia_url", "")

        _log_api(
            "/chat/plant-care",
            "retrieval",
            {
                "mode": retrieval_mode,
                "source": source_info,
                "context_length": len(context_text),
                "profile_found": bool(profile),
            },
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=f"Errore nel recupero contesto pianta: {e}")

    try:
        client = OpenAI(api_key=api_key)
        
        # Build user message with plant info
        user_message = f"Pianta: {plant_title}"
        if common_name:
            user_message += f" ({common_name})"
        profile_context = _build_profile_context(profile)
        user_message += f"\nDomanda: {payload.question}\n\n"
        if profile_context:
            user_message += f"{profile_context}\n\n"
        user_message += f"Contesto dalla base di dati:\n{context_text}\n\n"
        user_message += (
            "Rispondi con:\n"
            "1) Risposta breve\n"
            "2) Cosa fare oggi\n"
            "3) Errori da evitare"
        )
        
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sei un assistente botanico pratico e chiaro. "
                        "Rispondi in italiano con consigli concreti per la cura della pianta "
                        "(irrigazione, luce, terreno, potatura, parassiti, stagionalita). "
                        "Se l'informazione non e certa, dichiaralo esplicitamente. "
                        "Non dare indicazioni mediche per persone o animali."
                    ),
                },
                {
                    "role": "user",
                    "content": user_message,
                },
            ],
        )
        answer = completion.choices[0].message.content or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Errore chiamata OpenAI: {e}")

    response_payload = {
        "plant": plant_title,
        "common_name": common_name,
        "question": payload.question,
        "answer": answer.strip(),
        "source": source_info,
        "source_url": source_url,
        "model": OPENAI_MODEL,
    }

    _log_api(
        "/chat/plant-care",
        "output",
        {
            "plant": response_payload["plant"],
            "source": response_payload["source"],
            "model": response_payload["model"],
            "answer_preview": _truncate(response_payload["answer"]),
        },
    )

    return JSONResponse(content=response_payload)

@app.get("/debug/routes")
def debug_routes():
    return [r.path for r in app.routes]


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
