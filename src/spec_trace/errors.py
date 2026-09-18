class SpecTraceError(Exception):
    """Base exception for expected application failures."""

    exit_code = 6


class ValidationError(SpecTraceError):
    exit_code = 2


class ResourceNotFound(SpecTraceError):
    exit_code = 3


class StateConflict(SpecTraceError):
    exit_code = 4


class ExternalServiceError(SpecTraceError):
    exit_code = 5


class InvariantViolation(SpecTraceError):
    exit_code = 6
