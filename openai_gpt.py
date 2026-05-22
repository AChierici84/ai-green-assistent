import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


logger = logging.getLogger("ai_green_assistant.api")


def _should_trigger_gpt_fallback(
    top_score: float,
    results: list[tuple[str, float, list]],
    *,
    force_openai_fallback: bool,
    faiss_confidence_threshold: float,
    faiss_ambiguity_margin: float,
    rrf_ambiguity_margin: float,
) -> tuple[bool, str]:
    """Decide whether GPT vision fallback should run."""
    if force_openai_fallback:
        return True, "forced_by_env"

    if top_score < faiss_confidence_threshold:
        return True, "low_top_score"

    if len(results) < 2:
        return False, "single_result"

    top_result_score = float(results[0][1])
    second_result_score = float(results[1][1])
    gap = max(0.0, top_result_score - second_result_score)
    rrf_like = top_result_score <= 0.1 and second_result_score <= 0.1

    if rrf_like and gap < rrf_ambiguity_margin:
        return True, "ambiguous_rrf_gap"

    if (not rrf_like) and gap < faiss_ambiguity_margin:
        return True, "ambiguous_similarity_gap"

    return False, "high_confidence"


def _gpt_vision_identify_plant(
    image_path: str,
    api_key: str,
    candidate_species: list[str] | None = None,
) -> tuple[str | None, str]:
    """Ask GPT-4o to identify the plant species from an image."""
    suffix = Path(image_path).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
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
        logger.info("GPT vision raw output: %s", raw[:200] if raw else "<empty>")

        if not raw or raw.lower().startswith("unknown"):
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
                logger.info(
                    "GPT vision candidate-mode output: %s",
                    raw2[:200] if raw2 else "<empty>",
                )
                cleaned2 = raw2.replace("*", " ").replace("`", " ").replace("_", " ")
                match2 = re.search(r"\b([A-Z][a-z\-]+)\s+([a-z][a-z\-]+)\b", cleaned2)
                if match2:
                    picked = f"{match2.group(1)} {match2.group(2)}"
                    if any(picked.lower() == c.lower() for c in candidate_species):
                        return picked, "ok_candidate_mode"

            return None, "model returned unknown or empty"

        cleaned = raw.replace("*", " ").replace("`", " ").replace("_", " ")
        match = re.search(r"\b([A-Z][a-z\-]+)\s+([a-z][a-z\-]+)\b", cleaned)
        if not match:
            return None, f"no binomial found in model output: {raw[:120]}"

        return f"{match.group(1)} {match.group(2)}", "ok"
    except Exception as exc:
        logger.warning("GPT vision fallback failed: %s", exc)
        return None, f"exception: {type(exc).__name__}: {exc}"
