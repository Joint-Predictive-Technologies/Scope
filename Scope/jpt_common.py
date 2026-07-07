#!/usr/bin/env python3

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


SCHEMA_PATH = Path(__file__).resolve().parent / "schema_sqlite.sql"


AMOUNT_BANDS: dict[str, str] = {
    "$1,001 - $15,000": "$1k–15k",
    "$15,001 - $50,000": "$15k–50k",
    "$50,001 - $100,000": "$50k–100k",
    "$100,001 - $250,000": "$100k–250k",
    "$250,001 - $500,000": "$250k–500k",
    "$500,001 - $1,000,000": "$500k–1M",
    "$1,000,001 - $5,000,000": "$1M–5M",
    "$5,000,001 - $25,000,000": "$5M–25M",
    "$25,000,001 - $50,000,000": "$25M–50M",
    "Over $50,000,000": "$50M+",
}


CRITICAL_TAGS = {"cluster", "cross_reference"}
HIGH_TAGS = {"amount_above_50k"}


def _initialize_schema(conn: sqlite3.Connection) -> None:
    if not SCHEMA_PATH.exists():
        return

    with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        conn.executescript(handle.read())

    conn.commit()


def db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Return a SQLite connection for the project database.

    DB path priority:
    1. Explicit db_path argument
    2. DATABASE_PATH from .env
    3. Default: ./data/jpt.db

    Initializes all tables from schema_sqlite.sql before returning.
    """
    load_dotenv()

    default = Path(__file__).resolve().parent / "data" / "jpt.db"
    path = db_path or os.getenv("DATABASE_PATH") or str(default)
    db_file = Path(path)

    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    _initialize_schema(conn)

    return conn


def severity_score(tags: list[str]) -> str:
    """
    CRITICAL if a cluster or cross-reference tag is present, HIGH if the
    transaction amount exceeds $50k (tagged "amount_above_50k"), MEDIUM otherwise.
    """
    tag_set = {str(tag).strip().casefold() for tag in tags if tag}

    if tag_set & CRITICAL_TAGS:
        return "CRITICAL"

    if tag_set & HIGH_TAGS:
        return "HIGH"

    return "MEDIUM"
