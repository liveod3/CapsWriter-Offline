"""LLM 失败的受控诊断：解析服务端原因，不保存任意响应正文或请求内容。"""

from __future__ import annotations

import re
import ssl

import httpx


HTTP_REASONS = {
    400: "Invalid request or unmet account requirements.",
    401: "API authentication failed. Check the API key.",
    403: "Access denied. Check API key permissions and region availability.",
    404: "Model or API endpoint not found.",
    408: "The provider timed out receiving the request.",
    429: "Rate limit or quota exceeded. Check usage and billing.",
    500: "The provider encountered an internal error.",
    502: "The provider gateway failed.",
    503: "The provider is unavailable or overloaded.",
    504: "The provider gateway timed out.",
}

REASONS = {
    "INVALID_ARGUMENT": "Invalid request parameters.",
    "INVALID_REQUEST": "Invalid request parameters.",
    "FAILED_PRECONDITION": "Account requirements are not met. Check billing and region availability.",
    "UNAUTHENTICATED": "API authentication failed. Check the API key.",
    "AUTHENTICATION": "API authentication failed. Check the API key.",
    "API_KEY_INVALID": "The API key is invalid.",
    "INVALID_API_KEY": "The API key is invalid.",
    "API_KEY_EXPIRED": "The API key has expired.",
    "API_KEY_LEAKED": "The provider blocked an exposed API key. Replace it.",
    "PERMISSION_DENIED": "The API key does not have permission for this resource.",
    "BILLING_DISABLED": "Billing is disabled for this API project.",
    "SERVICE_DISABLED": "The API service is disabled for this project.",
    "NOT_FOUND": "Model or API endpoint not found.",
    "MODEL_NOT_FOUND": "The requested model was not found.",
    "RESOURCE_EXHAUSTED": "Rate limit or quota exceeded. Check usage and billing.",
    "QUOTA_EXCEEDED": "API quota exceeded. Check usage and billing.",
    "INSUFFICIENT_QUOTA": "API quota exceeded. Check usage and billing.",
    "RATE_LIMIT_EXCEEDED": "API rate limit exceeded. Wait before trying again.",
    "TOO_MANY_REQUESTS": "API rate limit exceeded. Wait before trying again.",
    "INTERNAL": "The provider encountered an internal error.",
    "API_ERROR": "The provider encountered an internal error.",
    "UNAVAILABLE": "The provider is unavailable or overloaded.",
    "SERVICE_UNAVAILABLE": "The provider is unavailable or overloaded.",
    "DEADLINE_EXCEEDED": "The provider could not finish within its deadline.",
    "SAFETY": "The provider blocked the content for safety reasons.",
    "CONTENT_FILTER": "The provider filtered the generated content.",
    "CONTENT_BLOCKED": "The provider blocked the content.",
    "RECITATION": "The provider stopped generation because of recitation restrictions.",
    "LANGUAGE": "The provider does not support this language.",
    "BLOCKLIST": "The provider blocked terms in the content.",
    "PROHIBITED_CONTENT": "The provider flagged prohibited content.",
    "SPII": "The provider flagged sensitive personal information.",
    "MAX_TOKENS": "LLM output reached the token limit and may be incomplete.",
    "LENGTH": "LLM output reached the token limit and may be incomplete.",
    "MALFORMED_FUNCTION_CALL": "The provider generated an invalid tool call.",
    "UNEXPECTED_TOOL_CALL": "The provider generated an unexpected tool call.",
    "TOOL_CALLS": "The provider returned tool calls instead of transcription text.",
    "FUNCTION_CALL": "The provider returned a function call instead of transcription text.",
    "OTHER": "The provider stopped generation for an unspecified reason.",
    "MALFORMED_RESPONSE": "The provider generated an invalid response.",
    "MISSING_THOUGHT_SIGNATURE": "The provider requires a missing thought signature.",
    "TOO_MANY_TOOL_CALLS": "The provider stopped after too many tool calls.",
    "IMAGE_SAFETY": "The provider blocked image content for safety reasons.",
    "IMAGE_PROHIBITED_CONTENT": "The provider flagged prohibited image content.",
    "IMAGE_RECITATION": "The provider stopped image generation because of recitation restrictions.",
    "IMAGE_OTHER": "The provider stopped image generation for an unspecified reason.",
    "NO_IMAGE": "The provider returned no required image.",
    "ESCALATION": "The provider filtered the request through an escalation rule.",
}


def known_reason(value) -> str:
    # 枚举也来自网络；未知值不原样记录，避免把伪装成 code 的密钥/文本写入日志。
    return value.upper() if isinstance(value, str) and value.upper() in REASONS else ""


class LLMResponseError(ValueError):
    def __init__(self, category: str, message: str, **fields):
        super().__init__(message)
        self.category = category
        self.user_message = message
        self.fields = fields


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
    # message 可能回显文本、URL 或凭据，只提取明确诊断语义，不保存任意片段。
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
    description = REASONS.get(diagnosis, HTTP_REASONS.get(status_code, "The provider rejected the request."))
    fields = {"http_status": status_code, "api_status": status or "unknown",
              "api_code": code or "unknown", "reason": reason or "unknown"}
    numeric_code = error.get("code")
    if type(numeric_code) is int and 100 <= numeric_code <= 599:
        fields["api_numeric_code"] = numeric_code
    if retry:
        fields["retry_after_s"] = retry
        description += f" Retry after {retry} s."
    return LLMResponseError("http_error" if status_code >= 300 else "api_error", description, **fields)


def generation_error(value, field: str) -> LLMResponseError:
    reason = known_reason(value)
    return LLMResponseError(
        "incomplete_output" if reason in {"MAX_TOKENS", "LENGTH"} else "generation_stopped",
        REASONS.get(reason, "The provider stopped generation without a usable result."),
        **{field: reason or "unknown"},
    )


def describe_failure(exc: Exception) -> tuple[str, str, dict]:
    if isinstance(exc, LLMResponseError):
        return exc.category, exc.user_message, exc.fields
    if isinstance(exc, httpx.HTTPStatusError):
        error = api_error(exc.response.status_code, {})
        return error.category, error.user_message, error.fields
    network = (
        (httpx.ConnectTimeout, "connect_timeout", "Timed out connecting to the LLM provider."),
        (httpx.ReadTimeout, "read_timeout", "Timed out waiting for the LLM response."),
        (httpx.WriteTimeout, "write_timeout", "Timed out sending the LLM request."),
        (httpx.PoolTimeout, "pool_timeout", "Timed out waiting for an available HTTP connection."),
        (TimeoutError, "request_timeout", "The LLM request exceeded its total time limit."),
        (httpx.ConnectError, "connection_failed", "Could not connect to the LLM provider. Check the network."),
        (httpx.RemoteProtocolError, "remote_protocol_error", "The provider interrupted the HTTP response."),
        (httpx.NetworkError, "network_error", "The network connection failed during the LLM request."),
        (httpx.RequestError, "request_error", "The LLM HTTP request failed."),
    )
    for kind, category, message in network:
        if isinstance(exc, kind):
            fields = {}
            cause = exc
            for _ in range(8):
                if isinstance(cause, ssl.SSLCertVerificationError):
                    fields["network_reason"] = "tls_certificate_verification_failed"
                    message = "LLM TLS certificate verification failed. Check certificates and network."
                    break
                if isinstance(cause, OSError) and isinstance(cause.errno, int):
                    fields["os_errno"] = cause.errno
                cause = cause.__cause__ or cause.__context__
                if cause is None:
                    break
            return category, message, fields
    return "unexpected_error", "LLM processing failed. Check the diagnostic log.", {}
