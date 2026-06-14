"""Domain service: ImageSpec — deterministic image cache key.

Pure domain value object. Zero framework imports.

ImageSpec computes a deterministic image tag (cache identity) from:
  - ``base_image``: the base OS image used in the Containerfile FROM directive
  - ``tooling_fingerprint``: a hash of the Containerfile content and pinned
    tool versions

The derived ``tag`` is the single source of cache identity — it is never
copied or stored separately; callers always read it from ``ImageSpec.tag``.

Determinism guarantee: given the same (base_image, tooling_fingerprint) pair,
``tag`` always returns the same string.  Changing either input changes the tag,
triggering a cache miss in :class:`~agent_sandbox.infrastructure.image_builder.ContainerfileImageBuilder`.

Domain layer rules (ADR-001):
  - No imports from ``subprocess``, ``click``, ``fastapi``, ``sqlalchemy``,
    ``docker``, ``podman``, or any infrastructure/application module.
  - Only stdlib imports (``dataclasses``, ``hashlib``) are permitted here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class ImageSpec:
    """Deterministic image specification and cache-key holder.

    A frozen dataclass so it is hashable, equality-comparable, and safe to
    use as a dict key.  The ``tag`` property is derived (not stored) to
    ensure a single source of truth for the cache identity.

    Attributes:
        base_image: Base OS image referenced in the Containerfile FROM
            directive, e.g. ``"ubuntu:22.04"``.
        tooling_fingerprint: A hash string derived from the Containerfile
            content and all pinned tool versions.  Changing any pinned
            version or Containerfile instruction changes this string and
            therefore changes ``tag``, invalidating the local cache entry.

    Example::

        spec = ImageSpec(
            base_image="ubuntu:22.04",
            tooling_fingerprint="sha256-abc123...",
        )
        print(spec.tag)  # "agent-sandbox:a3f9b2c1d4e5f6a7"
    """

    base_image: str
    tooling_fingerprint: str

    @property
    def tag(self) -> str:
        """Compute the deterministic image tag (cache key).

        The tag is a pure function of ``base_image`` and ``tooling_fingerprint``.
        The format is ``agent-sandbox:<hex-digest>`` where the hex digest is
        the first 16 characters of SHA-256(``base_image:tooling_fingerprint``).

        Returns:
            A lowercase, colon-separated Docker/Podman-compatible image tag,
            e.g. ``"agent-sandbox:a3f9b2c1d4e5"``.  Always lowercase, never
            contains whitespace.

        Notes:
            - Identical inputs → identical tag (determinism guarantee)
            - Different inputs → different tag (collision probability negligible
              for the set of build inputs this project handles)
            - The tag never contains spaces, tabs, or newlines
            - The ``agent-sandbox:`` name prefix is stable; only the digest
              portion changes when inputs change
        """
        raw = f"{self.base_image}:{self.tooling_fingerprint}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"agent-sandbox:{digest}"
