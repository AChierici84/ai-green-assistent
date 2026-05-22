import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cloudinary

from db_config import load_mysql_config


TRUE_VALUES = {"1", "true", "yes", "on"}


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in TRUE_VALUES


def default_asset_path(filename: str) -> str:
    if os.getenv("SPACE_ID") and Path("/data").exists():
        return str(Path("/data") / "greenassistent-assets" / filename)
    return str(Path("data") / filename)


@dataclass(frozen=True)
class AppConfig:
    index_path: str
    cache_path: str
    model_name: str
    leafsnap_index_path: str
    leafsnap_cache_path: str
    rag_db_path: str
    wiki_user_agent: str
    openai_model: str
    mysql_config: dict[str, Any]
    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str
    google_client_ids: list[str]
    require_google_auth: bool
    admin_users: set[str]
    pwa_dist_dir: Path
    plant_card_cache_enabled: bool
    faiss_confidence_threshold: float
    faiss_ambiguity_margin: float
    rrf_ambiguity_margin: float
    force_openai_fallback: bool


def load_app_config() -> AppConfig:
    cloudinary_cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    cloudinary_api_key = os.getenv("CLOUDINARY_API_KEY", "")
    cloudinary_api_secret = os.getenv("CLOUDINARY_API_SECRET", "")

    google_client_ids = [
        value.strip()
        for value in os.getenv("GOOGLE_CLIENT_ID", "").split(",")
        if value.strip()
    ]

    admin_users = {
        value.strip().lower()
        for value in os.getenv("ADMIN_USERS", "").split(",")
        if value.strip()
    }

    return AppConfig(
        index_path=os.getenv("PLANCLEF_INDEX_PATH", default_asset_path("planclef.faiss")),
        cache_path=os.getenv("PLANCLEF_CACHE_PATH", default_asset_path("planclef_cache.pt")),
        model_name=os.getenv("PLANCLEF_MODEL_NAME", "ViT-B-32"),
        leafsnap_index_path=os.getenv("LEAFSNAP_INDEX_PATH", default_asset_path("leafsnap.faiss")),
        leafsnap_cache_path=os.getenv("LEAFSNAP_CACHE_PATH", default_asset_path("leafsnap_cache.pt")),
        rag_db_path=os.getenv("RAG_DB_PATH", default_asset_path("plant_rag")),
        wiki_user_agent=os.getenv("WIKI_USER_AGENT", "clorofilla/1.0 (contact: local-dev)"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        mysql_config=load_mysql_config(),
        cloudinary_cloud_name=cloudinary_cloud_name,
        cloudinary_api_key=cloudinary_api_key,
        cloudinary_api_secret=cloudinary_api_secret,
        google_client_ids=google_client_ids,
        require_google_auth=_to_bool(os.getenv("REQUIRE_GOOGLE_AUTH", "0")),
        admin_users=admin_users,
        pwa_dist_dir=Path(os.getenv("PWA_DIST_DIR", "pwa-app/dist")),
        plant_card_cache_enabled=_to_bool(os.getenv("PLANT_CARD_CACHE_ENABLED", "1"), default=True),
        faiss_confidence_threshold=float(os.getenv("FAISS_CONFIDENCE_THRESHOLD", "0.82")),
        faiss_ambiguity_margin=float(os.getenv("FAISS_AMBIGUITY_MARGIN", "0.015")),
        rrf_ambiguity_margin=float(os.getenv("RRF_AMBIGUITY_MARGIN", "0.0025")),
        force_openai_fallback=_to_bool(os.getenv("FORCE_OPENAI_FALLBACK", "0")),
    )


def configure_cloudinary_if_enabled(config: AppConfig) -> None:
    if not (
        config.cloudinary_cloud_name
        and config.cloudinary_api_key
        and config.cloudinary_api_secret
    ):
        return

    cloudinary.config(
        cloud_name=config.cloudinary_cloud_name,
        api_key=config.cloudinary_api_key,
        api_secret=config.cloudinary_api_secret,
        secure=True,
    )
