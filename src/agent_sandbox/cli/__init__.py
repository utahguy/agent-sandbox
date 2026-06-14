"""agent_sandbox.cli — presentation-layer CLI package.

This package is the PRESENTATION layer composition root for the agent-sandbox
command-line interface.

Canonical entry point: ``agent_sandbox.cli.main:main``
  Both ``pyproject.toml [project.scripts]`` and the ``Containerfile``
  ENTRYPOINT reference ``agent_sandbox.cli.main`` as the single, importable
  module that wires infrastructure adapters into application use cases.

Usage::

    from agent_sandbox.cli.main import main, EXIT_SANDBOX_ERROR

Architecture:
  cli/__init__.py  — package init; lightweight, no attribute shadows submodule
  cli/main.py      — canonical composition root (Click command + adapter wiring)

Note on submodule visibility:
  This __init__.py intentionally does NOT bind the name ``main`` to the Click
  command, because doing so would shadow the ``cli.main`` submodule and break
  ``import agent_sandbox.cli.main`` (Python would resolve the attribute instead
  of the submodule).  Import directly from the canonical module::

      from agent_sandbox.cli.main import main, EXIT_SANDBOX_ERROR, EXIT_TIMEOUT
"""

# Re-export exit-code constants and the composition-root helper so that
# code which previously did ``from agent_sandbox.cli import EXIT_SANDBOX_ERROR``
# continues to work after the flat-module → package refactoring.
#
# NOTE: We do NOT re-export ``main`` (the Click command) here because that
# would shadow the ``agent_sandbox.cli.main`` *submodule*.  Callers that
# need the Click command must import it from the canonical module path:
#
#     from agent_sandbox.cli.main import main

from agent_sandbox.cli.main import (  # noqa: F401
    EXIT_SANDBOX_ERROR,
    EXIT_SIGINT,
    EXIT_TIMEOUT,
    _build_run_agent_use_case,
)

__all__ = [
    "EXIT_SANDBOX_ERROR",
    "EXIT_TIMEOUT",
    "EXIT_SIGINT",
    "_build_run_agent_use_case",
]
