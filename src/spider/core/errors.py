"""Error vocabulary shared by the server and the CLI.

The server turns these into HTTP responses; the CLI turns the same codes back
into readable messages and process exit codes. Defining both mappings here is
what keeps the two sides in agreement.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    unauthorized = "unauthorized"
    bad_request = "bad_request"
    not_found = "not_found"
    ambiguous_id = "ambiguous_id"
    checksum_mismatch = "checksum_mismatch"
    disk_full = "disk_full"
    unreachable = "unreachable"


_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.unauthorized: 401,
    ErrorCode.bad_request: 400,
    ErrorCode.not_found: 404,
    ErrorCode.ambiguous_id: 409,
    ErrorCode.checksum_mismatch: 422,
    ErrorCode.disk_full: 507,
    ErrorCode.unreachable: 503,
}

_EXIT_CODE: dict[ErrorCode, int] = {
    ErrorCode.unauthorized: 3,
    ErrorCode.not_found: 4,
    ErrorCode.ambiguous_id: 5,
    ErrorCode.checksum_mismatch: 6,
    ErrorCode.disk_full: 7,
    ErrorCode.unreachable: 8,
    ErrorCode.bad_request: 2,
}


def error_payload(code: ErrorCode, message: str) -> dict:
    return {"error": {"code": code.value, "message": message}}


class SpiderError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]

    @property
    def exit_code(self) -> int:
        return _EXIT_CODE[self.code]

    def to_payload(self) -> dict:
        return error_payload(self.code, self.message)
