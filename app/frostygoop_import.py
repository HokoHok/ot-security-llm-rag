"""Convert FrostyGoop normalized output into stable RAG knowledge files.

The FrostyGoop Normalizer is the authority for parsing the safe Lab task JSON
and PCAP metadata.  This importer deliberately reads only its normalized JSON
and JSONL output, assigns stable evidence IDs, and keeps the source location
needed to trace every generated chunk back to that normalized record.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from app.config import PROJECT_ROOT
from app.schemas import EvidenceCatalog, EvidenceRecord, KnowledgeChunk


SCENARIO = "frostygoop"
DEFAULT_NORMALIZED_ROOT = (
    PROJECT_ROOT / "scenarios" / SCENARIO / "input" / "normalized"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scenarios" / SCENARIO

_CHUNK_DETAILS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "case_summary": (
        "incident_overview",
        "normalized_summary",
        "安全重建案例摘要",
        ("安全重建", "報告衍生", "Lab", "FrostyGoop"),
    ),
    "task_pcap_correlation": (
        "traffic_analysis",
        "normalized_correlation",
        "Task JSON 與 PCAP 對照",
        ("Task JSON", "PCAP", "Modbus", "tshark", "對照"),
    ),
    "pcap_follow_up": (
        "verification",
        "normalized_timeline",
        "PCAP 後續觀測",
        ("PCAP", "後續觀測", "驗證讀取", "不代表設備接受"),
    ),
    "behavior_detection": (
        "detection",
        "normalized_detection",
        "Modbus 行為偵測結果",
        ("Modbus", "偵測", "NOTICE", "ALERT", "register"),
    ),
    "data_quality": (
        "data_quality",
        "normalized_data_quality",
        "資料品質與安全限制",
        ("資料品質", "安全限制", "unknowns", "TCP/1502", "非生產環境"),
    ),
}

_TECHNICAL_MARKERS = (
    "FC03",
    "FC3",
    "FC06",
    "FC6",
    "FC16",
    "Modbus",
    "TCP/1502",
    "TCP/502",
    "127.0.0.1",
    "53760",
    "53882",
    "PCAP",
    "NOTICE",
    "ALERT",
    "WRITE",
    "READ",
)


@dataclass(frozen=True)
class FrostyGoopImportResult:
    """Files and counts written by one deterministic conversion run."""

    chunks_path: Path
    evidence_catalog_path: Path
    chunk_count: int
    evidence_count: int
    normalizer_version: str


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"Required normalized file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc


def _require_string(value: Any, field_name: str, source: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {field_name} must be a non-empty string")
    return value.strip()


def _read_jsonl(path: Path, expected_case_id: str) -> list[tuple[int, dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError as exc:
        raise ValueError(f"Required normalized RAG file does not exist: {path}") from exc

    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSONL in {path} at line {line_number}: {exc.msg}"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"{path} line {line_number}: record must be an object")
        case_id = _require_string(record.get("case_id"), "case_id", path)
        if case_id != expected_case_id:
            raise ValueError(
                f"{path} line {line_number}: case_id {case_id!r} does not match "
                f"directory {expected_case_id!r}"
            )
        _require_string(record.get("chunk_type"), "chunk_type", path)
        _require_string(record.get("text"), "text", path)
        if not isinstance(record.get("metadata", {}), dict):
            raise ValueError(f"{path} line {line_number}: metadata must be an object")
        records.append((line_number, record))

    if not records:
        raise ValueError(f"No RAG records found in {path}")
    return records


def _case_slug(case_id: str) -> str:
    prefix = "frostygoop_"
    if case_id.lower().startswith(prefix):
        case_id = case_id[len(prefix) :]
    return case_id.replace("_", "-").lower()


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _chunk_keywords(
    case_id: str,
    classification: str,
    safety_role: str,
    chunk_type: str,
    text: str,
    teaching_keywords: Sequence[str],
) -> list[str]:
    keywords = [
        "FrostyGoop",
        case_id,
        classification,
        safety_role,
        chunk_type,
        *teaching_keywords,
    ]
    keywords.extend(marker for marker in _TECHNICAL_MARKERS if marker in text)
    return _unique(keywords)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def convert_frostygoop_normalized(
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> FrostyGoopImportResult:
    """Create traceable FrostyGoop chunks and an evidence catalog.

    The output order is deterministic: `case_index.json` order followed by
    each case's `rag_chunks.jsonl` line order.  All companion normalized files
    must exist and agree on their case ID before either output is written.
    """

    normalized_root = normalized_root.resolve()
    output_dir = output_dir.resolve()
    index_path = normalized_root / "case_index.json"
    index = _read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path}: top-level JSON value must be an object")
    scenario = _require_string(index.get("scenario"), "scenario", index_path)
    if scenario != SCENARIO:
        raise ValueError(f"{index_path}: scenario must be {SCENARIO!r}, got {scenario!r}")
    normalizer_version = _require_string(
        index.get("normalizer_version"), "normalizer_version", index_path
    )
    case_entries = index.get("cases")
    if not isinstance(case_entries, list) or not case_entries:
        raise ValueError(f"{index_path}: cases must be a non-empty list")

    chunks: list[KnowledgeChunk] = []
    evidence: list[EvidenceRecord] = []
    seen_case_ids: set[str] = set()
    evidence_number = 1

    for entry in case_entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{index_path}: each cases entry must be an object")
        case_id = _require_string(entry.get("case_id"), "case_id", index_path)
        if case_id in seen_case_ids:
            raise ValueError(f"{index_path}: duplicate case_id {case_id!r}")
        seen_case_ids.add(case_id)
        classification = _require_string(
            entry.get("classification"), "classification", index_path
        )
        safety_role = _require_string(entry.get("safety_role"), "safety_role", index_path)
        case_dir = normalized_root / case_id
        summary_path = case_dir / "incident_summary.json"
        timeline_path = case_dir / "timeline.json"
        quality_path = case_dir / "data_quality.json"
        rag_path = case_dir / "rag_chunks.jsonl"
        summary = _read_json(summary_path)
        _read_json(timeline_path)
        _read_json(quality_path)
        if not isinstance(summary, dict):
            raise ValueError(f"{summary_path}: top-level JSON value must be an object")
        if _require_string(summary.get("case_id"), "case_id", summary_path) != case_id:
            raise ValueError(f"{summary_path}: case_id does not match case index {case_id!r}")
        if _require_string(summary.get("classification"), "classification", summary_path) != classification:
            raise ValueError(f"{summary_path}: classification does not match case index")

        for ordinal, (line_number, record) in enumerate(
            _read_jsonl(rag_path, case_id), start=1
        ):
            chunk_type = _require_string(record["chunk_type"], "chunk_type", rag_path)
            try:
                stage, source_type, title_suffix, teaching_keywords = _CHUNK_DETAILS[
                    chunk_type
                ]
            except KeyError as exc:
                raise ValueError(
                    f"{rag_path} line {line_number}: unsupported chunk_type "
                    f"{chunk_type!r}; update the importer mapping first"
                ) from exc
            source_text = _require_string(record["text"], "text", rag_path)
            evidence_id = f"FROSTY-N{evidence_number:02d}"
            chunk_id = f"FROSTY-{_case_slug(case_id)}-{chunk_type.replace('_', '-')}-{ordinal:02d}"
            title = f"{case_id}：{title_suffix}"
            text = (
                f"FrostyGoop 報告衍生安全重建；case_id={case_id}；"
                f"classification={classification}；safety_role={safety_role}；"
                f"證據類型={title_suffix}。{source_text}"
            )
            chunks.append(
                KnowledgeChunk(
                    chunk_id=chunk_id,
                    scenario=SCENARIO,
                    stage=stage,
                    source_type=source_type,
                    title=title,
                    text=text,
                    evidence_ids=[evidence_id],
                    keywords=_chunk_keywords(
                        case_id,
                        classification,
                        safety_role,
                        chunk_type,
                        text,
                        teaching_keywords,
                    ),
                )
            )
            evidence.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    source_path=(
                        f"scenarios/{SCENARIO}/input/normalized/"
                        f"{case_id}/rag_chunks.jsonl"
                    ),
                    source_type=source_type,
                    locator=(
                        f"line {line_number}; chunk_type={chunk_type}; "
                        f"normalizer={normalizer_version}"
                    ),
                    description=(
                        f"{case_id} 的 {title_suffix}；"
                        f"分類為 {classification}。"
                    ),
                )
            )
            evidence_number += 1

    if not chunks:
        raise ValueError(f"No converted chunks were produced from {normalized_root}")
    catalog = EvidenceCatalog(
        catalog_version="1.0",
        scenario=SCENARIO,
        evidence=evidence,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = output_dir / "chunks.json"
    catalog_path = output_dir / "evidence_catalog.json"
    _write_json(chunks_path, [chunk.model_dump() for chunk in chunks])
    _write_json(catalog_path, catalog.model_dump())
    return FrostyGoopImportResult(
        chunks_path=chunks_path,
        evidence_catalog_path=catalog_path,
        chunk_count=len(chunks),
        evidence_count=len(evidence),
        normalizer_version=normalizer_version,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the conversion as an explicit local import command."""

    parser = argparse.ArgumentParser(
        description="Convert FrostyGoop normalized output for the local RAG"
    )
    parser.add_argument("--normalized-root", type=Path, default=DEFAULT_NORMALIZED_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    result = convert_frostygoop_normalized(args.normalized_root, args.output_dir)
    print(f"[OK] Normalizer version: {result.normalizer_version}")
    print(f"[OK] Converted chunks: {result.chunk_count}")
    print(f"[OK] Evidence records: {result.evidence_count}")
    print(f"[OK] Chunks: {result.chunks_path}")
    print(f"[OK] Evidence catalog: {result.evidence_catalog_path}")


if __name__ == "__main__":
    main()
