from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import requests


class OllamaEmbeddingClient:
    """Small Ollama `/api/embed` client with response validation."""

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: int = 300,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            raise ValueError("At least one text is required for embedding")
        if any(not text.strip() for text in texts):
            raise ValueError("Embedding texts must not be empty")

        try:
            response = requests.post(
                f"{self.base_url}/api/embed",
                json={
                    "model": self.model,
                    "input": list(texts),
                    "truncate": False,
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload: Any = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Ollama embedding request failed for model {self.model!r}: {exc}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("Ollama returned invalid JSON for embedding") from exc

        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise RuntimeError(
                "Ollama embedding response count does not match input count"
            )

        dimensions: set[int] = set()
        validated: list[list[float]] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or not embedding:
                raise RuntimeError("Ollama returned an empty embedding")
            if not all(isinstance(value, (int, float)) for value in embedding):
                raise RuntimeError("Ollama embedding contains a non-numeric value")
            vector = [float(value) for value in embedding]
            dimensions.add(len(vector))
            validated.append(vector)

        if len(dimensions) != 1:
            raise RuntimeError("Ollama returned inconsistent embedding dimensions")
        return validated
