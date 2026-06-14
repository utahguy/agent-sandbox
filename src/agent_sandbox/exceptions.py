"""Framework-free exception hierarchy for agent_sandbox.

Domain layer — zero framework imports (no subprocess, click, fastapi, etc.).

All failures in the agent_sandbox system are represented as subclasses of
SandboxError. Each exception carries:
  - message: human-readable description of what went wrong
  - code: machine-readable error code from ErrorCode (per ADR-005)

This enables callers to handle errors programmatically by code while
presenting actionable messages to end users.
"""


class ErrorCode:
    """Machine-readable error codes per ADR-005.

    These string constants are stable identifiers that callers can match
    against without parsing human-readable messages.
    """

    RUNTIME_NOT_FOUND = "RUNTIME_NOT_FOUND"
    CONFIG_MALFORMED = "CONFIG_MALFORMED"
    IMAGE_BUILD_FAILED = "IMAGE_BUILD_FAILED"
    CONTAINER_START_FAILED = "CONTAINER_START_FAILED"
    EXEC_TIMEOUT = "EXEC_TIMEOUT"


class SandboxError(Exception):
    """Root exception for all agent_sandbox errors.

    Carries a human-readable message and a machine-readable code so that
    callers can distinguish failure modes without string parsing.

    Example::

        raise SandboxError(
            "No container runtime found; install Docker or Podman",
            code=ErrorCode.RUNTIME_NOT_FOUND,
        )
    """

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"{self.message!r}, code={self.code!r})"
        )


class TimeoutError(SandboxError):
    """Raised when a container exec command exceeds its allowed duration.

    Distinct from a non-zero exit code (which is returned in ExecResult)
    — TimeoutError means the process was forcibly killed.

    The default code is EXEC_TIMEOUT; callers may override for clarity.

    Attributes:
        timed_out: Always ``True`` — this exception IS a timeout event.
            Provided so callers can check ``exc.timed_out`` without
            ``isinstance`` tests.

    Example::

        raise TimeoutError(
            "Command 'make test' exceeded 30s timeout",
            code=ErrorCode.EXEC_TIMEOUT,
        )
    """

    #: Always True on TimeoutError — the command was killed by a timeout.
    timed_out: bool = True

    def __init__(
        self,
        message: str,
        *,
        code: str = ErrorCode.EXEC_TIMEOUT,
    ) -> None:
        super().__init__(message, code=code)
        self.timed_out = True
