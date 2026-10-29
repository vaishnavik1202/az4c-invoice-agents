"""Paths and settings. Everything is read from DATA_DIR so evaluators can swap in their own copy."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def data_dir() -> Path:
    d = os.getenv("DATA_DIR")
    if not d:
        raise SystemExit("DATA_DIR is not set. Example: set DATA_DIR=./data-pack (see README).")
    p = Path(d).resolve()
    if not (p / "data" / "ap.sqlite").exists():
        raise SystemExit(f"DATA_DIR={p} does not contain data/ap.sqlite")
    return p


def db_path() -> Path:
    return data_dir() / "data" / "ap.sqlite"


def docs_dir() -> Path:
    return data_dir() / "docs"


# Output folders live at the repo root (the current working directory when you run the CLI)
MOCK_SYSTEM_DIR = Path("mock_system")
LOGS_DIR = Path("logs")
