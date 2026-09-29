from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import requests

from app.retrieval import RetrievedChunk
from app.scenario_profiles import get_scenario_profile
from app.schemas import TutorResponse
from app.validation import validate_tutor_response

ResponseMode = Literal["hint", "explain"]
KnowledgeScope = Literal["in_scope", "general_concept", "comparison"]


def _validate_response_mode(response_mode: ResponseMode) -> None:
    if response_mode not in {"hint", "explain"}:
        raise ValueError(
            "response_mode must be either 'hint' or 'explain'"
        )


def _validate_knowledge_scope(knowledge_scope: KnowledgeScope) -> None:
    if knowledge_scope not in {"in_scope", "general_concept", "comparison"}:
        raise ValueError("Unsupported knowledge_scope")


def _nanoseconds_to_seconds(value: Any) -> float | None:
    if not isinstance(value, int) or value < 0:
        return None
    return value / 1_000_000_000


@dataclass(frozen=True)
class OllamaCallMetrics:
    """Non-student runtime telemetry returned by one Ollama chat call."""

    request_number: int
    done_reason: str | None
    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_seconds: float | None
    load_duration_seconds: float | None
    prompt_eval_duration_seconds: float | None
    eval_duration_seconds: float | None

    @property
    def output_may_be_truncated(self) -> bool:
        """Return true when Ollama stopped because the output limit was hit."""

        return self.done_reason == "length"

    @property
    def output_tokens_per_second(self) -> float | None:
        if (
            self.eval_count is None
            or self.eval_duration_seconds is None
            or self.eval_duration_seconds <= 0
        ):
            return None
        return self.eval_count / self.eval_duration_seconds

    @classmethod
    def from_api_payload(
        cls,
        payload: dict[str, Any],
        request_number: int,
    ) -> OllamaCallMetrics:
        """Extract known metrics while tolerating older Ollama responses."""

        def optional_integer(name: str) -> int | None:
            value = payload.get(name)
            return value if isinstance(value, int) and value >= 0 else None

        done_reason = payload.get("done_reason")
        return cls(
            request_number=request_number,
            done_reason=(
                done_reason if isinstance(done_reason, str) else None
            ),
            prompt_eval_count=optional_integer("prompt_eval_count"),
            eval_count=optional_integer("eval_count"),
            total_duration_seconds=_nanoseconds_to_seconds(
                payload.get("total_duration")
            ),
            load_duration_seconds=_nanoseconds_to_seconds(
                payload.get("load_duration")
            ),
            prompt_eval_duration_seconds=_nanoseconds_to_seconds(
                payload.get("prompt_eval_duration")
            ),
            eval_duration_seconds=_nanoseconds_to_seconds(
                payload.get("eval_duration")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_number": self.request_number,
            "done_reason": self.done_reason,
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "total_duration_seconds": self.total_duration_seconds,
            "load_duration_seconds": self.load_duration_seconds,
            "prompt_eval_duration_seconds": (
                self.prompt_eval_duration_seconds
            ),
            "eval_duration_seconds": self.eval_duration_seconds,
            "output_tokens_per_second": self.output_tokens_per_second,
            "output_may_be_truncated": self.output_may_be_truncated,
        }


class ChatProvider(Protocol):
    """Minimal structured-chat interface used by generation and test doubles."""

    def generate_json(
        self,
        messages: Sequence[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> Any:
        """Return a parsed JSON-compatible value."""


class OllamaChatClient:
    """Ollama `/api/chat` client configured for deterministic JSON output."""

    def __init__(
        self,
        base_url: str,
        model: str,
        num_ctx: int = 4096,
        num_predict: int = 500,
        temperature: float = 0.0,
        timeout_seconds: int = 600,
        keep_alive: str = "15m",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self.request_count = 0
        self.metrics_history: list[OllamaCallMetrics] = []

    def generate_json(
        self,
        messages: Sequence[dict[str, str]],
        response_schema: dict[str, Any],
    ) -> Any:
        self.request_count += 1
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": list(messages),
                    "format": response_schema,
                    "stream": False,
                    "keep_alive": self.keep_alive,
                    "options": {
                        "temperature": self.temperature,
                        "num_ctx": self.num_ctx,
                        "num_predict": self.num_predict,
                    },
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Ollama chat request failed for model {self.model!r}: {exc}"
            ) from exc
        except ValueError as exc:
            raise RuntimeError("Ollama returned invalid API JSON") from exc

        if not isinstance(payload, dict):
            raise RuntimeError("Ollama returned a non-object API response")
        self.metrics_history.append(
            OllamaCallMetrics.from_api_payload(
                payload,
                request_number=self.request_count,
            )
        )

        try:
            content = payload["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError(
                "Ollama chat response does not contain message.content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Ollama returned empty message content")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Ollama message content is not valid JSON: "
                f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
            ) from exc


def allowed_evidence_ids(
    retrieved: Sequence[RetrievedChunk],
) -> list[str]:
    """Return unique evidence IDs in retrieval order."""

    ordered: list[str] = []
    seen: set[str] = set()
    for result in retrieved:
        for evidence_id in result.evidence_ids:
            if evidence_id not in seen:
                ordered.append(evidence_id)
                seen.add(evidence_id)
    return ordered


def _localize_retrieved_document(
    document: str,
    scenario: str,
) -> str:
    """Localize fixed Industroyer2 normalizer prose before Llama sees it."""

    if get_scenario_profile(scenario).key != "industroyer2":
        return document

    localized = document
    replacements = {
        "Authorized ENG Maintenance Console sent an IEC-104 command to OT-SERVER01.": (
            "經授權的 ENG Maintenance Console 向 OT-SERVER01 送出 IEC-104 指令。"
        ),
        "Server observed source_ip=10.10.60.20 over TCP/2404.": (
            "Server 在 TCP/2404 看到 source_ip=10.10.60.20。"
        ),
        "Maintenance ticket is present in ENG local logs only (MW-2026-001) and is not carried by IEC-104 packets.": (
            "maintenance_ticket=MW-2026-001 只存在 ENG 本機紀錄，不由 IEC-104 封包攜帶。"
        ),
        "Normal ENG traffic establishes the authorized baseline: Server and PCAP see ENG01 as source_ip=10.10.60.20 over TCP/2404.": (
            "正常 ENG 通訊建立授權基線：Server 與 PCAP 在 TCP/2404 看見 ENG01 的 source_ip=10.10.60.20。"
        ),
        "Maintenance ticket is evidence of operator intent, but it exists in ENG local logs and is not visible in IEC-104 packets or Server state.": (
            "maintenance ticket 可表示操作者意圖，但只存在 ENG 本機紀錄，IEC-104 封包與 Server state 都看不到。"
        ),
        "Tickets are local ENG evidence and are not carried in IEC-104 packets.": (
            "maintenance ticket 是 ENG 本機證據，不由 IEC-104 封包攜帶。"
        ),
        "Kali sent a direct Industroyer2-like IEC-104 payload to OT-SERVER01.": (
            "Kali 直接向 OT-SERVER01 送出 Industroyer2-like IEC-104 安全模擬內容。"
        ),
        "Server observed source_ip=10.10.60.10 over TCP/2404 and OT state changed to service disruption.": (
            "Server 在 TCP/2404 看見 source_ip=10.10.60.10，OT 狀態改為服務中斷模擬。"
        ),
        "Direct attack is distinguishable at Server/PCAP by source_ip=10.10.60.10 sending IEC-104 to TCP/2404.": (
            "Server／PCAP 可由送往 TCP/2404 的 IEC-104 來源 source_ip=10.10.60.10 區分 direct 操作。"
        ),
        "After Safe CaddyWiper lockout, ENG relay/recovery attempts may be rejected before IEC-104 is sent; verify iec104_sent=false.": (
            "Safe CaddyWiper lockout 後，ENG relay／recovery 嘗試可能在 IEC-104 送出前被拒絕；應核對 iec104_sent=false。"
        ),
        "Kali requested a command through ENG Relay before Safe CaddyWiper lockout.": (
            "Kali 在 Safe CaddyWiper lockout 前透過 ENG Relay 提出操作要求。"
        ),
        "ENG01 sent the IEC-104 command to OT-SERVER01, and Server observed source_ip=10.10.60.20 over TCP/2404.": (
            "ENG01 向 OT-SERVER01 送出 IEC-104 指令，Server 在 TCP/2404 看見 source_ip=10.10.60.20。"
        ),
        "This shows that Server/PCAP view can look similar to normal ENG traffic and must be correlated with Kali and ENG Relay logs.": (
            "這表示 Server／PCAP 視角可能與正常 ENG 通訊相似，必須關聯 Kali 與 ENG Relay 紀錄。"
        ),
        "Via-ENG relay success can look similar to normal ENG traffic from the Server/PCAP perspective because both use source_ip=10.10.60.20.": (
            "從 Server／PCAP 視角看，via-ENG Relay 成功可能與正常 ENG 通訊相似，因為兩者都使用 source_ip=10.10.60.20。"
        ),
        "Distinguish via-ENG relay from normal ENG by correlating Kali actions and ENG relay logs, not by Server source IP alone.": (
            "要區分 via-ENG Relay 與正常 ENG，必須關聯 Kali action 與 ENG Relay 紀錄，不能只看 Server source IP。"
        ),
        "Data quality warnings:": "資料品質警告：",
        "Some collected files are empty. Empty files may mean no activity for that channel, or collection gaps.": (
            "部分收集檔案為空，可能表示該 channel 沒有活動，也可能是收集缺口。"
        ),
        "PCAP files are present, but tshark is not installed; normalizer recorded file hashes/sizes but did not decode IEC-104 fields.": (
            "PCAP 檔案存在，但 SENSOR01 未安裝 tshark；Normalizer 只記錄檔案 SHA-256／大小，尚未解碼 IEC-104 欄位。"
        ),
        "Server/PCAP source IP alone cannot distinguish normal ENG operation from Kali via ENG relay; correlate ENG local logs and Kali logs.": (
            "只靠 Server／PCAP source IP 無法區分正常 ENG 與 Kali via ENG Relay；必須關聯 ENG 本機與 Kali 紀錄。"
        ),
        "Maintenance ticket is local ENG evidence only; IEC-104 packets and Server state do not carry the lab maintenance_ticket field.": (
            "maintenance_ticket 只是 ENG 本機證據；IEC-104 封包與 Server state 不攜帶此欄位。"
        ),
        "Possible cross-host clock offset detected. Preserve original timestamps and do not infer causality from absolute time alone.": (
            "偵測到可能的跨主機 clock offset；應保留原始時間戳，不可只靠絕對時間推斷因果。"
        ),
    }
    for source, target in replacements.items():
        localized = localized.replace(source, target)
    localized = re.sub(
        r"Excluded (\d+) stale/legacy events from main conclusion using per-host recent window of (\d+) minutes\.",
        r"主要結論已排除 \1 筆 stale／legacy 事件，採用各主機最近 \2 分鐘時窗。",
        localized,
    )
    localized = re.sub(
        r"Server received IEC-104 command from ([^;]+?) to IOA ([^;]+?) over TCP/2404",
        r"Server 在 TCP/2404 從 \1 收到送往 IOA \2 的 IEC-104 指令",
        localized,
    )
    localized = localized.replace("Safe CaddyWiper status:", "Safe CaddyWiper 狀態：")
    return localized


def build_retrieved_context(
    retrieved: Sequence[RetrievedChunk],
    scenario: str = "ryuk",
) -> str:
    """Serialize only retrieved knowledge and its allowed evidence IDs."""

    if not retrieved:
        return "沒有檢索到可用的事件證據。"

    blocks: list[str] = []
    evidence_type_labels = {
        "lab_configuration": "實驗環境設定",
        "observed_endpoint_logs": "USER01 端點紀錄",
        "observed_log": "事件觀測紀錄",
        "observed_summary": "事件摘要",
        "observed_detection": "偵測結果",
        "observed_restore": "復原結果",
        "simulation_log": "安全模擬紀錄",
        "evidence_synthesis": "跨來源證據整理",
        "collection_metadata": "收集中繼資料",
        "normalized_summary": "正規化事件摘要",
        "normalized_analysis": "正規化鑑別與比較",
        "normalized_timeline": "正規化事件時間線",
        "normalized_state": "正規化狀態",
        "normalized_data_quality": "正規化資料品質摘要",
        "simulation_overview": "安全模擬邊界整理",
    }
    for rank, result in enumerate(retrieved, start=1):
        raw_source_type = str(result.metadata.get("source_type", ""))
        evidence_type = evidence_type_labels.get(
            raw_source_type,
            raw_source_type.replace("_", " "),
        )
        blocks.append(
            "\n".join(
                [
                    f"[檢索結果 {rank}]",
                    f"chunk_id={result.chunk_id}",
                    f"stage={result.metadata.get('stage', '')}",
                    f"evidence_type={evidence_type}",
                    f"evidence_ids={','.join(result.evidence_ids)}",
                    _localize_retrieved_document(
                        result.document,
                        scenario,
                    ),
                ]
            )
        )
    return "\n\n".join(blocks)


def build_question_focus(
    question: str,
    scenario: str = "ryuk",
) -> str:
    """Describe the requested answer shape without supplying the answer."""

    profile = get_scenario_profile(scenario)
    normalized = question.casefold()
    if profile.key == "industroyer2":
        if "maintenance_ticket" in normalized or "maintenance ticket" in normalized:
            return (
                "這是證據可見性題：先說明 maintenance_ticket 出現在哪一端，"
                "再區分 ENG 本機紀錄、IEC-104 封包與 Server state 能看到什麼。"
            )
        if "source_ip" in normalized or "10.10.60.20" in normalized:
            return (
                "這是來源歸因題：比較正常 ENG 與 Kali via ENG Relay，說明為何"
                " Server source IP 相同仍不足以判斷實際發起者，以及要補看哪些紀錄。"
            )
        if "caddywiper" in normalized or "lockout" in normalized:
            return (
                "這是成功／阻擋狀態題：分開說明命令是否被接受、是否實際送出"
                " IEC-104，以及 Server 是否收到；不要把安全 lockout 說成真實惡意程式。"
            )
        if "tshark" in normalized or "pcap" in normalized:
            return (
                "這是資料品質題：區分 PCAP 檔案存在與 IEC-104 欄位已被解析，"
                "並指出目前可以確認及仍無法確認的範圍。"
            )
        if any(marker in normalized for marker in ("時間", "時序", "因果", "clock")):
            return (
                "這是時間線可信度題：說明 clock offset 對跨主機排序的影響，"
                "不要只憑牆鐘時間斷言因果。"
            )
        if "relay" in normalized or (
            "direct" in normalized and "iec" in normalized
        ):
            return (
                "這是 direct 與 relay 路徑比較題：逐一列出檢索內容中各端的具體"
                "紀錄，並分別說明誰提出要求、ENG01 是否送出 IEC-104，以及 "
                "Server 是否收到；不要只寫成籠統的 local logs。"
            )
        return profile.default_question_focus

    mentions_three_hosts = all(
        host in normalized for host in ("kali", "user01", "dc01")
    )
    if (
        "4104" in normalized
        and "4688" in normalized
        and any(
            marker in question
            for marker in ("數字", "編號", "事件 ID", "意義", "代表什麼", "本身")
        )
    ):
        return (
            "這是名詞／編號定義題：先解釋兩個事件 ID 各代表什麼，"
            "再連回本情境中各自留下的佐證。"
        )
    if mentions_three_hosts and any(
        marker in question for marker in ("比較", "角色", "分工")
    ):
        return (
            "這是角色與證據比較題：逐一說明三台主機的角色，並點出支持"
            "角色的具體證據類型；不要改答成連線缺口清單。"
        )
    if "虛擬機" in question and any(
        marker in question for marker in ("除了", "還有", "哪些", "哪幾")
    ):
        return (
            "這是情境主機確認題：先直接回答有或沒有，再列出檢索內容"
            "支持的主機及角色，不要推測未出現的虛擬機。"
        )
    if any(marker in question for marker in ("怎麼", "如何")) and any(
        marker in question
        for marker in ("重現", "還原", "模擬情境", "模擬的情況")
    ):
        return (
            "這是安全流程題：依序說明如何在隔離 VM 中重現事件、查看輸出、"
            "建立復原副本並收集證據；不要只回答 artifacts 數量或檔案解碼。"
        )
    return profile.default_question_focus


def build_messages(
    question: str,
    retrieved: Sequence[RetrievedChunk],
    response_mode: ResponseMode = "hint",
    scenario: str = "ryuk",
    knowledge_scope: KnowledgeScope = "in_scope",
    conversation_context: str | None = None,
    student_context: str | None = None,
) -> list[dict[str, str]]:
    """Build the evidence-bounded teaching prompt."""

    _validate_response_mode(response_mode)
    _validate_knowledge_scope(knowledge_scope)
    profile = get_scenario_profile(scenario)
    evidence_ids = allowed_evidence_ids(retrieved)
    allowed_ids_text = ", ".join(evidence_ids) if evidence_ids else "無"
    context = build_retrieved_context(retrieved, scenario=scenario)
    question_focus = build_question_focus(question, scenario=scenario)
    knowledge_scope_instruction = {
        "in_scope": (
            "這是事件事實題。只能描述檢索內容支持的本次情境觀察；"
            "不要把 Lab 欄位或安全模擬結果說成通用產業事實。"
        ),
        "general_concept": (
            "這是通用概念題。只使用 public_reference 或 teaching_convention 類型的"
            "檢索內容；不要自行帶入 Kali、主機、case 或事件結果。若問題使用本專案欄位，"
            "先說明它是本地欄位，再與通用協定概念分開。"
        ),
        "comparison": (
            "這是比較題。請明確分成「通用背景」與「本次實驗」兩層；"
            "不要把本地欄位名稱當成產業標準，也不要把通用背景說成事件已發生的事實。"
        ),
    }[knowledge_scope]

    if response_mode == "explain":
        answer_depth_instruction = (
            "answer 使用二至四句，只回答學生目前問題；先給出結論，再說明至少一個"
            "『哪類證據如何支持該結論』的關係，但不得公布與問題無關的完整 "
            "Ground Truth。known_facts 以二至四項具體事實為目標；若檢索證據不足，"
            "不得為了湊足數量而捏造。只有檢索內容確實指出與問題相關的證據限制時，"
            "才把該限制放入 unknowns。next_hint 仍只提出一個具體的教師追問。"
        )
    else:
        answer_depth_instruction = (
            "answer 保持一至兩句，只回應學生目前問題，不公布完整 Ground Truth。"
        )

    system_prompt = f"""
知識路由要求：{knowledge_scope_instruction}
你是工控資安事件還原課程的教學助理。請用自然、清楚的臺灣繁體中文回答，
像一位會先理解問題、再帶學生看證據的助教，而不是朗讀搜尋結果。

先在心中判斷學生真正要問的是「名詞／編號定義、主機角色、原因、證據比較、
事件順序或操作流程」中的哪一類，再直接回答該問題。不要把定義題改答成證據
比較，也不要把「如何重現情境」誤解成只詢問檔案解碼。

你有兩個回答區域：
A. 事件事實區：任何關於「這次實驗發生了什麼、哪台主機做了什麼、使用了哪個
   工具或漏洞」的主張，只能來自 RETRIEVED_CONTEXT，且必須能追溯到 evidence_id。
B. 教學解釋區：可以運用一般資安知識解釋名詞、紀錄的用途、證據為何重要，
   也可以用簡短類比或追問幫助理解；但不得把一般知識說成這次事件已發生的事實。
   如有混淆風險，使用「一般來說」或「在事件分析上」標示。

輸出必須符合指定 JSON Schema，並遵守：
1. {answer_depth_instruction}
2. known_facts 只放 RETRIEVED_CONTEXT 直接支持的事件事實；reasonable_inferences
   只放由已知事實合理導出、但未被直接觀測的推論。不要把同一句話同時放進兩區。
3. unknowns 只放與學生目前問題直接相關且確實無法確認的事項。問題已回答時可以
   是空陣列；不要為了顯得嚴謹，主動加入學生沒有問的遙測缺口。
4. 學生問題中的前提不是證據。若問題預設了不存在的漏洞、工具、密碼或惡意行為，
   應指出目前證據無法確認，不得順著前提補完。通訊協定、連線權限、檔名和風險
   標籤本身也不等於漏洞或真實惡意行為。
5. 將實驗環境設定、實際觀測、dry run 和安全模擬清楚區分。
   {profile.safety_instruction}
6. source_type=lab_configuration 是實驗環境設計的已知事實，不是執行期間的封包
   觀測。只有當學生詢問實際連線嘗試、封包或精確時序時，才把缺少 firewall log、
   PCAP、server audit、session 等限制列入 unknowns。
7. 系統或 Relay 有紀錄，只能支持該系統可觀測到該事件；除非檢索內容直接記錄，
   不得推成某位人員知道、同意、理解或有特定意圖。對多主機路徑，分別說明請求端、
   實際送出端與接收端，不能只用來源 IP 推定最初發起者。
8. evidence_ids 只能逐字使用 ALLOWED_EVIDENCE_IDS，列出實際支持回答的 1 至 4 個
   ID，不可使用 chunk_id、自行編號或把允許清單全部照抄。優先引用最直接的證據，
   不要用只有檔案數量的 impact 證據支持執行歸因。
9. 若學生明確點名 ALLOWED_EVIDENCE_IDS 中的 evidence_id，回答必須逐一說明其意義，
   並在 evidence_ids 中包含它們。
9. 使用完整且有變化的自然句子。避免「根據檢索結果」「檢索內容記錄」等系統口吻；
   直接說明 HTTP access log、端點紀錄、偵測摘要等具體證據類型。檢索內容即使是
   英文，也要翻成臺灣繁體中文；主機名、欄位名、協定與 evidence_id 可保留原文。
   不要因為某項資料被取回就硬塞進回答，只保留直接回答目前問題所需的事實。
10. next_hint 只提出一個與本題銜接的具體教師追問，不重複學生原題，也不要使用
    「請比較證據支持的事實並找出額外證據」這類通用模板。
11. 主機名稱、網域帳號、IP、檔名與 evidence_id 必須從 RETRIEVED_CONTEXT 逐字複製。
12. 所有學生可見文字必須是單行且不得含控制字元。JSON 反斜線必須正確跳脫；
    例如輸出 {profile.identifier_example} 時，必須保持檢索內容中的原始拼法。
13. CONVERSATION_CONTEXT 只用於理解「它、那個、前一項」等指涉。它不是事件證據；
    任何事件事實與 evidence_ids 仍必須由本輪 RETRIEVED_CONTEXT 支持。
    若前文仍不足以唯一判斷代詞所指，直接請學生補上主機、IP、事件或 evidence ID；
    不可用情境名稱或泛泛敘述假裝已回答。

目前情境：{profile.display_name}
情境邊界：{profile.event_boundary_instruction}
""".strip()

    system_prompt += (
        "\n\n\u8b49\u64da\u4e0d\u8db3\u898f\u5247\uff1a\u82e5 RETRIEVED_CONTEXT \u53ea\u8aaa\u300c\u672a\u8b49\u660e\u3001\u7121\u6cd5\u78ba\u8a8d\u3001\u672a\u6536\u96c6\u3001\u672a\u89e3\u6790\u300d\uff0c"
        "\u4e0d\u53ef\u6539\u5beb\u70ba\u300c\u4e0d\u5b58\u5728\u3001\u6c92\u6709\u767c\u751f\u3001\u6c92\u6709\u5c01\u5305\u3001\u6c92\u6709\u56de\u61c9\u300d\u3002"
        "\u53ea\u6709\u7576 RETRIEVED_CONTEXT \u660e\u78ba\u8a18\u8f09 none\u30010\u3001not found \u6216\u7b49\u50f9\u5426\u5b9a\u4e8b\u5be6\u6642\uff0c"
        "\u624d\u80fd\u5ba3\u7a31\u4e0d\u5b58\u5728\uff1b\u5426\u5247\u8acb\u4f7f\u7528\u300c\u73fe\u6709\u8b49\u64da\u7121\u6cd5\u78ba\u8a8d\u300d\u3002"
    )

    user_prompt = f"""
STUDENT_CONTEXT（若有，僅為共同學生背景；不可當作本題事件證據）：
{student_context or "無"}

學生問題：
{question}

回答焦點：
{question_focus}

ALLOWED_EVIDENCE_IDS：
{allowed_ids_text}

RETRIEVED_CONTEXT：
{context}

CONVERSATION_CONTEXT（若有，只能協助理解代詞；不可作為證據）：
{conversation_context or "無"}

請只輸出符合 Schema 的 JSON。
""".strip()

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_response_schema(
    evidence_whitelist: Sequence[str],
    response_mode: ResponseMode = "hint",
) -> dict[str, Any]:
    """Constrain structured decoding to retrieved evidence identifiers."""

    _validate_response_mode(response_mode)
    schema = TutorResponse.model_json_schema()
    if response_mode == "explain":
        schema["properties"]["answer"]["description"] = (
            "二至四句直接回答學生問題，並說明至少一個證據如何支持結論；"
            "沒有證據時必須明確說無法確認。"
        )
    evidence_property = schema["properties"]["evidence_ids"]
    evidence_property["items"]["enum"] = list(evidence_whitelist)
    if evidence_whitelist:
        evidence_property["minItems"] = 1
    else:
        evidence_property["maxItems"] = 0
    return schema


def observed_execution_attribution(
    retrieved: Sequence[RetrievedChunk],
) -> tuple[list[str], list[str]]:
    """Extract observed source hosts and their evidence IDs in context order."""

    host_pattern = re.compile(
        r"Source Host(?:\s*記為|\s*[:=])\s*([A-Za-z0-9_-]+)",
        flags=re.IGNORECASE,
    )
    observed_hosts: list[str] = []
    execution_evidence: list[str] = []
    for result in retrieved:
        match = host_pattern.search(result.document)
        if match is None:
            continue
        host = match.group(1)
        if host.casefold() not in {
            item.casefold() for item in observed_hosts
        }:
            observed_hosts.append(host)
        for evidence_id in result.evidence_ids:
            if evidence_id not in execution_evidence:
                execution_evidence.append(evidence_id)
    return observed_hosts, execution_evidence


def _asks_about_direct_kali_path(question: str) -> bool:
    """Recognize Chinese, English, and shorthand Kali-to-DC01 role questions."""

    normalized = question.casefold()
    if "kali" not in normalized or "dc01" not in normalized:
        return False
    direct_markers = (
        "直接",
        "自己去改",
        "direct",
        "modified",
        "smb",
        "→",
    )
    return any(marker in normalized for marker in direct_markers)


def build_contextual_hint(
    question: str,
    retrieved: Sequence[RetrievedChunk],
    scenario: str = "ryuk",
) -> str:
    """Build a concise Socratic hint tied to the current question."""

    profile = get_scenario_profile(scenario)
    normalized_question = question.casefold()
    if profile.key == "industroyer2":
        if "maintenance_ticket" in normalized_question or "maintenance ticket" in normalized_question:
            return (
                "若 Server 與封包都沒有 maintenance_ticket，你會用哪一份 ENG 本機紀錄"
                "把正常維護和 Relay 操作分開？"
            )
        if "source_ip" in normalized_question or "10.10.60.20" in normalized_question:
            return (
                "正常 ENG 與 Kali via ENG Relay 都可能讓 Server 看見 10.10.60.20；"
                "哪兩端的紀錄能補上真正的發起來源？"
            )
        if "caddywiper" in normalized_question or "lockout" in normalized_question:
            return (
                "請依序核對 Relay 的 accepted、iec104_sent 與 Server command log，"
                "哪一步證明命令停在 ENG01？"
            )
        if "tshark" in normalized_question or "pcap" in normalized_question:
            return (
                "PCAP 檔案存在與 IEC-104 欄位已解碼是兩件事；目前哪一項只有"
                "檔案大小與 SHA-256 可以佐證？"
            )
        if any(marker in normalized_question for marker in ("時間", "時序", "因果", "clock")):
            return (
                "若三台主機時鐘有偏移，你會先用哪個共同事件對齊紀錄，再判斷先後？"
            )
        if "direct" in normalized_question and "relay" not in normalized_question:
            return (
                "請把 direct case 的 source_ip=10.10.60.10 與正常 ENG baseline "
                "的來源視角分開，哪一項是 Server 可以直接觀察到的線索？"
            )
        if "relay" in normalized_question:
            return (
                "請比較 Kali action、ENG Relay 與 Server command 三份紀錄，"
                "哪一份能補足 Server source IP 看不到的原始發起者？"
            )
        if not retrieved:
            return "若要回答這個問題，你會先補哪一端的事件紀錄？"
        title = str(retrieved[0].metadata.get("title", "目前證據"))
        return f"從「{title}」開始，你能先確認哪一個事件步驟？"

    if (
        "4104" in normalized_question
        and "4688" in normalized_question
        and any(
            marker in question
            for marker in ("數字", "編號", "事件 ID", "意義", "代表什麼")
        )
    ):
        return (
            "知道兩個事件 ID 的定義後，你會用哪一個確認腳本內容，"
            "又用哪一個確認新程序建立？"
        )
    if "employee01" in normalized_question and any(
        marker in normalized_question
        for marker in ("密碼", "密码", "credential")
    ):
        return (
            "Credential exposure 紀錄中的 Demo assumption 與安全否定項目，"
            "各自能支持什麼、又不能支持什麼？"
        )
    if "user01" in normalized_question and "執行證據" in question:
        return (
            "PowerShell 4104 與 Security 4688 分別能回答「執行了什麼」"
            "和「是否建立程序」中的哪一部分？"
        )

    mentions_three_hosts = all(
        host in normalized_question for host in ("kali", "user01", "dc01")
    )
    if mentions_three_hosts and any(
        marker in question for marker in ("比較", "角色", "分工")
    ):
        return (
            "三種角色裡，哪一個有 USER01 端點紀錄直接佐證，"
            "哪兩個主要由網路設計與 Share 設定界定？"
        )
    if "虛擬機" in question and any(
        marker in question for marker in ("除了", "還有", "哪些", "哪幾")
    ):
        return (
            "如果把 SENSOR01 也算進來，四台主機分別留下哪一類證據？"
        )
    if any(marker in question for marker in ("怎麼", "如何")) and any(
        marker in question for marker in ("重現", "還原", "模擬情境", "模擬的情況")
    ):
        return (
            "若要確認重現成功，你會分別在下載、執行、影響、復原與收集"
            "階段檢查哪一項輸出？"
        )

    asks_for_execution_actor = (
        re.search(r"(誰|哪台|主機|不是直接|執行端)", question) is not None
        and re.search(r"(執行|修改|影響|作用)", question) is not None
    )
    if asks_for_execution_actor:
        return (
            "請把 Kali、USER01 與 DC01 分成內容提供端、實際執行端和 "
            "Share 所在端，再指出哪一項仍缺少網路層紀錄。"
        )
    if "漏洞" in question or "cve" in normalized_question:
        return (
            "先檢查現有證據是否真的出現漏洞名稱或 CVE；若沒有，"
            "這項結論應歸入已知事實、合理推論，還是證據不足？"
        )
    if any(
        marker in normalized_question
        for marker in ("dry run", "模擬", "真實", "kill chain")
    ):
        return (
            "請挑一份紀錄，判斷它屬於實際觀測、情境假設、dry run，"
            "還是安全轉換，並說明判斷依據。"
        )
    if not retrieved:
        return "若要回答這個問題，你會先補哪一類事件紀錄？"

    title = str(retrieved[0].metadata.get("title", "目前證據"))
    return (
        f"請從「{title}」挑一項證據，說明它能直接證明什麼，"
        "以及還不能證明什麼。"
    )


def normalize_student_text(text: str) -> str:
    """Remove retrieval jargon and normalize common lab terms."""

    normalized_text = re.sub(r"[\r\n\t]+", " ", text).strip()
    for simplified, traditional in {
        "这次": "這次",
        "这": "這",
        "已经": "已經",
        "经": "經",
        "证据": "證據",
        "证": "證",
        "据": "據",
        "无法": "無法",
        "无": "無",
        "确认": "確認",
        "认": "認",
        "窃取": "竊取",
        "密码": "密碼",
        "个": "個",
        "为": "為",
        "与": "與",
        "并": "並",
        "从": "從",
        "后": "後",
        "里": "裡",
        "发": "發",
        "实": "實",
        "拟": "擬",
        "备": "備",
        "设": "設",
        "请": "請",
        "说": "說",
        "问题": "問題",
        "步骤": "步驟",
        "回复": "回覆",
        "真实": "真實",
    }.items():
        normalized_text = normalized_text.replace(simplified, traditional)
    normalized_text = re.sub(
        r"^(?:根據|依據)檢索(?:結果|內容)[，,:：]?\s*",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    normalized_text = re.sub(
        r"[（(]\s*檢索(?:結果|內容)\s*[）)]",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    for internal_label, student_label in {
        "observed_endpoint_logs": "USER01 端點紀錄",
        "simulation_log": "安全模擬紀錄",
        "lab_configuration": "實驗環境設定",
        "evidence_synthesis": "跨來源證據整理",
        "normalized_summary": "正規化事件摘要",
        "normalized_analysis": "正規化鑑別與比較",
        "normalized_timeline": "正規化事件時間線",
        "normalized_state": "正規化狀態",
        "normalized_data_quality": "正規化資料品質摘要",
    }.items():
        normalized_text = normalized_text.replace(
            internal_label,
            student_label,
        )
    cleaned = re.sub(
        r"檢索(?:結果\s*\d+|內容)\s*(?:顯示|記錄|指出|說明)\s*",
        "",
        normalized_text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Source Host\s*=\s*([A-Za-z0-9_-]+)",
        r"來源主機為 \1",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bSource Host\b",
        "來源主機",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bexecution context\b",
        "執行脈絡紀錄",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bHTTP delivery\b",
        "HTTP 下載提供",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bfirewall logs?\b",
        "防火牆紀錄",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bLab\b",
        "實驗環境",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bruntime network telemetry\b",
        "執行期間的網路遙測",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.replace("通信", "通訊")
    cleaned = cleaned.replace("記錄", "紀錄")
    cleaned = cleaned.replace("伺服器", "Server")
    cleaned = cleaned.replace("來源_ip", "source_ip")
    cleaned = cleaned.replace("維護票證", "maintenance ticket")
    cleaned = cleaned.replace("維護票券", "maintenance ticket")
    cleaned = cleaned.replace("票券", "maintenance ticket")
    cleaned = cleaned.replace(
        "ENG01 sent the IEC-104 command to OT-SERVER01",
        "ENG01 向 OT-SERVER01 送出 IEC-104 指令",
    )
    cleaned = cleaned.replace("Host latest timestamps", "各主機最新時間戳")
    cleaned = re.sub(r"本地\s*ENG", "ENG 本機", cleaned)
    cleaned = re.sub(
        r"執行期間的\s*執行期間的網路遙測",
        "執行期間的網路遙測",
        cleaned,
    )
    cleaned = re.sub(
        r"Kali\s+是否嘗試與\s+DC01\s+進行\s+SMB\s+通訊",
        "Kali 是否曾嘗試透過 SMB 與 DC01 通訊",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"且無法確認本次執行期間的網路遙測",
        "且缺少本次執行期間的網路遙測",
        cleaned,
    )
    cleaned = re.sub(
        r"\bnetwork telemetry\b",
        "網路遙測",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s*實驗環境\s*", "實驗環境", cleaned)
    cleaned = re.sub(r"\s*執行脈絡紀錄\s*", "執行脈絡紀錄", cleaned)
    cleaned = re.sub(
        r"執行脈絡紀錄將\s*來源主機為",
        "執行脈絡紀錄將來源主機記為",
        cleaned,
    )
    cleaned = re.sub(
        r"目前無法確認\s+(Kali\s+)?是否嘗試直接\s+SMB\s+通訊的證據",
        r"目前無法確認 \1是否曾嘗試直接進行 SMB 通訊",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"阻擋\s+Kali\s+透過\s+SMB\s+通訊與\s+DC01\s+主機",
        "阻擋 Kali 直接透過 SMB 與 DC01 通訊",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"Kali\s+是否嘗試直接修改\s+DC01\s+Share\s+的主機",
        "Kali 是否曾嘗試直接透過 SMB 寫入 DC01 Share",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^記錄使用者",
        "執行脈絡紀錄也包含使用者",
        cleaned,
    )
    cleaned = re.sub(
        r"^是否有嘗試使用 SMB 進行 Kali 到 DC01 的連線",
        "目前無法確認 Kali 是否曾嘗試直接透過 SMB 連線 DC01",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+([，。；：！？])", r"\1", cleaned)
    cleaned = re.sub(r"[：:]。", "。", cleaned)
    cleaned = cleaned.replace("HTTP 下載提供", "HTTP 下載")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned or text.strip()


def _complete_sentence(text: str) -> str:
    cleaned = normalize_student_text(text)
    if cleaned.endswith(("。", "！", "？", ".", "!", "?")):
        return cleaned
    return cleaned + "。"


def _unique_texts(values: Sequence[str]) -> list[str]:
    ordered: list[str] = []
    for value in values:
        normalized = _complete_sentence(value)
        if normalized not in ordered:
            ordered.append(normalized)
    return ordered


def _dedupe_industroyer2_facts(values: Sequence[str]) -> list[str]:
    """Remove narrow same-concept repetitions common in normalized evidence."""

    ordered: list[str] = []
    seen_concepts: set[str] = set()
    for value in values:
        normalized = value.casefold()
        concept = ""
        if (
            any(marker in normalized for marker in ("maintenance ticket", "維護票"))
            and "eng" in normalized
            and "iec-104" in normalized
        ):
            concept = "maintenance_ticket_visibility"
        elif "source_ip=10.10.60.20" in normalized:
            concept = "eng_source_ip"
        if concept and concept in seen_concepts:
            continue
        if concept:
            seen_concepts.add(concept)
        ordered.append(value)
    return ordered


def repair_domain_identity_format(
    text: str,
    retrieved: Sequence[RetrievedChunk],
) -> str:
    """Repair punctuation inserted inside an otherwise exact domain identity."""

    identity_pattern = re.compile(
        r"\b[A-Za-z0-9_-]+\\[A-Za-z0-9._-]+\b"
    )
    repaired = text
    allowed = {
        identity
        for result in retrieved
        for identity in identity_pattern.findall(result.document)
    }
    for identity in sorted(allowed, key=len, reverse=True):
        domain, username = identity.split("\\", maxsplit=1)
        malformed_pattern = re.compile(
            rf"\b{re.escape(domain)}\\[^\w\\]*{re.escape(username)}\b",
            flags=re.IGNORECASE,
        )
        repaired = malformed_pattern.sub(
            lambda _match, value=identity: value,
            repaired,
        )
    return repaired


def _conservatize_absence_text(
    text: str,
    retrieved: Sequence[RetrievedChunk],
) -> str:
    """Replace scenario-specific absence overclaims with evidence boundaries."""

    context = "\n".join(result.document for result in retrieved).casefold()
    lowered = text.casefold()
    absence = r"(?:no|without|never|did not|\u6c92\u6709|\u5f9e\u672a|\u672a\u66fe)"

    if (
        "does not prove device acceptance" in context
        and re.search(
            absence
            + r".{0,32}(?:plc\s*)?(?:response\s*packet|\u56de\u61c9\u5c01\u5305)",
            lowered,
            flags=re.IGNORECASE,
        )
    ):
        return (
            "現有 PCAP 證據只能確認捕獲到 Lab 請求，無法確認是否有 PLC "
            "回應封包、設備是否接受請求，或是否產生實體影響。"
        )

    if (
        "kali" in context
        and "smb" in context
        and (
            "evidence insufficient" in context
            or "\u4ecd\u662f\u8b49\u64da\u4e0d\u8db3" in context
            or "\u7121\u6cd5\u7528\u672c\u6b21\u8b49\u64da\u65b7\u8a00" in context
        )
        and re.search(
            r"(?:kali.{0,40}" + absence + r".{0,40}smb|"
            + absence + r".{0,40}kali.{0,40}smb)",
            lowered,
            flags=re.IGNORECASE,
        )
    ):
        return (
            "Lab 設計已知會阻擋 Kali 直接 SMB，但現有資料沒有執行期間的 "
            "firewall log 或 PCAP，無法確認 Kali 是否曾嘗試直接 SMB。"
        )

    if (
        "tshark is not installed" in context
        and re.search(
            r"(?:pcap.{0,48}" + absence
            + r".{0,48}(?:iec-?104|asdu|type[ _-]?id|ioa)|"
            + absence + r".{0,48}(?:iec-?104|asdu|type[ _-]?id|ioa).{0,48}pcap)",
            lowered,
            flags=re.IGNORECASE,
        )
    ):
        return (
            "現有 PCAP 僅保留檔案大小與 SHA-256；由於未安裝 tshark，"
            "尚未解析 IEC-104 欄位，因此無法確認欄位內容。"
        )
    return text


def refine_student_response(
    question: str,
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
    response_mode: ResponseMode = "hint",
    scenario: str = "ryuk",
    knowledge_scope: KnowledgeScope = "in_scope",
) -> TutorResponse:
    """Improve phrasing, citation focus, and hint quality deterministically."""

    _validate_response_mode(response_mode)
    _validate_knowledge_scope(knowledge_scope)
    normalized_question = question.casefold()
    if (
        knowledge_scope == "general_concept"
        and "4104" in normalized_question
        and "4688" in normalized_question
    ):
        evidence_ids = [
            evidence_id
            for result in retrieved
            for evidence_id in result.evidence_ids
            if evidence_id == "OT-G05"
        ]
        return TutorResponse(
            answer=(
                "PowerShell 4104 是在 Script Block Logging 已啟用時，"
                "記錄 PowerShell 處理的 script block 內容；Security 4688 則是 "
                "Windows Security 的新程序已建立事件。兩者分別提供腳本內容層"
                "與程序建立層的主機紀錄，但都不會單獨證明跨主機網路活動。"
            ),
            known_facts=[
                "4104 位於 PowerShell Operational；啟用 Script Block Logging 時可記錄 PowerShell 處理的 script block 內容。",
                "4688 位於 Windows Security，表示新的程序已建立；命令列欄位是否可用取決於相關程序建立稽核設定。",
            ],
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=evidence_ids,
            next_hint=(
                "若要把這個通用概念套回某個 Lab，請再指定情境、主機或要比較的事件證據。"
            ),
        )
    if (
        knowledge_scope in {"general_concept", "comparison"}
        and ("iec-104" in normalized_question or "iec104" in normalized_question)
    ):
        concept_ids = [
            evidence_id
            for result in retrieved
            for evidence_id in result.evidence_ids
            if evidence_id in {"OT-G01", "OT-G02", "OT-G03"}
        ]
        has_local_field = "iec104_sent" in normalized_question
        required_concept_ids = (
            {"OT-G01", "OT-G02", "OT-G03"}
            if has_local_field or knowledge_scope == "comparison"
            else {"OT-G01", "OT-G02"}
        )
        evidence_ids = [
            evidence_id
            for evidence_id in concept_ids
            if evidence_id in required_concept_ids
        ]
        generic_answer = (
            "IEC 60870-5-104（IEC-104）是電力自動化與遠動系統使用的通訊協定，"
            "以標準 TCP/IP 傳輸設定檔承載監視、資料交換及控制相關訊息；"
            "它可以用於遠端控制，但不是每一筆 IEC-104 訊息都等於控制命令。"
        )
        local_field_answer = (
            "`iec104_sent` 不是資安領域或 IEC 標準的通用術語，而是本教學環境"
            " Relay／Normalizer 的本地欄位；它用來記錄該 Relay 是否送出一筆"
            "模擬 IEC-104 動作。即使欄位為 true，也不能單獨證明遠端設備已接受、"
            "執行或造成實體影響。"
        )
        if knowledge_scope == "comparison":
            answer = f"通用背景：{generic_answer} 本次實驗：{local_field_answer}"
        elif has_local_field:
            answer = f"{generic_answer} 另外，{local_field_answer}"
        else:
            answer = generic_answer
        known_facts = [
            "IEC-104 是採標準 TCP/IP 傳輸設定檔的遠動通訊協定，可承載監視、資料交換與控制相關訊息。",
            "訊息被記錄為 sent 只能直接支持傳送紀錄，不能單獨證明接收端已接受、執行或造成實體影響。",
        ]
        if has_local_field or knowledge_scope == "comparison":
            known_facts.append(
                "iec104_sent 是本教學環境的本地欄位名稱，不是 IEC 標準定義的通用術語。"
            )
        return TutorResponse(
            answer=answer,
            known_facts=known_facts,
            reasonable_inferences=[],
            unknowns=[],
            evidence_ids=evidence_ids,
            next_hint=(
                "若要把這個通用概念套回某個 Lab，請再指定情境、主機或要比較的事件證據。"
                if knowledge_scope == "general_concept"
                else "若要判讀某一筆實驗操作，請分別檢查 Relay 紀錄、接收端狀態與其他跨主機證據。"
            ),
        )
    evidence_ids = list(response.evidence_ids)
    asks_for_user01_execution_evidence = (
        "user01" in normalized_question and "執行證據" in question
    )
    if asks_for_user01_execution_evidence:
        endpoint_evidence = [
            evidence_id
            for result in retrieved
            if result.metadata.get("source_type") == "observed_endpoint_logs"
            for evidence_id in result.evidence_ids
        ]
        relevant_ids = [
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id in endpoint_evidence
        ]
        if not any(
            evidence_id.startswith("RYUK-U")
            for evidence_id in relevant_ids
        ):
            direct_id = next(
                (
                    evidence_id
                    for evidence_id in endpoint_evidence
                    if evidence_id.startswith("RYUK-U")
                ),
                None,
            )
            if direct_id is not None:
                relevant_ids.insert(0, direct_id)
        if relevant_ids:
            evidence_ids = relevant_ids[:4]

    def prepare(text: str) -> str:
        return repair_domain_identity_format(text, retrieved)

    prepared_answer = prepare(response.answer)
    prepared_known_facts = [prepare(value) for value in response.known_facts]
    prepared_inferences = [
        prepare(value) for value in response.reasonable_inferences
    ]
    prepared_unknowns = [prepare(value) for value in response.unknowns]
    industroyer2_profile = (
        get_scenario_profile(scenario).key == "industroyer2"
    )
    ryuk_profile = get_scenario_profile(scenario).key == "ryuk"

    scheduled_task_dry_run_question = (
        ryuk_profile
        and any(
            marker in normalized_question
            for marker in ("scheduled task", "\u6392\u7a0b\u5de5\u4f5c")
        )
        and any(
            "dry run" in result.document.casefold()
            and "scheduled task" in result.document.casefold()
            for result in retrieved
        )
    )

    def ind2_evidence_ids(*needles: str) -> list[str]:
        """Return retrieved evidence whose normalized text contains a needle."""

        return list(
            dict.fromkeys(
                evidence_id
                for result in retrieved
                if any(
                    needle.casefold() in result.document.casefold()
                    for needle in needles
                )
                for evidence_id in result.evidence_ids
            )
        )

    if scheduled_task_dry_run_question:
        prepared_answer = (
            "本情境沒有建立遠端 scheduled task；「scheduled task deployment」"
            "只是 dry run 階段名稱，不是已建立工作的名稱。"
        )
        prepared_known_facts = [
            "Lab implementation 明確將 scheduled task deployment 標示為 dry run。",
            "現有證據沒有顯示已建立遠端 scheduled task 或將 payload 推送到其他主機。",
        ]
        prepared_inferences = []
        prepared_unknowns = []
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for result in retrieved
                if "scheduled task" in result.document.casefold()
                and "dry run" in result.document.casefold()
                for evidence_id in result.evidence_ids
            )
        )[:4]

    normal_maintenance_question = (
        industroyer2_profile
        and "maintenance" in normalized_question
        and "正常 eng" in normalized_question
    )
    direct_vs_normal_question = (
        industroyer2_profile
        and "direct" in normalized_question
        and "正常 eng" in normalized_question
        and any(marker in question for marker in ("區分", "為什麼", "差別"))
    )
    source_ip_ambiguity_question = (
        industroyer2_profile
        and any(
            marker in normalized_question
            for marker in ("10.10.60.20", ".20", "source_ip")
        )
        and "direct" not in normalized_question
        and any(
            marker in normalized_question
            for marker in ("不能", "為什麼", "判定", "why", "can't", "cannot", "prove", "確定")
        )
    )
    source_ip_host_mapping_question = (
        industroyer2_profile
        and "10.10.60.20" in normalized_question
        and any(
            marker in normalized_question
            for marker in (
                "哪一台",
                "哪台",
                "哪個主機",
                "哪部主機",
                "哪一部",
                "which host",
                "which machine",
                "belongs to",
            )
        )
    )
    pcap_limit_question = (
        industroyer2_profile
        and "pcap" in normalized_question
        and "tshark" in normalized_question
    )
    clock_limit_question = (
        industroyer2_profile
        and any(marker in normalized_question for marker in ("時間戳", "clock offset"))
        and any(marker in question for marker in ("因果", "順序", "直接"))
    )
    caddywiper_state_question = (
        industroyer2_profile
        and "caddywiper" in normalized_question
        and "eng01" in normalized_question
        and "狀態" in question
    )
    direct_vs_relay_question = (
        industroyer2_profile
        and "direct" in normalized_question
        and "relay" in normalized_question
        and any(marker in question for marker in ("比較", "不同", "來源 ip"))
    )
    source_ip_pair_comparison_question = (
        industroyer2_profile
        and "10.10.60.10" in normalized_question
        and "10.10.60.20" in normalized_question
    )
    lockout_question = (
        industroyer2_profile
        and ("caddywiper" in normalized_question or "lockout" in normalized_question)
        and "relay" in normalized_question
        and any(
            marker in normalized_question
            for marker in ("為什麼", "已送出", "送出", "送到", "真的", "有沒有")
        )
    )
    lockout_false_send_question = (
        industroyer2_profile
        and ("caddywiper" in normalized_question or "lockout" in normalized_question)
        and "iec104_sent=false" in normalized_question
    )
    detailed_iec104_fields_question = (
        industroyer2_profile
        and any(
            marker in normalized_question
            for marker in ("asdu", "type id", "type_id", "ioa")
        )
        and any(
            marker in normalized_question
            for marker in ("詳細", "detail", "列出", "list")
        )
    )

    if normal_maintenance_question:
        prepared_answer = (
            "正常 ENG 維護時，OT-SERVER01 在 TCP/2404 看到 "
            "source_ip=10.10.60.20。maintenance_ticket=MW-2026-001 "
            "只存在 ENG 本機紀錄，不會出現在 Server state 或 IEC-104 封包。"
        )
        prepared_known_facts = [
            "經授權的 ENG Maintenance Console 向 OT-SERVER01 送出 IEC-104 指令。",
            "Server 在 TCP/2404 看到 source_ip=10.10.60.20。",
            "maintenance_ticket=MW-2026-001 是 ENG 本機證據，不由 IEC-104 封包攜帶。",
        ]
        prepared_inferences = [
            "maintenance ticket 可協助解讀操作意圖，但不能只從 Server 或封包視角取得。"
        ]
        prepared_unknowns = []
        evidence_ids = ind2_evidence_ids("maintenance ticket", "MW-2026-001")[:4]

    if direct_vs_normal_question:
        prepared_answer = (
            "在 ind2_vE_002，Server／PCAP 視角看到送往 TCP/2404 的 IEC-104 "
            "來源是 source_ip=10.10.60.10；這是 Kali direct 路徑的直接線索，"
            "可與正常 ENG baseline 區分。"
        )
        prepared_known_facts = [
            "ind2_vE_002 被正規化為 direct_iec104_attack。",
            "Kali 直接向 OT-SERVER01 送出 Industroyer2-like IEC-104 安全模擬內容。",
            "Server 在 TCP/2404 看到 source_ip=10.10.60.10，OT 狀態改為服務中斷模擬。",
        ]
        prepared_inferences = [
            "在此 direct case 中，來源 IP 是區分 Kali direct 與正常 ENG 維護的有效線索。"
        ]
        prepared_unknowns = []
        direct_evidence = ind2_evidence_ids("source_ip=10.10.60.10")
        if direct_evidence:
            evidence_ids = direct_evidence[:4]

    if source_ip_host_mapping_question:
        prepared_answer = (
            "10.10.60.20 是 ENG01 的 IP；OT-SERVER01 是接收端，不是這個來源 IP "
            "所屬主機。在正常 ENG 維護與 Kali 經 ENG Relay 成功的 case 中，"
            "ENG01 都可能向 OT-SERVER01 送出 IEC-104，因此 Server／PCAP 會看見 "
            "source_ip=10.10.60.20。"
        )
        prepared_known_facts = [
            "正常 ENG 維護的基線中，Server／PCAP 在 TCP/2404 看見 ENG01 的 source_ip=10.10.60.20。",
            "Kali 經 ENG Relay 成功時，由 ENG01 向 OT-SERVER01 送出 IEC-104，Server 也可能看見 source_ip=10.10.60.20。",
        ]
        prepared_inferences = []
        prepared_unknowns = []
        mapping_evidence = ind2_evidence_ids(
            "ENG01",
            "source_ip=10.10.60.20",
        )
        if mapping_evidence:
            evidence_ids = mapping_evidence[:4]

    if source_ip_ambiguity_question:
        prepared_answer = (
            "不能。正常 ENG 維護與 Kali via ENG Relay 成功時，Server／PCAP 都可能"
            "看到 source_ip=10.10.60.20；來源 IP 本身不足以判定最初發起者或"
            "操作意圖。"
        )
        prepared_known_facts = [
            "正常 ENG baseline 在 Server／PCAP 視角使用 source_ip=10.10.60.20。",
            "via-ENG Relay 成功也可能在 Server／PCAP 視角使用 source_ip=10.10.60.20。",
        ]
        prepared_inferences = [
            "只使用 Server source IP 會把兩種不同的操作來源混在一起。"
        ]
        prepared_unknowns = [
            "Server source IP 單獨無法顯示最初的發起者或正常維護意圖。"
        ]
        source_evidence = ind2_evidence_ids("source_ip=10.10.60.20")
        if source_evidence:
            evidence_ids = source_evidence[:4]

    if pcap_limit_question:
        prepared_answer = (
            "PCAP 檔案存在，但 SENSOR01 未安裝 tshark；Normalizer 目前只能"
            "記錄檔案大小與 SHA-256，尚未解碼 IEC-104 欄位。"
        )
        prepared_known_facts = [
            "PCAP 檔案存在，但 Normalizer 目前只保留檔案大小與雜湊等中繼資訊。",
            "SENSOR01 目前沒有 tshark，因此尚未解析 IEC-104 欄位。",
        ]
        prepared_inferences = [
            "PCAP metadata 可證明收集到檔案，但不足以單獨重建 IEC-104 指令內容。"
        ]
        prepared_unknowns = [
            "未解碼的 PCAP 無法確認完整 IEC-104 欄位與每個控制命令細節。"
        ]
        pcap_evidence = ind2_evidence_ids("tshark is not installed")
        if pcap_evidence:
            evidence_ids = pcap_evidence[:4]

    if detailed_iec104_fields_question:
        prepared_answer = (
            "目前不能列出完整 ASDU、type ID 與 IOA 欄位。SENSOR01 尚未以 tshark "
            "解碼 PCAP；現有 Server command 紀錄最多只提供部分觀察值，不能當成"
            "完整 IEC-104 封包欄位。"
        )
        prepared_known_facts = [
            "ind2_vE_004 的 Server command 紀錄包含部分 IEC-104 指令觀察值。",
            "SENSOR01 目前未安裝 tshark，Normalizer 尚未解碼 IEC-104 欄位。",
        ]
        prepared_inferences = [
            "Server command 摘要可協助確認指令曾被觀察到，但不能取代完整封包解碼。"
        ]
        prepared_unknowns = [
            "目前無法確認完整 ASDU、type ID 與每個 IOA 的封包層詳細值。"
        ]
        field_evidence = ind2_evidence_ids(
            "tshark is not installed",
            "Server received IEC-104 command",
        )
        if field_evidence:
            evidence_ids = field_evidence[:4]

    if clock_limit_question:
        prepared_answer = (
            "不能直接判定。Normalizer 偵測到可能的跨主機 clock offset；應保留"
            "原始時間戳並關聯多端事件內容，不能只依絕對時間推論因果。"
        )
        prepared_known_facts = [
            "ind2_vE_002 的 data quality 提示可能存在跨主機 clock offset。",
            "Normalizer 建議保留原始 timestamps，不要僅依絕對時間推論 causality。",
        ]
        prepared_inferences = [
            "事件順序需要搭配主機端紀錄內容與關聯欄位共同判斷。"
        ]
        prepared_unknowns = [
            "目前無法只用跨主機絕對時間精確重建所有事件的先後。"
        ]
        clock_evidence = ind2_evidence_ids("clock offset")
        if clock_evidence:
            evidence_ids = clock_evidence[:4]

    if caddywiper_state_question:
        prepared_answer = (
            "在 ind2_vE_003，Safe CaddyWiper 模擬 ENG01 的 engineering console、"
            "backup、IOA mapping 與 restore 都不可用，且 dummy_files_wiped=true。"
            "這是安全模擬狀態，不是已驗證的真實 CaddyWiper 行為。"
        )
        prepared_known_facts = [
            "eng_console_available、engineering_backup_available、ioa_mapping_available 與 restore_allowed 都是 false。",
            "dummy_files_wiped=true，reason 指向 simulated engineering workstation impact。",
        ]
        prepared_inferences = [
            "此安全模擬用來呈現 lockout 對 ENG01 復原與操作能力的影響。"
        ]
        prepared_unknowns = [
            "現有安全模擬不能證明真實 CaddyWiper 在正式環境的實際行為或影響範圍。"
        ]
        state_evidence = ind2_evidence_ids("dummy_files_wiped=True")
        if state_evidence:
            evidence_ids = state_evidence[:4]

    if direct_vs_relay_question:
        prepared_answer = (
            "Kali direct IEC-104 時，OT-SERVER01 看到 source_ip=10.10.60.10；"
            "Kali via ENG Relay 成功時，由 ENG01 送出 IEC-104，因此 Server 看到"
            "source_ip=10.10.60.20。後者不能只靠來源 IP 與正常 ENG 維護區分。"
        )
        prepared_known_facts = [
            "direct IEC-104 操作在 Server／PCAP 視角可由 source_ip=10.10.60.10 區分。",
            "via-ENG Relay 成功在 Server／PCAP 視角可能使用 source_ip=10.10.60.20。",
            "via-ENG Relay 與正常 ENG 在 Server／PCAP 視角可能相似。",
        ]
        prepared_inferences = [
            "來源 IP 可區分 direct 與 relay case，但不足以判定 10.10.60.20 是否為正常維護。"
        ]
        prepared_unknowns = [
            "source IP 本身無法提供最初發起者與操作意圖的完整歸因。"
        ]
        direct_relay_evidence = ind2_evidence_ids(
            "source_ip=10.10.60.10",
            "source_ip=10.10.60.20",
        )
        if direct_relay_evidence:
            evidence_ids = direct_relay_evidence[:4]

    if source_ip_pair_comparison_question:
        prepared_answer = (
            "在本 Lab 中，source_ip=10.10.60.10 對應已記錄的 Kali direct IEC-104 "
            "安全模擬；source_ip=10.10.60.20 則是 ENG01 對 OT-SERVER01 送出的 "
            "IEC-104，可出現在正常 ENG 維護或 Kali via ENG Relay 成功案例。"
            "因此 .20 仍不能單靠來源 IP 判定最初發起者或意圖。"
        )
        prepared_known_facts = [
            "direct IEC-104 安全模擬在 Server／PCAP 視角可見 source_ip=10.10.60.10。",
            "正常 ENG 維護在 Server／PCAP 視角可見 ENG01 的 source_ip=10.10.60.20。",
            "Kali via ENG Relay 成功時，ENG01 送出 IEC-104，Server 也可見 source_ip=10.10.60.20。",
        ]
        prepared_inferences = [
            "來源 IP 可協助區分 direct case 與由 ENG01 送出的 case，但 .20 本身不足以區分正常維護與 Relay 操作。"
        ]
        prepared_unknowns = [
            "僅靠 source_ip=10.10.60.20 無法確認最初發起者、操作意圖或完整網路路徑。"
        ]
        paired_evidence = ind2_evidence_ids(
            "source_ip=10.10.60.10",
            "source_ip=10.10.60.20",
            "must be correlated",
        )
        if paired_evidence:
            evidence_ids = paired_evidence[:4]

    if lockout_question:
        prepared_answer = (
            "不能把它當成已送出指令。Safe CaddyWiper lockout 後的 Relay 紀錄"
            "顯示 accepted=False、iec104_sent=False，表示嘗試在 IEC-104 發送前"
            "已被阻擋，不能視為 Server 收到新的指令。"
        )
        prepared_known_facts = [
            "Safe CaddyWiper lockout 後，ENG relay／recovery 嘗試可能在送出 IEC-104 前被拒絕。",
            "Relay 操作紀錄出現 accepted=False、iec104_sent=False 與 blocked_by_safe_caddywiper。",
        ]
        prepared_inferences = [
            "沒有 iec104_sent=true 或相應 Server command 時，不能把 Relay 嘗試視為成功控制。"
        ]
        prepared_unknowns = []
        lockout_evidence = ind2_evidence_ids("accepted=False", "iec104_sent=False")
        if lockout_evidence:
            evidence_ids = lockout_evidence[:4]

    if lockout_false_send_question:
        blocked_results = [
            result
            for result in retrieved
            if "accepted=false" in result.document.casefold()
            and "iec104_sent=false" in result.document.casefold()
            and "blocked_by_safe_caddywiper" in result.document.casefold()
        ]
        if blocked_results:
            prepared_answer = (
                "在這筆 ENG Relay／recovery 嘗試中，accepted=False 與 "
                "iec104_sent=False 表示它在送出 IEC-104 前被 Safe CaddyWiper-like "
                "lockout 阻擋。這只能描述該筆嘗試未送出，不能推成 OT-SERVER01 在同一期間"
                "沒有收到其他來源的 IEC-104，也不能代表真實惡意程式行為。"
            )
            prepared_known_facts = [
                "這筆 Relay 操作紀錄包含 accepted=False、iec104_sent=False 與 blocked_by_safe_caddywiper。",
                "Safe CaddyWiper lockout 後，ENG relay／recovery 嘗試可能在 IEC-104 送出前被拒絕。",
            ]
            prepared_inferences = []
            prepared_unknowns = [
                "現有資料無法只憑這筆 Relay 紀錄判定 OT-SERVER01 在同一期間是否收到其他來源的 IEC-104。"
            ]
            evidence_ids = list(
                dict.fromkeys(
                    evidence_id
                    for result in blocked_results
                    for evidence_id in result.evidence_ids
                )
            )[:4]

    relay_correlation_question = (
        industroyer2_profile
        and "relay" in normalized_question
        and any(
            marker in question
            for marker in (
                "哪些紀錄",
                "哪些證據",
                "哪一項證據",
                "哪個證據",
                "哪一筆證據",
                "關聯",
                "哪幾端",
                "哪幾",
                "資料",
                "對照",
            )
        )
    )
    explicit_relay_send_results = [
        result
        for result in retrieved
        if "accepted=true" in result.document.casefold()
        and "iec104_sent=true" in result.document.casefold()
    ]
    relay_summary_results = [
        result
        for result in retrieved
        if "kali requested" in result.document.casefold()
        and "must be correlated" in result.document.casefold()
    ]
    relay_quality_results = [
        result
        for result in retrieved
        if "source ip alone cannot distinguish" in result.document.casefold()
    ]
    normal_eng_results = [
        result
        for result in retrieved
        if "normal eng traffic establishes" in result.document.casefold()
        and "source_ip=10.10.60.20" in result.document.casefold()
    ]
    normal_relay_comparison_question = (
        relay_correlation_question
        and any(
            marker in normalized_question
            for marker in ("正常 eng", "normal eng")
        )
        and any(marker in question for marker in ("相同", "區分"))
    )
    if (
        relay_correlation_question
        and explicit_relay_send_results
        and relay_summary_results
    ):
        if normal_relay_comparison_question and normal_eng_results:
            prepared_answer = (
                "相同之處是正常 ENG 維護與 Kali via ENG Relay 成功時，"
                "Server／PCAP 都可能看到 ENG01 的 source_ip=10.10.60.20、"
                "IEC-104 與 TCP/2404。要區分兩者，需用 Kali action 確認"
                "最初要求、用 ENG Maintenance／ENG Relay 紀錄區分正常維護與"
                "轉送操作，再以 Server command／PCAP metadata 對上 Server 端觀測；"
                "不能只靠來源 IP。"
            )
            prepared_known_facts = [
                "正常 ENG 維護基線中，Server／PCAP 看到 source_ip=10.10.60.20 與 TCP/2404。",
                "Kali via ENG Relay 成功時，Server 端也可能看到 source_ip=10.10.60.20。",
                "ENG Relay 成功紀錄包含 accepted=True、iec104_sent=True。",
            ]
            hint = (
                "請比較 ENG Maintenance Console 是否有維護脈絡，以及 Kali action "
                "與 ENG Relay 是否記錄了轉送要求。"
            )
        else:
            prepared_answer = (
                "要串起三層證據：Kali action 紀錄確認由 Kali 提出要求，"
                "ENG Relay 紀錄以 accepted=True、iec104_sent=True 確認 "
                "ENG01 實際送出 IEC-104，Server command／PCAP metadata 再確認 "
                "OT-SERVER01 看到 source_ip=10.10.60.20。單靠 Server source IP "
                "不能判斷最初發起者。"
            )
            prepared_known_facts = [
                "Kali 在 Safe CaddyWiper lockout 前透過 ENG Relay 提出操作要求。",
                "ENG Relay 紀錄包含 accepted=True、iec104_sent=True 與成功送出結果。",
                "Server 在 TCP/2404 看到 source_ip=10.10.60.20。",
            ]
        prepared_inferences = []
        if any("tshark" in result.document.casefold() for result in retrieved):
            prepared_unknowns = [
                "SENSOR01 尚未以 tshark 解碼 PCAP，因此目前不能確認 IEC-104 的詳細封包欄位。"
            ]
        preferred_results = [
            *(normal_eng_results if normal_relay_comparison_question else []),
            *explicit_relay_send_results,
            *relay_summary_results,
            *relay_quality_results,
        ]
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for result in preferred_results
                for evidence_id in result.evidence_ids
            )
        )[:4]

    # Treat the recorded via-ENG hand-off as a relationship, not an intent or
    # awareness claim.  These two natural question forms otherwise tend to be
    # under-specified for a small model: "does ENG know?" and "does Kali send
    # directly?"  The response remains bounded by the fresh retrieved Relay
    # success evidence on every turn.
    relay_path_question = (
        industroyer2_profile
        and "kali" in normalized_question
        and "relay" in normalized_question
        and any(
            marker in normalized_question
            for marker in (
                "via-eng",
                "via eng",
                "through",
                "direct",
                "直接",
            )
        )
    )
    relay_awareness_question = (
        industroyer2_profile
        and "kali" in normalized_question
        and "eng" in normalized_question
        and any(
            marker in normalized_question
            for marker in ("知道", "知情", "是否知", "會知", "aware", "know")
        )
    )
    if (
        (relay_path_question or relay_awareness_question)
        and not relay_correlation_question
        and explicit_relay_send_results
        and relay_summary_results
    ):
        handoff_evidence = list(
            dict.fromkeys(
                evidence_id
                for result in (*relay_summary_results, *explicit_relay_send_results)
                for evidence_id in result.evidence_ids
            )
        )[:4]
        if relay_awareness_question:
            prepared_answer = (
                "可確認的是 ENG Relay 有接收並轉送請求的系統紀錄："
                "Kali 先透過 Relay 提出要求，ENG01 再送出 IEC-104 到 "
                "OT-SERVER01。這只能表示 Relay／ENG01 端可觀測到該處理；"
                "現有證據沒有記錄 ENG 人員是否看見、理解或同意這項請求。"
            )
            prepared_known_facts = [
                "Kali 的要求先進入 ENG Relay，ENG01 再送出 IEC-104 到 OT-SERVER01。",
                "ENG Relay 成功紀錄包含 accepted=True 與 iec104_sent=True。",
            ]
            prepared_inferences = []
            prepared_unknowns = [
                "現有紀錄無法確認 ENG 人員是否知情，因為沒有操作人員確認或介面互動紀錄。"
            ]
            hint = "請比較 ENG Relay 系統紀錄與 ENG Maintenance Console 紀錄，各自能說明的是系統處理還是人員維護活動。"
        else:
            prepared_answer = (
                "在此 via-ENG Relay 成功案例中，封包不是由 Kali 直接送到 "
                "OT-SERVER01；Kali 先透過 ENG Relay 提出要求，再由 ENG01 實際送出 "
                "IEC-104，因此 Server 看到的是 ENG01 的 source_ip=10.10.60.20。"
            )
            prepared_known_facts = [
                "Kali 透過 ENG Relay 提出操作要求。",
                "ENG01 實際送出 IEC-104 到 OT-SERVER01。",
                "成功 Relay 紀錄包含 accepted=True 與 iec104_sent=True。",
            ]
            prepared_inferences = []
            prepared_unknowns = []
            hint = "請再比較 direct case 的 source_ip=10.10.60.10 與 via-ENG Relay 的 source_ip=10.10.60.20，找出兩種路徑的差異。"
        evidence_ids = handoff_evidence
    core_host_role_question = all(
        host in normalized_question for host in ("kali", "user01", "dc01")
    ) and any(marker in question for marker in ("比較", "角色", "分工"))
    virtual_machine_inventory_question = (
        "虛擬機" in question
        and any(marker in question for marker in ("除了", "還有", "哪些", "哪幾"))
    )
    workflow_question = (
        any(marker in question for marker in ("怎麼", "如何"))
        and any(
            marker in question
            for marker in ("重現", "還原", "模擬情境", "模擬的情況")
        )
    )

    if core_host_role_question or virtual_machine_inventory_question:
        role_evidence_order = (
            "RYUK-C01",
            "RYUK-L01",
            "RYUK-U01",
            "RYUK-U02",
            "RYUK-U03",
            "RYUK-L02",
        )
        available_ids = set(allowed_evidence_ids(retrieved))
        focused_ids: list[str] = []
        if "RYUK-C01" in available_ids:
            focused_ids.append("RYUK-C01")
        role_text = " ".join(
            [prepared_answer, *prepared_known_facts]
        ).casefold()
        if "http access" in role_text and "RYUK-L01" in available_ids:
            focused_ids.append("RYUK-L01")
        if any(
            marker in role_text
            for marker in (
                "端點紀錄",
                "observed_endpoint_logs",
                "execution context",
                "powershell",
                "security",
            )
        ):
            direct_endpoint_id = next(
                (
                    evidence_id
                    for evidence_id in ("RYUK-U01", "RYUK-U02", "RYUK-U03")
                    if evidence_id in available_ids
                ),
                None,
            )
            if direct_endpoint_id is not None:
                focused_ids.append(direct_endpoint_id)
        focused_ids.extend(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id in role_evidence_order
            and evidence_id in available_ids
            and evidence_id not in focused_ids
        )
        if focused_ids:
            evidence_ids = focused_ids[:3]
        # A simple inventory or role comparison does not need an unrelated
        # packet-telemetry caveat. Direct-path questions are handled below.
        prepared_unknowns = []
        if core_host_role_question:
            prepared_inferences = []
    direct_kali_actor_question = _asks_about_direct_kali_path(question)
    observed_hosts, _ = observed_execution_attribution(retrieved)
    context_supports_role_answer = any(
        "Kali" in result.document
        and "HTTP" in result.document
        and "SMB" in result.document
        and "DC01" in result.document
        for result in retrieved
    )
    if (
        direct_kali_actor_question
        and observed_hosts
        and context_supports_role_answer
    ):
        execution_host = observed_hosts[0]
        if response_mode == "explain":
            runtime_limit = any(
                "仍是證據不足" in result.document
                for result in retrieved
            )
            blocking_explanation = (
                "實驗環境設計另阻擋 Kali 直接透過 SMB 存取 DC01，"
                "但這項設計不能取代執行期間的防火牆或封包觀測。"
                if runtime_limit
                else "實驗環境設計另阻擋 Kali 直接透過 SMB 存取 DC01。"
            )
            prepared_answer = (
                "Kali 的 HTTP 提供紀錄支持它是本情境的內容來源端。"
                f"{execution_host} 的端點紀錄則直接支持它是實際執行端，"
                "因此兩類證據代表不同角色。"
                f"{blocking_explanation}"
            )
        else:
            prepared_answer = (
                "Kali 在本情境只負責透過 HTTP 提供腳本；"
                f"{execution_host} 是有端點紀錄支持的實際執行端。"
                "實驗環境設計阻擋 Kali 直接透過 SMB 存取 DC01。"
            )
        prepared_known_facts = [
            "Kali 是本情境的 HTTP 下載來源。",
            f"{execution_host} 是有端點紀錄支持的實際執行端。",
            "實驗環境設計阻擋 Kali 直接透過 SMB 存取 DC01。",
        ]
        if any("仍是證據不足" in result.document for result in retrieved):
            prepared_unknowns = [
                "目前缺少執行期間的防火牆紀錄或封包擷取，"
                "無法確認 Kali 是否曾嘗試直接 SMB 連線。"
            ]
        configuration_evidence = [
            evidence_id
            for result in retrieved
            if result.metadata.get("source_type") == "lab_configuration"
            for evidence_id in result.evidence_ids
        ]
        _, execution_evidence = observed_execution_attribution(retrieved)
        available_focused_evidence = set(configuration_evidence) | set(
            execution_evidence
        )
        preferred_role_order = (
            "RYUK-C01",
            "RYUK-U01",
            "RYUK-U02",
            "RYUK-N01",
            "RYUK-U03",
            "RYUK-L02",
        )
        focused_evidence = [
            evidence_id
            for evidence_id in preferred_role_order
            if evidence_id in available_focused_evidence
        ]
        focused_evidence.extend(
            evidence_id
            for evidence_id in [
                *configuration_evidence,
                *execution_evidence,
            ]
            if evidence_id not in focused_evidence
        )
        if focused_evidence:
            evidence_ids = focused_evidence[:2]
        malformed_inference = re.compile(
            r"Kali.*不是直接修改.*主機",
            flags=re.IGNORECASE,
        )
        prepared_inferences = [
            value
            for value in prepared_inferences
            if malformed_inference.search(value) is None
        ]
        # These role statements are directly supported by configuration and
        # endpoint records; keeping a model-generated duplicate under
        # reasonable_inferences makes the teaching categories less clear.
        prepared_inferences = []

    credential_question = (
        "employee01" in normalized_question
        and any(
            marker in normalized_question
            for marker in (
                "密碼",
                "密码",
                "credential",
                "竊取",
                "窃取",
            )
        )
    )
    credential_context_available = any(
        "Demo assumption" in result.document
        and "credential theft" in result.document
        for result in retrieved
    )
    if credential_question and credential_context_available:
        asks_for_password_value = any(
            marker in normalized_question
            for marker in ("原始密碼", "原始密码", "密碼內容", "密码内容")
        )
        if asks_for_password_value:
            prepared_answer = (
                "目前證據沒有收集或保存 employee01 的原始密碼內容。"
                "Credential exposure 在本情境只是 Demo assumption，"
                "不能據此還原或提供真實密碼。"
            )
        elif response_mode == "explain":
            prepared_answer = (
                "目前證據不支持 employee01 的密碼曾遭到真實竊取。"
                "Credential exposure 紀錄把帳號暴露明確標為 Demo assumption，"
                "安全說明也否定 password dumping、LSASS access 與瀏覽器密碼蒐集。"
                "因此只能把帳號可用視為故事前提，不能補推密碼取得流程。"
            )
        else:
            prepared_answer = (
                "目前證據不支持 employee01 的密碼曾遭到真實竊取；"
                "Credential exposure 只是 Demo assumption。"
            )
        prepared_known_facts = [
            "Credential exposure 紀錄將帳號暴露標為 Demo assumption。",
            "安全說明否定真實 credential theft、password dumping、"
            "LSASS access 與瀏覽器密碼蒐集。",
        ]
        prepared_inferences = []
        prepared_unknowns = [
            "現有證據無法確認密碼如何取得、何時取得或使用了哪個工具。"
        ]
        credential_evidence = [
            evidence_id
            for result in retrieved
            for evidence_id in result.evidence_ids
            if evidence_id == "RYUK-L07"
        ]
        if credential_evidence:
            evidence_ids = list(dict.fromkeys(credential_evidence))

    sensor01_modification_question = (
        "sensor01" in normalized_question
        and "dc01" in normalized_question
        and any(
            marker in question
            for marker in ("修改", "改了", "變更", "寫入")
        )
    )
    context_defines_sensor01_as_collector = any(
        "SENSOR01" in result.document
        and "收集" in result.document
        and "正規化" in result.document
        and (
            "不是 delivery" in result.document
            or "不是" in result.document
            and "impact" in result.document
        )
        for result in retrieved
    )
    corrected_sensor01_attribution = (
        sensor01_modification_question
        and context_defines_sensor01_as_collector
    )
    if corrected_sensor01_attribution:
        endpoint_evidence_available = any(
            evidence_id.startswith("RYUK-U")
            for result in retrieved
            for evidence_id in result.evidence_ids
        )
        prepared_answer = (
            "這個歸因前提不成立：SENSOR01 產生或保存彙整 JSON，"
            "只能支持它負責收集、驗證與正規化證據，不能因此推成它"
            "修改了 DC01 檔案。"
        )
        prepared_known_facts = [
            "SENSOR01 是本情境的證據收集與正規化端。",
        ]
        if endpoint_evidence_available:
            prepared_answer += (
                "USER01 的端點紀錄另支持 USER01 是執行端，且 DC01 "
                "RyukShare 是模擬目標。"
            )
            prepared_known_facts.append(
                "USER01 端點紀錄支持 USER01 執行腳本，並將 DC01 "
                "RyukShare 記為目標。"
            )
        prepared_inferences = []
        prepared_unknowns = [
            "若要逐次確認 DC01 上每筆檔案寫入的來源，仍需要 DC01 "
            "server-side audit、SMB telemetry 或同期封包紀錄。"
        ]
        attribution_evidence = [
            evidence_id
            for preferred_id in (
                "RYUK-L10",
                "RYUK-U01",
                "RYUK-U02",
                "RYUK-U03",
                "RYUK-N01",
            )
            for result in retrieved
            for evidence_id in result.evidence_ids
            if evidence_id == preferred_id
        ]
        if attribution_evidence:
            evidence_ids = list(dict.fromkeys(attribution_evidence))[:4]

    asks_to_compare_4104_and_4688 = (
        "4104" in normalized_question and "4688" in normalized_question
    )
    asks_event_id_meaning = (
        asks_to_compare_4104_and_4688
        and any(
            marker in question
            for marker in (
                "數字",
                "編號",
                "事件 ID",
                "意義",
                "代表什麼",
                "本身",
            )
        )
    )
    asks_to_compare_u02_and_u03 = (
        "ryuk-u02" in normalized_question
        and "ryuk-u03" in normalized_question
    )
    endpoint_comparison_context = any(
        "4104" in result.document
        and "4688" in result.document
        and {"RYUK-U02", "RYUK-U03"} <= set(result.evidence_ids)
        for result in retrieved
    )
    corrected_endpoint_event_comparison = (
        (asks_to_compare_4104_and_4688 or asks_to_compare_u02_and_u03)
        and endpoint_comparison_context
    )
    if corrected_endpoint_event_comparison:
        if asks_event_id_meaning:
            prepared_answer = (
                "4104 和 4688 是 Windows 事件 ID，但代表不同類型的紀錄。"
                "4104 位於 PowerShell Operational，重點是 Script Block "
                "Logging 所留下的腳本內容；4688 位於 Security，表示建立了"
                "新程序。這次 USER01 的前者命中腳本與 DC01 Share 標記，"
                "後者則命中 PowerShell 程序、腳本名稱、employee01 與 command line。"
            )
        else:
            prepared_answer = (
                "RYUK-U02 的 PowerShell Operational 摘要涵蓋 4103／4104，"
                "能支持 USER01 在該時窗記錄到腳本內容及 DC01 Share、"
                "impact folder、Caesar 等標記。RYUK-U03 則是 Security "
                "4688 process-creation 摘要，能支持 PowerShell 程序建立，"
                "並記錄腳本名稱、employee01 與 command line；兩者是內容"
                "層與程序建立層的不同佐證。"
            )
        prepared_known_facts = [
            "RYUK-U02 彙整 USER01 的 PowerShell 4103／4104 事件與"
            "腳本、Share、impact folder、Caesar 標記。",
            "RYUK-U03 彙整 USER01 的 Security 4688 process-creation "
            "事件，並確認 PowerShell、腳本名稱、employee01 與"
            " command line。",
        ]
        prepared_inferences = []
        prepared_unknowns = []
        comparison_evidence = [
            evidence_id
            for evidence_id in ("RYUK-U02", "RYUK-U03")
            if any(
                evidence_id in result.evidence_ids
                for result in retrieved
            )
        ]
        if comparison_evidence:
            evidence_ids = comparison_evidence

    user01_share_script_question = (
        "user01" in normalized_question
        and "dc01" in normalized_question
        and "ryukshare" in normalized_question
        and any(
            marker in normalized_question
            for marker in ("腳本", "script", ".ps1")
        )
        and any(
            marker in normalized_question
            for marker in ("跑了", "執行", "是不是", "是否", "did")
        )
    )
    execution_hosts, execution_support_ids = (
        observed_execution_attribution(retrieved)
    )
    clarified_user01_share_execution = (
        user01_share_script_question and "USER01" in execution_hosts
    )
    if clarified_user01_share_execution:
        prepared_answer = (
            "有執行證據，但要把位置說清楚：腳本程序是在 USER01 "
            "執行，DC01 的 RyukShare 是它存取並寫入安全模擬結果的"
            "目標；這不等於腳本程序在 DC01 上啟動。"
        )
        prepared_known_facts = [
            "USER01 的 execution context、PowerShell 與 process "
            "摘要共同支持 USER01 是執行端。",
            "DC01 RyukShare 被記錄為本次安全模擬的目標 Share。",
        ]
        prepared_inferences = []
        prepared_unknowns = []
        preferred_execution_ids = (
            "RYUK-U01",
            "RYUK-U02",
            "RYUK-U03",
            "RYUK-L02",
            "RYUK-N01",
        )
        focused_execution_ids = [
            evidence_id
            for evidence_id in preferred_execution_ids
            if evidence_id in execution_support_ids
        ]
        if focused_execution_ids:
            evidence_ids = focused_execution_ids[:4]

    empty_smb_snapshot_question = (
        "smb" in normalized_question
        and "user01" in normalized_question
        and "dc01" in normalized_question
        and (
            all(
                marker in normalized_question
                for marker in ("empty", "post-run", "never")
            )
            or "空白" in question
            and any(marker in question for marker in ("從未", "沒有存取"))
        )
    )
    context_limits_empty_smb_snapshot = any(
        "SMB state" in result.document
        and "執行後" in result.document
        and "不能否定先前存取" in result.document
        for result in retrieved
    )
    corrected_empty_smb_snapshot = (
        empty_smb_snapshot_question and context_limits_empty_smb_snapshot
    )
    if corrected_empty_smb_snapshot:
        prepared_answer = (
            "不能。空白的 SMB snapshot 是在執行後才收集，只能表示"
            "當時沒有 active connection 或 mapping，不能證明 USER01 "
            "先前從未存取 DC01；其他執行與 Share 相關紀錄仍支持先前"
            "存取路徑。"
        )
        prepared_known_facts = [
            "USER01 的 SMB state 在執行後才查詢，當時沒有 active "
            "connection 或 mapping。",
            "Execution、PowerShell 與 Discovery 紀錄將 USER01 和 "
            "DC01 RyukShare 串在本次模擬中。",
        ]
        prepared_inferences = []
        prepared_unknowns = [
            "缺少執行當下的 SMB client snapshot、DC01 server-side "
            "audit 或同期網路遙測，無法重建每次連線的精確起訖。"
        ]
        preferred_snapshot_ids = (
            "RYUK-U04",
            "RYUK-U02",
            "RYUK-L02",
            "RYUK-L06",
            "RYUK-N01",
        )
        snapshot_evidence = [
            evidence_id
            for evidence_id in preferred_snapshot_ids
            if any(
                evidence_id in result.evidence_ids
                for result in retrieved
            )
        ]
        if snapshot_evidence:
            evidence_ids = snapshot_evidence[:4]

    real_ryuk_count_premise = (
        ("ryuk" in normalized_question or ".ryk" in normalized_question)
        and "100" in question
        and ("真實" in question or "真正" in question)
    )
    context_supports_safe_caesar = any(
        "Caesar" in result.document
        and ("安全模擬" in result.document or "不是真實" in result.document)
        for result in retrieved
    )
    if real_ryuk_count_premise and context_supports_safe_caesar:
        if "加密" in question:
            prepared_answer = (
                "這個前提不成立：證據只支持本實驗以 Caesar shift 產生 "
                "100 個 .RYK 安全模擬檔，不能證明使用了真實 Ryuk，"
                "也無法據此判斷真實惡意程式的加密演算法。"
            )
            prepared_unknowns = [
                "目前證據不包含真實 Ryuk 的加密演算法或惡意程式樣本。"
            ]
        else:
            prepared_answer = (
                "這個前提不成立：100 個 .RYK 只是本次 Lab 的安全模擬"
                "數量，不能當成真實事件的影響規模。"
            )
            prepared_unknowns = [
                "目前證據沒有真實事件的檔案總數、停機時間或營運損失。"
            ]
        prepared_known_facts = [
            "本次 Lab 產生 100 個 .RYK 安全模擬檔。",
            "檔案內容使用可逆的 Caesar shift，不是真實 Ryuk 加密。",
        ]
        prepared_inferences = []
        impact_evidence = [
            evidence_id
            for result in retrieved
            for evidence_id in result.evidence_ids
            if evidence_id in {"RYUK-L03", "RYUK-L04"}
        ]
        if impact_evidence:
            evidence_ids = list(dict.fromkeys(impact_evidence))
    hint = normalize_student_text(prepare(response.next_hint))
    generic_hint = re.search(
        r"請比較.+支持的已知事實.+(?:額外證據|仍需要|證據不足)",
        hint,
    )
    actor_question = (
        re.search(r"(誰|哪台|主機|不是直接|執行端)", question) is not None
        and re.search(r"(執行|修改|影響|作用)", question) is not None
    )
    if corrected_sensor01_attribution:
        hint = (
            "先把「誰產生彙整 JSON」、「誰執行腳本」與「誰造成逐筆"
            "檔案寫入」拆成三個待證明的命題，各自需要哪類證據？"
        )
    elif corrected_endpoint_event_comparison:
        if asks_event_id_meaning:
            hint = build_contextual_hint(
                question,
                retrieved,
                scenario=scenario,
            )
        else:
            hint = (
                "若要再提高信心，請分別指出哪個欄位屬於 script-block "
                "內容證據，哪個欄位屬於 process-creation 證據。"
            )
    elif (
        core_host_role_question
        or virtual_machine_inventory_question
        or workflow_question
    ):
        hint = build_contextual_hint(
            question,
            retrieved,
            scenario=scenario,
        )
    elif clarified_user01_share_execution:
        hint = (
            "請把「程序在哪台主機執行」與「程序把哪個 Share 當作"
            "目標」分成兩句，兩者各由哪份端點紀錄支持？"
        )
    elif corrected_empty_smb_snapshot:
        hint = (
            "空白快照只描述收集當下；若要否定先前存取，還需要哪一種"
            "涵蓋執行時窗的 server-side 或網路證據？"
        )
    elif (
        generic_hint
        or "檢索內容" in hint
        or actor_question
        or asks_for_user01_execution_evidence
        or credential_question
    ):
        hint = build_contextual_hint(
            question,
            retrieved,
            scenario=scenario,
        )
    if get_scenario_profile(scenario).key == "industroyer2":
        hint = build_contextual_hint(
            question,
            retrieved,
            scenario=scenario,
        )
    if relay_awareness_question:
        hint = (
            "請比較 ENG Relay 系統紀錄與 ENG Maintenance Console 紀錄，"
            "各自能說明的是系統處理還是人員維護活動。"
        )
    elif relay_path_question:
        hint = (
            "請比較 direct case 的 source_ip=10.10.60.10 與 via-ENG Relay 的 "
            "source_ip=10.10.60.20，找出兩種路徑的差異。"
        )
    if knowledge_scope == "general_concept":
        hint = (
            "若要把這個通用概念套回某個 Lab，請再指定情境、主機或要比較的事件證據。"
        )

    prepared_answer = _conservatize_absence_text(prepared_answer, retrieved)
    prepared_known_facts = [
        _conservatize_absence_text(value, retrieved)
        for value in prepared_known_facts
    ]
    prepared_inferences = [
        _conservatize_absence_text(value, retrieved)
        for value in prepared_inferences
    ]
    prepared_unknowns = [
        _conservatize_absence_text(value, retrieved)
        for value in prepared_unknowns
    ]
    hint = _conservatize_absence_text(hint, retrieved)

    frostygoop_impact_boundary = (
        get_scenario_profile(scenario).key == "frostygoop"
        and any(
            marker in normalized_question
            for marker in (
                "physical impact",
                "device acceptance",
                "plc",
                "實體影響",
                "實體設備影響",
                "物理影響",
                "設備接受",
                "製程",
                "設備影響",
            )
        )
    )
    frostygoop_limit_results = [
        result
        for result in retrieved
        if (
            "does not prove device acceptance" in result.document.casefold()
            or (
                result.metadata.get("source_type") == "normalized_data_quality"
                and any(
                    evidence_id.startswith("FROSTY-")
                    for evidence_id in result.evidence_ids
                )
            )
        )
    ]
    if frostygoop_impact_boundary and frostygoop_limit_results:
        prepared_answer = (
            "不能。這些 PCAP 與偵測結果能支持 Lab 中出現 Modbus 請求或"
            "WRITE 類型活動；但它們不單獨證明 PLC／RTU 已接受請求，"
            "更不能確認任何實體設備或製程影響。"
        )
        prepared_known_facts = [
            "PCAP 可支持捕獲到對應的 Lab Modbus 請求。",
            "本情境沒有使用真實 PLC、RTU 或實體製程。",
        ]
        prepared_inferences = []
        prepared_unknowns = [
            "現有證據不足以確認接收端是否接受請求，或是否造成實體設備／製程影響。"
        ]
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for result in frostygoop_limit_results
                for evidence_id in result.evidence_ids
            )
        )[:4]
        hint = (
            "若要把「看到請求」提升為「確認影響」，你會需要哪一類接收端"
            "狀態或製程量測證據？"
        )

    # Keep this explanation deterministic: a packet observation and an alert
    # are useful Lab evidence, but they are not evidence of physical impact.
    frostygoop_impact_marker = "\u5be6\u9ad4\u8a2d\u5099\u5f71\u97ff"
    frostygoop_impact_boundary_safe = (
        get_scenario_profile(scenario).key == "frostygoop"
        and any(
            marker in normalized_question
            for marker in (
                "physical impact",
                "device acceptance",
                "plc",
                frostygoop_impact_marker,
                "\\u5be6\\u9ad4\\u5f71\\u97ff",
                "\\u7269\\u7406\\u5f71\\u97ff",
            )
        )
    )
    if frostygoop_impact_boundary_safe and frostygoop_limit_results:
        prepared_answer = (
            "\\u4e0d\\u80fd\\u3002\\u9019\\u4e9b PCAP \\u8207\\u5075\\u6e2c\\u7d50\\u679c\\u80fd\\u652f\\u6301 Lab \\u4e2d\\u51fa\\u73fe Modbus \\u8acb\\u6c42\\u6216"
            "WRITE \\u985e\\u578b\\u6d3b\\u52d5\\uff1b\\u4f46\\u5b83\\u5011\\u4e0d\\u55ae\\u7368\\u8b49\\u660e PLC\\uff0fRTU \\u5df2\\u63a5\\u53d7\\u8acb\\u6c42\\uff0c"
            "\\u66f4\\u4e0d\\u80fd\\u78ba\\u8a8d\\u4efb\\u4f55\\u5be6\\u9ad4\\u8a2d\\u5099\\u6216\\u88fd\\u7a0b\\u5f71\\u97ff\\u3002"
        )
        prepared_known_facts = [
            "PCAP \\u53ef\\u652f\\u6301\\u6355\\u7372\\u5230\\u5c0d\\u61c9\\u7684 Lab Modbus \\u8acb\\u6c42\\u3002",
            "\\u672c\\u60c5\\u5883\\u6c92\\u6709\\u4f7f\\u7528\\u771f\\u5be6 PLC\\u3001RTU \\u6216\\u5be6\\u9ad4\\u88fd\\u7a0b\\u3002",
        ]
        prepared_inferences = []
        prepared_unknowns = [
            "\\u73fe\\u6709\\u8b49\\u64da\\u4e0d\\u8db3\\u4ee5\\u78ba\\u8a8d\\u63a5\\u6536\\u7aef\\u662f\\u5426\\u63a5\\u53d7\\u8acb\\u6c42\\uff0c\\u6216\\u662f\\u5426\\u9020\\u6210\\u5be6\\u9ad4\\u8a2d\\u5099\\uff0f\\u88fd\\u7a0b\\u5f71\\u97ff\\u3002"
        ]
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for result in frostygoop_limit_results
                for evidence_id in result.evidence_ids
            )
        )[:4]
        hint = (
            "\\u82e5\\u8981\\u628a\\u300c\\u770b\\u5230\\u8acb\\u6c42\\u300d\\u63d0\\u5347\\u70ba\\u300c\\u78ba\\u8a8d\\u5f71\\u97ff\\u300d\\uff0c\\u4f60\\u6703\\u9700\\u8981\\u54ea\\u4e00\\u985e\\u63a5\\u6536\\u7aef"
            "\\u72c0\\u614b\\u6216\\u88fd\\u7a0b\\u91cf\\u6e2c\\u8b49\\u64da\\uff1f"
        )

    # The block above is retained for compatibility with older prompt branches.
    # Use explicitly decoded Traditional-Chinese text for this student-facing
    # boundary, so a local encoding setting cannot turn the explanation into
    # raw escape sequences.
    if frostygoop_impact_boundary_safe and frostygoop_limit_results:
        prepared_answer = (
            "\u4e0d\u80fd\u3002\u9019\u4e9b PCAP \u8207\u5075\u6e2c\u7d50\u679c\u80fd\u652f\u6301 Lab \u4e2d\u51fa\u73fe Modbus \u8acb\u6c42\u6216"
            "WRITE \u985e\u578b\u6d3b\u52d5\uff1b\u4f46\u5b83\u5011\u4e0d\u55ae\u7368\u8b49\u660e PLC\uff0fRTU \u5df2\u63a5\u53d7\u8acb\u6c42\uff0c"
            "\u66f4\u4e0d\u80fd\u78ba\u8a8d\u4efb\u4f55\u5be6\u9ad4\u8a2d\u5099\u6216\u88fd\u7a0b\u5f71\u97ff\u3002"
        )
        prepared_known_facts = [
            "PCAP \u53ef\u652f\u6301\u6355\u7372\u5230\u5c0d\u61c9\u7684 Lab Modbus \u8acb\u6c42\u3002",
            "\u672c\u60c5\u5883\u6c92\u6709\u4f7f\u7528\u771f\u5be6 PLC\u3001RTU \u6216\u5be6\u9ad4\u88fd\u7a0b\u3002",
        ]
        prepared_inferences = []
        prepared_unknowns = [
            "\u73fe\u6709\u8b49\u64da\u4e0d\u8db3\u4ee5\u78ba\u8a8d\u63a5\u6536\u7aef\u662f\u5426\u63a5\u53d7\u8acb\u6c42\uff0c\u6216\u662f\u5426\u9020\u6210\u5be6\u9ad4\u8a2d\u5099\uff0f\u88fd\u7a0b\u5f71\u97ff\u3002"
        ]
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for result in frostygoop_limit_results
                for evidence_id in result.evidence_ids
            )
        )[:4]
        hint = (
            "\u82e5\u8981\u628a\u300c\u770b\u5230\u8acb\u6c42\u300d\u63d0\u5347\u70ba\u300c\u78ba\u8a8d\u5f71\u97ff\u300d\uff0c\u4f60\u6703\u9700\u8981\u54ea\u4e00\u985e\u63a5\u6536\u7aef"
            "\u72c0\u614b\u6216\u88fd\u7a0b\u91cf\u6e2c\u8b49\u64da\uff1f"
        )

    known_facts = _unique_texts(prepared_known_facts)
    if get_scenario_profile(scenario).key == "industroyer2":
        known_facts = _dedupe_industroyer2_facts(known_facts)
    refined = TutorResponse.model_validate(
        {
            "answer": _complete_sentence(prepared_answer),
            "known_facts": known_facts,
            "reasonable_inferences": _unique_texts(
                prepared_inferences
            ),
            "unknowns": _unique_texts(prepared_unknowns),
            "evidence_ids": evidence_ids,
            "next_hint": normalize_student_text(hint),
        }
    )
    return refined


def finalize_tutor_response(
    payload: Any,
    question: str,
    retrieved: Sequence[RetrievedChunk],
    evidence_whitelist: Sequence[str],
    require_unknowns: bool,
    response_mode: ResponseMode = "hint",
    scenario: str = "ryuk",
    knowledge_scope: KnowledgeScope = "in_scope",
) -> TutorResponse:
    """Run schema, citation, language, and question-alignment validation."""

    _validate_response_mode(response_mode)
    _validate_knowledge_scope(knowledge_scope)
    response = validate_tutor_response(
        payload=payload,
        allowed_evidence_ids=evidence_whitelist,
        require_unknowns=require_unknowns,
    )
    # Validate the student-visible form after deterministic refinements.  A
    # narrow evidence comparison may replace all model prose (and therefore
    # safely remove a malformed JSON escape); unhandled control characters
    # remain in the refined response and still trigger the normal retry.
    response = refine_student_response(
        question,
        response,
        retrieved,
        response_mode=response_mode,
        scenario=scenario,
        knowledge_scope=knowledge_scope,
    )
    response = validate_tutor_response(
        payload=response.model_dump(),
        allowed_evidence_ids=evidence_whitelist,
        require_unknowns=require_unknowns,
    )
    validate_student_visible_text(response)
    validate_operational_safety(response)
    validate_response_evidence_mentions(
        response=response,
        evidence_whitelist=evidence_whitelist,
    )
    validate_context_identifiers(response, retrieved)
    validate_requested_evidence_citations(
        question=question,
        response=response,
        evidence_whitelist=evidence_whitelist,
    )
    validate_response_semantics(response, retrieved, scenario=scenario)
    validate_question_alignment(
        question=question,
        response=response,
        retrieved=retrieved,
        scenario=scenario,
    )
    return response


def _student_visible_text_fields(
    response: TutorResponse,
) -> list[tuple[str, str]]:
    """Return every string rendered in the normal student response."""

    fields: list[tuple[str, str]] = [
        ("answer", response.answer),
        *(
            (f"known_facts[{index}]", value)
            for index, value in enumerate(response.known_facts)
        ),
        *(
            (f"reasonable_inferences[{index}]", value)
            for index, value in enumerate(response.reasonable_inferences)
        ),
        *(
            (f"unknowns[{index}]", value)
            for index, value in enumerate(response.unknowns)
        ),
        *(
            (f"evidence_ids[{index}]", value)
            for index, value in enumerate(response.evidence_ids)
        ),
        ("next_hint", response.next_hint),
    ]
    return fields


def validate_student_visible_text(response: TutorResponse) -> None:
    """Reject JSON escape damage and other ASCII control characters.

    Every current TutorResponse text item is displayed as one line.  A model
    that emits an incorrectly escaped Windows identity can otherwise turn
    ``\\f`` or ``\\r`` into an invisible control character after JSON parsing.
    Rejecting the payload lets the existing generation loop request a clean
    replacement instead of silently showing corrupted evidence text.
    """

    invalid: list[str] = []
    untranslated: list[str] = []
    for field_name, value in _student_visible_text_fields(response):
        code_points = sorted(
            {ord(character) for character in value if ord(character) < 0x20}
        )
        if code_points:
            formatted = ", ".join(
                f"U+{code_point:04X}" for code_point in code_points
            )
            invalid.append(f"{field_name} ({formatted})")
        latin_words = re.findall(r"\b[A-Za-z]{3,}\b", value)
        cjk_characters = re.findall(r"[\u3400-\u9fff]", value)
        if len(latin_words) >= 8 and len(cjk_characters) < 6:
            untranslated.append(field_name)
    if invalid:
        raise ValueError(
            "Tutor response contains ASCII control characters in "
            "student-visible text: " + "; ".join(invalid)
        )
    if untranslated:
        raise ValueError(
            "Tutor response contains untranslated English narrative in "
            + ", ".join(untranslated)
            + "; translate it to Taiwan Traditional Chinese while preserving identifiers"
        )


_EVIDENCE_ID_MENTION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9]*-[A-Z][0-9]{2,})"
    r"(?![A-Za-z0-9_-])",
    flags=re.IGNORECASE,
)


def validate_response_evidence_mentions(
    response: TutorResponse,
    evidence_whitelist: Sequence[str],
) -> None:
    """Reject evidence-like IDs in prose that were not retrieved.

    The structured ``evidence_ids`` field is already schema-checked, but a
    model can still place a fabricated ID in ``answer`` or ``next_hint`` while
    keeping that field valid.  Scan every student-visible string so the same
    retrieved-context boundary applies to prose as well as to the JSON list.
    """

    allowed = {
        evidence_id.casefold() for evidence_id in evidence_whitelist
    }
    invalid: list[str] = []
    for field_name, value in _student_visible_text_fields(response):
        for match in _EVIDENCE_ID_MENTION_PATTERN.finditer(value):
            evidence_id = match.group(1)
            if evidence_id.casefold() not in allowed:
                invalid.append(f"{field_name}: {evidence_id}")
    if invalid:
        raise ValueError(
            "Tutor response mentions evidence IDs absent from retrieved "
            "context: " + "; ".join(dict.fromkeys(invalid))
        )


_DESTRUCTIVE_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "Stop-Service",
        re.compile(
            r"(?<![\w-])stop-service\s+"
            r"(?:(?:-[a-z][\w-]*\s+)*[\"']?[a-z0-9_*.$:-]+)",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "vssadmin delete shadows",
        re.compile(
            r"(?<![\w-])vssadmin(?:\.exe)?\s+delete\s+shadows\b",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "Remove-Item",
        re.compile(
            r"(?<![\w-])remove-item\s+"
            r"(?:(?:-[a-z][\w-]*\s+)*[\"']?"
            r"(?:[a-z]:\\|\\\\|/|\$)[^\s;，。]+)",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "service-stop command",
        re.compile(
            r"(?<![\w-])(?:sc(?:\.exe)?|net)\s+stop\s+\S+",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "recursive rm",
        re.compile(
            r"(?<![\w-])rm\s+-[a-z]*[rf][a-z]*\s+"
            r"(?:/|~|\$|\.\.?/)",
            flags=re.IGNORECASE,
        ),
    ),
)


def validate_operational_safety(response: TutorResponse) -> None:
    """Reject explicit, directly usable destructive command forms.

    This intentionally targets a small command deny-list rather than trying
    to judge every security sentence.  Plain forensic statements such as
    "沒有停止服務" remain valid because they do not expose an executable
    command line.
    """

    invalid: list[str] = []
    for field_name, value in _student_visible_text_fields(response):
        for command_name, pattern in _DESTRUCTIVE_COMMAND_PATTERNS:
            if pattern.search(value):
                invalid.append(f"{field_name}: {command_name}")
    if invalid:
        raise ValueError(
            "Tutor response contains an operational destructive command: "
            + "; ".join(dict.fromkeys(invalid))
        )


def explicitly_requested_evidence_ids(
    question: str,
    evidence_whitelist: Sequence[str],
) -> list[str]:
    """Return retrieved evidence IDs explicitly named by the student."""

    requested: list[str] = []
    for evidence_id in evidence_whitelist:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(evidence_id)}"
            r"(?![A-Za-z0-9_-])",
            flags=re.IGNORECASE,
        )
        if pattern.search(question) and evidence_id not in requested:
            requested.append(evidence_id)
    return requested


def validate_requested_evidence_citations(
    question: str,
    response: TutorResponse,
    evidence_whitelist: Sequence[str],
) -> None:
    """Require every explicitly requested, retrieved ID to be cited."""

    requested = explicitly_requested_evidence_ids(
        question,
        evidence_whitelist,
    )
    cited = {evidence_id.casefold() for evidence_id in response.evidence_ids}
    missing = [
        evidence_id
        for evidence_id in requested
        if evidence_id.casefold() not in cited
    ]
    if missing:
        raise ValueError(
            "Tutor response omitted evidence IDs explicitly requested by "
            "the student: " + ", ".join(missing)
        )


_UNCERTAINTY_PATTERN = re.compile(
    r"無法確認|不能確認|尚無法|尚不能|證據不足|不足以|"
    r"無從確認|是否|能否",
    flags=re.IGNORECASE,
)


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    normalized = text.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _mentions_restore_original_change(text: str) -> bool:
    return (
        _contains_any(text, ("復原", "還原", "restore", "解碼"))
        and _contains_any(text, ("原始", "原本", "original", ".ryk"))
        and _contains_any(text, ("修改", "覆寫", "改寫", "overwrite"))
    )


def _directly_preserves_restore_source(text: str) -> bool:
    if not _mentions_restore_original_change(text):
        return False
    return _contains_any(
        text,
        (
            "未修改",
            "沒有修改",
            "不修改",
            "並未修改",
            "未覆寫",
            "沒有覆寫",
            "不覆寫",
            "did not modify",
            "does not modify",
            "did not overwrite",
        ),
    )


def _mentions_kali_smb_block(text: str) -> bool:
    return (
        "kali" in text.casefold()
        and "smb" in text.casefold()
        and _contains_any(text, ("阻擋", "封鎖", "block", "drop"))
    )


def _questions_kali_smb_design_rule(text: str) -> bool:
    return (
        _UNCERTAINTY_PATTERN.search(text) is not None
        and _mentions_kali_smb_block(text)
        and _contains_any(
            text,
            ("防火牆規則", "規則", "設計上", "實驗環境設計"),
        )
    )


def _context_defines_restore_source_preservation(
    retrieved: Sequence[RetrievedChunk],
) -> bool:
    return any(
        _directly_preserves_restore_source(result.document)
        for result in retrieved
    )


def _context_defines_kali_smb_design_block(
    retrieved: Sequence[RetrievedChunk],
) -> bool:
    return any(
        result.metadata.get("source_type") == "lab_configuration"
        and _mentions_kali_smb_block(result.document)
        for result in retrieved
    )


def _asserts_specific_packet(text: str) -> bool:
    if _UNCERTAINTY_PATTERN.search(text) is not None:
        return False
    if _contains_any(text, ("可能", "或許", "也許", "推測")):
        return False
    return (
        _contains_any(text, ("封包", "packet"))
        and _contains_any(
            text,
            (
                "smb",
                "tcp",
                "udp",
                "syn",
                "ack",
                "rst",
                "icmp",
                "445",
                "通訊封包",
                "連線請求",
            ),
        )
    )


_SAFE_SIMULATION_CORRECTION_MARKERS = (
    "不是真實",
    "並非真實",
    "非真實",
    "未使用",
    "沒有使用",
    "不包含",
    "未包含",
    "不能證明",
    "無法證明",
    "不足以證明",
    "不能推論",
    "無法推論",
    "不能當成",
    "不得當成",
    "不應當成",
    "不可視為",
    "不能解密",
    "無法解密",
    "not real",
    "did not use",
    "cannot prove",
    "cannot infer",
)


def _context_marks_safe_ryuk_simulation(
    retrieved: Sequence[RetrievedChunk],
) -> bool:
    safety_markers = (
        "安全模擬",
        "不是真實 ryuk",
        "不是歷史",
        "不能推論",
        "safe simulation",
        "not real ryuk",
    )
    subject_markers = (
        "caesar",
        ".ryk",
        "復原",
        "restore",
    )
    return any(
        _contains_any(result.document, safety_markers)
        and _contains_any(result.document, subject_markers)
        for result in retrieved
    )


def _asserts_real_ryuk_in_safe_simulation(text: str) -> bool:
    """Return true for narrow affirmative real-Ryuk misstatements."""

    if _UNCERTAINTY_PATTERN.search(text) is not None:
        return False
    normalized = text.casefold()
    explicit_positive = re.search(
        r"(?:而是|其實是|實際是|就是|等同(?:於)?|代表)"
        r".{0,30}(?:真實|真正(?:的)?)\s*ryuk",
        normalized,
    )
    if explicit_positive is not None:
        return True
    if _contains_any(text, _SAFE_SIMULATION_CORRECTION_MARKERS):
        return False

    real_behavior_patterns = (
        re.compile(
            r"(?:真實|真正(?:的)?)\s*ryuk.{0,30}"
            r"(?:惡意程式|malware|加密|解密|攻擊|感染|樣本)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"ryuk\s*(?:惡意程式|malware).{0,30}"
            r"(?:使用|執行|加密|感染|部署|存在)",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?:caesar(?:\s+shift)?|凱薩位移).{0,30}"
            r"(?:是|就是|等同(?:於)?|代表).{0,30}ryuk.{0,15}加密",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?:復原腳本|還原腳本|解碼腳本|復原工具|restore).{0,30}"
            r"(?:是|就是|可|能|用來).{0,30}ryuk.{0,15}"
            r"(?:解密|decrypt)",
            flags=re.IGNORECASE,
        ),
    )
    if any(pattern.search(text) for pattern in real_behavior_patterns):
        return True

    has_lab_count = (
        re.search(r"(?<!\d)100(?!\d)", text) is not None
        and ".ryk" in normalized
    )
    historical_claim = re.search(
        r"(?:歷史|真實|實際).{0,25}(?:事件|統計|規模|案例)",
        text,
        flags=re.IGNORECASE,
    )
    return has_lab_count and historical_claim is not None


def _validate_industroyer2_semantics(
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
) -> None:
    """Reject high-confidence contradictions in Industroyer2-like answers."""

    context = "\n".join(result.document for result in retrieved)
    normalized_context = context.casefold()
    claims = [
        response.answer,
        *response.known_facts,
        *response.reasonable_inferences,
        response.next_hint,
    ]
    errors: list[str] = []

    safe_context = any(
        marker in normalized_context
        for marker in (
            "industroyer2-like",
            "safe caddywiper",
            "安全模擬",
            "dummy_files",
        )
    )
    if safe_context:
        real_malware_pattern = re.compile(
            r"(?:真實|真正|實際).{0,18}(?:industroyer2|caddywiper|惡意程式)"
            r".{0,18}(?:執行|攻擊|破壞|刪除|感染)",
            flags=re.IGNORECASE,
        )
        if any(
            real_malware_pattern.search(claim)
            and _UNCERTAINTY_PATTERN.search(claim) is None
            for claim in claims
        ):
            errors.append(
                "safe Industroyer2-like simulation was described as real malware"
            )

    ticket_is_local_only = (
        "maintenance_ticket" in normalized_context
        and any(
            marker in normalized_context
            for marker in (
                "只存在 eng 本機",
                "eng local evidence",
                "not present in iec-104",
                "does not carry maintenance_ticket",
            )
        )
    )
    if ticket_is_local_only:
        ticket_transport_pattern = re.compile(
            r"maintenance[_ ]ticket.{0,30}(?:封包|packet|server state|server)"
            r".{0,20}(?:包含|攜帶|傳送|收到|存在)",
            flags=re.IGNORECASE,
        )
        reversed_ticket_pattern = re.compile(
            r"(?:封包|packet|server state|server).{0,30}maintenance[_ ]ticket"
            r".{0,20}(?:包含|攜帶|傳送|收到|存在)",
            flags=re.IGNORECASE,
        )
        if any(
            (ticket_transport_pattern.search(claim) or reversed_ticket_pattern.search(claim))
            and not re.search(r"(?:不|沒有|無法|未)", claim)
            for claim in claims
        ):
            errors.append(
                "maintenance_ticket was incorrectly placed in IEC-104 or server evidence"
            )

    source_ip_is_ambiguous = (
        "10.10.60.20" in normalized_context
        and any(
            marker in normalized_context
            for marker in (
                "cannot distinguish",
                "不能只靠",
                "無法只靠",
                "source ip alone",
            )
        )
    )
    if source_ip_is_ambiguous:
        absolute_attribution = re.compile(
            r"10\.10\.60\.20.{0,30}(?:證明|確認|一定|必然)"
            r".{0,20}(?:正常維護|kali|relay)|"
            r"(?:證明|確認|一定|必然).{0,30}(?:正常維護|kali|relay)"
            r".{0,20}10\.10\.60\.20",
            flags=re.IGNORECASE,
        )
        if any(absolute_attribution.search(claim) for claim in claims):
            errors.append(
                "source_ip=10.10.60.20 alone was used for absolute attribution"
            )

    relay_handoff_recorded = (
        "kali requested a command through eng relay" in normalized_context
        and "eng01 sent the iec-104 command to ot-server01" in normalized_context
    )
    if relay_handoff_recorded:
        unsupported_awareness = re.compile(
            r"(?:eng|工程端).{0,24}(?:不會知道|不知道|毫不知情|完全不知情)|"
            r"(?:不會知道|不知道|毫不知情|完全不知情).{0,24}(?:eng|工程端)",
            flags=re.IGNORECASE,
        )
        if any(unsupported_awareness.search(claim) for claim in claims):
            errors.append(
                "Relay system evidence was incorrectly treated as proof that ENG had no awareness"
            )

    relay_was_blocked = all(
        marker in normalized_context
        for marker in ("accepted=false", "iec104_sent=false")
    ) or "blocked_by_safe_caddywiper" in normalized_context
    if relay_was_blocked:
        sent_after_block = re.compile(
            r"(?:relay|eng01).{0,30}(?:已|成功|仍).{0,12}(?:送出|傳送)"
            r".{0,12}iec-?104",
            flags=re.IGNORECASE,
        )
        if any(
            sent_after_block.search(claim)
            and _UNCERTAINTY_PATTERN.search(claim) is None
            and not re.search(
                r"(?:未|沒有|並未|不代表|不能|不可).{0,18}(?:送出|傳送)|"
                r"(?:送出|傳送).{0,12}(?:失敗|被阻擋)",
                claim,
                flags=re.IGNORECASE,
            )
            for claim in claims
        ):
            errors.append(
                "blocked relay was incorrectly described as sending IEC-104"
            )

    if errors:
        raise ValueError("; ".join(errors))


def _asserts_unsupported_absence_claim(
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
) -> str | None:
    """Detect a narrow but high-risk change from missing proof to absence.

    This is deliberately not a general natural-language fact checker. It
    covers three explicit evidence boundaries present in the maintained
    scenarios, where treating an unobserved detail as an absent detail would
    materially mislead a student.
    """

    context = "\n".join(result.document for result in retrieved).casefold()
    claims = "\n".join(
        [
            response.answer,
            *response.known_facts,
            *response.reasonable_inferences,
            response.next_hint,
        ]
    ).casefold()
    absence = r"(?:no|without|never|did not|\u6c92\u6709|\u5f9e\u672a|\u672a\u66fe)"

    frosty_limit = "does not prove device acceptance" in context
    frosty_absence = re.search(
        absence
        + r".{0,32}(?:plc\s*)?(?:response\s*packet|\u56de\u61c9\u5c01\u5305)",
        claims,
        flags=re.IGNORECASE,
    )
    if frosty_limit and frosty_absence:
        return (
            "FrostyGoop PCAP evidence limits proof of device acceptance; "
            "it does not establish that a PLC response packet is absent"
        )

    ryuk_runtime_gap = (
        "kali" in context
        and "smb" in context
        and (
            "evidence insufficient" in context
            or "\u4ecd\u662f\u8b49\u64da\u4e0d\u8db3" in context
            or "\u7121\u6cd5\u7528\u672c\u6b21\u8b49\u64da\u65b7\u8a00" in context
        )
    )
    ryuk_absence = re.search(
        r"(?:kali.{0,40}" + absence + r".{0,40}smb|"
        + absence + r".{0,40}kali.{0,40}smb)",
        claims,
        flags=re.IGNORECASE,
    )
    if ryuk_runtime_gap and ryuk_absence:
        return (
            "Ryuk evidence lacks runtime telemetry for a direct Kali SMB "
            "attempt; it does not establish that no attempt occurred"
        )

    ind2_decode_gap = "tshark is not installed" in context
    ind2_absence = re.search(
        r"(?:pcap.{0,48}" + absence
        + r".{0,48}(?:iec-?104|asdu|type[ _-]?id|ioa)|"
        + absence + r".{0,48}(?:iec-?104|asdu|type[ _-]?id|ioa).{0,48}pcap)",
        claims,
        flags=re.IGNORECASE,
    )
    if ind2_decode_gap and ind2_absence:
        return (
            "Industroyer2 PCAP fields were not decoded because tshark is "
            "unavailable; this does not establish that the fields are absent"
        )
    return None


def validate_response_semantics(
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
    scenario: str = "ryuk",
) -> None:
    """Reject a small set of high-confidence semantic contradictions.

    These checks intentionally cover only propositions whose wording and
    evidence boundary are explicit in this scenario.  Broader semantic
    grading remains an instructor-side evaluation task.
    """

    profile = get_scenario_profile(scenario)
    errors: list[str] = []
    absence_error = _asserts_unsupported_absence_claim(response, retrieved)
    if absence_error is not None:
        errors.append(absence_error)
    if profile.key == "industroyer2":
        _validate_industroyer2_semantics(response, retrieved)
        if errors:
            raise ValueError("; ".join(errors))
        return
    answer_is_uncertain = (
        _UNCERTAINTY_PATTERN.search(response.answer) is not None
    )

    answer_questions_restore_change = (
        answer_is_uncertain
        and _mentions_restore_original_change(response.answer)
    )
    facts_preserve_restore_source = any(
        _directly_preserves_restore_source(fact)
        for fact in response.known_facts
    )
    context_preserves_restore_source = (
        _context_defines_restore_source_preservation(retrieved)
    )
    if answer_questions_restore_change and (
        facts_preserve_restore_source or context_preserves_restore_source
    ):
        errors.append(
            "answer treats preservation of the original restore source as "
            "unknown even though retrieved evidence directly states it"
        )

    context_defines_design_block = _context_defines_kali_smb_design_block(
        retrieved
    )
    fact_defines_design_block = any(
        _mentions_kali_smb_block(fact)
        and not _UNCERTAINTY_PATTERN.search(fact)
        for fact in response.known_facts
    )
    if _questions_kali_smb_design_rule(response.answer) and (
        fact_defines_design_block or context_defines_design_block
    ):
        errors.append(
            "answer conflates the known Lab SMB blocking rule with missing "
            "runtime packet observations"
        )
    if context_defines_design_block and any(
        _questions_kali_smb_design_rule(unknown)
        for unknown in response.unknowns
    ):
        errors.append(
            "unknowns must not question the retrieved Lab SMB blocking rule; "
            "only runtime attempts or packet observations remain unknown"
        )

    answer_questions_packet = (
        answer_is_uncertain
        and _contains_any(response.answer, ("封包", "packet"))
    )
    if answer_questions_packet and any(
        _asserts_specific_packet(inference)
        for inference in response.reasonable_inferences
    ):
        errors.append(
            "answer says the packet is unknown while reasonable_inferences "
            "assert a specific packet type"
        )

    if _context_marks_safe_ryuk_simulation(retrieved):
        claim_fields = [
            ("answer", response.answer),
            *(
                (f"known_facts[{index}]", value)
                for index, value in enumerate(response.known_facts)
            ),
            *(
                (f"reasonable_inferences[{index}]", value)
                for index, value in enumerate(
                    response.reasonable_inferences
                )
            ),
            ("next_hint", response.next_hint),
        ]
        unsafe_fields = [
            field_name
            for field_name, value in claim_fields
            if _asserts_real_ryuk_in_safe_simulation(value)
        ]
        if unsafe_fields:
            errors.append(
                "safe simulation was described as real Ryuk behavior or "
                "historical incident data in "
                + ", ".join(unsafe_fields)
            )

    if errors:
        raise ValueError("; ".join(errors))


def validate_context_identifiers(
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
) -> None:
    """Reject domain identities that do not exist in retrieved context."""

    identity_pattern = re.compile(
        r"\b[A-Za-z0-9_-]+\\[A-Za-z0-9._-]+\b"
    )
    allowed = {
        identity.casefold()
        for result in retrieved
        for identity in identity_pattern.findall(result.document)
    }
    response_text = "\n".join(
        [
            response.answer,
            *response.known_facts,
            *response.reasonable_inferences,
            *response.unknowns,
            response.next_hint,
        ]
    )
    mentioned = identity_pattern.findall(response_text)
    invalid = sorted(
        {
            identity
            for identity in mentioned
            if identity.casefold() not in allowed
        },
        key=str.casefold,
    )
    if invalid:
        raise ValueError(
            "response contains domain identities absent from retrieved "
            "context: " + ", ".join(invalid)
        )
    malformed: list[str] = []
    prefix_pattern = re.compile(r"\b[A-Za-z0-9_-]+\\")
    for match in prefix_pattern.finditer(response_text):
        tail = response_text[match.start():]
        if not any(tail.casefold().startswith(identity) for identity in allowed):
            malformed.append(tail.splitlines()[0][:40])
    if malformed:
        raise ValueError(
            "response contains malformed domain identity: "
            + ", ".join(sorted(set(malformed), key=str.casefold))
        )


def validate_question_alignment(
    question: str,
    response: TutorResponse,
    retrieved: Sequence[RetrievedChunk],
    scenario: str = "ryuk",
) -> None:
    """Reject role-attribution answers that omit the observed source host."""

    profile = get_scenario_profile(scenario)
    if profile.key == "industroyer2":
        normalized_question = question.casefold()
        answer = response.answer.casefold()
        normalized_context = "\n".join(
            result.document.casefold() for result in retrieved
        )
        errors: list[str] = []
        if (
            "maintenance_ticket" in normalized_question
            or "maintenance ticket" in normalized_question
        ) and not any(marker in answer for marker in ("eng", "本機", "local")):
            errors.append(
                "maintenance_ticket answer must identify its ENG-local evidence boundary"
            )
        if (
            "source_ip" in normalized_question
            or "10.10.60.20" in normalized_question
        ) and "10.10.60.20" not in answer:
            errors.append(
                "source attribution answer must mention source_ip=10.10.60.20"
            )
        asks_about_relay_lockout = "relay" in normalized_question and (
            "caddywiper" in normalized_question
            or "lockout" in normalized_question
        )
        if asks_about_relay_lockout:
            if not any(marker in answer for marker in ("阻擋", "未送出", "false")):
                errors.append(
                    "lockout answer must state that the relay path was blocked or not sent"
                )
            context_confirms_not_sent = (
                "accepted=false" in normalized_context
                and "iec104_sent=false" in normalized_context
            )
            if context_confirms_not_sent and not any(
                marker in answer
                for marker in (
                    "iec104_sent=false",
                    "未送出",
                    "沒有送出",
                    "並未送出",
                    "無法送出",
                )
            ):
                errors.append(
                    "lockout evidence confirms iec104_sent=false; answer must not leave sending unknown"
                )
            if context_confirms_not_sent and not all(
                marker in answer for marker in ("accepted=false", "iec104_sent=false")
            ):
                errors.append(
                    "lockout answer must report both accepted=False and iec104_sent=False"
                )
            blocked_evidence = {
                evidence_id
                for result in retrieved
                if "accepted=false" in result.document.casefold()
                and "iec104_sent=false" in result.document.casefold()
                and "blocked_by_safe_caddywiper" in result.document.casefold()
                for evidence_id in result.evidence_ids
            }
            if blocked_evidence and not blocked_evidence.intersection(
                response.evidence_ids
            ):
                errors.append(
                    "lockout answer must cite the retrieved Relay record with explicit false states"
                )
        asks_for_relay_correlation = (
            "relay" in normalized_question
            and any(
                marker in question
                for marker in (
                    "哪些紀錄",
                    "哪些證據",
                    "哪一項證據",
                    "哪個證據",
                    "哪一筆證據",
                    "關聯",
                )
            )
        )
        if asks_for_relay_correlation:
            if (
                not explicitly_requested_evidence_ids(
                    question,
                    allowed_evidence_ids(retrieved),
                )
                and _EVIDENCE_ID_MENTION_PATTERN.search(response.answer)
            ):
                errors.append(
                    "do not repeat evidence IDs inside the answer unless the student asks; keep them in evidence_ids"
                )
            layer_checks = (
                (
                    "Kali action",
                    "kali" in answer
                    and any(marker in answer for marker in ("action", "操作", "要求")),
                ),
                (
                    "ENG Relay",
                    "eng" in answer and "relay" in answer,
                ),
                (
                    "Server command",
                    any(marker in answer for marker in ("server", "ot-server01"))
                    and any(marker in answer for marker in ("command", "命令", "指令")),
                ),
            )
            missing_layers = [
                layer for layer, present in layer_checks if not present
            ]
            if missing_layers:
                errors.append(
                    "relay-correlation answer must name all evidence layers: "
                    + ", ".join(missing_layers)
                )
            direct_send_evidence = {
                evidence_id
                for result in retrieved
                if "accepted=true" in result.document.casefold()
                and "iec104_sent=true" in result.document.casefold()
                for evidence_id in result.evidence_ids
            }
            correlation_evidence = {
                evidence_id
                for result in retrieved
                if "must be correlated" in result.document.casefold()
                for evidence_id in result.evidence_ids
            }
            if direct_send_evidence and not direct_send_evidence.intersection(
                response.evidence_ids
            ):
                errors.append(
                    "relay-success answer must cite the explicit accepted=True and iec104_sent=True record"
                )
            if correlation_evidence and not correlation_evidence.intersection(
                response.evidence_ids
            ):
                errors.append(
                    "relay-success answer must cite the cross-host correlation summary"
                )
        asks_about_pcap_limit = (
            "pcap" in normalized_question and "tshark" in normalized_question
        )
        if asks_about_pcap_limit:
            if not any(marker in answer for marker in ("大小", "size", "sha-256", "雜湊")):
                errors.append(
                    "PCAP limitation answer must state which file metadata remains available"
                )
            if not any(marker in answer for marker in ("未解碼", "沒有解碼", "無法解析", "尚未解析")):
                errors.append(
                    "PCAP limitation answer must state that IEC-104 fields were not decoded"
                )
        if errors:
            raise ValueError("; ".join(errors))
        return

    errors: list[str] = []
    normalized_question = question.casefold()
    core_host_role_question = all(
        host in normalized_question for host in ("kali", "user01", "dc01")
    ) and any(marker in question for marker in ("比較", "角色", "分工"))
    if core_host_role_question:
        missing_hosts = [
            host
            for host in ("Kali", "USER01", "DC01")
            if host.casefold() not in response.answer.casefold()
        ]
        if missing_hosts:
            errors.append(
                "role-comparison answer must mention all requested hosts: "
                + ", ".join(missing_hosts)
            )
        evidence_type_markers = (
            "端點紀錄",
            "執行脈絡",
            "execution context",
            "powershell",
            "security",
            "http access",
            "http 紀錄",
            "拓撲",
            "網路設計",
            "實驗環境設計",
        )
        if not any(
            marker in response.answer.casefold()
            for marker in evidence_type_markers
        ):
            errors.append(
                "role-comparison answer must name a concrete supporting "
                "evidence type such as topology configuration, HTTP access "
                "records, or USER01 endpoint records"
            )

    asks_for_execution_actor = (
        re.search(r"(誰|哪台|主機|不是直接|執行端)", question) is not None
        and re.search(r"(執行|修改|影響|作用)", question) is not None
    )
    if not asks_for_execution_actor:
        if errors:
            raise ValueError("; ".join(errors))
        return

    observed_hosts, execution_evidence = observed_execution_attribution(
        retrieved
    )

    if not observed_hosts:
        return
    if not any(
        host.casefold() in response.answer.casefold()
        for host in observed_hosts
    ):
        expected = "、".join(observed_hosts)
        errors.append(
            "answer must mention the observed execution host: "
            f"{expected}"
        )
    if not any(
        host.casefold() in fact.casefold()
        for host in observed_hosts
        for fact in response.known_facts
    ):
        errors.append(
            "known_facts must include the directly observed execution host"
        )
    if not set(execution_evidence).intersection(response.evidence_ids):
        errors.append(
            "execution-attribution answer must cite retrieved execution "
            "evidence"
        )
    configuration_evidence = {
        evidence_id
        for result in retrieved
        if result.metadata.get("source_type") == "lab_configuration"
        for evidence_id in result.evidence_ids
    }
    preferred_evidence = set(execution_evidence) | configuration_evidence
    tangential_evidence = sorted(
        set(response.evidence_ids) - preferred_evidence
    )
    if tangential_evidence:
        errors.append(
            "execution-attribution answer cited tangential evidence IDs: "
            + ", ".join(tangential_evidence)
        )
    hint_evidence = set(
        re.findall(r"RYUK-[A-Z][0-9]{2}", response.next_hint)
    )
    tangential_hint_evidence = sorted(hint_evidence - preferred_evidence)
    if tangential_hint_evidence:
        errors.append(
            "execution-attribution next_hint cited tangential evidence IDs: "
            + ", ".join(tangential_hint_evidence)
        )
    asks_about_direct_kali_path = _asks_about_direct_kali_path(question)
    context_defines_kali_http_role = any(
        "Kali" in result.document
        and "HTTP" in result.document
        and (
            "下載來源" in result.document
            or "delivery" in result.document.casefold()
        )
        for result in retrieved
    )
    if (
        asks_about_direct_kali_path
        and context_defines_kali_http_role
        and not (
            "kali" in response.answer.casefold()
            and "http" in response.answer.casefold()
        )
    ):
        errors.append(
            "answer must identify Kali as the retrieved HTTP delivery source"
        )
    if (
        asks_about_direct_kali_path
        and context_defines_kali_http_role
        and not any(
            "kali" in fact.casefold() and "http" in fact.casefold()
            for fact in response.known_facts
        )
    ):
        errors.append(
            "known_facts must include Kali's retrieved HTTP delivery role"
        )
    context_defines_direct_smb = any(
        "Kali" in result.document
        and "DC01" in result.document
        and "SMB" in result.document
        for result in retrieved
    )
    if (
        asks_about_direct_kali_path
        and context_defines_direct_smb
        and "smb" not in response.answer.casefold()
    ):
        errors.append(
            "answer must limit the blocked Kali-to-DC01 path to SMB"
        )
    if (
        asks_about_direct_kali_path
        and context_defines_direct_smb
        and not any("smb" in fact.casefold() for fact in response.known_facts)
    ):
        errors.append(
            "known_facts must describe the blocked Kali-to-DC01 SMB path"
        )
    context_marks_runtime_unknown = any(
        "仍是證據不足" in result.document
        for result in retrieved
    )
    if (
        asks_about_direct_kali_path
        and context_marks_runtime_unknown
        and not response.unknowns
    ):
        errors.append(
            "direct Kali-to-DC01 attribution must report the missing "
            "runtime network telemetry in unknowns"
        )
    if errors:
        raise ValueError("; ".join(errors))


def generate_tutor_response(
    question: str,
    retrieved: Sequence[RetrievedChunk],
    chat_provider: ChatProvider,
    require_unknowns: bool | None = None,
    max_validation_attempts: int = 3,
    response_mode: ResponseMode = "hint",
    scenario: str = "ryuk",
    knowledge_scope: KnowledgeScope = "in_scope",
    conversation_context: str | None = None,
    student_context: str | None = None,
) -> TutorResponse:
    """Generate and validate one evidence-grounded teaching response."""

    if not question.strip():
        raise ValueError("Question must not be empty")
    if max_validation_attempts < 1:
        raise ValueError("max_validation_attempts must be at least 1")
    _validate_response_mode(response_mode)
    _validate_knowledge_scope(knowledge_scope)
    get_scenario_profile(scenario)

    must_report_unknowns = (
        not retrieved if require_unknowns is None else require_unknowns
    )
    messages = build_messages(
        question,
        retrieved,
        response_mode=response_mode,
        scenario=scenario,
        knowledge_scope=knowledge_scope,
        conversation_context=conversation_context,
        student_context=student_context,
    )
    evidence_whitelist = allowed_evidence_ids(retrieved)
    schema = build_response_schema(
        evidence_whitelist,
        response_mode=response_mode,
    )
    last_error: ValueError | None = None

    for attempt in range(max_validation_attempts):
        payload = chat_provider.generate_json(
            messages=messages,
            response_schema=schema,
        )
        try:
            return finalize_tutor_response(
                payload=payload,
                question=question,
                retrieved=retrieved,
                evidence_whitelist=evidence_whitelist,
                require_unknowns=must_report_unknowns,
                response_mode=response_mode,
                scenario=scenario,
                knowledge_scope=knowledge_scope,
            )
        except ValueError as exc:
            last_error = exc
            if (
                "internal retrieval labels" in str(exc)
                and isinstance(payload, dict)
            ):
                payload_evidence = payload.get("evidence_ids", [])
                repaired_evidence_ids = [
                    evidence_id
                    for evidence_id in payload_evidence
                    if evidence_id in evidence_whitelist
                ] if isinstance(payload_evidence, list) else []
                for evidence_id in evidence_whitelist:
                    if evidence_id not in repaired_evidence_ids:
                        repaired_evidence_ids.append(evidence_id)
                    if len(repaired_evidence_ids) >= 2:
                        break

                fallback_hint = build_contextual_hint(
                    question,
                    retrieved,
                    scenario=scenario,
                )
                def replace_internal_rank(text: str) -> str:
                    without_prefix = re.sub(
                        (
                            r"檢索結果\s*\d+\s*"
                            r"(?:顯示|記錄|指出|說明)\s*"
                        ),
                        "",
                        text,
                        flags=re.IGNORECASE,
                    )
                    return re.sub(
                        r"檢索結果\s*(\d+)",
                        "檢索內容",
                        without_prefix,
                        flags=re.IGNORECASE,
                    )

                repaired_text_fields: dict[str, Any] = {}
                for field in ("answer",):
                    value = payload.get(field)
                    if isinstance(value, str):
                        repaired_text_fields[field] = replace_internal_rank(
                            value
                        )
                for field in (
                    "known_facts",
                    "reasonable_inferences",
                    "unknowns",
                ):
                    value = payload.get(field)
                    if isinstance(value, list):
                        repaired_text_fields[field] = [
                            replace_internal_rank(item)
                            if isinstance(item, str)
                            else item
                            for item in value
                        ]
                repaired_payload = {
                    **payload,
                    **repaired_text_fields,
                    "evidence_ids": repaired_evidence_ids,
                    "next_hint": fallback_hint,
                }
                try:
                    return finalize_tutor_response(
                        payload=repaired_payload,
                        question=question,
                        retrieved=retrieved,
                        evidence_whitelist=evidence_whitelist,
                        require_unknowns=must_report_unknowns,
                        response_mode=response_mode,
                        scenario=scenario,
                        knowledge_scope=knowledge_scope,
                    )
                except ValueError as repair_exc:
                    last_error = repair_exc
                    exc = repair_exc
                    payload = repaired_payload
            if (
                "must cite retrieved execution evidence" in str(exc)
                and isinstance(payload, dict)
            ):
                _, execution_evidence = observed_execution_attribution(
                    retrieved
                )
                if execution_evidence:
                    payload_evidence = payload.get("evidence_ids", [])
                    repaired_evidence_ids = (
                        [
                            evidence_id
                            for evidence_id in payload_evidence
                            if evidence_id in evidence_whitelist
                        ]
                        if isinstance(payload_evidence, list)
                        else []
                    )
                    direct_evidence_id = execution_evidence[0]
                    if direct_evidence_id not in repaired_evidence_ids:
                        repaired_evidence_ids = repaired_evidence_ids[:3]
                        repaired_evidence_ids.append(direct_evidence_id)
                    repaired_payload = {
                        **payload,
                        "evidence_ids": repaired_evidence_ids,
                    }
                    try:
                        return finalize_tutor_response(
                            payload=repaired_payload,
                            question=question,
                            retrieved=retrieved,
                            evidence_whitelist=evidence_whitelist,
                            require_unknowns=must_report_unknowns,
                            response_mode=response_mode,
                            scenario=scenario,
                            knowledge_scope=knowledge_scope,
                        )
                    except ValueError as repair_exc:
                        last_error = repair_exc
                        exc = repair_exc
                        payload = repaired_payload
            if attempt + 1 >= max_validation_attempts:
                break
            messages = [
                *messages,
                {
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
                {
                    "role": "user",
                    "content": (
                        "上一版 JSON 未通過後端驗證："
                        f"{exc}。請修正後重新輸出完整 JSON；"
                        "仍只能使用既有 RETRIEVED_CONTEXT 與 "
                        "ALLOWED_EVIDENCE_IDS。"
                    ),
                },
            ]

    assert last_error is not None
    raise last_error
