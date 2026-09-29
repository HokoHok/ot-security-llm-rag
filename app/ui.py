from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from app.generation import ResponseMode
from app.schemas import TutorResponse
from app.service import TutorService, TutorSession

ScenarioKey = str
ServiceFactory = Callable[[ScenarioKey], TutorService]

SCENARIO_LABELS: dict[ScenarioKey, str] = {
    "ryuk": "Ryuk-like 安全模擬",
    "industroyer2": "Industroyer2-like 安全模擬",
    "frostygoop": "FrostyGoop 報告衍生安全重建",
}
DEFAULT_QUESTIONS: dict[ScenarioKey, str] = {
    "ryuk": "為什麼 Kali 不是直接修改 DC01 Share 的主機？",
    "industroyer2": (
        "為什麼只看到 source_ip=10.10.60.20，不能判定是正常 ENG 維護？"
    ),
    "frostygoop": "Task JSON 與 PCAP 對照相符時，在此 Lab 中能證明什麼？",
}
MODE_LABELS: dict[str, ResponseMode] = {
    "提示模式": "hint",
    "詳解模式": "explain",
}


def scenario_label(scenario: ScenarioKey) -> str:
    """Return the student-facing label for one supported scenario."""

    try:
        return SCENARIO_LABELS[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario: {scenario}") from exc


def scenario_from_label(label: str) -> ScenarioKey:
    """Map a student-facing label back to the stable scenario key."""

    for scenario, candidate in SCENARIO_LABELS.items():
        if candidate == label:
            return scenario
    raise ValueError(f"Unknown scenario label: {label}")


def default_question_for_scenario(scenario: ScenarioKey) -> str:
    """Return the starter question that matches the selected scenario."""

    try:
        return DEFAULT_QUESTIONS[scenario]
    except KeyError as exc:
        raise ValueError(f"Unknown scenario: {scenario}") from exc


def create_service_for_scenario(scenario: ScenarioKey) -> TutorService:
    """Open the Chroma collection that belongs only to ``scenario``."""

    return TutorService.from_settings(scenario=scenario)


@dataclass
class ScenarioServiceManager:
    """Keep one lazy, scenario-bound TutorService per desktop session."""

    service_factory: ServiceFactory = create_service_for_scenario
    _services: dict[ScenarioKey, TutorService] = field(default_factory=dict)
    active_scenario: ScenarioKey = "ryuk"

    def select(self, scenario: ScenarioKey) -> None:
        """Select a scenario without opening its database until it is used."""

        scenario_label(scenario)
        self.active_scenario = scenario

    def active_service(self) -> TutorService:
        """Return the active scenario's isolated service, creating it once."""

        service = self._services.get(self.active_scenario)
        if service is None:
            service = self.service_factory(self.active_scenario)
            self._services[self.active_scenario] = service
        return service

    def clear_active_conversation(self) -> None:
        """Clear only the selected scenario's transient follow-up context."""

        service = self._services.get(self.active_scenario)
        if service is not None:
            service.clear_conversation()


def response_mode_from_label(label: str) -> ResponseMode:
    """Map a student-facing Chinese label to the service mode."""

    try:
        return MODE_LABELS[label]
    except KeyError as exc:
        raise ValueError(f"Unknown response mode label: {label}") from exc


def format_tutor_response(response: TutorResponse) -> str:
    """Format validated JSON as a readable student-facing answer."""

    sections = [
        ("回答", [response.answer]),
        ("已知事實", response.known_facts),
        ("合理推論", response.reasonable_inferences),
        ("證據不足／未知事項", response.unknowns),
        ("Evidence IDs", response.evidence_ids),
        ("下一個提示", [response.next_hint]),
    ]
    lines: list[str] = []
    for title, items in sections:
        lines.append(title)
        lines.append("─" * len(title))
        if items:
            lines.extend(f"• {item}" for item in items)
        else:
            lines.append("• 無")
        lines.append("")
    return "\n".join(lines).rstrip()


def retrieval_trace_rows(
    session: TutorSession,
) -> list[tuple[str, ...]]:
    """Convert retrieval results into stable table rows for the UI."""

    rows: list[tuple[str, ...]] = []
    for rank, result in enumerate(session.retrieved, start=1):
        rows.append(
            (
                str(rank),
                result.chunk_id,
                str(result.metadata.get("stage", "")),
                str(result.metadata.get("title", "")),
                ", ".join(result.evidence_ids),
                str(result.dense_rank or "-"),
                str(result.sparse_rank or "-"),
                f"{result.rrf_score:.6f}",
            )
        )
    return rows


def developer_trace_payload(session: TutorSession) -> dict[str, Any]:
    """Build developer-only JSON without changing TutorResponse schema."""

    return {
        "scenario": session.scenario,
        "tutor_response": session.response.model_dump(),
        "routing": {
            "question_scope": session.question_scope,
            "response_mode": session.response_mode,
            "evidence_adequacy": (
                {
                    "status": session.evidence_adequacy.status,
                    "requires_unknowns": (
                        session.evidence_adequacy.require_unknowns
                    ),
                    "reasons": list(session.evidence_adequacy.reasons),
                    "best_dense_distance": (
                        session.evidence_adequacy.best_dense_distance
                    ),
                    "best_sparse_score": (
                        session.evidence_adequacy.best_sparse_score
                    ),
                }
                if session.evidence_adequacy is not None
                else None
            ),
        },
        "retrieval": [
            {
                "rank": rank,
                "chunk_id": result.chunk_id,
                "evidence_ids": result.evidence_ids,
                "dense_rank": result.dense_rank,
                "dense_distance": result.dense_distance,
                "bm25_rank": result.sparse_rank,
                "bm25_score": result.sparse_score,
                "rrf_score": result.rrf_score,
            }
            for rank, result in enumerate(session.retrieved, start=1)
        ],
        "runtime": {
            "cache_hit": session.cache_hit,
            "conversation_context_used": session.conversation_context_used,
            "conversation_turns_available": session.conversation_turns_available,
            "retrieval_seconds": round(session.retrieval_seconds, 4),
            "generation_seconds": round(session.generation_seconds, 4),
            "generation_attempts": session.generation_attempts,
            "ollama_calls": [
                metrics.to_dict() for metrics in session.ollama_metrics
            ],
        },
    }


class TutorWindow:
    """Single-user Windows desktop interface for the local RAG tutor."""

    def __init__(
        self,
        root: tk.Tk,
        service_factory: ServiceFactory = create_service_for_scenario,
    ) -> None:
        self.root = root
        self.services = ScenarioServiceManager(service_factory)
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.root.title(f"OT Security Tutor — {scenario_label('ryuk')}")
        self.root.geometry("1180x780")
        self.root.minsize(920, 660)
        self.root.option_add("*Font", ("Microsoft JhengHei UI", 10))

        self.status_var = tk.StringVar(value="就緒")
        self.scenario_var = tk.StringVar(value=scenario_label("ryuk"))
        self.mode_var = tk.StringVar(value="提示模式")
        self.trace_enabled = tk.BooleanVar(value=False)

        self._configure_style()
        self._build_layout()
        self._set_trace_visibility()
        self.root.bind("<Control-Return>", self._submit_from_event)
        self.root.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 18, "bold"))
        style.configure(
            "Subtitle.TLabel",
            foreground="#4b5563",
            font=("Microsoft JhengHei UI", 10),
        )
        style.configure("Status.TLabel", foreground="#1d4ed8")

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="OT Security Tutor",
            style="Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.subtitle_label = ttk.Label(
            header,
            text="本機 Ryuk-like 事件還原教學助理｜RAG + Ollama",
            style="Subtitle.TLabel",
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w", pady=(3, 0))

        controls = ttk.LabelFrame(container, text="學生問題", padding=12)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        controls.columnconfigure(0, weight=1)

        self.question_text = tk.Text(
            controls,
            height=4,
            wrap=tk.WORD,
            undo=True,
            relief=tk.SOLID,
            borderwidth=1,
            font=("Microsoft JhengHei UI", 11),
        )
        self.question_text.grid(
            row=0,
            column=0,
            columnspan=8,
            sticky="ew",
            pady=(0, 10),
        )
        self.question_text.insert("1.0", default_question_for_scenario("ryuk"))

        ttk.Label(controls, text="情境：").grid(row=1, column=0, sticky="w")
        self.scenario_box = ttk.Combobox(
            controls,
            textvariable=self.scenario_var,
            values=tuple(SCENARIO_LABELS.values()),
            state="readonly",
            width=24,
        )
        self.scenario_box.grid(row=1, column=1, sticky="w", padx=(0, 14))
        self.scenario_box.bind("<<ComboboxSelected>>", self._on_scenario_changed)

        ttk.Label(controls, text="回答深度：").grid(
            row=1,
            column=2,
            sticky="w",
        )
        self.mode_box = ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=tuple(MODE_LABELS),
            state="readonly",
            width=10,
        )
        self.mode_box.grid(row=1, column=3, sticky="w", padx=(0, 14))

        self.trace_check = ttk.Checkbutton(
            controls,
            text="顯示開發者追蹤",
            variable=self.trace_enabled,
            command=self._set_trace_visibility,
        )
        self.trace_check.grid(row=1, column=4, sticky="w")

        controls.columnconfigure(5, weight=1)
        self.clear_button = ttk.Button(
            controls,
            text="清除",
            command=self._clear,
            width=10,
        )
        self.clear_button.grid(row=1, column=6, sticky="e", padx=(8, 0))
        self.ask_button = ttk.Button(
            controls,
            text="開始分析",
            command=self._submit,
            width=12,
        )
        self.ask_button.grid(row=1, column=7, sticky="e", padx=(8, 0))

        self.notebook = ttk.Notebook(container)
        self.notebook.grid(row=2, column=0, sticky="nsew")

        self.answer_frame = ttk.Frame(self.notebook, padding=8)
        self.trace_frame = ttk.Frame(self.notebook, padding=8)
        self.json_frame = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.answer_frame, text="教學回覆")

        self.answer_text = ScrolledText(
            self.answer_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Microsoft JhengHei UI", 11),
            padx=12,
            pady=12,
        )
        self.answer_text.pack(fill=tk.BOTH, expand=True)

        self._build_trace_table()
        self.raw_json_text = ScrolledText(
            self.json_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
            padx=10,
            pady=10,
        )
        self.raw_json_text.pack(fill=tk.BOTH, expand=True)

        footer = ttk.Frame(container)
        footer.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(1, weight=1)
        self.progress = ttk.Progressbar(
            footer,
            mode="indeterminate",
            length=180,
        )
        self.progress.grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(
            footer,
            textvariable=self.status_var,
            style="Status.TLabel",
        ).grid(row=0, column=1, sticky="w")
        ttk.Label(
            footer,
            text="Ctrl+Enter：開始分析",
            style="Subtitle.TLabel",
        ).grid(row=0, column=2, sticky="e")

    def _build_trace_table(self) -> None:
        columns = (
            "rank",
            "chunk",
            "stage",
            "title",
            "evidence",
            "dense",
            "bm25",
            "rrf",
        )
        wrapper = ttk.Frame(self.trace_frame)
        wrapper.pack(fill=tk.BOTH, expand=True)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)

        self.trace_tree = ttk.Treeview(
            wrapper,
            columns=columns,
            show="headings",
        )
        headings = {
            "rank": "#",
            "chunk": "Chunk ID",
            "stage": "Stage",
            "title": "標題",
            "evidence": "Evidence IDs",
            "dense": "Dense",
            "bm25": "BM25",
            "rrf": "RRF",
        }
        widths = {
            "rank": 45,
            "chunk": 190,
            "stage": 90,
            "title": 300,
            "evidence": 190,
            "dense": 65,
            "bm25": 65,
            "rrf": 85,
        }
        for column in columns:
            self.trace_tree.heading(column, text=headings[column])
            self.trace_tree.column(
                column,
                width=widths[column],
                minwidth=45,
                anchor=tk.W if column not in {"rank", "dense", "bm25", "rrf"} else tk.CENTER,
            )

        vertical = ttk.Scrollbar(
            wrapper,
            orient=tk.VERTICAL,
            command=self.trace_tree.yview,
        )
        horizontal = ttk.Scrollbar(
            wrapper,
            orient=tk.HORIZONTAL,
            command=self.trace_tree.xview,
        )
        self.trace_tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self.trace_tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

    def _set_trace_visibility(self) -> None:
        visible_tabs = self.notebook.tabs()
        trace_id = str(self.trace_frame)
        json_id = str(self.json_frame)
        if self.trace_enabled.get():
            if trace_id not in visible_tabs:
                self.notebook.add(self.trace_frame, text="檢索追蹤")
            if json_id not in self.notebook.tabs():
                self.notebook.add(self.json_frame, text="開發者 JSON")
        else:
            if trace_id in visible_tabs:
                self.notebook.forget(self.trace_frame)
            if json_id in self.notebook.tabs():
                self.notebook.forget(self.json_frame)

    def _submit_from_event(self, _event: tk.Event[Any]) -> str:
        self._submit()
        return "break"

    def _selected_scenario(self) -> ScenarioKey:
        """Return the stable key corresponding to the selected UI label."""

        return scenario_from_label(self.scenario_var.get())

    def _on_scenario_changed(self, _event: tk.Event[Any] | None = None) -> None:
        """Reset the visible session before changing the evidence collection."""

        if self.worker and self.worker.is_alive():
            return
        scenario = self._selected_scenario()
        self.services.select(scenario)
        self.services.clear_active_conversation()
        label = scenario_label(scenario)
        self.root.title(f"OT Security Tutor — {label}")
        self.subtitle_label.configure(
            text=f"本機 {label} 事件還原教學助理｜RAG + Ollama"
        )
        self.question_text.delete("1.0", tk.END)
        self.question_text.insert("1.0", default_question_for_scenario(scenario))
        self._replace_text(self.answer_text, "")
        self._replace_text(self.raw_json_text, "")
        self._clear_trace()
        self.status_var.set(f"已切換至 {label}；將在首次提問時載入對應索引。")
        self.question_text.focus_set()

    def _submit(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        question = self.question_text.get("1.0", tk.END).strip()
        if not question:
            self.status_var.set("請先輸入問題。")
            self.question_text.focus_set()
            return

        self._set_busy(True)
        self._replace_text(self.answer_text, "正在分析，請稍候…")
        self._clear_trace()
        self._replace_text(self.raw_json_text, "")
        scenario = self._selected_scenario()
        response_mode = response_mode_from_label(self.mode_var.get())
        self.worker = threading.Thread(
            target=self._run_pipeline,
            args=(question, scenario, response_mode),
            daemon=True,
        )
        self.worker.start()

    def _run_pipeline(
        self,
        question: str,
        scenario: str,
        response_mode: ResponseMode,
    ) -> None:
        try:
            self.services.select(scenario)
            service = self.services.active_service()
            session = service.ask(
                question=question,
                scenario=scenario,
                response_mode=response_mode,
                progress=lambda message: self.events.put(("status", message)),
            )
            self.events.put(("result", session))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "result":
                    self._show_result(payload)
                elif event == "error":
                    self._show_error(str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _show_result(self, session: TutorSession) -> None:
        self._replace_text(
            self.answer_text,
            format_tutor_response(session.response),
        )
        self._clear_trace()
        for row in retrieval_trace_rows(session):
            self.trace_tree.insert("", tk.END, values=row)
        self._replace_text(
            self.raw_json_text,
            json.dumps(
                developer_trace_payload(session),
                ensure_ascii=False,
                indent=2,
            ),
        )
        if session.cache_hit:
            status = "完成：使用本次工作階段快取。"
        elif session.question_scope != "in_scope":
            status = (
                f"完成：scope={session.question_scope}，"
                "未檢索且未呼叫 Llama。"
            )
        elif (
            session.evidence_adequacy is not None
            and session.evidence_adequacy.status == "insufficient"
        ):
            status = (
                "完成：檢索後判定證據不足，未呼叫 Llama，"
                f"耗時 {session.retrieval_seconds:.2f} 秒。"
            )
        elif session.retrieved:
            attempts = (
                f"，Llama 呼叫 {session.generation_attempts} 次"
                if session.generation_attempts is not None
                else ""
            )
            status = (
                "完成：檢索 "
                f"{session.retrieval_seconds:.2f} 秒，生成 "
                f"{session.generation_seconds:.2f} 秒{attempts}。"
            )
            if session.ollama_metrics:
                last_metrics = session.ollama_metrics[-1]
                status = (
                    status[:-1]
                    + f"，done={last_metrics.done_reason or 'unknown'}"
                    + f"，output tokens={last_metrics.eval_count or 'n/a'}。"
                )
                if last_metrics.output_may_be_truncated:
                    status += " 警告：輸出可能達到 NUM_PREDICT 上限。"
        else:
            status = "完成：沒有可用的事件證據，未呼叫 Llama。"
        if session.conversation_context_used:
            status += " 已使用前一輪語意脈絡，並重新檢索證據。"
        self.status_var.set(status)
        self._set_busy(False)

    def _show_error(self, message: str) -> None:
        self._replace_text(
            self.answer_text,
            "執行失敗\n────────\n"
            f"{message}\n\n"
            "請確認 Ollama、模型與 ChromaDB 索引是否可用。",
        )
        self.status_var.set("執行失敗")
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.ask_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.clear_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.scenario_box.configure(state=tk.DISABLED if busy else "readonly")
        self.mode_box.configure(state=tk.DISABLED if busy else "readonly")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _clear(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.question_text.delete("1.0", tk.END)
        self._replace_text(self.answer_text, "")
        self._replace_text(self.raw_json_text, "")
        self._clear_trace()
        self.services.clear_active_conversation()
        self.status_var.set("已清除畫面與本情境的短期對話脈絡。")
        self.question_text.focus_set()

    def _clear_trace(self) -> None:
        for item in self.trace_tree.get_children():
            self.trace_tree.delete(item)

    @staticmethod
    def _replace_text(widget: ScrolledText, value: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)


def create_window(
    service_factory: ServiceFactory = create_service_for_scenario,
) -> tuple[tk.Tk, TutorWindow]:
    """Create the Tk root and application without starting the event loop."""

    root = tk.Tk()
    window = TutorWindow(root, service_factory=service_factory)
    return root, window


def main() -> None:
    root, _window = create_window()
    root.mainloop()


if __name__ == "__main__":
    main()
