#!/usr/bin/env python3
"""Download FAISS/RAG assets from a Hugging Face dataset repo."""

import argparse
import os
from pathlib import Path

from huggingface_hub import hf_hub_download


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


def _resolve_target_paths() -> dict[str, Path]:
    asset_dir = _default_asset_dir()
    return {
        "planclef.faiss": Path(os.getenv("PLANCLEF_INDEX_PATH", str(asset_dir / "planclef.faiss"))),
        "planclef_cache.pt": Path(os.getenv("PLANCLEF_CACHE_PATH", str(asset_dir / "planclef_cache.pt"))),
        "leafsnap.faiss": Path(os.getenv("LEAFSNAP_INDEX_PATH", str(asset_dir / "leafsnap.faiss"))),
        "leafsnap_cache.pt": Path(os.getenv("LEAFSNAP_CACHE_PATH", str(asset_dir / "leafsnap_cache.pt"))),
        "chroma.sqlite3": Path(os.getenv("RAG_DB_PATH", str(asset_dir / "plant_rag" / "chroma.sqlite3"))),
    }


def download_assets(repo_id: str, force: bool = False) -> int:
    targets = _resolve_target_paths()
    to_download = [
        name for name, target in targets.items()
        if force or not target.exists()
    ]

    if not to_download:
        print("[assets] Nothing to download: all target files already exist.")
        return 0

    print(f"[assets] Downloading {len(to_download)} file(s) from {repo_id} ...")
    downloaded_count = 0
    for name in to_download:
        target = targets[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        cached_path = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=name,
                repo_type=DATASET_REPO_TYPE,
            )
        )
        if cached_path.resolve() != target.resolve():
            target.write_bytes(cached_path.read_bytes())
        print(f"[assets] Ready: {target}")
        downloaded_count += 1

    return downloaded_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Download project assets from Hugging Face dataset")
    parser.add_argument("--repo", default=DEFAULT_REPO_ID, help="Dataset repo id (owner/name)")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if target files already exist",
    )
    args = parser.parse_args()

    count = download_assets(repo_id=args.repo, force=args.force)
    print(f"[assets] Download completed. Files downloaded: {count}")


if __name__ == "__main__":
    main()
