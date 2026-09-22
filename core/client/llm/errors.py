"""Classify LLM failures without retaining arbitrary response or request content."""

from __future__ import annotations

from core.i18n import tr

import re
import ssl

import httpx


HTTP_REASONS = {
    400: "llm.http.400",
    401: "llm.http.401",
    403: "llm.http.403",
    404: "llm.http.404",
    408: "llm.http.408",
    429: "llm.http.429",
    500: "llm.http.500",
    502: "llm.http.502",
    503: "llm.http.503",
    504: "llm.http.504",
}

REASONS = {
    "API_ERROR": "llm.reason.api_error",
    "API_KEY_EXPIRED": "llm.reason.api_key_expired",
    "API_KEY_INVALID": "llm.reason.api_key_invalid",
    "API_KEY_LEAKED": "llm.reason.api_key_leaked",
    "AUTHENTICATION": "llm.reason.authentication",
    "BILLING_DISABLED": "llm.reason.billing_disabled",
    "BLOCKLIST": "llm.reason.blocklist",
    "CONTENT_BLOCKED": "llm.reason.content_blocked",
    "CONTENT_FILTER": "llm.reason.content_filter",
    "DEADLINE_EXCEEDED": "llm.reason.deadline_exceeded",
    "ESCALATION": "llm.reason.escalation",
    "FAILED_PRECONDITION": "llm.reason.failed_precondition",
    "FUNCTION_CALL": "llm.reason.function_call",
    "IMAGE_OTHER": "llm.reason.image_other",
    "IMAGE_PROHIBITED_CONTENT": "llm.reason.image_prohibited_content",
    "IMAGE_RECITATION": "llm.reason.image_recitation",
    "IMAGE_SAFETY": "llm.reason.image_safety",
    "INSUFFICIENT_QUOTA": "llm.reason.insufficient_quota",
    "INTERNAL": "llm.reason.internal",
    "INVALID_API_KEY": "llm.reason.invalid_api_key",
    "INVALID_ARGUMENT": "llm.reason.invalid_argument",
    "INVALID_REQUEST": "llm.reason.invalid_request",
    "LANGUAGE": "llm.reason.language",
    "LENGTH": "llm.reason.length",
    "MALFORMED_FUNCTION_CALL": "llm.reason.malformed_function_call",
    "MALFORMED_RESPONSE": "llm.reason.malformed_response",
    "MAX_TOKENS": "llm.reason.max_tokens",
    "MISSING_THOUGHT_SIGNATURE": "llm.reason.missing_thought_signature",
    "MODEL_NOT_FOUND": "llm.reason.model_not_found",
    "NOT_FOUND": "llm.reason.not_found",
    "NO_IMAGE": "llm.reason.no_image",
    "OTHER": "llm.reason.other",
    "PERMISSION_DENIED": "llm.reason.permission_denied",
    "PROHIBITED_CONTENT": "llm.reason.prohibited_content",
    "QUOTA_EXCEEDED": "llm.reason.quota_exceeded",
    "RATE_LIMIT_EXCEEDED": "llm.reason.rate_limit_exceeded",
    "RECITATION": "llm.reason.recitation",
    "RESOURCE_EXHAUSTED": "llm.reason.resource_exhausted",
    "SAFETY": "llm.reason.safety",
    "SERVICE_DISABLED": "llm.reason.service_disabled",
    "SERVICE_UNAVAILABLE": "llm.reason.service_unavailable",
    "SPII": "llm.reason.spii",
    "TOOL_CALLS": "llm.reason.tool_calls",
    "TOO_MANY_REQUESTS": "llm.reason.too_many_requests",
    "TOO_MANY_TOOL_CALLS": "llm.reason.too_many_tool_calls",
    "UNAUTHENTICATED": "llm.reason.unauthenticated",
    "UNAVAILABLE": "llm.reason.unavailable",
    "UNEXPECTED_TOOL_CALL": "llm.reason.unexpected_tool_call",
}


def known_reason(value) -> str:
    # Network enum values are untrusted; unknown codes may contain keys or user text.
    return value.upper() if isinstance(value, str) and value.upper() in REASONS else ""


class LLMResponseError(ValueError):
    def __init__(self, category: str, message_id: str, **fields):
        self.category = category
        self.message_id = message_id
        self.fields = fields
        super().__init__(self.describe(locale="en"))

    def describe(self, *, locale=None):
        text = tr(self.message_id, locale=locale)
        if self.fields.get("retry_after_s"):
            text += tr("llm.retry_after", locale=locale, seconds=self.fields["retry_after_s"])
        return text

    @property
    def user_message(self):
        return self.describe()


def api_error(status_code: int, body, retry_after: str = "") -> LLMResponseError:
    error = body.get("error", {}) if isinstance(body, dict) else {}
    if not isinstance(error, dict):
        error = {}
    status = known_reason(error.get("status"))
    code = known_reason(error.get("code")) or known_reason(error.get("type"))
    reason = ""
    retry = retry_after if re.fullmatch(r"\d{1,6}(?:\.\d{1,3})?", retry_after) else ""
    details = error.get("details", [])
    if isinstance(details, list):
        for detail in details[:20]:
            if not isinstance(detail, dict):
                continue
            reason = known_reason(detail.get("reason")) or reason
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and re.fullmatch(r"\d{1,6}(?:\.\d{1,3})?s", delay):
                retry = delay[:-1]
    # Messages may echo text, URLs, or credentials; retain only known diagnostic meanings.
    message = error.get("message", "")
    if isinstance(message, str):
        lowered = message[:8192].lower()
        for needle, diagnosis in (
            ("reported as leaked", "API_KEY_LEAKED"),
            ("api key not valid", "API_KEY_INVALID"),
            ("api key expired", "API_KEY_EXPIRED"),
            ("billing is disabled", "BILLING_DISABLED"),
            ("quota exceeded", "QUOTA_EXCEEDED"),
            ("exceeded your current quota", "QUOTA_EXCEEDED"),
        ):
            if needle in lowered:
                reason = reason or diagnosis
                break
    diagnosis = reason or code or status
    description = REASONS.get(diagnosis, HTTP_REASONS.get(status_code, "llm.rejected"))
    fields = {
        "http_status": status_code,
        "api_status": status or "unknown",
        "api_code": code or "unknown",
        "reason": reason or "unknown",
    }
    numeric_code = error.get("code")
    if type(numeric_code) is int and 100 <= numeric_code <= 599:
        fields["api_numeric_code"] = numeric_code
    if retry:
        fields["retry_after_s"] = retry
    return LLMResponseError(
        "http_error" if status_code >= 300 else "api_error", description, **fields
    )


def generation_error(value, field: str) -> LLMResponseError:
    reason = known_reason(value)
    return LLMResponseError(
        "incomplete_output" if reason in {"MAX_TOKENS", "LENGTH"} else "generation_stopped",
        REASONS.get(reason, "llm.no_result"),
        **{field: reason or "unknown"},
    )


def describe_failure(exc: Exception) -> tuple[str, str, dict]:
    if isinstance(exc, LLMResponseError):
        return exc.category, exc.describe(locale="en"), exc.fields
    if isinstance(exc, httpx.HTTPStatusError):
        error = api_error(exc.response.status_code, {})
        return error.category, error.describe(locale="en"), error.fields
    network = (
        (httpx.ConnectTimeout, "connect_timeout", "llm.connect_timeout"),
        (httpx.ReadTimeout, "read_timeout", "llm.read_timeout"),
        (httpx.WriteTimeout, "write_timeout", "llm.write_timeout"),
        (httpx.PoolTimeout, "pool_timeout", "llm.pool_timeout"),
        (TimeoutError, "request_timeout", "llm.request_timeout"),
        (httpx.ConnectError, "connection_failed", "llm.connection_failed"),
        (httpx.RemoteProtocolError, "remote_protocol_error", "llm.remote_protocol_error"),
        (httpx.NetworkError, "network_error", "llm.network_error"),
        (httpx.RequestError, "request_error", "llm.request_error"),
    )
    for kind, category, message in network:
        if isinstance(exc, kind):
            fields = {}
            cause = exc
            for _ in range(8):
                if isinstance(cause, ssl.SSLCertVerificationError):
                    fields["network_reason"] = "tls_certificate_verification_failed"
                    message = "llm.tls"
                    break
                if isinstance(cause, OSError) and isinstance(cause.errno, int):
                    fields["os_errno"] = cause.errno
                cause = cause.__cause__ or cause.__context__
                if cause is None:
                    break
            return category, tr(message, locale="en"), fields
    return "unexpected_error", tr("llm.unexpected_error", locale="en"), {}


def localized_failure(exc: Exception) -> str:
    """Render only controlled categories; never display arbitrary exception text."""
    if isinstance(exc, LLMResponseError):
        return exc.user_message
    if isinstance(exc, httpx.HTTPStatusError):
        return api_error(exc.response.status_code, {}).user_message
    category, _, fields = describe_failure(exc)
    key = (
        "tls" if fields.get("network_reason") == "tls_certificate_verification_failed" else category
    )
    return tr("llm." + key)
