"""Infrastructure (adapter) layer for agent_sandbox.

This package contains concrete implementations of the application-layer ports:

  - FileConfigSource: reads .agent-sandbox / .claude-sandbox config from disk

Dependency rule: infrastructure imports from domain and application (via ports),
but nothing in domain or application imports from infrastructure.
"""
