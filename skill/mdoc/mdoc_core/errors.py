from __future__ import annotations


class MdocError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def payload(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}

