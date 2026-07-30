"""Application use case: ParseConfigUseCase.

Maps raw ``.agent-sandbox`` (or ``.claude-sandbox``) config text into a
validated :class:`~agent_sandbox.domain.entities.SandboxConfig` aggregate.

Dependency rule: this module imports ONLY from ``agent_sandbox.domain``
and the Python standard library.  No subprocess, click, docker, podman,
sqlalchemy, fastapi, or any infrastructure import is permitted here.

Config file format
------------------
Lines beginning with ``#`` are comments and are ignored.
Blank lines are ignored.
Each non-empty, non-comment line is a *directive*::

    volume        HOST_PATH:CONTAINER_PATH[:mode]
    port          HOST_PORT:CONTAINER_PORT[/protocol]
    env           KEY=VALUE
    mise
    memory        VALUE<unit>       # unit: b / k / m / g
    runtime       auto|docker|podman
    packages      PACKAGE_NAME      # apt package; repeat for multiple packages
    claude-config PATH              # host Claude config dir for account selection

Examples::

    volume /src:/workspace:rw
    volume /data:/data:ro
    port 8080:80/tcp
    port 5432:5432
    env API_KEY=s3cr3t
    env DEBUG=1
    mise
    memory 512m
    runtime docker
    claude-config ~/.claude-client-acme
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Optional

from agent_sandbox.domain.entities import SandboxConfig
from agent_sandbox.domain.value_objects import (
    MemoryLimit,
    PortMapping,
    RuntimeKind,
    Volume,
)
from agent_sandbox.exceptions import ErrorCode, SandboxError

# Supported directive names (lower-case)
_KNOWN_DIRECTIVES = frozenset({"volume", "port", "env", "mise", "memory", "runtime", "packages", "claude-config"})

# Valid apt package name: starts with alnum, remainder is alnum, +, -, or .
_PACKAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+\-.]*$")

# Regex for memory values: one or more digits followed by a unit letter
_MEMORY_RE = re.compile(r"^(\d+)([bkmg])$", re.IGNORECASE)


class ParseConfigUseCase:
    """Parse raw config text into a typed :class:`SandboxConfig` aggregate.

    Application layer — no I/O performed here.  The caller is responsible
    for supplying the raw text (e.g. read from disk via
    :class:`~agent_sandbox.infrastructure.file_config_source.FileConfigSource`).

    Usage::

        use_case = ParseConfigUseCase()
        config = use_case.execute(raw_text)
    """

    def execute(self, text: str) -> SandboxConfig:
        """Parse *text* and return a validated :class:`SandboxConfig`.

        Args:
            text: Raw config file contents.

        Returns:
            A fully populated :class:`SandboxConfig` instance (without
            ``config_path`` / ``source_filename`` provenance — those are set
            by the :meth:`SandboxConfig.from_file` facade after parsing).

        Raises:
            SandboxError: With code ``CONFIG_MALFORMED`` for any directive
                that is missing required arguments, has invalid values, or is
                completely unknown.
        """
        volumes: list[Volume] = []
        ports: list[PortMapping] = []
        env: dict[str, str] = {}
        mise: bool = False
        memory_limit: Optional[MemoryLimit] = None
        runtime: RuntimeKind = RuntimeKind.AUTO
        packages: list[str] = []
        claude_config_dir: Optional[Path] = None

        for lineno, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()

            # Skip comments and blank lines
            if not line or line.startswith("#"):
                continue

            parts = line.split(None, 1)  # split on first whitespace
            directive = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if directive not in _KNOWN_DIRECTIVES:
                raise SandboxError(
                    f"Unknown directive {directive!r} on line {lineno}. "
                    f"Supported directives: "
                    f"{', '.join(sorted(_KNOWN_DIRECTIVES))}.",
                    code=ErrorCode.CONFIG_MALFORMED,
                )

            if directive == "volume":
                volumes.append(self._parse_volume(arg, lineno))
            elif directive == "port":
                ports.append(self._parse_port(arg, lineno))
            elif directive == "env":
                key, value = self._parse_env(arg, lineno)
                env[key] = value
            elif directive == "mise":
                mise = True
            elif directive == "memory":
                memory_limit = self._parse_memory(arg, lineno)
            elif directive == "runtime":
                runtime = self._parse_runtime(arg, lineno)
            elif directive == "packages":
                packages.append(self._parse_package(arg, lineno))
            elif directive == "claude-config":
                claude_config_dir = self._parse_claude_config(arg, lineno)

        return SandboxConfig(
            volumes=volumes,
            ports=ports,
            env=env,
            mise=mise,
            memory_limit=memory_limit,
            runtime=runtime,
            packages=packages,
            claude_config_dir=claude_config_dir,
        )

    # ------------------------------------------------------------------
    # Private parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_volume(arg: str, lineno: int) -> Volume:
        """Parse a ``volume`` directive argument into a :class:`Volume`.

        Expected format: ``HOST_PATH:CONTAINER_PATH[:mode]``

        Raises:
            SandboxError: For missing colon, relative/traversal host paths,
                or invalid mode.
        """
        if not arg or ":" not in arg:
            raise SandboxError(
                f"Malformed 'volume' directive on line {lineno}: "
                f"expected HOST_PATH:CONTAINER_PATH[:mode], got {arg!r}. "
                f"Example: volume /src:/workspace:rw",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        # Split into at most 3 parts: host, container, [mode]
        parts = arg.split(":")
        if len(parts) < 2:
            raise SandboxError(
                f"Malformed 'volume' directive on line {lineno}: "
                f"expected HOST_PATH:CONTAINER_PATH, got {arg!r}.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        host_path = parts[0]
        container_path = parts[1]
        mode = parts[2] if len(parts) >= 3 else "rw"

        # Security: reject relative host paths
        if not host_path.startswith("/"):
            raise SandboxError(
                f"Malformed 'volume' directive on line {lineno}: "
                f"host_path must be absolute (start with '/'), got {host_path!r}. "
                f"Example: volume /src:/workspace:rw",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        # Security: reject path traversal
        pure_path = PurePosixPath(host_path)
        if ".." in pure_path.parts:
            raise SandboxError(
                f"Malformed 'volume' directive on line {lineno}: "
                f"host_path contains path traversal ('..'), got {host_path!r}. "
                f"Use an absolute, normalised path without '..' components.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        # Volume constructor validates mode
        return Volume(
            host_path=host_path,
            container_path=container_path,
            mode=mode,
        )

    @staticmethod
    def _parse_port(arg: str, lineno: int) -> PortMapping:
        """Parse a ``port`` directive argument into a :class:`PortMapping`.

        Expected format: ``HOST_PORT:CONTAINER_PORT[/protocol]``

        Raises:
            SandboxError: For missing colon, non-integer ports, out-of-range
                ports, or invalid protocol.
        """
        if not arg or ":" not in arg:
            raise SandboxError(
                f"Malformed 'port' directive on line {lineno}: "
                f"expected HOST_PORT:CONTAINER_PORT[/protocol], got {arg!r}. "
                f"Example: port 8080:80/tcp",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        # Split HOST:REST where REST may be CONTAINER_PORT or CONTAINER_PORT/PROTO
        colon_idx = arg.index(":")
        host_str = arg[:colon_idx]
        rest = arg[colon_idx + 1:]

        # Parse optional protocol suffix from container side
        if "/" in rest:
            slash_idx = rest.index("/")
            container_str = rest[:slash_idx]
            protocol = rest[slash_idx + 1:].lower()
        else:
            container_str = rest
            protocol = "tcp"

        try:
            host_port = int(host_str)
        except ValueError:
            raise SandboxError(
                f"Malformed 'port' directive on line {lineno}: "
                f"host port must be an integer, got {host_str!r}.",
                code=ErrorCode.CONFIG_MALFORMED,
            ) from None

        try:
            container_port = int(container_str)
        except ValueError:
            raise SandboxError(
                f"Malformed 'port' directive on line {lineno}: "
                f"container port must be an integer, got {container_str!r}.",
                code=ErrorCode.CONFIG_MALFORMED,
            ) from None

        # PortMapping constructor validates ranges and protocol
        return PortMapping(
            host_port=host_port,
            container_port=container_port,
            protocol=protocol,
        )

    @staticmethod
    def _parse_env(arg: str, lineno: int) -> tuple[str, str]:
        """Parse an ``env`` directive argument into a ``(key, value)`` pair.

        Expected format: ``KEY=VALUE``  (value may itself contain ``=``).

        Raises:
            SandboxError: If ``=`` is absent.
        """
        if not arg or "=" not in arg:
            raise SandboxError(
                f"Malformed 'env' directive on line {lineno}: "
                f"expected KEY=VALUE, got {arg!r}. "
                f"Example: env API_KEY=s3cr3t",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        key, _, value = arg.partition("=")
        return key, value

    @staticmethod
    def _parse_memory(arg: str, lineno: int) -> MemoryLimit:
        """Parse a ``memory`` directive argument into a :class:`MemoryLimit`.

        Expected format: ``<integer><unit>`` where unit ∈ {b, k, m, g}.
        E.g. ``512m``, ``2g``, ``1024k``.

        Raises:
            SandboxError: For missing argument, non-integer value, or invalid
                unit.
        """
        if not arg:
            raise SandboxError(
                f"Malformed 'memory' directive on line {lineno}: "
                f"expected VALUE<unit> (e.g. 512m), got nothing. "
                f"Valid units: b, k, m, g.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        match = _MEMORY_RE.match(arg)
        if not match:
            raise SandboxError(
                f"Malformed 'memory' directive on line {lineno}: "
                f"expected VALUE<unit> (e.g. 512m), got {arg!r}. "
                f"Valid units: b, k, m, g.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        value = int(match.group(1))
        unit = match.group(2).lower()

        # MemoryLimit constructor validates the unit
        return MemoryLimit(value=value, unit=unit)

    @staticmethod
    def _parse_package(arg: str, lineno: int) -> str:
        """Parse a ``packages`` directive argument into a package name.

        Expected format: a single apt package name (e.g. ``postgresql-client``).
        One package per directive line; repeat the directive for multiple packages.

        Raises:
            SandboxError: For missing or invalid package names.
        """
        if not arg:
            raise SandboxError(
                f"Malformed 'packages' directive on line {lineno}: "
                f"expected a package name (e.g. packages: postgresql-client), got nothing.",
                code=ErrorCode.CONFIG_MALFORMED,
            )
        if not _PACKAGE_NAME_RE.match(arg):
            raise SandboxError(
                f"Malformed 'packages' directive on line {lineno}: "
                f"invalid package name {arg!r}. "
                f"Package names must start with a letter or digit and contain "
                f"only letters, digits, '+', '-', or '.'.",
                code=ErrorCode.CONFIG_MALFORMED,
            )
        return arg

    @staticmethod
    def _parse_runtime(arg: str, lineno: int) -> RuntimeKind:
        """Parse a ``runtime`` directive argument into a :class:`RuntimeKind`.

        Accepted values (case-insensitive): ``auto``, ``docker``, ``podman``.

        Raises:
            SandboxError: For unknown runtime names.
        """
        mapping = {
            "auto": RuntimeKind.AUTO,
            "docker": RuntimeKind.DOCKER,
            "podman": RuntimeKind.PODMAN,
        }
        key = arg.strip().lower()
        if key not in mapping:
            raise SandboxError(
                f"Malformed 'runtime' directive on line {lineno}: "
                f"expected one of auto/docker/podman, got {arg!r}.",
                code=ErrorCode.CONFIG_MALFORMED,
            )
        return mapping[key]

    @staticmethod
    def _parse_claude_config(arg: str, lineno: int) -> Path:
        """Parse a ``claude-config`` directive into an expanded host :class:`Path`.

        Expected format: an absolute or ``~``-prefixed path pointing to a
        Claude config directory, e.g. ``~/.claude-client-acme`` or
        ``/home/alice/.claude-work``.  The ``~`` is expanded via
        :meth:`pathlib.Path.expanduser`.

        Raises:
            SandboxError: With code ``CONFIG_MALFORMED`` if the argument is
                missing or is not an absolute/home-relative path.
        """
        if not arg:
            raise SandboxError(
                f"Malformed 'claude-config' directive on line {lineno}: "
                f"expected a path (e.g. claude-config: ~/.claude-acme), got nothing.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        expanded = Path(arg).expanduser()

        if not str(expanded).startswith("/"):
            raise SandboxError(
                f"Malformed 'claude-config' directive on line {lineno}: "
                f"path must be absolute or start with '~', got {arg!r}.",
                code=ErrorCode.CONFIG_MALFORMED,
            )

        return expanded
