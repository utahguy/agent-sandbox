"""Application layer for agent_sandbox.

This package contains:
  - ports.py: typing.Protocol interfaces (I/O-free ports per ADR-001)

Application layer depends only on the domain layer.
No subprocess, click, docker, podman, or any infrastructure imports.
"""
