from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import re

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COLLECTION_NAME = "ot_security_chunks_v1"
_SCENARIO_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _integer_setting(name: str, default: int, minimum: int = 1) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}, got {value}")
    return value


def _float_setting(
    name: str,
    default: float,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric, got {raw_value!r}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


def _project_path(raw_value: str) -> Path:
    path = Path(raw_value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def collection_name_for_scenario(
    base_collection_name: str,
    scenario: str,
) -> str:
    """Return a persistent collection name isolated to one scenario.

    The existing Ryuk collection keeps its original name for backward
    compatibility. New scenarios receive a stable suffix so rebuilding one
    scenario cannot delete vectors belonging to another scenario.
    """

    normalized = scenario.strip().casefold()
    if not _SCENARIO_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "scenario must use lowercase letters, digits, and underscores"
        )
    if normalized == "ryuk":
        return base_collection_name
    return f"{base_collection_name}_{normalized}"


@dataclass(frozen=True)
class Settings:
    ollama_base_url: str
    chat_model: str
    embed_model: str
    chroma_path: Path
    top_k: int
    num_ctx: int
    num_predict: int
    dense_weight: float
    sparse_weight: float
    temperature: float
    ollama_keep_alive: str
    response_cache_size: int
    collection_name: str = DEFAULT_COLLECTION_NAME


def load_settings() -> Settings:
    """Load local settings from environment variables and optional `.env`."""

    load_dotenv(PROJECT_ROOT / ".env")
    ollama_base_url = os.getenv(
        "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
    ).rstrip("/")
    if not ollama_base_url:
        raise ValueError("OLLAMA_BASE_URL must not be empty")

    ollama_keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "15m").strip()
    if not ollama_keep_alive:
        raise ValueError("OLLAMA_KEEP_ALIVE must not be empty")

    return Settings(
        ollama_base_url=ollama_base_url,
        chat_model=os.getenv("CHAT_MODEL", "llama3.1:8b-instruct-q5_0"),
        embed_model=os.getenv(
            "EMBED_MODEL", "embeddinggemma:300m-qat-q8_0"
        ),
        chroma_path=_project_path(os.getenv("CHROMA_PATH", "./data/chroma")),
        top_k=_integer_setting("TOP_K", 4),
        num_ctx=_integer_setting("NUM_CTX", 4096),
        num_predict=_integer_setting("NUM_PREDICT", 500),
        dense_weight=_float_setting("DENSE_WEIGHT", 0.7),
        sparse_weight=_float_setting("SPARSE_WEIGHT", 0.3),
        # A small amount of sampling avoids repetitive, template-like prose
        # while structured decoding and validators keep event claims bounded.
        temperature=_float_setting("TEMPERATURE", 0.15),
        ollama_keep_alive=ollama_keep_alive,
        response_cache_size=_integer_setting(
            "RESPONSE_CACHE_SIZE",
            32,
            minimum=0,
        ),
    )
