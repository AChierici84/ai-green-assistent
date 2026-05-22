import os
from typing import Any


def load_mysql_config() -> dict[str, Any]:
    mysql_user = os.getenv("MYSQL_USER", "").strip()
    mysql_password = os.getenv("MYSQL_PASSWORD", "").strip() or os.getenv("DB_PASSWORD", "")
    mysql_database = os.getenv("MYSQL_DATABASE", "").strip()
    mysql_host = os.getenv("MYSQL_HOST", "localhost").strip() or "localhost"
    mysql_port_raw = os.getenv("MYSQL_PORT", "").strip()

    mysql_enabled_raw = os.getenv("MYSQL_ENABLED")
    mysql_enabled = (mysql_enabled_raw or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    mysql_use_unix_socket = os.getenv("MYSQL_USE_UNIX_SOCKET", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    mysql_unix_socket = os.getenv("MYSQL_UNIX_SOCKET", "").strip()

    return {
        "user": mysql_user,
        "password": mysql_password,
        "database": mysql_database,
        "host": mysql_host,
        "port_raw": mysql_port_raw,
        "enabled_raw": mysql_enabled_raw,
        "enabled": mysql_enabled,
        "use_unix_socket": mysql_use_unix_socket,
        "unix_socket": mysql_unix_socket,
        "components_present": bool(mysql_user and mysql_database),
    }


def is_mysql_enabled(config: dict[str, Any]) -> bool:
    # If explicitly configured, honor MYSQL_ENABLED strictly.
    if config.get("enabled_raw") is not None:
        return bool(config.get("enabled"))

    return bool(config.get("components_present"))


def parse_mysql_components(config: dict[str, Any]) -> dict[str, Any] | None:
    if not config.get("components_present"):
        return None

    mysql_user = str(config.get("user") or "")
    mysql_database = str(config.get("database") or "")
    if not mysql_user or not mysql_database:
        raise RuntimeError(
            "Configurazione MySQL incompleta: MYSQL_USER e MYSQL_DATABASE sono obbligatori."
        )

    params: dict[str, Any] = {
        "user": mysql_user,
        "password": config.get("password") or "",
        "database": mysql_database,
    }

    if bool(config.get("use_unix_socket")):
        mysql_unix_socket = str(config.get("unix_socket") or "")
        if not mysql_unix_socket:
            raise RuntimeError("MYSQL_USE_UNIX_SOCKET=1 ma MYSQL_UNIX_SOCKET non e impostata.")
        params["unix_socket"] = mysql_unix_socket
        return params

    mysql_host = str(config.get("host") or "localhost")
    try:
        port = int(str(config.get("port_raw") or "") or 3306)
    except ValueError as exc:
        raise RuntimeError("MYSQL_PORT non valido: usa un numero intero") from exc

    params["host"] = mysql_host
    params["port"] = port
    return params
