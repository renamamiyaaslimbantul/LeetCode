"""
shortener.py — candidate implementation file

Complete the URLShortener service used by app.py.
You may add helper classes, methods, and constants, but preserve the public
method names used by app.py and the tests.
"""

import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from db import get_db

AUTO_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{6,10}$")
CUSTOM_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
MAX_GENERATION_ATTEMPTS = 5

AUTO_CODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
AUTO_CODE_LENGTH = 8


class ShortenerError(Exception):
    pass


class ValidationError(ShortenerError):
    pass


class NotFoundError(ShortenerError):
    pass


class ConflictError(ShortenerError):
    pass


class ExpiredLinkError(ShortenerError):
    pass


class PermissionDeniedError(ShortenerError):
    pass


class CodeGenerationError(ShortenerError):
    pass


@dataclass(frozen=True)
class Link:
    short_code: str
    long_url: str
    created_at: float
    expires_at: Optional[float]
    hit_count: int
    owner_token: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and time.time() >= self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "short_code": self.short_code,
            "long_url": self.long_url,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "hit_count": self.hit_count,
            "is_expired": self.is_expired,
        }


class URLShortener:
    """
    SQLite-backed short link service.

    Required public methods:
      create(long_url, custom_code=None, ttl_seconds=None) -> Link
      get(short_code) -> Link | None
      resolve_redirect(short_code) -> str
      increment_hits(short_code) -> None
      delete(short_code, owner_token) -> bool
      get_stats() -> dict
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the database schema needed by the service."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    short_code TEXT PRIMARY KEY,
                    long_url TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    hit_count INTEGER NOT NULL DEFAULT 0,
                    owner_token TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _generate_code(self, long_url: str) -> str:
        """Return a new automatic short code matching [A-Za-z0-9]{6,10}."""
        return "".join(
            secrets.choice(AUTO_CODE_ALPHABET) for _ in range(AUTO_CODE_LENGTH)
        )

    def _generate_owner_token(self) -> str:
        """Return a secret token used to authorize deletion."""
        return "tok_" + secrets.token_urlsafe(24)

    def _exists(self, conn: sqlite3.Connection, short_code: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM links WHERE short_code = ?", (short_code,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _validate_long_url(long_url: Any) -> str:
        if not isinstance(long_url, str):
            raise ValidationError("long_url must be a string")
        if not long_url.strip():
            raise ValidationError("long_url must not be empty")
        parsed = urlparse(long_url.strip())
        if parsed.scheme not in ("http", "https"):
            raise ValidationError("long_url must use http:// or https://")
        if not parsed.netloc:
            raise ValidationError("long_url must include a valid host")
        return long_url.strip()

    @staticmethod
    def _validate_custom_code(custom_code: Any) -> str:
        if not isinstance(custom_code, str) or not CUSTOM_CODE_PATTERN.match(custom_code):
            raise ValidationError("custom_code must match ^[A-Za-z0-9_-]{3,32}$")
        return custom_code

    @staticmethod
    def _validate_ttl(ttl_seconds: Any) -> float:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ValidationError("ttl_seconds must be a positive number")
        if ttl_seconds <= 0:
            raise ValidationError("ttl_seconds must be a positive number")
        return float(ttl_seconds)

    def create(
        self,
        long_url: str,
        custom_code: Optional[str] = None,
        ttl_seconds: Optional[float] = None,
    ) -> Link:
        """
        Create a short link.

        Raise ValidationError for invalid input.
        Raise ConflictError if a custom code already exists.
        Raise CodeGenerationError if automatic code generation repeatedly collides.
        """
        long_url = self._validate_long_url(long_url)

        if custom_code is not None:
            short_code = self._validate_custom_code(custom_code)
        else:
            short_code = None

        ttl = None if ttl_seconds is None else self._validate_ttl(ttl_seconds)

        with self._lock:
            conn = self._get_conn()
            created_at = time.time()
            expires_at = None if ttl is None else created_at + ttl
            owner_token = self._generate_owner_token()

            if short_code is not None:
                if self._exists(conn, short_code):
                    raise ConflictError(f"short code already exists: {short_code}")
            else:
                for _ in range(MAX_GENERATION_ATTEMPTS):
                    candidate = self._generate_code(long_url)
                    if not isinstance(candidate, str) or not AUTO_CODE_PATTERN.match(
                        candidate
                    ):
                        raise CodeGenerationError("generated code has invalid format")
                    if not self._exists(conn, candidate):
                        short_code = candidate
                        break
                else:
                    raise CodeGenerationError("could not generate a unique short code")

            try:
                conn.execute(
                    """
                    INSERT INTO links
                        (short_code, long_url, created_at, expires_at, hit_count, owner_token)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (short_code, long_url, created_at, expires_at, owner_token),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                if custom_code is not None:
                    raise ConflictError(f"short code already exists: {short_code}")
                raise CodeGenerationError("could not generate a unique short code")

            return Link(
                short_code=short_code,
                long_url=long_url,
                created_at=created_at,
                expires_at=expires_at,
                hit_count=0,
                owner_token=owner_token,
            )

    def _row_to_link(self, row: sqlite3.Row) -> Link:
        return Link(
            short_code=row["short_code"],
            long_url=row["long_url"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            hit_count=row["hit_count"],
            owner_token=row["owner_token"],
        )

    def get(self, short_code: str) -> Optional[Link]:
        """Return the link for short_code, or None when it does not exist."""
        if not isinstance(short_code, str):
            return None
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM links WHERE short_code = ?", (short_code,)
            ).fetchone()
        return None if row is None else self._row_to_link(row)

    def resolve_redirect(self, short_code: str) -> str:
        """
        Return the long URL for a redirect and atomically count the hit.

        Raise NotFoundError if the code does not exist.
        Raise ExpiredLinkError if the link is expired.
        """
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM links WHERE short_code = ?", (short_code,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"short code not found: {short_code}")
            if row["expires_at"] is not None and now >= row["expires_at"]:
                raise ExpiredLinkError(f"short code expired: {short_code}")
            conn.execute(
                "UPDATE links SET hit_count = hit_count + 1 WHERE short_code = ?",
                (short_code,),
            )
            conn.commit()
            return row["long_url"]

    def increment_hits(self, short_code: str) -> None:
        """
        Atomically increment hit_count for an existing short code.

        Raise NotFoundError if the code does not exist.
        """
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "UPDATE links SET hit_count = hit_count + 1 WHERE short_code = ?",
                (short_code,),
            )
            if cursor.rowcount == 0:
                conn.rollback()
                raise NotFoundError(f"short code not found: {short_code}")
            conn.commit()

    def delete(self, short_code: str, owner_token: str) -> bool:
        """
        Delete a link if owner_token matches.

        Return True when deleted, False when the code does not exist.
        Raise ValidationError when owner_token is missing or empty.
        Raise PermissionDeniedError when the token is wrong.
        """
        if not isinstance(owner_token, str) or not owner_token:
            raise ValidationError("owner_token is required")

        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT owner_token FROM links WHERE short_code = ?", (short_code,)
            ).fetchone()
            if row is None:
                return False
            if not secrets.compare_digest(row["owner_token"], owner_token):
                raise PermissionDeniedError("owner_token does not match")
            conn.execute("DELETE FROM links WHERE short_code = ?", (short_code,))
            conn.commit()
            return True

    def get_stats(self) -> Dict[str, Any]:
        """Return total_links, total_hits, and the top five links by hit count."""
        with self._lock:
            conn = self._get_conn()
            totals = conn.execute(
                "SELECT COUNT(*) AS c, COALESCE(SUM(hit_count), 0) AS h FROM links"
            ).fetchone()
            rows = conn.execute(
                """
                SELECT short_code, long_url, hit_count
                FROM links
                ORDER BY hit_count DESC, short_code ASC
                LIMIT 5
                """
            ).fetchall()

        return {
            "total_links": int(totals["c"]),
            "total_hits": int(totals["h"]),
            "top_5_links": [
                {
                    "short_code": row["short_code"],
                    "long_url": row["long_url"],
                    "hit_count": row["hit_count"],
                }
                for row in rows
            ],
        }

    def _get_conn(self) -> sqlite3.Connection:
        return get_db(self._db_path)