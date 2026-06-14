"""Infrastructure adapter: FileConfigSource.

Reads raw configuration text from a file on disk.  Implements the
:class:`~agent_sandbox.application.ports.ConfigSourcePort` protocol.

Dependency rule: this module may import from domain, application/ports,
and the Python standard library.  It must NOT import subprocess, click,
docker, podman, sqlalchemy, fastapi, or any other heavy framework.
"""

from __future__ import annotations

from pathlib import Path

from agent_sandbox.exceptions import ErrorCode, SandboxError


class FileConfigSource:
    """Read raw config text from a ``.agent-sandbox`` (or ``.claude-sandbox``) file.

    Implements :class:`~agent_sandbox.application.ports.ConfigSourcePort`.

    Raises :class:`~agent_sandbox.exceptions.SandboxError` (instead of the raw
    :class:`FileNotFoundError`) when the file does not exist, so callers never
    need to handle OS-level exceptions.

    Args:
        path: Filesystem path to the config file.  Accepts both
            :class:`pathlib.Path` and plain :class:`str`.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def read_text(self) -> str:
        """Return the full text of the config file.

        Returns:
            The raw UTF-8 text of the configuration file.

        Raises:
            SandboxError: With code ``CONFIG_MALFORMED`` if the file is not
                found or cannot be read.
        """
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise SandboxError(
                f"Config file not found: {self._path!s}; "
                f"create a '.agent-sandbox' file in your project directory.",
                code=ErrorCode.CONFIG_MALFORMED,
            ) from None
        except OSError as exc:
            raise SandboxError(
                f"Cannot read config file {self._path!s}: {exc}",
                code=ErrorCode.CONFIG_MALFORMED,
            ) from exc
