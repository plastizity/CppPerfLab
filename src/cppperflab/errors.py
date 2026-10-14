"""Public error categories and CLI exit codes."""

EXIT_CODES = {
    "invalid_request": 2, "unsupported_schema": 2, "not_found": 2,
    "unavailable": 3, "permission_denied": 3,
    "tool_failed": 4, "parse_failed": 4, "target_failed": 5,
    "correctness_failed": 5, "timeout": 6, "cancelled": 130,
    "incomparable": 7, "storage_failed": 8, "internal_error": 70,
}


class LabError(Exception):
    def __init__(self, category, message, details=None):
        super().__init__(message)
        self.category = category
        self.details = details or {}

    def as_dict(self):
        return {"category": self.category, "message": str(self),
                "details": self.details, "exit_code": EXIT_CODES[self.category]}
