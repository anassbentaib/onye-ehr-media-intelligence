import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def data_dir() -> Path:
    path = Path(os.getenv("EHR_DATA_DIR", "runtime"))
    return path if path.is_absolute() else ROOT / path
