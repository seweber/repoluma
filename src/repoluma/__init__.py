"""repoluma: a domain-first, zoomable map of a codebase."""

__version__ = "0.1.0"

#: Map schema versions this release can read. See docs/schema.md for the policy.
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
CURRENT_SCHEMA_VERSION = 2

#: Default map location relative to the repository root.
DEFAULT_MAP_PATH = ".repoluma/map.yaml"
