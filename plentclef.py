import os
import pickle
import torch
import open_clip
import faiss
import logging
from PIL import Image
from collections import defaultdict

logger = logging.getLogger("ai_green_assistant.plentclef")


def _load_plantclef_cache(cache_path):
    """Load PlantCLEF cache across PyTorch versions.

    PyTorch >=2.6 defaults torch.load(..., weights_only=True), which may fail
    for legacy cache files serialized with pickle opcodes.
    """
    try:
        return torch.load(cache_path, map_location="cpu", weights_only=True)
    except TypeError:
        # Older PyTorch versions don't support weights_only argument.
        return torch.load(cache_path, map_location="cpu")
    except Exception:
        try:
            # Legacy trusted cache fallback for PyTorch >=2.6 behavior change.
            return torch.load(cache_path, map_location="cpu", weights_only=False)
        except Exception:
            # Some historical cache files are plain pickle dictionaries.
            with open(cache_path, "rb") as f:
                return pickle.load(f)

def _rrf_merge(results_list: list[list], k: int = 60, debug_log: bool = False) -> list[tuple]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    Each element of *results_list* is a list of (species, score, image_paths)
    tuples already sorted by descending score.  Returns a merged list sorted
    by descending RRF score, same tuple format (image_paths will be empty).
    
    If debug_log=True, logs detailed RRF calculations for each species.
    """
    combined: dict[str, float] = defaultdict(float)
    species_sources: dict[str, list] = defaultdict(list)  # Track which index found each species
    
    for index_idx, ranked in enumerate(results_list):
        index_name = ["PlantCLEF", "LeafSnap"][index_idx] if index_idx < 2 else f"Index_{index_idx}"
        if debug_log:
            logger.info(f"  {index_name} results (top {len(ranked)}):")
        
        for rank, (species, _score, _paths) in enumerate(ranked):
            rrf_contribution = 1.0 / (k + rank + 1)
            combined[species] += rrf_contribution
            species_sources[species].append({
                "index": index_name,
                "rank": rank,
                "score": _score,
                "rrf_contribution": rrf_contribution
            })
            
            if debug_log and rank < 10:  # Log top 10 from each index
                logger.info(f"    Rank {rank:2d}: {species:40s} | raw_score: {_score:7.4f} | RRF += {rrf_contribution:.6f}")
    
    final_results = sorted(
        [(species, rrf_score, []) for species, rrf_score in combined.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    
    if debug_log:
        logger.info(f"\n  RRF Final Results (merged):")
        for final_rank, (species, rrf_score, _) in enumerate(final_results[:20]):
            sources_str = ", ".join(
                f"{s['index']}(rank={s['rank']}, contrib={s['rrf_contribution']:.6f})"
                for s in species_sources.get(species, [])
            )
            logger.info(f"    Rank {final_rank:2d}: {species:40s} | RRF_score: {rrf_score:.6f} | sources: {sources_str}")
    
    return final_results


class PlentClefIndex():
    def __init__(self, model_name, index_path, index_cache,
                 leafsnap_index_path=None, leafsnap_cache_path=None,
                 leafsnap_aliases: dict | None = None):
        self.model, self.preprocess, self.tokenizer = open_clip.create_model_and_transforms(
            model_name=model_name,
            pretrained="laion2b_s34b_b79k"
        )
        self.index = faiss.read_index(index_path)
        self.model.eval()
        data = _load_plantclef_cache(index_cache)
        if not isinstance(data, dict):
            raise TypeError("PlantCLEF cache must be a dict-like object")
        self.plantclef_image_embeddings = data.get("embeddings")
        if "labels" not in data:
            raise KeyError("Missing 'labels' in PlantCLEF cache")
        self.plantclef_labels = data["labels"]
        logger.info(f"PlantCLEF loaded: {len(self.plantclef_labels)} species")

        # Optional LeafSnap index (leaf-only images, same embedding space)
        self.leafsnap_index = None
        self.leafsnap_labels: list = []
        
        logger.info(f"Attempting to load LeafSnap from: {leafsnap_index_path}")
        
        if leafsnap_index_path and os.path.exists(leafsnap_index_path):
            logger.info(f"  ✓ LeafSnap index found at {leafsnap_index_path}")
            try:
                self.leafsnap_index = faiss.read_index(leafsnap_index_path)
                logger.info(f"  ✓ LeafSnap index loaded successfully")
            except Exception as e:
                logger.warning(f"  ✗ Failed to load LeafSnap index: {e}")
                self.leafsnap_index = None
            
            if leafsnap_cache_path and os.path.exists(leafsnap_cache_path):
                logger.info(f"  ✓ LeafSnap cache found at {leafsnap_cache_path}")
                try:
                    ls_data = _load_plantclef_cache(leafsnap_cache_path)
                    if not isinstance(ls_data, dict) or "labels" not in ls_data:
                        raise KeyError("Missing 'labels' in LeafSnap cache")
                    self.leafsnap_labels = ls_data["labels"]
                    logger.info(f"  ✓ LeafSnap cache loaded: {len(self.leafsnap_labels)} species")
                except Exception as e:
                    logger.warning(f"  ✗ Failed to load LeafSnap cache: {e}")
                    self.leafsnap_index = None
                    self.leafsnap_labels = []
            else:
                logger.warning(f"  ✗ LeafSnap cache not found at {leafsnap_cache_path}")
        else:
            logger.info(f"  ✗ LeafSnap index not found at {leafsnap_index_path}")
        
        if self.leafsnap_index and self.leafsnap_labels:
            logger.info(f"✓ LeafSnap enabled for search")
        else:
            logger.info(f"✗ LeafSnap disabled - search will use PlantCLEF only")
        
        # Dict mapping LeafSnap label -> canonical DB species name
        self.leafsnap_aliases: dict[str, str] = leafsnap_aliases or {}

    def embed_image(self, path):
        img = self.preprocess(Image.open(path).convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            e = self.model.encode_image(img)
            e = e / e.norm(dim=-1, keepdim=True)
        return e.cpu().numpy().astype("float32")

    def _search_index(self, q, index, labels, k, index_name="Index"):
        """Search a single FAISS index and aggregate scores by species."""
        sims, idxs = index.search(q, k)
        aggregated: dict = defaultdict(lambda: {'score_sum': 0.0, 'count': 0, 'image_paths': []})
        
        for score, idx in zip(sims[0], idxs[0]):
            species_label = labels[idx]
            species_label = self.leafsnap_aliases.get(species_label, species_label)
            aggregated[species_label]['score_sum'] += score
            aggregated[species_label]['count'] += 1
        
        results = [
            (cat, d['score_sum'] / d['count'], d['image_paths'])
            for cat, d in aggregated.items()
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def search(self, path, labels, k=5, debug=False, search_k=None, return_scores=False):
        q = self.embed_image(path)
        effective_k = max(int(search_k or k), int(k))

        planclef_results = self._search_index(q, self.index, labels, effective_k, "PlantCLEF")
        top_planclef_score = planclef_results[0][1] if planclef_results else 0.0

        if self.leafsnap_index is not None and self.leafsnap_labels:
            leafsnap_results = self._search_index(q, self.leafsnap_index, self.leafsnap_labels, effective_k, "LeafSnap")
            merged_all = _rrf_merge([planclef_results, leafsnap_results], debug_log=debug)
            merged = merged_all[:k]
            
            if debug:
                logger.info(f"\n=== SEARCH SUMMARY (RRF Merged) ===")
                logger.info(f"PlantCLEF found {len(planclef_results)} species, top 10:")
                for rank, (sp, score, _) in enumerate(planclef_results[:10]):
                    logger.info(f"  {rank}: {sp} ({score:.4f})")
                logger.info(f"LeafSnap found {len(leafsnap_results)} species, top 10:")
                for rank, (sp, score, _) in enumerate(leafsnap_results[:10]):
                    logger.info(f"  {rank}: {sp} ({score:.4f})")
                logger.info(f"\nFinal RRF merged ranking (top 50, requested k={k}, search_k={effective_k}):")
                for rank, (sp, score, _) in enumerate(merged_all[:50]):
                    logger.info(f"  {rank}: {sp} (RRF: {score:.6f})")
            
            if return_scores:
                return merged, top_planclef_score
            return merged

        if debug:
            logger.info(f"\n=== SEARCH SUMMARY (PlantCLEF only) ===")
            logger.info(f"Found {len(planclef_results)} species, showing top 50 (requested k={k}, search_k={effective_k}):")
            for rank, (sp, score, _) in enumerate(planclef_results[:50]):
                logger.info(f"  {rank}: {sp} ({score:.6f})")
        
        if return_scores:
            return planclef_results[:k], top_planclef_score
        return planclef_results[:k]
