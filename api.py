
import httpx
import json
import logging
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Query, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from openai import OpenAI
from pydantic import BaseModel, Field
from app_config import load_app_config, configure_cloudinary_if_enabled
from api_logging import configure_logging, _log_api, _response_payload_for_log
from data_storage import DataStorage, PLANT_PROFILE_FIELDS
from google_auth import GoogleAuthService
from google_drive_storage import GoogleDriveStorageService
from openai_gpt import _should_trigger_gpt_fallback, _gpt_vision_identify_plant
from species import SpeciesService

load_dotenv()

APP_CONFIG = load_app_config()

INDEX_PATH = APP_CONFIG.index_path
CACHE_PATH = APP_CONFIG.cache_path
MODEL_NAME = APP_CONFIG.model_name
LEAFSNAP_INDEX_PATH = APP_CONFIG.leafsnap_index_path
LEAFSNAP_CACHE_PATH = APP_CONFIG.leafsnap_cache_path
RAG_DB_PATH = APP_CONFIG.rag_db_path
WIKI_USER_AGENT = APP_CONFIG.wiki_user_agent
OPENAI_MODEL = APP_CONFIG.openai_model
MYSQL_CONFIG = APP_CONFIG.mysql_config

CLOUDINARY_CLOUD_NAME = APP_CONFIG.cloudinary_cloud_name
CLOUDINARY_API_KEY = APP_CONFIG.cloudinary_api_key
CLOUDINARY_API_SECRET = APP_CONFIG.cloudinary_api_secret
configure_cloudinary_if_enabled(APP_CONFIG)

GOOGLE_CLIENT_IDS = APP_CONFIG.google_client_ids
REQUIRE_GOOGLE_AUTH = APP_CONFIG.require_google_auth
ADMIN_USERS = APP_CONFIG.admin_users
PWA_DIST_DIR = APP_CONFIG.pwa_dist_dir
PLANT_CARD_CACHE_ENABLED = APP_CONFIG.plant_card_cache_enabled
FAISS_CONFIDENCE_THRESHOLD = APP_CONFIG.faiss_confidence_threshold
FAISS_AMBIGUITY_MARGIN = APP_CONFIG.faiss_ambiguity_margin
RRF_AMBIGUITY_MARGIN = APP_CONFIG.rrf_ambiguity_margin
FORCE_OPENAI_FALLBACK = APP_CONFIG.force_openai_fallback

data_storage = DataStorage(
    MYSQL_CONFIG,
    plant_card_cache_enabled=PLANT_CARD_CACHE_ENABLED,
    format_datetime_display=lambda value: _format_datetime_display(value),
)

index: Any = None
rag_collection: Any = None


logger = logging.getLogger("ai_green_assistant.api")


configure_logging()


def _truncate(value: Any, max_len: int = 500) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _serve_pwa_index() -> HTMLResponse:
    pwa_index = PWA_DIST_DIR / "index.html"
    if pwa_index.exists():
        return HTMLResponse(content=pwa_index.read_text(encoding="utf-8"))

    raise HTTPException(status_code=503, detail="Frontend non disponibile.")


# Detection centralizzata lingua/paese
def detect_country_or_lang(request: 'Request') -> str:
    # 1. Query param ha priorità
    lang_param = request.query_params.get('lang', '').lower()
    if lang_param in {'it', 'en'}:
        return lang_param

    # 2. Accept-Language header
    accept_language = request.headers.get('accept-language', '').lower()
    if accept_language:
        if 'it' in accept_language:
            return 'it'
        if 'en' in accept_language:
            return 'en'

    # 3. Fallback su IP
    ip = request.headers.get('x-forwarded-for', '')
    if ip.startswith('2.') or ip.startswith('5.') or ip.startswith('79.'):
        return 'it'
    return 'en'


# Serve landing page con detection
def _serve_landing_page(request: Request) -> HTMLResponse:
    lang = detect_country_or_lang(request)
    if lang == 'it':
        html_path = Path("ui.html")
    else:
        html_path = Path("ui_en.html")
    if not html_path.exists():
        raise HTTPException(status_code=503, detail="Landing page non trovata.")
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))



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


def _profile_has_care_data(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False

    care_fields = (
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
    )

    for field in care_fields:
        value = profile.get(field)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return True
    return False


def _translate_profile_values_to_en(profile: dict[str, Any]) -> dict[str, Any]:
    translated = dict(profile)
    translatable_fields = (
        "annaffiatura_time",
        "luce",
        "temperatura",
        "umidita",
        "altezza_media",
        "pulizia",
        "terriccio",
        "concimazione",
        "prevenzione",
    )

    source_values: dict[str, str] = {}
    for key in translatable_fields:
        value = translated.get(key)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        source_values[key] = cleaned

    if not source_values:
        return translated

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return translated

    try:
        client = OpenAI(api_key=api_key)
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Translate Italian plant-care profile values to concise English. "
                        "Return only a JSON object with the same keys received in input. "
                        "Do not add or remove keys. Keep numbers, ranges, units, and botanical names intact."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(source_values, ensure_ascii=False),
                },
            ],
        )
        content = (completion.choices[0].message.content or "").strip()
        parsed = json.loads(content) if content else {}
        if isinstance(parsed, dict):
            for key, original_value in source_values.items():
                translated_value = parsed.get(key)
                if isinstance(translated_value, str) and translated_value.strip():
                    translated[key] = translated_value.strip()
                else:
                    translated[key] = original_value
    except Exception as exc:
        logger.warning("Traduzione profilo fallita, uso testo originale: %s", exc)

    return translated


def _translate_plant_profile(profile: dict[str, Any], lang: str) -> dict[str, Any]:
    normalized_lang = (lang or "it").strip().lower()
    if normalized_lang != "en":
        return profile
    return _translate_profile_values_to_en(profile)


def _normalize_image_path(raw_path: str) -> str:
    """Normalize image path to be relative to data/images."""
    normalized = str(raw_path or "").replace("\\", "/").strip().lstrip("/")
    if normalized.lower().startswith("data/"):
        normalized = normalized[5:]
    if normalized.lower().startswith("images/"):
        normalized = normalized[7:]
    return normalized


def _reset_search_handles() -> None:
    global index, rag_collection
    index = None
    rag_collection = None


def _get_index_handle() -> Any:
    return index


def _set_index_handle(value: Any) -> None:
    global index
    index = value


def _get_rag_collection_handle() -> Any:
    return rag_collection


def _set_rag_collection_handle(value: Any) -> None:
    global rag_collection
    rag_collection = value


species_service = SpeciesService(
    data_storage=data_storage,
    rag_db_path=RAG_DB_PATH,
    get_rag_collection_handle=_get_rag_collection_handle,
    set_rag_collection_handle=_set_rag_collection_handle,
    normalize_image_path=_normalize_image_path,
    plant_profile_fields=PLANT_PROFILE_FIELDS,
    model_name=MODEL_NAME,
    index_path=INDEX_PATH,
    cache_path=CACHE_PATH,
    leafsnap_index_path=LEAFSNAP_INDEX_PATH,
    leafsnap_cache_path=LEAFSNAP_CACHE_PATH,
    wiki_user_agent=WIKI_USER_AGENT,
    get_index_handle=_get_index_handle,
    set_index_handle=_set_index_handle,
    reset_search_handles=_reset_search_handles,
)

google_auth_service = GoogleAuthService(
    google_client_ids=GOOGLE_CLIENT_IDS,
    require_google_auth=REQUIRE_GOOGLE_AUTH,
    admin_users=ADMIN_USERS,
    data_storage=data_storage,
)

google_drive_storage_service = GoogleDriveStorageService()


def ensure_plant_cards_cache_table(conn: Any) -> None:
    data_storage.ensure_plant_cards_cache_table(conn)


def get_cached_plant_card(name: str, lang: str) -> dict[str, Any] | None:
    return data_storage.get_cached_plant_card(name, lang)


def upsert_cached_plant_card(name: str, lang: str, payload: dict[str, Any]) -> None:
    data_storage.upsert_cached_plant_card(name, lang, payload)


def get_plants_db_connection() -> Any:
    return data_storage.get_plants_db_connection()


def _get_species_images_from_db(species_name: str) -> list[str]:
    return data_storage.get_species_images_from_db(species_name)


def get_user_plants_db_connection() -> Any:
    return data_storage.get_user_plants_db_connection()


def get_plant_profile_from_db(name: str) -> dict[str, Any] | None:
    return data_storage.get_plant_profile_from_db(name)


def ensure_user_plants_table(conn: Any) -> None:
    data_storage.ensure_user_plants_table(conn)


def ensure_registered_users_table(conn: Any) -> None:
    data_storage.ensure_registered_users_table(conn)


def ensure_recognition_logs_table(conn: Any) -> None:
    data_storage.ensure_recognition_logs_table(conn)


def create_recognition_log(
    chosen_species: str,
    used_openai: bool,
    image_url: str | None,
    recognition_ms: int | None,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    return data_storage.create_recognition_log(
        chosen_species=chosen_species,
        used_openai=used_openai,
        image_url=image_url,
        recognition_ms=recognition_ms,
        user=user,
    )


def get_recognition_admin_aggregates(conn: Any, chart_days: int = 30) -> dict[str, Any]:
    return data_storage.get_recognition_admin_aggregates(conn, chart_days=chart_days)


def _get_user_plant_photo_urls(conn: Any, plant_id: int, fallback_url: str | None) -> list[str]:
    return data_storage._get_user_plant_photo_urls(conn, plant_id, fallback_url)


def _user_plant_row_to_payload(conn: Any, row: Any) -> dict[str, Any]:
    return data_storage._user_plant_row_to_payload(conn, row)


def create_user_plant(plant_name: str, user_given_name: str, user: dict[str, Any]) -> dict[str, Any]:
    return data_storage.create_user_plant(plant_name, user_given_name, user)


def list_user_plants(user: dict[str, Any]) -> list[dict[str, Any]]:
    return data_storage.list_user_plants(user)


def delete_user_plant_by_id(user: dict[str, Any], plant_id: int) -> bool:
    return data_storage.delete_user_plant_by_id(user, plant_id)


def update_user_plant_created_at_by_id(user: dict[str, Any], plant_id: int, created_at_iso: str) -> dict[str, Any] | None:
    return data_storage.update_user_plant_created_at_by_id(user, plant_id, created_at_iso)


from fastapi import Request
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


# Route principale che usa la nuova funzione
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request):
    """
    Serve la landing page in italiano o inglese in base alla provenienza utente.
    """
    return _serve_landing_page(request)

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


def _normalize_ui_lang(lang: str | None) -> str:
    return "en" if (lang or "").strip().lower() == "en" else "it"


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


class AdminSpeciesBuildRequest(BaseModel):
    species_name: str = Field(..., min_length=2, max_length=160, description="Nome specie")
    force_rebuild: bool = Field(default=False, description="Se true forza rebuild anche se gia completata")


@app.post("/search")
async def search_similar(
    file: UploadFile = File(..., description="Immagine della pianta da ricercare"),
    k: int = Query(default=5, ge=1, le=50, description="Numero di risultati da restituire"),
    authorization: str | None = Header(default=None),
):
    started_at = datetime.utcnow()
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
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
        loaded_index = species_service.get_index()
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
        should_trigger_gpt, gpt_trigger_basis = _should_trigger_gpt_fallback(
            top_planclef_score,
            results,
            force_openai_fallback=FORCE_OPENAI_FALLBACK,
            faiss_confidence_threshold=FAISS_CONFIDENCE_THRESHOLD,
            faiss_ambiguity_margin=FAISS_AMBIGUITY_MARGIN,
            rrf_ambiguity_margin=RRF_AMBIGUITY_MARGIN,
        )
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
                species_service._insert_draft_plant_if_missing(gpt_species, api_key)
                gpt_job_status = species_service._ensure_species_build_job(gpt_species)
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
        draft_species = data_storage.get_draft_species_set(species_names)
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
                    "build_status": species_service._species_build_status(species),
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
    validated = google_auth_service.validate_google_token(payload.id_token)

    user = {
        "sub": validated.get("sub", ""),
        "email": validated.get("email", ""),
        "name": validated.get("name", ""),
        "picture": validated.get("picture", ""),
    }
    is_new_user, registered_at = google_auth_service.register_google_user_if_needed(user)
    is_admin = google_auth_service.is_admin_email(str(user.get("email") or ""))

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
    admin_user = google_auth_service.require_admin_user(authorization)
    users = google_auth_service.list_registered_users_for_admin(limit=limit)
    inventory = species_service.get_catalog_and_faiss_stats()
    totals, recognition = data_storage.get_admin_console_stats(chart_days=chart_days)

    return JSONResponse(
        content={
            "ok": True,
            "admin_email": admin_user.get("email", ""),
            "stats": {
                "registered_users_total": totals["registered_users_total"],
                "saved_plants_total": totals["saved_plants_total"],
                "external_user_images_total": totals["external_user_images_total"],
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
    user = google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
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
    user = google_auth_service.get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per salvare una pianta.")

    saved = create_user_plant(
        plant_name=payload.plant_name,
        user_given_name=payload.user_given_name,
        user=user,
    )

    species_name = str(saved.get("plant_name") or payload.plant_name or "").strip()
    if species_name:
        try:
            profile = get_plant_profile_from_db(species_name)
            needs_enrichment = (not _profile_has_care_data(profile)) or bool(profile and not profile.get("indexed"))
            if profile is None:
                species_service._insert_draft_plant_if_missing(species_name, os.getenv("OPENAI_API_KEY", "").strip())

            if needs_enrichment:
                species_service._ensure_species_build_job(species_name)
        except Exception as exc:
            logger.warning(
                "Impossibile avviare arricchimento automatico per '%s': %s",
                species_name,
                exc,
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
    user = google_auth_service.get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per vedere le tue piante.")

    items = list_user_plants(user)
    return JSONResponse(content={"items": items})


@app.delete("/user/plants/{plant_id}")
def delete_user_plant(plant_id: int, authorization: str | None = Header(default=None)):
    user = google_auth_service.get_google_user_from_authorization(authorization)
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
    user = google_auth_service.get_google_user_from_authorization(authorization)
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
    """Upload a user photo, preferring Google Drive and falling back to Cloudinary."""
    user = google_auth_service.get_google_user_from_authorization(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Accedi con Google per caricare una foto.")

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Il file caricato non è un'immagine valida.")

    user_id = str(user.get("sub") or "").strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="Utente non valido.")

    if not data_storage.user_plant_exists_for_user(plant_id, user_id):
        raise HTTPException(status_code=404, detail="Pianta non trovata.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File immagine vuoto.")

    photo_url = ""
    storage_provider = ""
    drive_error = ""
    cloudinary_error = ""

    # Usa il token di autenticazione utente anche per Google Drive
    try:
        drive_result = google_drive_storage_service.upload_user_plant_photo(
            access_token=authorization.split(" ")[-1] if authorization else "",
            user_id=user_id,
            plant_id=plant_id,
            filename=file.filename or "plant-photo.jpg",
            content_type=file.content_type,
            file_content=file_bytes,
        )
        photo_url = str(drive_result.get("photo_url") or "").strip()
        if photo_url:
            storage_provider = "google_drive"
    except Exception as exc:
        drive_error = _truncate(exc, 300)
        logger.error("[!!! ERRORE DRIVE !!!] plant_id=%s user_id=%s: %s", plant_id, user_id, drive_error)
        print(f"[!!! ERRORE DRIVE !!!] plant_id={plant_id} user_id={user_id}: {drive_error}")

    if not photo_url:
        if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET:
            suffix = os.path.splitext(file.filename or "")[1] or ".jpg"
            tmp_path: str | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(file_bytes)
                    tmp_path = tmp.name

                result = cloudinary.uploader.upload(
                    tmp_path,
                    folder="clorofilla/user-plants",
                    public_id=f"plant_{plant_id}_user_{user_id[:12]}_{uuid4().hex[:10]}",
                    overwrite=False,
                    resource_type="image",
                    transformation=[{"width": 1200, "crop": "limit", "quality": "auto:good"}],
                )
                photo_url = str(result.get("secure_url") or "").strip()
                if photo_url:
                    storage_provider = "cloudinary"
                else:
                    cloudinary_error = "Cloudinary non ha restituito URL immagine."
            except Exception as exc:
                cloudinary_error = _truncate(exc, 300)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
        else:
            cloudinary_error = "Cloudinary non configurato."

    if not photo_url:
        detail = "Upload foto fallito."
        if drive_error and cloudinary_error:
            detail = f"Upload fallito su Google Drive e Cloudinary: {drive_error} | {cloudinary_error}"
        elif drive_error:
            detail = f"Upload Google Drive fallito: {drive_error}"
        elif cloudinary_error:
            detail = f"Upload Cloudinary fallito: {cloudinary_error}"
        raise HTTPException(status_code=500, detail=detail)

    updated_payload = data_storage.save_user_plant_photo(plant_id, user_id, photo_url)

    _log_api(
        "/user/plants/{plant_id}/photo",
        "uploaded",
        {"plant_id": plant_id, "provider": storage_provider or "unknown"},
    )
    return JSONResponse(content={"updated": updated_payload, "storage_provider": storage_provider})


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


@app.get("/workbox-{workbox_hash}.js")
def pwa_workbox_js(workbox_hash: str):
    # vite-plugin-pwa generates hashed workbox runtime chunks (workbox-<hash>.js).
    filename = f"workbox-{workbox_hash}.js"
    return _serve_pwa_file(filename, media_type="application/javascript")


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
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
    if not names:
        return JSONResponse(content={"previews": {}})

    previews = {name: species_service._get_species_preview_image_url(name) for name in names}
    return JSONResponse(content={"previews": previews})


@app.get("/species/common-names")
def species_common_names(
    names: list[str] = Query(default=[], description="Nomi specie di cui ottenere il nome comune"),
    authorization: str | None = Header(default=None),
):
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
    if not names:
        return JSONResponse(content={"common_names": {}})

    try:
        collection = species_service.get_rag_collection()
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
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
    status = species_service._species_build_status(name)
    profile = get_plant_profile_from_db(name)
    ready = bool(profile and profile.get("indexed"))
    return JSONResponse(content={"species": name, "ready": ready, "status": status})


@app.post("/admin/species/build")
def admin_species_build(
    payload: AdminSpeciesBuildRequest,
    authorization: str | None = Header(default=None),
):
    admin_user = google_auth_service.require_admin_user(authorization)

    species_name = str(payload.species_name or "").strip()
    if not species_name:
        raise HTTPException(status_code=400, detail="Nome specie obbligatorio.")

    try:
        species_service._insert_draft_plant_if_missing(species_name, os.getenv("OPENAI_API_KEY", "").strip())
    except Exception as exc:
        logger.warning("Inserimento draft non riuscito per '%s': %s", species_name, exc)

    status = species_service.trigger_species_build(
        species_name,
        force_rebuild=bool(payload.force_rebuild),
    )

    action = "rebuild" if payload.force_rebuild else "build"
    _log_api(
        "/admin/species/build",
        "triggered",
        {
            "admin": admin_user.get("email", ""),
            "species": species_name,
            "action": action,
            "status": status.get("status"),
        },
    )

    return JSONResponse(
        content={
            "ok": True,
            "species": species_name,
            "action": action,
            "status": status,
        }
    )




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
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
    _log_api("/plant/{name}", "input", {"name": name, "lang": lang, "refresh_cache": refresh_cache})

    normalized_name = (name or "").strip()
    normalized_lang = (lang or "it").strip().lower()
    is_english = normalized_lang == "en"

    if not refresh_cache:
        cached_payload = get_cached_plant_card(normalized_name, normalized_lang)
        if cached_payload is not None:
            cached_markdown = str(cached_payload.get("markdown") or "")
            if is_english and ("**Nome comune:**" in cached_markdown or "\n---\nFonte:" in cached_markdown):
                cached_payload = None
            if not is_english and ("**Common name:**" in cached_markdown or "\n---\nSource:" in cached_markdown):
                cached_payload = None

        if cached_payload is not None:
            cached_payload["build_status"] = species_service._species_build_status(cached_payload.get("title") or normalized_name)
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
        collection = species_service.get_rag_collection()
        results = collection.get(
            where={"species_name": {"$eq": normalized_name}},
            limit=20,
        )

        if not results or not results.get("documents"):
            wiki_data = None
            try:
                retrieval_mode = "wikipedia_fallback"
                wiki_data = species_service.fetch_wikipedia_text_context(normalized_name, normalized_lang)
            except Exception:
                if normalized_lang != "en":
                    try:
                        retrieval_mode = "wikipedia_fallback_en"
                        wiki_data = species_service.fetch_wikipedia_text_context(normalized_name, "en")
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
                        species_service._ensure_species_build_job(title)
                    if db_profile.get("indexed"):
                        if is_english:
                            extract = (
                                "The card is not available yet from the RAG knowledge base. "
                                "We are completing the content for this species."
                            )
                        else:
                            extract = (
                                "Scheda non ancora disponibile dalla base conoscenza RAG. "
                                "Stiamo completando i contenuti per questa specie."
                            )
                    else:
                        if is_english:
                            extract = (
                                "Card under construction. This species has been recognized, "
                                "but descriptive content is still being prepared."
                            )
                        else:
                            extract = (
                                "Scheda in costruzione. Questa specie e stata riconosciuta, "
                                "ma i contenuti descrittivi sono ancora in preparazione."
                            )
                else:
                    retrieval_mode = "db_draft"
                    rag_used = False
                    title = normalized_name
                    common_name = ""
                    image_paths = []
                    try:
                        species_service._insert_draft_plant_if_missing(title, api_key)
                    except Exception as draft_exc:
                        logger.warning(
                            "Impossibile inserire draft per '%s': %s",
                            title,
                            draft_exc,
                        )
                    species_service._ensure_species_build_job(title)
                    if is_english:
                        extract = (
                            "Species not found in RAG/Wikipedia/local catalog: "
                            "I created a draft card and started automatic content generation."
                        )
                    else:
                        extract = (
                            "Specie non presente in RAG/Wikipedia/catalogo locale: "
                            "ho creato una scheda bozza e avviato la costruzione automatica dei contenuti."
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
                    system_prompt = (
                        "You are an expert botanist. Generate a concise and engaging summary "
                        "of the plant using the provided reference text. Include: description, habitat, "
                        "distinctive characteristics, and uses. Respond in English."
                        if is_english
                        else "Sei un botanico esperto. Genera un riassunto conciso e affascinante "
                        "della pianta in base al testo fornito. Includi: descrizione, habitat, "
                        "caratteristiche distintive e usi. Rispondi in italiano."
                    )
                    user_prompt = (
                        f"Create an engaging summary of the plant '{title}'.\n\n"
                        f"Reference text:\n{combined_text}"
                        if is_english
                        else f"Crea un riassunto affascinante della pianta '{title}'.\n\n"
                        f"Testo di riferimento:\n{combined_text}"
                    )
                    completion = client.chat.completions.create(
                        model=OPENAI_MODEL,
                        temperature=0.3,
                        messages=[
                            {
                                "role": "system",
                                "content": system_prompt,
                            },
                            {
                                "role": "user",
                                "content": user_prompt,
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
        md_lines.append(f"**{'Common name' if is_english else 'Nome comune'}:** {common_name}\n")

    if images:
        img_tags = "".join(
            f'<img src="{url}" alt="{title}" width="280" style="margin:4px;border-radius:8px"/>'
            for url in images
        )
        md_lines.append(img_tags + "\n")

    md_lines.append(extract + "\n")

    if rag_used:
        source_info = "Source: RAG database" if is_english else "Fonte: Database RAG"
    elif retrieval_mode.startswith("wikipedia"):
        source_info = "Source: Wikipedia" if is_english else "Fonte: Wikipedia"
    else:
        source_info = "Source: Local database" if is_english else "Fonte: Database locale"
    md_lines.append(f"\n---\n{source_info}")

    markdown = "\n".join(md_lines)

    payload = {
        "title": title,
        "common_name": common_name,
        "markdown": markdown,
        "summary": extract,
        "images": images,
        "source": "rag" if rag_used else ("wikipedia" if retrieval_mode.startswith("wikipedia") else "db_draft"),
        "build_status": species_service._species_build_status(title),
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

# Endpoint proxy per immagini Google Drive pubbliche
@app.get("/proxy/drive-image/{file_id}")
def proxy_drive_image(file_id: str):
    """Proxy per servire immagini pubbliche da Google Drive, dato un file_id."""
    if not file_id or not re.match(r"^[a-zA-Z0-9_-]{10,}$", file_id):
        raise HTTPException(status_code=400, detail="ID file non valido.")
    drive_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            resp = client.get(drive_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=404, detail="Immagine non trovata su Google Drive.")
            content_type = resp.headers.get("content-type", "image/jpeg")
            from fastapi import Response
            return Response(
                content=resp.content,
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="drive-image-{file_id}"'
                }
            )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Errore proxy Google Drive: {exc}")

@app.get("/plant/{name}/profile")
def plant_profile(
    name: str,
    lang: str = Query(default="it", description="Codice lingua profilo (es. it, en)"),
    authorization: str | None = Header(default=None),
):
    google_auth_service.get_google_user_from_authorization(authorization, require_auth=False)
    _log_api("/plant/{name}/profile", "input", {"name": name, "lang": lang})

    try:
        profile = get_plant_profile_from_db(name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore accesso database piante: {type(e).__name__}: {e}")

    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profilo DB non trovato per '{name}'.")

    translated_profile = _translate_plant_profile(profile, lang)

    _log_api(
        "/plant/{name}/profile",
        "output",
        {
            "species_name": translated_profile["species_name"],
            "indexed": translated_profile["indexed"],
            "updated_at": translated_profile["updated_at"],
            "lang": (lang or "it").strip().lower(),
        },
    )

    return JSONResponse(content=translated_profile)


@app.post("/chat/plant-care")
def plant_care_chat(payload: PlantChatRequest, authorization: str | None = Header(default=None)):
    google_auth_service.get_google_user_from_authorization(authorization)
    lang = _normalize_ui_lang(payload.lang)
    _log_api(
        "/chat/plant-care",
        "input",
        {
            "plant_name": payload.plant_name,
            "question": _truncate(payload.question, 300),
            "lang": lang,
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
        collection = species_service.get_rag_collection()
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
            wiki_data = species_service.fetch_wikipedia_text_context(payload.plant_name, lang)
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
        prompt_labels = {
            "plant": "Plant" if lang == "en" else "Pianta",
            "question": "Question" if lang == "en" else "Domanda",
            "context": "Context from the knowledge base" if lang == "en" else "Contesto dalla base di dati",
            "reply_with": "Reply with" if lang == "en" else "Rispondi con",
            "short_answer": "Short answer" if lang == "en" else "Risposta breve",
            "what_to_do_today": "What to do today" if lang == "en" else "Cosa fare oggi",
            "mistakes_to_avoid": "Mistakes to avoid" if lang == "en" else "Errori da evitare",
        }
        user_message = f"{prompt_labels['plant']}: {plant_title}"
        if common_name:
            user_message += f" ({common_name})"
        profile_context = species_service.build_profile_context(profile)
        user_message += f"\n{prompt_labels['question']}: {payload.question}\n\n"
        if profile_context:
            user_message += f"{profile_context}\n\n"
        user_message += f"{prompt_labels['context']}:\n{context_text}\n\n"
        user_message += (
            f"{prompt_labels['reply_with']}:\n"
            f"1) {prompt_labels['short_answer']}\n"
            f"2) {prompt_labels['what_to_do_today']}\n"
            f"3) {prompt_labels['mistakes_to_avoid']}"
        )

        system_message = (
            "You are a practical, clear botanical assistant. "
            "Reply in English with concrete plant-care advice "
            "(watering, light, soil, pruning, pests, seasonality). "
            "If information is uncertain, state that explicitly. "
            "Do not provide medical advice for people or animals. "
            "Always write the whole answer in English, even if the retrieved context contains Italian text."
            if lang == "en"
            else (
                "Sei un assistente botanico pratico e chiaro. "
                "Rispondi in italiano con consigli concreti per la cura della pianta "
                "(irrigazione, luce, terreno, potatura, parassiti, stagionalita). "
                "Se l'informazione non e certa, dichiaralo esplicitamente. "
                "Non dare indicazioni mediche per persone o animali."
            )
        )
        
        completion = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0.3,
            messages=[
                {
                    "role": "system",
                    "content": system_message,
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
