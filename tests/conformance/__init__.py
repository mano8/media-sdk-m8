"""
Executable S3 conformance contract for the media object-storage backend.

``contract`` holds the machine-readable case table; ``CONTRACT.md`` is its
human-readable rendering. ``test_contract_spec.py`` proves the two agree and
that no row of the migration plan's §4 survives only as prose.

``backends`` boots a pinned backend container and ``probe`` observes it over
raw HTTP; ``test_s3_surface`` and ``test_security_invariants`` hold the cases
those two serve. They are marked ``conformance`` and deselected from the
ordinary run — ``HARNESS.md`` explains how to run them and what a result means.
"""
