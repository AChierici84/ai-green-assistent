#!/usr/bin/env python3
"""Upload FAISS/RAG assets to a Hugging Face dataset repo."""

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi


DEFAULT_REPO_ID = os.getenv("HF_ASSETS_DATASET_REPO", "AChierici84/GreenAssistent-assets")
DATASET_REPO_TYPE = "dataset"
ASSET_FILENAMES = (
    "planclef.faiss",
    "planclef_cache.pt",
    "leafsnap.faiss",
    "leafsnap_cache.pt",
    "chroma.sqlite3",
)


def _default_asset_dir() -> Path:
    if os.getenv("SPACE_ID") and Path("/data").exists():
        return Path("/data") / "greenassistent-assets"
    return Path("data")


def _resolve_local_sources(include_plants_db: bool = False) -> dict[str, Path]:
    asset_dir = _default_asset_dir()
    paths = {
        "planclef.faiss": Path(os.getenv("PLANCLEF_INDEX_PATH", str(asset_dir / "planclef.faiss"))),
        "planclef_cache.pt": Path(os.getenv("PLANCLEF_CACHE_PATH", str(asset_dir / "planclef_cache.pt"))),
        "leafsnap.faiss": Path(os.getenv("LEAFSNAP_INDEX_PATH", str(asset_dir / "leafsnap.faiss"))),
        "leafsnap_cache.pt": Path(os.getenv("LEAFSNAP_CACHE_PATH", str(asset_dir / "leafsnap_cache.pt"))),
        "chroma.sqlite3": Path(os.getenv("RAG_DB_PATH", str(asset_dir / "plant_rag" / "chroma.sqlite3"))),
    }
    if include_plants_db:
        paths["plants.db"] = Path(os.getenv("PLANTS_SQLITE_PATH", str(Path("data") / "plants.db")))
    return paths


def upload_assets(
    repo_id: str,
    private: bool,
    include_plants_db: bool,
    skip_missing: bool,
    token: str | None,
) -> int:
    api = HfApi(token=token)

    api.create_repo(
        repo_id=repo_id,
        repo_type=DATASET_REPO_TYPE,
        private=private,
        exist_ok=True,
    )

    sources = _resolve_local_sources(include_plants_db=include_plants_db)

    uploaded = 0
    for remote_name, local_path in sources.items():
        if not local_path.exists():
            msg = f"Local file not found: {local_path}"
            if skip_missing:
                print(f"[assets] Skip: {msg}")
                continue
            raise FileNotFoundError(msg)

        print(f"[assets] Uploading {local_path} -> {repo_id}/{remote_name}")
        api.upload_file(
            path_or_fileobj=str(local_path),
            path_in_repo=remote_name,
            repo_id=repo_id,
            repo_type=DATASET_REPO_TYPE,
            commit_message=f"Update asset: {remote_name}",
        )
        uploaded += 1

    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload project assets to Hugging Face dataset")
    parser.add_argument("--repo", default=DEFAULT_REPO_ID, help="Dataset repo id (owner/name)")
    parser.add_argument(
        "--private",
        action="store_true",
        help="Create the dataset as private if it does not exist",
    )
    parser.add_argument(
        "--include-plants-db",
        action="store_true",
        help="Upload plants.db too (in addition to FAISS/RAG assets)",
    )
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Skip local files that are missing instead of failing",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("HF_TOKEN", ""),
        help="HF token (defaults to HF_TOKEN env var or local HF login)",
    )
    args = parser.parse_args()

    token = args.token.strip() or None
    uploaded = upload_assets(
        repo_id=args.repo,
        private=args.private,
        include_plants_db=args.include_plants_db,
        skip_missing=args.skip_missing,
        token=token,
    )
    print(f"[assets] Upload completed. Files uploaded: {uploaded}")


if __name__ == "__main__":
    main()
