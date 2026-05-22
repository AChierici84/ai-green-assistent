import json
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


def configure_logging() -> None:
    """Configure logging for all ai_green_assistant modules."""
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


def _truncate_for_log(value: Any, max_len: int = 500) -> str:
    text = str(value or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _log_api(endpoint: str, event: str, payload: dict[str, Any]) -> None:
    logger = logging.getLogger("ai_green_assistant.api")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        serialized = str(payload)
    logger.info("%s | %s | %s", endpoint, event, serialized)


def _response_payload_for_log(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status_code": getattr(response, "status_code", None),
        "content_type": getattr(response, "media_type", None)
        or getattr(response, "headers", {}).get("content-type", ""),
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
            payload["body"] = _truncate_for_log(text)
        return payload

    if content_type.startswith("text/") or "xml" in content_type or "javascript" in content_type:
        payload["body"] = _truncate_for_log(text)

    return payload
