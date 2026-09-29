from __future__ import annotations

from app.evidence_cards import cited_evidence_records, render_evidence_cards
from app.retrieval import RetrievedChunk


def test_cited_evidence_records_keeps_only_requested_ids() -> None:
    records = cited_evidence_records("ryuk", ["RYUK-C01", "RYUK-C01"])

    assert [record.evidence_id for record in records] == ["RYUK-C01"]
    assert "Kali" in records[0].description


def test_evidence_cards_render_only_cited_catalog_records() -> None:
    cards = render_evidence_cards("industroyer2", ["IND2-N21"])

    assert "## 這些證據如何支持回答" in cards
    assert "<details>" in cards
    assert "IND2-N21" in cards
    assert "IND2-N20" not in cards
    assert "觀測內容" in cards
    assert "本題中的作用" in cards
    assert "需搭配／限制" in cards


def test_evidence_cards_support_shared_reference_catalog() -> None:
    cards = render_evidence_cards("frostygoop", ["OT-G02"])

    assert "OT-G02" in cards
    assert "教學安全界線" in cards


def test_evidence_cards_group_duplicate_clock_offset_messages() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-002-data-quality-05",
            document="Possible cross-host clock offset detected. Preserve original timestamps.",
            metadata={
                "source_type": "normalized_data_quality",
                "evidence_ids": "IND2-N12",
            },
            rrf_score=0.1,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-ve-003-data-quality-07",
            document="Possible cross-host clock offset detected. Preserve original timestamps.",
            metadata={
                "source_type": "normalized_data_quality",
                "evidence_ids": "IND2-N19",
            },
            rrf_score=0.09,
            dense_rank=2,
            sparse_rank=2,
        ),
        RetrievedChunk(
            chunk_id="IND2-ve-004-data-quality-07",
            document="Possible cross-host clock offset detected. Preserve original timestamps.",
            metadata={
                "source_type": "normalized_data_quality",
                "evidence_ids": "IND2-N26",
            },
            rrf_score=0.08,
            dense_rank=3,
            sparse_rank=3,
        ),
    ]

    cards = render_evidence_cards(
        "industroyer2",
        ["IND2-N12", "IND2-N19", "IND2-N26"],
        retrieved,
    )

    assert cards.count("<details>") == 1
    assert all(
        evidence_id in cards
        for evidence_id in ("IND2-N12", "IND2-N19", "IND2-N26")
    )
    assert "跨案例資料品質限制" in cards
    assert "證據涵蓋" in cards
    assert "Kali direct IEC-104 案例" in cards
    assert "Safe CaddyWiper-like lockout 案例" in cards
    assert "Kali via ENG Relay 成功案例" in cards
    assert "合併後能說明" in cards
    assert "不是單一 case 的偶發問題" in cards
    assert "NTP 同步狀態" in cards


def test_evidence_cards_keep_user01_event_sources_distinct() -> None:
    cards = render_evidence_cards("ryuk", ["RYUK-U02", "RYUK-U03"])

    assert cards.count("<details>") == 1
    assert "端點紀錄交叉驗證" in cards
    assert "PowerShell 4103／4104 摘要" in cards
    assert "Security 4688 摘要" in cards
    assert "腳本內容層證據" in cards
    assert "程序建立層證據" in cards


def test_evidence_cards_localize_frostygoop_detection_card() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="FROSTY-fc06-write-001-behavior-detection-04",
            document=(
                "Behavior detector classified FC6 as WRITE with Lab risk MEDIUM "
                "and result ALERT."
            ),
            metadata={
                "source_type": "normalized_detection",
                "evidence_ids": "FROSTY-N08",
            },
            rrf_score=0.1,
            dense_rank=1,
            sparse_rank=1,
        )
    ]

    cards = render_evidence_cards("frostygoop", ["FROSTY-N08"], retrieved)

    assert "偵測結果" in cards
    assert "實驗環境偵測器將 FC6 歸類為 WRITE" in cards
    assert "說明偵測器如何分類" in cards
    assert "不等同設備接受、實體影響或根因" in cards


def test_mixed_relay_timeline_card_does_not_hide_successful_attempt() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-ve-004-eng-relay-05",
            document=(
                "accepted=False iec104_sent=False "
                "outcome=eng_relay_connection_timeout; "
                "accepted=True iec104_sent=True "
                "outcome=eng_relay_successful_iec104_send"
            ),
            metadata={
                "source_type": "normalized_timeline",
                "evidence_ids": "IND2-N24",
            },
            rrf_score=0.1,
            dense_rank=1,
            sparse_rank=1,
        )
    ]

    cards = render_evidence_cards(
        "industroyer2",
        ["IND2-N24"],
        retrieved,
    )

    assert "同時包含失敗嘗試與成功事件" in cards
    assert "accepted=True、iec104_sent=True" in cards
    assert "區分同一時間線中的失敗與成功嘗試" in cards
    assert "尚未由 Relay 送出" not in cards


def test_same_analysis_type_is_combined_as_a_cross_case_comparison() -> None:
    retrieved = [
        RetrievedChunk(
            chunk_id="IND2-normal-eng-001-comparison-note-02",
            document=(
                "case_id=ind2_normal_eng_001. Normal ENG traffic establishes "
                "source_ip=10.10.60.20."
            ),
            metadata={
                "source_type": "normalized_analysis",
                "evidence_ids": "IND2-N02",
            },
            rrf_score=0.1,
            dense_rank=1,
            sparse_rank=1,
        ),
        RetrievedChunk(
            chunk_id="IND2-ve-004-comparison-note-02",
            document=(
                "case_id=ind2_vE_004. Via-ENG relay success can look similar "
                "because source_ip=10.10.60.20."
            ),
            metadata={
                "source_type": "normalized_analysis",
                "evidence_ids": "IND2-N21",
            },
            rrf_score=0.09,
            dense_rank=2,
            sparse_rank=2,
        ),
    ]

    cards = render_evidence_cards(
        "industroyer2",
        ["IND2-N02", "IND2-N21"],
        retrieved,
    )

    assert cards.count("<details>") == 1
    assert "跨案例證據比較" in cards
    assert "正常 ENG 維護對照組" in cards
    assert "Kali via ENG Relay 成功案例" in cards
    assert "來源 IP 不具唯一歸因能力" in cards
    assert "仍需搭配／限制" in cards


def test_lab_configuration_card_explains_design_vs_runtime_boundary() -> None:
    cards = render_evidence_cards("ryuk", ["RYUK-C01"])

    assert "建立主機角色、允許路徑與預期阻擋規則的設計基線" in cards
    assert "不等同執行期間真的觀測到該行為" in cards


def test_teaching_convention_card_is_not_presented_as_event_observation() -> None:
    cards = render_evidence_cards("frostygoop", ["OT-G02"])

    assert "界定什麼能由傳送紀錄推論" in cards
    assert "不是本次事件中某項行為已發生的觀測" in cards
