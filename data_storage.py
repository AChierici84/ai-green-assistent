import json
from datetime import datetime, timedelta
from typing import Any, Callable

from fastapi import HTTPException

from db_config import is_mysql_enabled, parse_mysql_components
from mysql_compat import _MySQLCompatConnection


PLANT_PROFILE_FIELDS = (
    "species_name",
    "indexed",
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
    "updated_at",
)


class DataStorage:
    def __init__(
        self,
        mysql_config: dict[str, Any],
        *,
        plant_card_cache_enabled: bool,
        format_datetime_display: Callable[[Any], Any],
    ) -> None:
        self.mysql_config = mysql_config
        self.plant_card_cache_enabled = bool(plant_card_cache_enabled)
        self.format_datetime_display = format_datetime_display

    def _is_mysql_conn(self, conn: Any) -> bool:
        return isinstance(conn, _MySQLCompatConnection)

    def get_plants_db_connection(self) -> Any:
        mysql_params = parse_mysql_components(self.mysql_config)
        if not mysql_params:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Configurazione MySQL mancante: imposta MYSQL_USER, MYSQL_DATABASE e "
                    "connessione (MYSQL_HOST/MYSQL_PORT oppure socket)."
                ),
            )
        return _MySQLCompatConnection(mysql_params)

    def get_user_plants_db_connection(self) -> Any:
        if not is_mysql_enabled(self.mysql_config):
            raise HTTPException(
                status_code=503,
                detail="Configurazione MySQL obbligatoria: imposta MYSQL_ENABLED=1 e credenziali valide.",
            )

        mysql_params = parse_mysql_components(self.mysql_config)
        conn = _MySQLCompatConnection(mysql_params)
        self.ensure_user_plants_table(conn)
        self.ensure_registered_users_table(conn)
        self.ensure_recognition_logs_table(conn)
        return conn

    def ensure_plant_cards_cache_table(self, conn: Any) -> None:
        if self._is_mysql_conn(conn):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS plant_cards_cache (
                    species_name VARCHAR(255) NOT NULL,
                    lang VARCHAR(10) NOT NULL,
                    title TEXT NOT NULL,
                    common_name TEXT,
                    summary TEXT NOT NULL,
                    markdown TEXT NOT NULL,
                    images_json TEXT NOT NULL,
                    source VARCHAR(64) NOT NULL,
                    updated_at VARCHAR(40) NOT NULL,
                    PRIMARY KEY (species_name, lang)
                )
                """
            )
            try:
                conn.execute(
                    "CREATE INDEX idx_plant_cards_cache_updated_at ON plant_cards_cache(updated_at)"
                )
            except Exception:
                pass
            conn.commit()
            return

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS plant_cards_cache (
                species_name TEXT NOT NULL,
                lang TEXT NOT NULL,
                title TEXT NOT NULL,
                common_name TEXT,
                summary TEXT NOT NULL,
                markdown TEXT NOT NULL,
                images_json TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (species_name, lang)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_plant_cards_cache_updated_at ON plant_cards_cache(updated_at)"
        )
        conn.commit()

    def get_cached_plant_card(self, name: str, lang: str) -> dict[str, Any] | None:
        if not self.plant_card_cache_enabled:
            return None

        species_name = (name or "").strip()
        lang_code = (lang or "it").strip().lower()
        if not species_name:
            return None

        with self.get_plants_db_connection() as conn:
            self.ensure_plant_cards_cache_table(conn)
            row = conn.execute(
                (
                    "SELECT title, common_name, summary, markdown, images_json, source, updated_at "
                    "FROM plant_cards_cache "
                    "WHERE lower(species_name) = lower(?) AND lower(lang) = lower(?) "
                    "LIMIT 1"
                ),
                (species_name, lang_code),
            ).fetchone()

        if row is None:
            return None

        images: list[str] = []
        raw_images = row["images_json"] if "images_json" in row.keys() else "[]"
        try:
            parsed = json.loads(raw_images or "[]")
            if isinstance(parsed, list):
                images = [str(item) for item in parsed if str(item).strip()]
        except Exception:
            images = []

        return {
            "title": row["title"],
            "common_name": row["common_name"] or "",
            "markdown": row["markdown"],
            "summary": row["summary"],
            "images": images,
            "source": row["source"],
            "cache_updated_at": row["updated_at"],
        }

    def upsert_cached_plant_card(self, name: str, lang: str, payload: dict[str, Any]) -> None:
        if not self.plant_card_cache_enabled:
            return

        species_name = (name or "").strip()
        lang_code = (lang or "it").strip().lower()
        if not species_name:
            return

        title = str(payload.get("title") or species_name)
        common_name = str(payload.get("common_name") or "")
        summary = str(payload.get("summary") or "")
        markdown = str(payload.get("markdown") or "")
        source = str(payload.get("source") or "rag")
        images = payload.get("images")
        images_json = json.dumps(images if isinstance(images, list) else [], ensure_ascii=False)
        updated_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        with self.get_plants_db_connection() as conn:
            self.ensure_plant_cards_cache_table(conn)
            conn.execute(
                (
                    "INSERT INTO plant_cards_cache "
                    "(species_name, lang, title, common_name, summary, markdown, images_json, source, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON DUPLICATE KEY UPDATE "
                    "title=VALUES(title), "
                    "common_name=VALUES(common_name), "
                    "summary=VALUES(summary), "
                    "markdown=VALUES(markdown), "
                    "images_json=VALUES(images_json), "
                    "source=VALUES(source), "
                    "updated_at=VALUES(updated_at)"
                ),
                (
                    species_name,
                    lang_code,
                    title,
                    common_name,
                    summary,
                    markdown,
                    images_json,
                    source,
                    updated_at,
                ),
            )
            conn.commit()

    def get_species_images_from_db(self, species_name: str) -> list[str]:
        query = "SELECT image_paths FROM plants WHERE lower(species_name) = lower(?) LIMIT 1"
        with self.get_plants_db_connection() as conn:
            row = conn.execute(query, (species_name.strip(),)).fetchone()

        if row is None:
            return []

        raw = row["image_paths"] if "image_paths" in row.keys() else None
        if not raw:
            return []

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

        if not isinstance(parsed, list):
            return []

        return [str(v).strip() for v in parsed if str(v).strip()]

    def insert_draft_plant_if_missing(self, species_name: str) -> bool:
        with self.get_plants_db_connection() as conn:
            row = conn.execute(
                "SELECT id FROM plants WHERE lower(species_name) = lower(?) LIMIT 1",
                (species_name.strip(),),
            ).fetchone()
            if row is not None:
                return False

        profile: dict[str, Any] = {}
        now_iso = datetime.utcnow().isoformat()
        with self.get_plants_db_connection() as conn:
            conn.execute(
                """
                INSERT IGNORE INTO plants (
                    species_name, indexed, annaffiatura_gg, annaffiatura_time, luce, temperatura,
                    umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione, updated_at
                ) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    species_name,
                    profile.get("annaffiatura_gg"),
                    profile.get("annaffiatura_time"),
                    profile.get("luce"),
                    profile.get("temperatura"),
                    profile.get("umidita"),
                    profile.get("altezza_media"),
                    profile.get("pulizia"),
                    profile.get("terriccio"),
                    profile.get("concimazione"),
                    profile.get("prevenzione"),
                    now_iso,
                ),
            )
            conn.commit()
        return True

    def get_plant_profile_from_db(self, name: str) -> dict[str, Any] | None:
        query = (
            "SELECT species_name, indexed, annaffiatura_gg, annaffiatura_time, luce, temperatura, "
            "umidita, altezza_media, pulizia, terriccio, concimazione, prevenzione, updated_at "
            "FROM plants WHERE lower(species_name) = lower(?) LIMIT 1"
        )

        with self.get_plants_db_connection() as conn:
            row = conn.execute(query, (name.strip(),)).fetchone()

        if row is None:
            return None

        payload = {field: row[field] for field in PLANT_PROFILE_FIELDS}
        payload["indexed"] = bool(payload["indexed"])
        payload["updated_at"] = self.format_datetime_display(payload["updated_at"])
        return payload

    def ensure_user_plants_table(self, conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plants (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                plant_name VARCHAR(255) NOT NULL,
                user_given_name VARCHAR(255) NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                user_email VARCHAR(255) NULL,
                user_photo_url TEXT NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_plant_photos (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                plant_id BIGINT NOT NULL,
                photo_url TEXT NOT NULL,
                created_at VARCHAR(40) NOT NULL,
                FOREIGN KEY (plant_id) REFERENCES user_plants(id) ON DELETE CASCADE
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_user_plant_photos_plant_id ON user_plant_photos(plant_id)"
            )
        except Exception:
            pass
        conn.commit()

    def ensure_registered_users_table(self, conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registered_users (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                google_sub VARCHAR(255) NOT NULL UNIQUE,
                email VARCHAR(255) NOT NULL,
                registered_at VARCHAR(40) NOT NULL
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_registered_users_email ON registered_users(email)"
            )
        except Exception:
            pass
        conn.commit()

    def ensure_recognition_logs_table(self, conn: Any) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recognition_logs (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                user_id VARCHAR(255) NOT NULL,
                user_email VARCHAR(255) NULL,
                user_type VARCHAR(16) NOT NULL,
                chosen_species VARCHAR(255) NOT NULL,
                image_url TEXT NULL,
                used_openai TINYINT(1) NOT NULL DEFAULT 0,
                recognition_ms INT NULL,
                created_at VARCHAR(40) NOT NULL
            )
            """
        )
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_created_at ON recognition_logs(created_at)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_species ON recognition_logs(chosen_species)"
            )
        except Exception:
            pass
        try:
            conn.execute(
                "CREATE INDEX idx_recognition_logs_user_id ON recognition_logs(user_id)"
            )
        except Exception:
            pass
        conn.commit()

    def create_recognition_log(
        self,
        chosen_species: str,
        used_openai: bool,
        image_url: str | None,
        recognition_ms: int | None,
        user: dict[str, Any] | None,
    ) -> dict[str, Any]:
        species_clean = str(chosen_species or "").strip()
        if not species_clean:
            raise HTTPException(status_code=400, detail="Specie scelta obbligatoria.")

        user_id = str((user or {}).get("sub") or "").strip() or "guest"
        user_email = str((user or {}).get("email") or "").strip() or None
        user_type = "user" if user and user_id != "guest" else "guest"
        image_url_clean = str(image_url or "").strip() or None
        recognition_ms_value = None if recognition_ms is None else max(0, int(recognition_ms))
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        with self.get_user_plants_db_connection() as conn:
            self.ensure_recognition_logs_table(conn)
            cursor = conn.execute(
                (
                    "INSERT INTO recognition_logs "
                    "(user_id, user_email, user_type, chosen_species, image_url, used_openai, recognition_ms, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    user_id,
                    user_email,
                    user_type,
                    species_clean,
                    image_url_clean,
                    1 if used_openai else 0,
                    recognition_ms_value,
                    created_at,
                ),
            )
            conn.commit()

        return {
            "id": int(cursor.lastrowid),
            "user_id": user_id,
            "user_email": user_email,
            "user_type": user_type,
            "chosen_species": species_clean,
            "image_url": image_url_clean,
            "used_openai": bool(used_openai),
            "recognition_ms": recognition_ms_value,
            "created_at": created_at,
        }

    def get_recognition_admin_aggregates(self, conn: Any, chart_days: int = 30) -> dict[str, Any]:
        self.ensure_recognition_logs_table(conn)

        safe_days = int(chart_days) if chart_days in (7, 30, 90) else 30
        window_start = (datetime.utcnow() - timedelta(days=safe_days - 1)).strftime("%Y-%m-%d") + "T00:00:00Z"

        totals = conn.execute(
            """
            SELECT
                COUNT(1) AS total,
                SUM(CASE WHEN user_type = 'guest' THEN 1 ELSE 0 END) AS guest_total,
                SUM(CASE WHEN user_type = 'user' THEN 1 ELSE 0 END) AS user_total,
                SUM(CASE WHEN used_openai = 1 THEN 1 ELSE 0 END) AS openai_total,
                SUM(CASE WHEN image_url IS NOT NULL AND trim(image_url) <> '' THEN 1 ELSE 0 END) AS with_image_total,
                COUNT(recognition_ms) AS timed_total,
                AVG(recognition_ms * 1.0) AS avg_recognition_ms
            FROM recognition_logs
            WHERE created_at >= ?
            """,
            (window_start,),
        ).fetchone()

        top_species_rows = conn.execute(
            """
            SELECT chosen_species, COUNT(1) AS count
            FROM recognition_logs
            WHERE created_at >= ?
            GROUP BY chosen_species
            ORDER BY count DESC, chosen_species ASC
            LIMIT 8
            """,
            (window_start,),
        ).fetchall()

        daily_rows = conn.execute(
            """
            SELECT
                substr(created_at, 1, 10) AS day,
                COUNT(1) AS total,
                SUM(CASE WHEN used_openai = 1 THEN 1 ELSE 0 END) AS openai
            FROM recognition_logs
            WHERE created_at >= ?
            GROUP BY substr(created_at, 1, 10)
            ORDER BY day DESC
            LIMIT ?
            """,
            (window_start, safe_days),
        ).fetchall()

        daily_series = [
            {
                "day": str(row["day"] or ""),
                "total": int(row["total"] or 0),
                "openai": int(row["openai"] or 0),
            }
            for row in reversed(daily_rows)
        ]

        top_species = [
            {
                "species": str(row["chosen_species"] or ""),
                "count": int(row["count"] or 0),
            }
            for row in top_species_rows
        ]

        return {
            "chart_days": safe_days,
            "total": int((totals["total"] or 0) if totals else 0),
            "guest_total": int((totals["guest_total"] or 0) if totals else 0),
            "user_total": int((totals["user_total"] or 0) if totals else 0),
            "openai_total": int((totals["openai_total"] or 0) if totals else 0),
            "with_image_total": int((totals["with_image_total"] or 0) if totals else 0),
            "avg_recognition_ms": (
                float(totals["avg_recognition_ms"])
                if totals and int(totals["timed_total"] or 0) > 0 and totals["avg_recognition_ms"] is not None
                else None
            ),
            "top_species": top_species,
            "daily_series": daily_series,
        }

    def register_google_user_if_needed(self, user: dict[str, Any]) -> tuple[bool, str]:
        google_sub = str(user.get("sub") or "").strip()
        email = str(user.get("email") or "").strip()
        if not google_sub or not email:
            return False, ""

        with self.get_user_plants_db_connection() as conn:
            self.ensure_registered_users_table(conn)
            existing = conn.execute(
                "SELECT registered_at FROM registered_users WHERE google_sub = ? LIMIT 1",
                (google_sub,),
            ).fetchone()
            if existing:
                return False, str(existing["registered_at"] or "")

            registered_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
            conn.execute(
                (
                    "INSERT INTO registered_users "
                    "(google_sub, email, registered_at) VALUES (?, ?, ?)"
                ),
                (google_sub, email, registered_at),
            )
            conn.commit()
            return True, registered_at

    def list_registered_users_for_admin(self, limit: int = 300) -> list[dict[str, Any]]:
        max_limit = max(1, min(int(limit), 1000))
        with self.get_user_plants_db_connection() as conn:
            self.ensure_registered_users_table(conn)
            rows = conn.execute(
                (
                    "SELECT email, registered_at "
                    "FROM registered_users "
                    "ORDER BY registered_at DESC "
                    "LIMIT ?"
                ),
                (max_limit,),
            ).fetchall()
            return [
                {
                    "email": str(row["email"] or ""),
                    "registered_at": str(row["registered_at"] or ""),
                    "registered_at_display": self.format_datetime_display(row["registered_at"]),
                }
                for row in rows
            ]

    def _get_user_plant_photo_urls(self, conn: Any, plant_id: int, fallback_url: str | None) -> list[str]:
        rows = conn.execute(
            "SELECT photo_url FROM user_plant_photos WHERE plant_id = ? ORDER BY id DESC",
            (plant_id,),
        ).fetchall()
        urls = [str(r["photo_url"] or "").strip() for r in rows if str(r["photo_url"] or "").strip()]
        if urls:
            return urls

        fallback = str(fallback_url or "").strip()
        return [fallback] if fallback else []

    def _user_plant_row_to_payload(self, conn: Any, row: Any) -> dict[str, Any]:
        plant_id = int(row["id"])
        fallback_photo = row["user_photo_url"] if "user_photo_url" in row.keys() else None
        photo_urls = self._get_user_plant_photo_urls(conn, plant_id, fallback_photo)
        return {
            "id": plant_id,
            "plant_name": row["plant_name"],
            "user_given_name": row["user_given_name"],
            "user": row["user_email"] or row["user_id"],
            "user_photo_url": (photo_urls[0] if photo_urls else None),
            "user_photos": photo_urls,
            "created_at_iso": row["created_at"],
            "created_at": self.format_datetime_display(row["created_at"]),
        }

    def create_user_plant(self, plant_name: str, user_given_name: str, user: dict[str, Any]) -> dict[str, Any]:
        plant_name_clean = plant_name.strip()
        user_given_name_clean = user_given_name.strip()
        user_id = str(user.get("sub") or "").strip()
        user_email = str(user.get("email") or "").strip()
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

        if not plant_name_clean:
            raise HTTPException(status_code=400, detail="Nome pianta obbligatorio.")
        if not user_given_name_clean:
            raise HTTPException(status_code=400, detail="Nome scelto dall'utente obbligatorio.")
        if not user_id:
            raise HTTPException(status_code=401, detail="Utente Google non valido.")

        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            cursor = conn.execute(
                (
                    "INSERT INTO user_plants "
                    "(plant_name, user_given_name, user_id, user_email, created_at) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                (plant_name_clean, user_given_name_clean, user_id, user_email, created_at),
            )
            conn.commit()

            row = conn.execute(
                (
                    "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                    "FROM user_plants WHERE id = ?"
                ),
                (cursor.lastrowid,),
            ).fetchone()
            return self._user_plant_row_to_payload(conn, row)

    def list_user_plants(self, user: dict[str, Any]) -> list[dict[str, Any]]:
        user_id = str(user.get("sub") or "").strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="Utente Google non valido.")

        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            rows = conn.execute(
                (
                    "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                    "FROM user_plants WHERE user_id = ? ORDER BY id DESC"
                ),
                (user_id,),
            ).fetchall()
            return [self._user_plant_row_to_payload(conn, row) for row in rows]

    def delete_user_plant_by_id(self, user: dict[str, Any], plant_id: int) -> bool:
        user_id = str(user.get("sub") or "").strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="Utente Google non valido.")

        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            existing = conn.execute(
                "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
                (plant_id, user_id),
            ).fetchone()

            if existing is None:
                return False

            conn.execute(
                "DELETE FROM user_plant_photos WHERE plant_id = ?",
                (plant_id,),
            )

            conn.execute(
                "DELETE FROM user_plants WHERE id = ? AND user_id = ?",
                (plant_id, user_id),
            )
            conn.commit()

        return True

    def update_user_plant_created_at_by_id(
        self,
        user: dict[str, Any],
        plant_id: int,
        created_at_iso: str,
    ) -> dict[str, Any] | None:
        user_id = str(user.get("sub") or "").strip()
        if not user_id:
            raise HTTPException(status_code=401, detail="Utente Google non valido.")

        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            existing = conn.execute(
                "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
                (plant_id, user_id),
            ).fetchone()

            if existing is None:
                return None

            conn.execute(
                "UPDATE user_plants SET created_at = ? WHERE id = ? AND user_id = ?",
                (created_at_iso, plant_id, user_id),
            )
            conn.commit()

            row = conn.execute(
                (
                    "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                    "FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1"
                ),
                (plant_id, user_id),
            ).fetchone()
            if row is None:
                return None
            return self._user_plant_row_to_payload(conn, row)

    def get_catalog_species_stats(self) -> tuple[int, int]:
        with self.get_plants_db_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT lower(species_name)) AS c FROM plants"
            ).fetchone()
            species_db_total = int((row["c"] if row else 0) or 0)

            row_rag = conn.execute(
                "SELECT COUNT(DISTINCT lower(species_name)) AS c FROM plants WHERE indexed = 1"
            ).fetchone()
            species_rag_total = int((row_rag["c"] if row_rag else 0) or 0)

        return species_db_total, species_rag_total

    def get_leafsnap_aliases(self) -> dict[str, str]:
        try:
            with self.get_plants_db_connection() as conn:
                rows = conn.execute(
                    "SELECT leafsnap_label, db_species_name FROM leafsnap_aliases"
                ).fetchall()
            normalized_rows = [
                (
                    r["leafsnap_label"] if hasattr(r, "keys") else r[0],
                    r["db_species_name"] if hasattr(r, "keys") else r[1],
                )
                for r in rows
            ]
            return {
                str(left): str(right) for left, right in normalized_rows if left and right
            }
        except Exception:
            return {}

    def get_draft_species_set(self, species_names: list[str]) -> set[str]:
        if not species_names:
            return set()

        with self.get_plants_db_connection() as conn:
            placeholders = ",".join("?" * len(species_names))
            rows = conn.execute(
                f"SELECT species_name, indexed FROM plants WHERE lower(species_name) IN ({placeholders})",
                [n.lower() for n in species_names],
            ).fetchall()

        indexed_map = {row["species_name"].lower(): bool(row["indexed"]) for row in rows}
        return {name.lower() for name in species_names if not indexed_map.get(name.lower(), True)}

    def get_admin_console_stats(self, chart_days: int) -> tuple[dict[str, int], dict[str, Any]]:
        with self.get_user_plants_db_connection() as conn:
            self.ensure_recognition_logs_table(conn)
            total_registered = conn.execute("SELECT COUNT(1) AS c FROM registered_users").fetchone()["c"]
            total_saved_plants = conn.execute("SELECT COUNT(1) AS c FROM user_plants").fetchone()["c"]
            total_external_user_images = conn.execute(
                "SELECT COUNT(1) AS c FROM user_plant_photos WHERE photo_url IS NOT NULL AND trim(photo_url) <> ''"
            ).fetchone()["c"]
            recognition = self.get_recognition_admin_aggregates(conn, chart_days=chart_days)

        totals = {
            "registered_users_total": int(total_registered or 0),
            "saved_plants_total": int(total_saved_plants or 0),
            "external_user_images_total": int(total_external_user_images or 0),
        }
        return totals, recognition

    def user_plant_exists_for_user(self, plant_id: int, user_id: str) -> bool:
        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            row = conn.execute(
                "SELECT id FROM user_plants WHERE id = ? AND user_id = ? LIMIT 1",
                (plant_id, user_id),
            ).fetchone()
        return row is not None

    def save_user_plant_photo(self, plant_id: int, user_id: str, photo_url: str) -> dict[str, Any]:
        with self.get_user_plants_db_connection() as conn:
            self.ensure_user_plants_table(conn)
            created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
            conn.execute(
                "INSERT INTO user_plant_photos (plant_id, photo_url, created_at) VALUES (?, ?, ?)",
                (plant_id, photo_url, created_at),
            )
            conn.execute(
                "UPDATE user_plants SET user_photo_url = ? WHERE id = ? AND user_id = ?",
                (photo_url, plant_id, user_id),
            )
            conn.commit()
            updated_row = conn.execute(
                "SELECT id, plant_name, user_given_name, user_id, user_email, user_photo_url, created_at "
                "FROM user_plants WHERE id = ?",
                (plant_id,),
            ).fetchone()
            return self._user_plant_row_to_payload(conn, updated_row)
