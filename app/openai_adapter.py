"""Local OpenAI-compatible facade for the validated OT tutor pipeline.

Open WebUI uses this module as a presentation layer only.  It never receives
scenario chunks, Ground Truth, or permission to run its own retrieval: every
chat completion is delegated to :class:`app.service.TutorService`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Lock
from time import time
from typing import Any, Callable, Mapping, Sequence
from uuid import uuid4
from urllib.parse import urlparse

from app.evidence_cards import render_evidence_cards
from app.retrieval import RetrievedChunk
from app.schemas import TutorResponse
from app.service import TutorService


@dataclass(frozen=True)
class TutorModel:
    """One Open WebUI-selectable view of a scenario and teaching depth."""

    model_id: str
    scenario: str
    response_mode: str
    display_name: str


TUTOR_MODELS: tuple[TutorModel, ...] = (
    TutorModel("ot-tutor-ryuk-hint", "ryuk", "hint", "OT Tutor | Ryuk | 提示"),
    TutorModel("ot-tutor-ryuk-explain", "ryuk", "explain", "OT Tutor | Ryuk | 詳解"),
    TutorModel(
        "ot-tutor-industroyer2-hint",
        "industroyer2",
        "hint",
        "OT Tutor | Industroyer2 | 提示",
    ),
    TutorModel(
        "ot-tutor-industroyer2-explain",
        "industroyer2",
        "explain",
        "OT Tutor | Industroyer2 | 詳解",
    ),
    TutorModel(
        "ot-tutor-frostygoop-hint",
        "frostygoop",
        "hint",
        "OT Tutor | FrostyGoop | 提示",
    ),
    TutorModel(
        "ot-tutor-frostygoop-explain",
        "frostygoop",
        "explain",
        "OT Tutor | FrostyGoop | 詳解",
    ),
)

_MODELS_BY_ID = {model.model_id: model for model in TUTOR_MODELS}


class AdapterRequestError(ValueError):
    """A client-visible OpenAI-compatible request error."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _content_to_text(content: object) -> str:
    """Extract plain text from an OpenAI chat message content value."""

    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        if block.get("type") in {"text", "input_text"}:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text.strip())
    return "\n".join(part for part in parts if part).strip()


def extract_latest_user_question(messages: object) -> str:
    """Return the newest non-empty user question, ignoring UI system prompts."""

    if not isinstance(messages, list):
        raise AdapterRequestError("messages must be a JSON array")
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        question = _content_to_text(message.get("content"))
        if question:
            return question
    raise AdapterRequestError("messages must contain a non-empty user message")


def extract_previous_user_question(messages: object) -> str | None:
    """Return exactly one earlier user turn from an Open WebUI history.

    Only the previous question is retained. The previous assistant prose is
    deliberately excluded so it cannot become evidence in a new event answer.
    """

    if not isinstance(messages, list):
        return None
    questions = [
        _content_to_text(message.get("content"))
        for message in messages
        if isinstance(message, Mapping) and message.get("role") == "user"
    ]
    nonempty = [question for question in questions if question]
    return nonempty[-2] if len(nonempty) >= 2 else None


def format_tutor_markdown(
    response: Any,
    scenario: str,
    retrieved: Sequence[RetrievedChunk] = (),
) -> str:
    """Render the already-validated TutorResponse as concise chat Markdown."""

    def bullets(values: list[str]) -> str:
        return "\n".join(f"- {value}" for value in values) if values else "- 無"

    evidence = (
        "\n".join(f"- `{value}`" for value in response.evidence_ids)
        if response.evidence_ids
        else "- 無（此回答未使用事件檢索證據）"
    )
    sections = [
        "## 回答\n" + response.answer,
        "## 已知事實\n" + bullets(response.known_facts),
        "## 合理推論\n" + bullets(response.reasonable_inferences),
        "## 證據不足／未知事項\n" + bullets(response.unknowns),
        "## Evidence IDs\n" + evidence,
    ]
    evidence_cards = render_evidence_cards(
        scenario,
        response.evidence_ids,
        retrieved,
    )
    if evidence_cards:
        sections.append(evidence_cards)
    sections.append("## 下一個提示\n" + response.next_hint)
    return "\n\n".join(sections)


def build_validation_fallback_response() -> TutorResponse:
    """Return a safe chat response when generated event claims fail validation.

    A semantic validation failure is not a malformed OpenAI request.  Returning
    an HTTP error makes presentation clients such as Open WebUI mark the whole
    conversation turn as broken.  This evidence-free response keeps the chat
    usable without exposing or silently accepting the rejected model output.
    """

    return TutorResponse(
        answer=(
            "這一輪產生的回答沒有通過證據完整性檢查，因此系統沒有顯示"
            "未驗證的內容。這不會影響下一題，你可以直接繼續提問。"
        ),
        known_facts=[],
        reasonable_inferences=[],
        unknowns=["本輪尚未產生通過驗證的完整事件回答。"],
        evidence_ids=[],
        next_hint=(
            "若要繼續查證這一題，可先拆成「目前證據直接支持什麼」與"
            "「還需要比對哪些證據」兩個問題。"
        ),
    )


class TutorOpenAIAdapter:
    """Translate OpenAI chat requests into isolated scenario TutorService calls."""

    def __init__(
        self,
        service_factory: Callable[[str], TutorService] | None = None,
    ) -> None:
        self._service_factory = service_factory or (
            lambda scenario: TutorService.from_settings(scenario=scenario)
        )
        self._services: dict[str, TutorService] = {}
        # Llama on the local 6 GB GPU is a single-user demonstration resource.
        # Serializing requests prevents concurrent UI tabs from interleaving
        # model calls or sharing a scenario's two-turn follow-up memory.
        self._completion_lock = Lock()

    def models_payload(self) -> dict[str, object]:
        """Return the OpenAI-compatible catalog used by Open WebUI."""

        return {
            "object": "list",
            "data": [
                {
                    "id": model.model_id,
                    "object": "model",
                    "created": 0,
                    "owned_by": "ot-security-tutor",
                    "name": model.display_name,
                }
                for model in TUTOR_MODELS
            ],
        }

    def complete(self, request: Mapping[str, object]) -> dict[str, object]:
        """Generate one validated tutor answer in OpenAI response shape."""

        requested_model = request.get("model")
        if not isinstance(requested_model, str):
            raise AdapterRequestError("model must be a supported tutor model")
        model = _MODELS_BY_ID.get(requested_model)
        if model is None:
            raise AdapterRequestError(
                f"Unknown tutor model: {requested_model}",
                status_code=404,
            )
        messages = request.get("messages")
        question = extract_latest_user_question(messages)
        previous_question = extract_previous_user_question(messages)
        with self._completion_lock:
            service = self._services.get(model.scenario)
            if service is None:
                service = self._service_factory(model.scenario)
                self._services[model.scenario] = service
            # Open WebUI supplies whole chat histories but no trustworthy
            # server-side conversation ID. Keep no global service memory; pass
            # only one earlier user question to resolve an explicit pronoun.
            service.clear_conversation()
            try:
                session = service.ask(
                    question=question,
                    scenario=model.scenario,
                    response_mode=model.response_mode,  # type: ignore[arg-type]
                    previous_question=previous_question,
                )
            except ValueError as exc:
                # Generation/schema/semantic validation failures must remain
                # strict, but they are not malformed HTTP requests.  Replace
                # only the rejected presentation turn with an evidence-free
                # fallback so a client can submit the next question normally.
                print(
                    "[adapter] Tutor answer validation failed; returned safe "
                    f"fallback: {exc}"
                )
                session = None

        completion_id = f"chatcmpl-{uuid4().hex}"
        if session is None:
            response = build_validation_fallback_response()
            retrieved: Sequence[RetrievedChunk] = ()
            prompt_tokens = 0
            completion_tokens = 0
        else:
            response = session.response
            retrieved = getattr(session, "retrieved", ())
            prompt_tokens = sum(
                metric.prompt_eval_count or 0 for metric in session.ollama_metrics
            )
            completion_tokens = sum(
                metric.eval_count or 0 for metric in session.ollama_metrics
            )
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time()),
            "model": model.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": format_tutor_markdown(
                            response,
                            scenario=model.scenario,
                            retrieved=retrieved,
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }


def _error_payload(message: str, code: str) -> dict[str, object]:
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error",
            "code": code,
        }
    }


def _handler_type(adapter: TutorOpenAIAdapter) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one adapter instance."""

    class TutorAdapterHandler(BaseHTTPRequestHandler):
        server_version = "OTSecurityTutorAdapter/0.1"

        def log_message(self, format: str, *args: object) -> None:
            print("[adapter] " + format % args)

        def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_stream(self, completion: Mapping[str, object]) -> None:
            choice = completion["choices"][0]  # type: ignore[index]
            message = choice["message"]  # type: ignore[index]
            first = {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": message["content"]},
                        "finish_reason": None,
                    }
                ],
            }
            last = {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # The answer is generated and validated before the first SSE
            # event, so this endpoint has no later tokens to keep alive for.
            # Closing explicitly after [DONE] lets strict HTTP clients finish
            # their request instead of waiting for a socket timeout.
            self.send_header("Connection", "close")
            self.end_headers()
            for payload in (first, last):
                line = "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                self.wfile.write(line.encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            self.close_connection = True

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path in {"/v1/models", "/models"}:
                self._send_json(HTTPStatus.OK, adapter.models_payload())
                return
            if path in {"/health", "/healthz"}:
                self._send_json(
                    HTTPStatus.OK,
                    {"status": "ok", "source_revision": "evidence-card-audit"},
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                _error_payload("Unknown endpoint", "not_found"),
            )

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/")
            if path not in {"/v1/chat/completions", "/chat/completions"}:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    _error_payload("Unknown endpoint", "not_found"),
                )
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(content_length)
                request = json.loads(raw_body.decode("utf-8"))
                if not isinstance(request, Mapping):
                    raise AdapterRequestError("request body must be a JSON object")
                completion = adapter.complete(request)
            except AdapterRequestError as exc:
                self._send_json(
                    exc.status_code,
                    _error_payload(str(exc), "invalid_request"),
                )
                return
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    _error_payload(f"Invalid JSON request: {exc}", "invalid_json"),
                )
                return
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _error_payload(f"Tutor pipeline failed: {exc}", "internal_error"),
                )
                return
            except Exception as exc:  # pragma: no cover - operational boundary
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    _error_payload(f"Tutor pipeline failed: {exc}", "internal_error"),
                )
                return

            if request.get("stream") is True:
                self._send_stream(completion)
            else:
                self._send_json(HTTPStatus.OK, completion)

    return TutorAdapterHandler


def serve(host: str, port: int) -> None:
    """Run the local adapter until interrupted with Ctrl+C."""

    if not host:
        raise ValueError("host must not be empty")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    server = ThreadingHTTPServer((host, port), _handler_type(TutorOpenAIAdapter()))
    print(f"OT Security Tutor adapter listening at http://{host}:{port}/v1")
    print("Open WebUI Docker connection: http://host.docker.internal:" f"{port}/v1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping OT Security Tutor adapter.")
    finally:
        server.server_close()


def main() -> None:
    """Parse local-only listener settings and serve the adapter."""

    parser = argparse.ArgumentParser(
        description="Expose the validated OT tutor as an OpenAI-compatible API"
    )
    parser.add_argument(
        "--host",
        default=os.getenv("OT_TUTOR_ADAPTER_HOST", "127.0.0.1"),
        help="Local listener address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OT_TUTOR_ADAPTER_PORT", "8001")),
        help="Local listener port (default: 8001)",
    )
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
