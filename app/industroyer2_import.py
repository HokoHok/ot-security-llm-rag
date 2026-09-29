"""Convert Industroyer2 normalized SENSOR01 output into RAG knowledge files.

The Normalizer remains the authority for raw-log parsing and correlation.  This
module only preserves its JSONL RAG output in the application's stable chunk
schema and assigns traceable, scenario-local evidence identifiers.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Sequence

from app.config import PROJECT_ROOT
from app.schemas import EvidenceCatalog, EvidenceRecord, KnowledgeChunk


SCENARIO = "industroyer2"
DEFAULT_NORMALIZED_ROOT = (
    PROJECT_ROOT / "scenarios" / SCENARIO / "input" / "normalized"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "scenarios" / SCENARIO

_CHUNK_DETAILS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "executive_summary": (
        "incident_overview",
        "normalized_summary",
        "事件摘要",
        ("事件摘要", "事件鏈", "安全模擬"),
    ),
    "comparison_note": (
        "analysis",
        "normalized_analysis",
        "鑑別與比較",
        ("比較", "鑑別", "來源 IP", "來源位址"),
    ),
    "server_commands": (
        "command_execution",
        "normalized_timeline",
        "OT-SERVER01 IEC-104 指令",
        ("OT-SERVER01", "IEC-104", "TCP/2404", "控制指令"),
    ),
    "eng_maintenance": (
        "normal_operations",
        "normalized_timeline",
        "ENG 正常維護證據",
        ("ENG01", "正常維護", "maintenance ticket"),
    ),
    "eng_relay": (
        "relay",
        "normalized_timeline",
        "ENG Relay 操作證據",
        ("ENG01", "ENG Relay", "工程站中繼", "Kali"),
    ),
    "caddywiper_status": (
        "recovery_impact",
        "normalized_state",
        "Safe CaddyWiper 狀態",
        ("Safe CaddyWiper", "ENG01", "復原能力", "lockout"),
    ),
    "data_quality": (
        "data_quality",
        "normalized_data_quality",
        "資料品質與限制",
        ("資料品質", "證據限制", "tshark", "PCAP", "unknowns"),
    ),
}

_CASE_TYPE_TEACHING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "normal_eng_maintenance": (
        "正常 ENG",
        "ENG01",
        "OT-SERVER01",
        "baseline",
        "正常維護",
    ),
    "direct_iec104_attack": (
        "Kali",
        "正常 ENG",
        "OT-SERVER01",
        "direct IEC-104",
        "區分",
    ),
    "direct_attack_plus_caddywiper_lockout": (
        "Kali",
        "ENG Relay",
        "OT-SERVER01",
        "CaddyWiper lockout",
        "iec104_sent",
    ),
    "via_eng_relay_success": (
        "Kali",
        "ENG Relay",
        "正常 ENG",
        "OT-SERVER01",
        "來源 IP 歸因",
    ),
}


@dataclass(frozen=True)
class Industroyer2ImportResult:
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


def _require_string_list(value: Any, field_name: str, source: Path) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{source}: {field_name} must be a list of non-empty strings")
    return [item.strip() for item in value]


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
        metadata = record.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError(f"{path} line {line_number}: metadata must be an object")
        records.append((line_number, record))

    if not records:
        raise ValueError(f"No RAG records found in {path}")
    return records


def _case_slug(case_id: str) -> str:
    prefix = "ind2_"
    if case_id.lower().startswith(prefix):
        case_id = case_id[len(prefix) :]
    return case_id.replace("_", "-").lower()


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _chunk_keywords(
    case_id: str,
    primary_case_type: str,
    tags: Sequence[str],
    chunk_type: str,
    chunk_text: str,
    teaching_keywords: Sequence[str],
) -> list[str]:
    keywords = [
        "Industroyer2",
        case_id,
        primary_case_type,
        chunk_type,
        *tags,
        *teaching_keywords,
    ]
    if chunk_type == "comparison_note":
        keywords.extend(
            _CASE_TYPE_TEACHING_KEYWORDS.get(primary_case_type, ())
        )
    technical_markers = (
        "Kali",
        "ENG01",
        "OT-SERVER01",
        "IEC-104",
        "TCP/2404",
        "CaddyWiper",
        "PCAP",
        "tshark",
        "maintenance_ticket",
        "source_ip",
    )
    keywords.extend(marker for marker in technical_markers if marker in chunk_text)
    return _unique(keywords)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def convert_industroyer2_normalized(
    normalized_root: Path = DEFAULT_NORMALIZED_ROOT,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> Industroyer2ImportResult:
    """Create stable RAG chunks and evidence catalog from normalized cases.

    The function is deterministic: the same normalized files produce the same
    ordered `IND2-Nxx` evidence IDs and chunk IDs.  It verifies that each case
    listed in `case_index.json` has all companion normalized JSON files before
    writing either generated output.
    """

    normalized_root = normalized_root.resolve()
    output_dir = output_dir.resolve()
    index_path = normalized_root / "case_index.json"
    index = _read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError(f"{index_path}: top-level JSON value must be an object")

    normalizer_version = _require_string(
        index.get("normalizer_version"), "normalizer_version", index_path
    )
    case_entries = index.get("cases")
    if not isinstance(case_entries, list) or not case_entries:
        raise ValueError(f"{index_path}: cases must be a non-empty list")

    chunks: list[KnowledgeChunk] = []
    evidence: list[EvidenceRecord] = []
    case_ids: set[str] = set()
    evidence_number = 1

    for entry in case_entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{index_path}: each cases entry must be an object")
        case_id = _require_string(entry.get("case_id"), "case_id", index_path)
        if case_id in case_ids:
            raise ValueError(f"{index_path}: duplicate case_id {case_id!r}")
        case_ids.add(case_id)
        primary_case_type = _require_string(
            entry.get("primary_case_type"), "primary_case_type", index_path
        )
        tags = _require_string_list(entry.get("tags"), "tags", index_path)
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
        summary_case_id = _require_string(summary.get("case_id"), "case_id", summary_path)
        if summary_case_id != case_id:
            raise ValueError(
                f"{summary_path}: case_id {summary_case_id!r} does not match "
                f"case index {case_id!r}"
            )
        classification = summary.get("classification")
        if not isinstance(classification, dict):
            raise ValueError(f"{summary_path}: classification must be an object")
        summary_case_type = _require_string(
            classification.get("primary_case_type"),
            "classification.primary_case_type",
            summary_path,
        )
        if summary_case_type != primary_case_type:
            raise ValueError(
                f"{summary_path}: primary case type {summary_case_type!r} does not "
                f"match case index {primary_case_type!r}"
            )

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
            evidence_id = f"IND2-N{evidence_number:02d}"
            chunk_id = (
                f"IND2-{_case_slug(case_id)}-{chunk_type.replace('_', '-')}-{ordinal:02d}"
            )
            title = f"{case_id}：{title_suffix}"
            text = (
                f"Industroyer2-like 安全模擬；case_id={case_id}；"
                f"classification={primary_case_type}；證據類型={title_suffix}。"
                f"{source_text}"
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
                        primary_case_type,
                        tags,
                        chunk_type,
                        source_text,
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
                        f"{case_id} 的正規化 {title_suffix}；"
                        f"分類為 {primary_case_type}。"
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

    return Industroyer2ImportResult(
        chunks_path=chunks_path,
        evidence_catalog_path=catalog_path,
        chunk_count=len(chunks),
        evidence_count=len(evidence),
        normalizer_version=normalizer_version,
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Run the conversion as a small explicit import command."""

    parser = argparse.ArgumentParser(
        description="Convert Industroyer2 normalized SENSOR01 output for RAG"
    )
    parser.add_argument(
        "--normalized-root",
        type=Path,
        default=DEFAULT_NORMALIZED_ROOT,
        help="Directory containing case_index.json and per-case normalized output",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Scenario directory where chunks.json and evidence_catalog.json are written",
    )
    args = parser.parse_args(argv)
    result = convert_industroyer2_normalized(
        normalized_root=args.normalized_root,
        output_dir=args.output_dir,
    )
    print(f"[OK] Normalizer version: {result.normalizer_version}")
    print(f"[OK] Converted chunks: {result.chunk_count}")
    print(f"[OK] Evidence records: {result.evidence_count}")
    print(f"[OK] Chunks: {result.chunks_path}")
    print(f"[OK] Evidence catalog: {result.evidence_catalog_path}")


if __name__ == "__main__":
    main()
