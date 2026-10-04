from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class RuntimeRequirement:
    runtime: str
    minimum: str
    before: str
    required: bool = False


@dataclass(frozen=True, order=True)
class MigrationRequirement:
    migration_id: str
    from_schema: str
    to_schema: str
    requires_backup: bool = True
    reversible: bool = False


@dataclass(frozen=True)
class ReleaseManifest:
    format_version: int
    release_id: str
    chief_version: str
    source_tree_sha256: str
    schema_sha256: str
    capability_versions: tuple[tuple[str, str], ...]
    changed_nodes: tuple[str, ...]
    required_tests: tuple[str, ...]
    runtime_requirements: tuple[RuntimeRequirement, ...]
    model_requirements: tuple[str, ...]
    migrations: tuple[MigrationRequirement, ...]
    rollback_compatible_with: tuple[str, ...]
    benchmark_requirements: tuple[str, ...]
    private_state_excluded: bool
    created_at: str
