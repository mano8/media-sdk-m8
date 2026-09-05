"""
Executable S3 conformance contract for the media object-storage backend.

``contract`` holds the machine-readable case table; ``CONTRACT.md`` is its
human-readable rendering. ``test_contract_spec.py`` proves the two agree and
that no row of the migration plan's §4 survives only as prose.

The docker-backed suite that *executes* these cases is added by
``T1-conformance-harness``; this package is the specification it must satisfy.
"""
