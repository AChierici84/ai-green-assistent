#!/usr/bin/env python3
import os
from pathlib import Path

from huggingface_hub import hf_hub_download


DATASET_REPO_ID = os.getenv("HF_ASSETS_DATASET_REPO", "AChierici84/GreenAssistent-assets")
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


def ensure_assets() -> None:
    targets = _resolve_target_paths()
    missing = [name for name, target in targets.items() if not target.exists()]
    if not missing:
        print("[assets] FAISS/cache artifacts already available.")
        return

    print(f"[assets] Downloading {len(missing)} artifact(s) from {DATASET_REPO_ID} ...")
    for name in missing:
        target = targets[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        downloaded = Path(
            hf_hub_download(
                repo_id=DATASET_REPO_ID,
                filename=name,
                repo_type=DATASET_REPO_TYPE,
            )
        )
        if downloaded.resolve() != target.resolve():
            target.write_bytes(downloaded.read_bytes())
        print(f"[assets] Ready: {target}")


if __name__ == "__main__":
    ensure_assets()