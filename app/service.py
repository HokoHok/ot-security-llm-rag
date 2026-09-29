from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from app.adequacy import (
    EvidenceAdequacy,
    assess_evidence_adequacy,
    build_insufficient_evidence_response,
)
from app.config import Settings, collection_name_for_scenario, load_settings
from app.embeddings import OllamaEmbeddingClient
from app.generation import (
    ChatProvider,
    OllamaCallMetrics,
    OllamaChatClient,
    ResponseMode,
    generate_tutor_response,
)
from app.retrieval import HybridRetriever, RetrievedChunk
from app.schemas import TutorResponse
from app.scope import (
    QuestionScope,
    build_ambiguous_response,
    build_scope_response,
    classify_question_scope,
    depends_on_missing_conversation_context,
    normalize_question_identifiers,
    scope_uses_retrieval,
)

ProgressCallback = Callable[[str], None]

_GENERAL_REFERENCE_SOURCE_TYPES = (
    "public_reference",
    "teaching_convention",
)

_MINIMAL_CONTEXT_FOLLOW_UP_PATTERN = re.compile(
    r"^\s*(?:那個|這個|它|他|前一個|後一個|上一個)"
    r"(?:是什麼|呢|怎麼樣|有什麼)?[？?！!。．…]*\s*$"
)

_LAB_ENTITY_REFERENCE_PATTERN = re.compile(
    r"\b(?:Kali|USER01|DC01|SENSOR01|ENG01|OT-SERVER01|PLC|RTU)\b",
    re.IGNORECASE,
)

_SELF_CONTAINED_ENTITY_PATTERNS = (
    ("kali", re.compile(r"\bKali\b", re.IGNORECASE)),
    ("eng", re.compile(r"\bENG(?:01)?\b", re.IGNORECASE)),
    (
        "server",
        re.compile(r"\b(?:OT[- ]?)?SERVER(?:01)?\b", re.IGNORECASE),
    ),
    ("user01", re.compile(r"\bUSER01\b", re.IGNORECASE)),
    ("dc01", re.compile(r"\bDC01\b", re.IGNORECASE)),
    ("sensor01", re.compile(r"\bSENSOR01\b", re.IGNORECASE)),
)


def _is_self_contained_follow_up(question: str) -> bool:
    """Return whether a discourse-style follow-up names its own entities.

    Phrases such as ``前一輪提到的`` do not actually require history when the
    remainder already names Kali, ENG, and Server.  Treating such a question as
    dependent needlessly mixes the previous retrieval intent into the new one.
    """

    entities = {
        name
        for name, pattern in _SELF_CONTAINED_ENTITY_PATTERNS
        if pattern.search(question)
    }
    return len(entities) >= 2


def _external_reference_is_ambiguous(
    question: str,
    previous_question: str | None,
) -> bool:
    """Reject a bare external pronoun when its prior turn has many entities.

    Open WebUI supplies a message transcript rather than a trusted conversation
    identifier. When the previous student question names Kali and DC01, for
    example, a follow-up beginning with ``那它`` has no safe unique antecedent.
    Request clarification instead of letting retrieval or the model choose one.
    """

    if not previous_question or not depends_on_missing_conversation_context(question):
        return False
    entities = {
        match.group(0).casefold()
        for match in _LAB_ENTITY_REFERENCE_PATTERN.finditer(previous_question)
    }
    return len(entities) > 1


@dataclass(frozen=True)
class TutorSession:
    """One completed question, including retrieval trace and validated answer."""

    question: str
    scenario: str
    retrieved: list[RetrievedChunk]
    response: TutorResponse
    retrieval_seconds: float = 0.0
    generation_seconds: float = 0.0
    generation_attempts: int | None = None
    ollama_metrics: tuple[OllamaCallMetrics, ...] = ()
    cache_hit: bool = False
    question_scope: QuestionScope = "in_scope"
    evidence_adequacy: EvidenceAdequacy | None = None
    response_mode: ResponseMode = "hint"
    conversation_context_used: bool = False
    conversation_turns_available: int = 0


class TutorService:
    """Shared orchestration layer for CLI and desktop UI."""

    def __init__(
        self,
        retriever: HybridRetriever,
        chat_provider: ChatProvider,
        top_k: int = 4,
        response_cache_size: int = 32,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if response_cache_size < 0:
            raise ValueError("response_cache_size must not be negative")
        self.retriever = retriever
        self.chat_provider = chat_provider
        self.top_k = top_k
        self.response_cache_size = response_cache_size
        self._response_cache: OrderedDict[
            tuple[str, str, str], TutorSession
        ] = OrderedDict()
        self._recent_sessions: list[TutorSession] = []

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        scenario: str = "ryuk",
    ) -> TutorService:
        resolved = settings or load_settings()
        collection_name = collection_name_for_scenario(
            resolved.collection_name,
            scenario,
        )
        embedder = OllamaEmbeddingClient(
            resolved.ollama_base_url,
            resolved.embed_model,
        )
        retriever = HybridRetriever.from_persistent_chroma(
            chroma_path=str(resolved.chroma_path),
            collection_name=collection_name,
            embedder=embedder,
            dense_weight=resolved.dense_weight,
            sparse_weight=resolved.sparse_weight,
        )
        chat_provider = OllamaChatClient(
            base_url=resolved.ollama_base_url,
            model=resolved.chat_model,
            num_ctx=resolved.num_ctx,
            num_predict=resolved.num_predict,
            temperature=resolved.temperature,
            keep_alive=resolved.ollama_keep_alive,
        )
        return cls(
            retriever=retriever,
            chat_provider=chat_provider,
            top_k=resolved.top_k,
            response_cache_size=resolved.response_cache_size,
        )

    def ask(
        self,
        question: str,
        scenario: str = "ryuk",
        response_mode: ResponseMode = "hint",
        progress: ProgressCallback | None = None,
        previous_question: str | None = None,
    ) -> TutorSession:
        """Run deterministic retrieval followed by validated generation.

        ``previous_question`` is a non-evidentiary one-turn hint from a
        presentation client. It can resolve an explicit pronoun in the latest
        question, but every event claim still requires fresh retrieval.
        """

        if not question.strip():
            raise ValueError("Question must not be empty")
        if response_mode not in {"hint", "explain"}:
            raise ValueError("response_mode must be 'hint' or 'explain'")

        notify = progress or (lambda _message: None)
        processing_question = normalize_question_identifiers(question)
        conversation_context, retrieval_question = self._conversation_context_for(
            processing_question,
            scenario,
            previous_question=previous_question,
        )
        conversation_context_used = conversation_context is not None
        conversation_turns_available = (
            1
            if previous_question and previous_question.strip()
            else len(self._recent_sessions)
        )
        cache_key = (
            scenario.casefold(),
            response_mode,
            " ".join(retrieval_question.casefold().split()),
        )
        cached = self._response_cache.get(cache_key)
        if cached is not None:
            self._response_cache.move_to_end(cache_key)
            notify("已使用本次工作階段快取，略過重複檢索與生成。")
            session = TutorSession(
                question=question,
                scenario=scenario,
                retrieved=cached.retrieved,
                response=cached.response,
                retrieval_seconds=0.0,
                generation_seconds=0.0,
                generation_attempts=0,
                ollama_metrics=(),
                cache_hit=True,
                question_scope=cached.question_scope,
                evidence_adequacy=cached.evidence_adequacy,
                response_mode=response_mode,
                conversation_context_used=conversation_context_used,
                conversation_turns_available=conversation_turns_available,
            )
            self._remember_session(session)
            return session

        if (
            conversation_context_used
            and _external_reference_is_ambiguous(
                processing_question,
                previous_question,
            )
        ):
            notify("前一輪含多個 Lab 實體，追問代詞無法安全判定。")
            session = TutorSession(
                question=question,
                scenario=scenario,
                retrieved=[],
                response=build_ambiguous_response(
                    scenario=scenario,
                    conversation_context_available=True,
                ),
                question_scope="ambiguous",
                response_mode=response_mode,
                generation_attempts=0,
                conversation_context_used=True,
                conversation_turns_available=conversation_turns_available,
            )
            self._cache(cache_key, session)
            return session

        if (
            conversation_context_used
            and _MINIMAL_CONTEXT_FOLLOW_UP_PATTERN.fullmatch(processing_question)
        ):
            notify("追問缺少可唯一判定的主詞，請學生補充指涉對象。")
            session = TutorSession(
                question=question,
                scenario=scenario,
                retrieved=[],
                response=build_ambiguous_response(
                    scenario=scenario,
                    conversation_context_available=True,
                ),
                question_scope="ambiguous",
                response_mode=response_mode,
                generation_attempts=0,
                conversation_context_used=True,
                conversation_turns_available=conversation_turns_available,
            )
            self._cache(cache_key, session)
            return session

        question_scope = classify_question_scope(
            retrieval_question,
            scenario=scenario,
        )
        if not scope_uses_retrieval(question_scope):
            notify(f"問題範圍判定：{question_scope}。")
            notify("已略過 ChromaDB 與本機 Llama。")
            session = TutorSession(
                question=question,
                scenario=scenario,
                retrieved=[],
                response=build_scope_response(
                    question_scope,
                    scenario=scenario,
                ),
                question_scope=question_scope,
                response_mode=response_mode,
                generation_attempts=0,
            )
            self._cache(cache_key, session)
            return session

        notify("正在執行 dense 與 BM25 檢索…")
        retrieval_started = perf_counter()
        retrieved = self._retrieve_for_scope(
            question=retrieval_question,
            scenario=scenario,
            question_scope=question_scope,
        )
        retrieval_seconds = perf_counter() - retrieval_started
        notify(f"RRF 已選出 {len(retrieved)} 個 chunks。")
        evidence_adequacy = assess_evidence_adequacy(
            retrieval_question,
            retrieved,
        )
        notify(
            "證據充分性判定："
            f"{evidence_adequacy.status} "
            f"({', '.join(evidence_adequacy.reasons)})。"
        )
        if evidence_adequacy.status == "insufficient":
            notify("證據不足，已略過本機 Llama，避免模型補猜。")
            session = TutorSession(
                question=question,
                scenario=scenario,
                retrieved=retrieved,
                response=build_insufficient_evidence_response(
                    scenario=scenario,
                    question=retrieval_question,
                ),
                retrieval_seconds=retrieval_seconds,
                generation_attempts=0,
                question_scope=question_scope,
                evidence_adequacy=evidence_adequacy,
                response_mode=response_mode,
                conversation_context_used=conversation_context_used,
                conversation_turns_available=conversation_turns_available,
            )
            self._cache(cache_key, session)
            self._remember_session(session)
            return session

        notify("正在呼叫本機 Llama…")
        generation_started = perf_counter()
        request_count_before = getattr(
            self.chat_provider,
            "request_count",
            None,
        )
        metrics_history_before = len(
            getattr(self.chat_provider, "metrics_history", ())
        )
        response = generate_tutor_response(
            question=processing_question,
            retrieved=retrieved,
            chat_provider=self.chat_provider,
            require_unknowns=evidence_adequacy.require_unknowns,
            response_mode=response_mode,
            scenario=scenario,
            knowledge_scope=question_scope,
            conversation_context=conversation_context,
        )
        generation_seconds = perf_counter() - generation_started
        request_count_after = getattr(
            self.chat_provider,
            "request_count",
            None,
        )
        generation_attempts = (
            request_count_after - request_count_before
            if isinstance(request_count_before, int)
            and isinstance(request_count_after, int)
            else None
        )
        metrics_history = getattr(
            self.chat_provider,
            "metrics_history",
            (),
        )
        ollama_metrics = tuple(metrics_history[metrics_history_before:])
        notify("JSON Schema 與 evidence IDs 驗證通過。")
        session = TutorSession(
            question=question,
            scenario=scenario,
            retrieved=retrieved,
            response=response,
            retrieval_seconds=retrieval_seconds,
            generation_seconds=generation_seconds,
            generation_attempts=generation_attempts,
            ollama_metrics=ollama_metrics,
            question_scope=question_scope,
            evidence_adequacy=evidence_adequacy,
            response_mode=response_mode,
            conversation_context_used=conversation_context_used,
            conversation_turns_available=conversation_turns_available,
        )
        self._cache(cache_key, session)
        self._remember_session(session)
        return session

    def clear_conversation(self) -> None:
        """Forget the in-memory semantic context for this scenario service."""

        self._recent_sessions.clear()

    def _conversation_context_for(
        self,
        question: str,
        scenario: str,
        previous_question: str | None = None,
    ) -> tuple[str | None, str]:
        """Resolve only context-dependent follow-ups with one earlier turn.

        Previous prose is supplied only to resolve a pronoun or ordinal. Every
        answer still requires fresh retrieval and the current evidence
        whitelist, so conversation context cannot become event evidence.
        """

        if not depends_on_missing_conversation_context(question):
            return None, question
        if _is_self_contained_follow_up(question):
            return None, question
        if previous_question and previous_question.strip():
            previous_text = " ".join(previous_question.split())[:400]
            context = (
                f"上一輪學生問題：{previous_text}\n"
                "上一輪回覆摘要：由教學助理產生；僅協助理解追問指涉，"
                "不是事件證據。"
            )
            retrieval_question = f"{previous_text}\n追問：{question}"
            return context, retrieval_question
        if not self._recent_sessions:
            return None, question
        previous = self._recent_sessions[-1]
        if previous.scenario.casefold() != scenario.casefold():
            return None, question
        previous_answer = " ".join(previous.response.answer.split())[:400]
        context = (
            f"上一輪學生問題：{previous.question}\n"
            f"上一輪回覆摘要：{previous_answer}"
        )
        retrieval_question = f"{previous.question}\n追問：{question}"
        return context, retrieval_question

    def _remember_session(self, session: TutorSession) -> None:
        """Keep at most two evidence-bounded turns for natural follow-ups."""

        if not scope_uses_retrieval(session.question_scope):
            return
        self._recent_sessions.append(session)
        del self._recent_sessions[:-2]

    def _retrieve_for_scope(
        self,
        *,
        question: str,
        scenario: str,
        question_scope: QuestionScope,
    ) -> list[RetrievedChunk]:
        """Retrieve only the knowledge source appropriate to the question."""

        if question_scope == "general_concept":
            return self.retriever.retrieve(
                question=question,
                scenario=scenario,
                top_k=self.top_k,
                source_types=_GENERAL_REFERENCE_SOURCE_TYPES,
            )
        if question_scope == "comparison":
            is_iec104_sent_comparison = (
                "iec104_sent" in question.casefold() and self.top_k >= 3
            )
            if is_iec104_sent_comparison:
                event_count = 1
                concept_count = self.top_k - event_count
            else:
                event_count = max(1, (self.top_k + 1) // 2)
                concept_count = max(1, self.top_k - event_count)
            event_results = self.retriever.retrieve(
                question=question,
                scenario=scenario,
                top_k=event_count,
                excluded_source_types=_GENERAL_REFERENCE_SOURCE_TYPES,
            )
            concept_results = self.retriever.retrieve(
                question=question,
                scenario=scenario,
                top_k=(8 if is_iec104_sent_comparison else concept_count),
                source_types=_GENERAL_REFERENCE_SOURCE_TYPES,
            )
            if is_iec104_sent_comparison:
                concept_results = self._prioritize_evidence(
                    concept_results,
                    ("OT-G01", "OT-G02", "OT-G03"),
                )[:concept_count]
            combined: list[RetrievedChunk] = []
            seen_chunk_ids: set[str] = set()
            for result in [*event_results, *concept_results]:
                if result.chunk_id not in seen_chunk_ids:
                    combined.append(result)
                    seen_chunk_ids.add(result.chunk_id)
            return combined[: self.top_k]
        initial = self.retriever.retrieve(
            question=question,
            scenario=scenario,
            top_k=self.top_k,
        )
        if scenario.casefold() == "industroyer2":
            return self._retrieve_industroyer2_complementary_evidence(
                question=question,
                initial=initial,
            )
        if scenario.casefold() == "frostygoop":
            return self._retrieve_frostygoop_impact_boundary_evidence(
                question=question,
                initial=initial,
            )
        return initial

    def _retrieve_frostygoop_impact_boundary_evidence(
        self,
        *,
        question: str,
        initial: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Keep one direct Lab-impact limitation beside a Modbus write result.

        A detector alert or a decoded request is useful context, but neither
        proves device acceptance or physical process impact.  This narrow
        supplement prevents a student-facing answer from silently replacing
        that limitation with an unrelated risk label.
        """

        normalized = question.casefold()
        asks_impact = any(
            marker in normalized
            for marker in (
                "physical impact",
                "device acceptance",
                "plc",
                "\u5be6\u9ad4\u8a2d\u5099\u5f71\u97ff",
                "\u5be6\u9ad4\u5f71\u97ff",
                "\u7269\u7406\u5f71\u97ff",
                "實體影響",
                "實體設備影響",
                "物理影響",
                "設備接受",
                "製程",
                "設備影響",
            )
        )
        if not asks_impact:
            return initial

        candidates = list(initial)
        candidates.extend(
            self.retriever.retrieve(
                question=(
                    "Modbus PCAP captured Lab request does not prove device "
                    "acceptance or physical impact"
                ),
                scenario="frostygoop",
                top_k=max(self.top_k, 4),
            )
        )
        return self._prioritize_evidence_groups(
            candidates,
            (("FROSTY-N09", "FROSTY-N14", "FROSTY-N04"), ("FROSTY-N06", "FROSTY-N11", "FROSTY-N02")),
            self.top_k,
        )

    def _retrieve_industroyer2_complementary_evidence(
        self,
        *,
        question: str,
        initial: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Pin complementary records for compact, high-value IND2 comparisons.

        Dense/BM25 remains the normal retrieval path.  The three patterns below
        require multiple observational layers, so one top-ranked summary is
        insufficient as teaching context.  The helper supplements the normal
        result and still returns at most Top-K chunks.
        """

        normalized = question.casefold()
        source_ip_pair = (
            "10.10.60.10" in normalized and "10.10.60.20" in normalized
        )
        lockout_false_send = (
            ("caddywiper" in normalized or "lockout" in normalized)
            and "iec104_sent=false" in normalized
        )
        relay_send_evidence = (
            "relay" in normalized
            and any(marker in normalized for marker in ("iec104", "送出", "sent"))
            and any(
                marker in question
                for marker in ("哪一項證據", "哪個證據", "哪一筆證據", "支持")
            )
        )
        relay_correlation = (
            "relay" in normalized
            and any(
                marker in question
                for marker in (
                    "哪些紀錄",
                    "哪些證據",
                    "額外比對",
                    "區分兩者",
                    "關聯",
                )
            )
        )
        relay_awareness = (
            "kali" in normalized
            and "eng" in normalized
            and any(
                marker in normalized
                for marker in ("知道", "知情", "是否知", "會知", "aware", "know")
            )
        )
        relay_path = (
            "kali" in normalized
            and "relay" in normalized
            and any(
                marker in normalized
                for marker in ("經由", "直接", "發送", "送到", "路徑")
            )
        )
        relay_handoff = relay_correlation or relay_awareness or relay_path
        if not (
            source_ip_pair
            or lockout_false_send
            or relay_send_evidence
            or relay_handoff
        ):
            return initial

        if source_ip_pair:
            queries = (
                "source_ip=10.10.60.10 direct IEC-104 TCP/2404",
                "normal ENG source_ip=10.10.60.20 TCP/2404",
                "Kali via ENG Relay source_ip=10.10.60.20",
            )
            evidence_groups = (
                ("IND2-N08", "IND2-N09"),
                ("IND2-N02",),
                ("IND2-N21",),
                ("IND2-N22",),
            )
        elif lockout_false_send:
            queries = (
                "accepted=false iec104_sent=false blocked_by_safe_caddywiper",
                "Safe CaddyWiper lockout relay rejected before IEC-104 is sent",
            )
            evidence_groups = (("IND2-N17",), ("IND2-N15",), ("IND2-N18",))
        elif relay_correlation and any(
            marker in normalized for marker in ("正常 eng", "normal eng")
        ):
            queries = (
                "Normal ENG traffic source_ip=10.10.60.20 IEC-104 TCP/2404",
                "accepted=true iec104_sent=true eng_relay_successful_iec104_send",
                "Kali requested command through ENG Relay ENG01 sent IEC-104",
                "distinguish via-ENG Relay normal ENG correlate Kali Relay logs",
            )
            evidence_groups = (
                ("IND2-N02",),
                ("IND2-N24",),
                ("IND2-N20",),
                ("IND2-N22",),
            )
        else:
            queries = (
                "accepted=true iec104_sent=true eng_relay_successful_iec104_send",
                "Kali requested command through ENG Relay ENG01 sent IEC-104",
                "distinguish via-ENG Relay normal ENG correlate Kali Relay logs",
            )
            evidence_groups = (("IND2-N24",), ("IND2-N20",), ("IND2-N22",))

        candidates = list(initial)
        for query in queries:
            candidates.extend(
                self.retriever.retrieve(
                    question=query,
                    scenario="industroyer2",
                    top_k=max(self.top_k, 4),
                )
            )
        return self._prioritize_evidence_groups(
            candidates,
            evidence_groups,
            self.top_k,
        )

    @staticmethod
    def _prioritize_evidence_groups(
        results: list[RetrievedChunk],
        evidence_groups: tuple[tuple[str, ...], ...],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Choose one matching chunk per required evidence layer in order."""

        prioritized: list[RetrievedChunk] = []
        selected_ids: set[str] = set()
        for alternatives in evidence_groups:
            match = next(
                (
                    result
                    for result in results
                    if result.chunk_id not in selected_ids
                    and any(
                        evidence_id in result.evidence_ids
                        for evidence_id in alternatives
                    )
                ),
                None,
            )
            if match is not None:
                prioritized.append(match)
                selected_ids.add(match.chunk_id)
        prioritized.extend(
            result for result in results if result.chunk_id not in selected_ids
        )
        return prioritized[:top_k]

    @staticmethod
    def _prioritize_evidence(
        results: list[RetrievedChunk],
        preferred_evidence_ids: tuple[str, ...],
    ) -> list[RetrievedChunk]:
        """Keep complementary evidence for a defined teaching comparison."""

        prioritized: list[RetrievedChunk] = []
        selected_ids: set[str] = set()
        for evidence_id in preferred_evidence_ids:
            match = next(
                (
                    result
                    for result in results
                    if evidence_id in result.evidence_ids
                ),
                None,
            )
            if match is not None and match.chunk_id not in selected_ids:
                prioritized.append(match)
                selected_ids.add(match.chunk_id)
        prioritized.extend(
            result for result in results if result.chunk_id not in selected_ids
        )
        return prioritized

    def _cache(
        self,
        key: tuple[str, str, str],
        session: TutorSession,
    ) -> None:
        if self.response_cache_size == 0:
            return
        self._response_cache[key] = session
        self._response_cache.move_to_end(key)
        while len(self._response_cache) > self.response_cache_size:
            self._response_cache.popitem(last=False)
