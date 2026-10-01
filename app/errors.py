from __future__ import annotations

from typing import Any


class APIError(Exception):
    """业务错误，携带 HTTP 状态码与机器可读错误码。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}


def bad_request(code: str, message: str) -> APIError:
    return APIError(400, code, message)


def not_found(code: str, message: str) -> APIError:
    return APIError(404, code, message)


def conflict(
    code: str, message: str, details: dict[str, Any] | None = None
) -> APIError:
    return APIError(409, code, message, details)
