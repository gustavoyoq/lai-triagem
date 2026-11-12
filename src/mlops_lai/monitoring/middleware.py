"""Observa o ciclo ASGI, incluindo rejeições anteriores aos handlers BentoML."""

from __future__ import annotations

import asyncio
import json
from time import perf_counter_ns
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mlops_lai.core.settings import get_settings
from mlops_lai.monitoring.telemetry import CURRENT_EVENT, INFERENCE_ENDPOINTS, get_recorder

OBSERVED_ENDPOINTS = INFERENCE_ENDPOINTS | {"/health", "/metadata", "/healthz", "/readyz", "/livez"}
CAPTURE_LIMIT = 65_536
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


class TelemetryMiddleware:
    """Mantém o prazo HTTP e registra o código que efetivamente enviou."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.recorder = get_recorder()
        self.timeout = get_settings().request_timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return
        endpoint = scope["path"] if scope["path"] in OBSERVED_ENDPOINTS else "unmatched"
        method = scope["method"] if scope["method"] in HTTP_METHODS else "OTHER"
        event = self.recorder.new_event(endpoint, method)
        token = CURRENT_EVENT.set(event)
        started = perf_counter_ns()
        request_body = bytearray()
        response_body = bytearray()
        request_complete = False
        completed = False
        response_started = False

        async def observe_receive() -> Message:
            nonlocal request_complete
            message = await receive()
            if message["type"] == "http.request" and not request_complete:
                request_body.extend(
                    message.get("body", b"")[: CAPTURE_LIMIT + 1 - len(request_body)]
                )
                if not message.get("more_body", False):
                    request_complete = True
                    self._capture_request(event, request_body)
            return message

        async def observe_send(message: Message) -> None:
            nonlocal completed, response_started
            if message["type"] == "http.response.start":
                response_started = True
                event["http_status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", event["request_id"].encode("ascii")))
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                response_body.extend(
                    message.get("body", b"")[: CAPTURE_LIMIT + 1 - len(response_body)]
                )
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                self._capture_response(event, response_body)
                self.recorder.finish(event, started)
                completed = True

        try:
            async with asyncio.timeout(self.timeout):
                await self.app(scope, observe_receive, observe_send)
        except TimeoutError:
            if not completed:
                self.recorder.classify_error(event, "timeout")
                event["outcome"] = "server_error"
                if not response_started:
                    response = JSONResponse({"error": "request_timeout"}, status_code=504)
                    await response(scope, receive, observe_send)
        except asyncio.CancelledError:
            if not completed:
                self.recorder.classify_error(event, "cancelled")
                event["outcome"] = "cancelled"
            raise
        except Exception:
            if not completed:
                if event["error"] is None:
                    self.recorder.classify_error(event, "server_error")
                event["outcome"] = "server_error"
                if not response_started:
                    response = JSONResponse({"error": "internal_server_error"}, status_code=500)
                    await response(scope, receive, observe_send)
            raise
        finally:
            if not completed:
                if event["error"] is None:
                    self.recorder.classify_error(event, "incomplete_response")
                    event["outcome"] = "cancelled"
                self.recorder.finish(event, started)
            CURRENT_EVENT.reset(token)

    def _capture_request(self, event: dict[str, Any], body: bytearray) -> None:
        if event["endpoint"] not in INFERENCE_ENDPOINTS:
            return
        event["request_body_truncated"] = len(body) > CAPTURE_LIMIT
        if len(body) > CAPTURE_LIMIT:
            return
        try:
            envelope = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            self.recorder.classify_error(event, "invalid_json")
            return
        if isinstance(envelope, dict) and isinstance(envelope.get("pedido"), dict):
            self.recorder.capture_input(event, envelope["pedido"])

    def _capture_response(self, event: dict[str, Any], body: bytearray) -> None:
        status = event["http_status"]
        if status >= 400:
            event["outcome"] = "validation_error" if status < 500 else "server_error"
            if event["error"] is None:
                self.recorder.classify_error(
                    event, "http_validation" if status < 500 else "server_error"
                )
            # Preservar tipo e localização dos erros sem copiar os valores rejeitados.
            try:
                detail = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                detail = None
            if isinstance(detail, dict) and isinstance(detail.get("detail"), list):
                event["error"]["validation"] = [
                    {"type": item.get("type"), "loc": item.get("loc")}
                    for item in detail["detail"]
                    if isinstance(item, dict)
                ]
                event["error"]["fields"] = [
                    str(item["loc"][-1]) for item in event["error"]["validation"] if item.get("loc")
                ]
            event["response"] = {"error": event["error"], "http_status": status}
            return
        if event["endpoint"] in INFERENCE_ENDPOINTS and len(body) <= CAPTURE_LIMIT:
            try:
                event["response"] = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                self.recorder.classify_error(event, "invalid_response_json")
                event["outcome"] = "server_error"
