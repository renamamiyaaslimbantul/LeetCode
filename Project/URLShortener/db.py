"""
db.py — SQLite connection helper (read-only, do not modify)

The URLShortener service uses this module to obtain SQLite connections.
The default database file is shortener.db. Tests may pass an explicit db_path
or set URL_SHORTENER_DB_PATH before importing the app.
"""

import os
import sqlite3
import threading
from typing import Dict, Optional


_DEFAULT_DB_PATH = "shortener.db"
_connections: Dict[str, sqlite3.Connection] = {}
_lock = threading.Lock()


def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or os.environ.get("URL_SHORTENER_DB_PATH") or _DEFAULT_DB_PATH
    with _lock:
        conn = _connections.get(path)
        if conn is None:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            if path != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
            conn.commit()
            _connections[path] = conn
        return conn


def reset_connections_for_tests() -> None:
    with _lock:
        for conn in _connections.values():
            conn.close()
        _connections.clear()
