from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.errors import NotFoundError

from app.schemas import KnowledgeChunk

INDEX_SCHEMA_VERSION = "1"


def chunk_to_document(chunk: KnowledgeChunk) -> str:
    """Create natural embedding text without serializing raw JSON syntax."""

    keywords = "、".join(chunk.keywords)
    return (
        f"標題：{chunk.title}\n"
        f"情境：{chunk.scenario}\n"
        f"階段：{chunk.stage}\n"
        f"內容：{chunk.text}\n"
        f"關鍵字：{keywords}"
    )


def index_fingerprint(
    chunks: Sequence[KnowledgeChunk],
    embedding_model: str,
) -> str:
    """Return a stable hash for the data and embedding-model combination."""

    canonical = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "embedding_model": embedding_model,
        "chunks": [
            chunk.model_dump(mode="json")
            for chunk in sorted(chunks, key=lambda item: item.chunk_id)
        ],
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_for_chunk(
    chunk: KnowledgeChunk,
    embedding_model: str,
    fingerprint: str,
) -> dict[str, str]:
    return {
        "scenario": chunk.scenario,
        "stage": chunk.stage,
        "source_type": chunk.source_type,
        "title": chunk.title,
        "evidence_ids": ",".join(chunk.evidence_ids),
        "keywords": ",".join(chunk.keywords),
        "embedding_model": embedding_model,
        "data_fingerprint": fingerprint,
    }


def _get_collection(
    client: chromadb.ClientAPI,
    collection_name: str,
) -> Collection | None:
    try:
        return client.get_collection(
            name=collection_name,
            embedding_function=None,
        )
    except NotFoundError:
        return None


def persist_index(
    chunks: Sequence[KnowledgeChunk],
    embeddings: Sequence[Sequence[float]],
    chroma_path: Path,
    collection_name: str,
    embedding_model: str,
    rebuild: bool = False,
) -> Collection:
    """Persist validated vectors and reject incompatible existing indexes."""

    if not chunks:
        raise ValueError("Cannot build an index without chunks")
    if len(chunks) != len(embeddings):
        raise ValueError("Chunk and embedding counts do not match")

    dimensions = {len(vector) for vector in embeddings}
    if 0 in dimensions or len(dimensions) != 1:
        raise ValueError("Embeddings must have one consistent non-zero dimension")

    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=chroma_path)

    existing = _get_collection(client, collection_name)
    if existing is not None and rebuild:
        client.delete_collection(collection_name)
        existing = None

    fingerprint = index_fingerprint(chunks, embedding_model)
    if existing is not None:
        metadata: dict[str, Any] = existing.metadata or {}
        if (
            metadata.get("data_fingerprint") != fingerprint
            or metadata.get("embedding_model") != embedding_model
        ):
            raise ValueError(
                "Existing Chroma collection is incompatible with the current "
                "chunks or embedding model; rebuild the index explicitly"
            )
        collection = existing
    else:
        collection = client.create_collection(
            name=collection_name,
            embedding_function=None,
            metadata={
                "hnsw:space": "cosine",
                "schema_version": INDEX_SCHEMA_VERSION,
                "embedding_model": embedding_model,
                "data_fingerprint": fingerprint,
            },
        )

    documents = [chunk_to_document(chunk) for chunk in chunks]
    collection.upsert(
        ids=[chunk.chunk_id for chunk in chunks],
        embeddings=[list(vector) for vector in embeddings],
        documents=documents,
        metadatas=[
            _metadata_for_chunk(chunk, embedding_model, fingerprint)
            for chunk in chunks
        ],
    )
    return collection
