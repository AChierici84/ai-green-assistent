import importlib
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


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
