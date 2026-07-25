"""Derived, owner-aware Asset Library usage counts.

``usage_count`` is intentionally a relationship count, not a popularity or
generation-history metric.  One edge is counted for each persisted storyboard
frame that resolves to the exact asset and for each valid, non-cyclic
``base_character_id`` lineage edge.  Resolution follows the runtime precedence
project -> parent series -> global.  Membership, variants, favorites, prompts,
media URLs, and jobs that merely generate the target asset never count.

The canonical identity is always ``(owner_kind, owner_id, asset_type,
asset_id)``.  Raw asset IDs are not globally unique and must never be used as a
mutation or aggregation key on their own.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Optional

AssetOwnerKind = Literal["project", "series", "global"]
AssetType = Literal["character", "scene", "prop"]
CanonicalAssetIdentity = tuple[AssetOwnerKind, str, AssetType, str]


@dataclass(frozen=True, slots=True)
class AssetUsageIndex:
    """Immutable aggregate produced in one pass over a workspace snapshot."""

    counts: Mapping[CanonicalAssetIdentity, int]
    asset_count: int
    relationship_count: int
    processed_reference_count: int
    ambiguous_asset_count: int
    build_ms: float

    def count_for(self, identity: CanonicalAssetIdentity) -> int:
        return int(self.counts.get(identity, 0))


def _assets(owner: Any, asset_type: AssetType) -> list[Any]:
    attribute = {
        "character": "characters",
        "scene": "scenes",
        "prop": "props",
    }[asset_type]
    return list(getattr(owner, attribute, None) or [])


def build_asset_usage_index(
    *,
    projects: Iterable[Any],
    series: Iterable[Any],
    library: Any,
) -> AssetUsageIndex:
    """Build exact-owner usage counts in O(assets + persisted reference edges).

    The current persisted models do not retain any trustworthy, owner-qualified
    external generation-input relationship.  Consequently no generation job is
    counted here.  A future input relationship may be added only when it carries
    a stable logical request identity and a complete canonical asset identity;
    names, prompts, URLs, unqualified ``asset_id`` fields, attempts, and retries
    are deliberately insufficient.
    """

    started = perf_counter()
    project_values = list(projects)
    series_values = list(series)
    series_by_id = {str(owner.id): owner for owner in series_values}
    project_by_id = {str(owner.id): owner for owner in project_values}

    objects: dict[CanonicalAssetIdentity, Any] = {}
    ids_by_owner: dict[tuple[AssetOwnerKind, str, AssetType], set[str]] = {}
    ambiguous_identities: set[CanonicalAssetIdentity] = set()

    def index_owner(
        owner: Any,
        owner_kind: AssetOwnerKind,
        owner_id: str,
    ) -> None:
        for asset_type in ("character", "scene", "prop"):
            owner_key = (owner_kind, owner_id, asset_type)
            ids = ids_by_owner.setdefault(owner_key, set())
            for asset in _assets(owner, asset_type):
                asset_id = str(getattr(asset, "id", "") or "")
                if not asset_id:
                    continue
                identity: CanonicalAssetIdentity = (
                    owner_kind,
                    owner_id,
                    asset_type,
                    asset_id,
                )
                # A duplicate inside the same canonical owner cannot be
                # distinguished by the public identity. Keep one zero-count
                # slot so every visible card still receives an integer, but
                # resolve no relationship to or from the ambiguous identity.
                if identity in objects:
                    ambiguous_identities.add(identity)
                else:
                    objects[identity] = asset
                ids.add(asset_id)

    for owner in series_values:
        index_owner(owner, "series", str(owner.id))
    for owner in project_values:
        index_owner(owner, "project", str(owner.id))
    index_owner(library, "global", "global")

    def identity_if_present(
        owner_kind: AssetOwnerKind,
        owner_id: str,
        asset_type: AssetType,
        asset_id: str,
    ) -> Optional[CanonicalAssetIdentity]:
        identity: CanonicalAssetIdentity = (
            owner_kind,
            owner_id,
            asset_type,
            asset_id,
        )
        if identity in ambiguous_identities:
            return None
        if asset_id not in ids_by_owner.get((owner_kind, owner_id, asset_type), set()):
            return None
        return identity if identity in objects else None

    def resolve_for_project(
        project: Any,
        asset_type: AssetType,
        asset_id: str,
    ) -> Optional[CanonicalAssetIdentity]:
        project_id = str(project.id)
        local = identity_if_present("project", project_id, asset_type, asset_id)
        if local is not None:
            return local
        series_id = str(getattr(project, "series_id", "") or "")
        if series_id and series_id in series_by_id:
            shared = identity_if_present("series", series_id, asset_type, asset_id)
            if shared is not None:
                return shared
        return identity_if_present("global", "global", asset_type, asset_id)

    def resolve_for_owner(
        identity: CanonicalAssetIdentity,
        asset_id: str,
    ) -> Optional[CanonicalAssetIdentity]:
        owner_kind, owner_id, _asset_type, _current_id = identity
        if owner_kind == "project":
            project = project_by_id.get(owner_id)
            return (
                resolve_for_project(project, "character", asset_id) if project is not None else None
            )
        if owner_kind == "series":
            shared = identity_if_present("series", owner_id, "character", asset_id)
            return shared or identity_if_present("global", "global", "character", asset_id)
        return identity_if_present("global", "global", "character", asset_id)

    counts: dict[CanonicalAssetIdentity, int] = {identity: 0 for identity in objects}
    relationship_edges: set[tuple[Any, ...]] = set()
    processed_reference_count = 0

    def add_edge(edge: tuple[Any, ...], target: CanonicalAssetIdentity) -> None:
        if edge in relationship_edges:
            return
        relationship_edges.add(edge)
        counts[target] += 1

    for project in project_values:
        project_id = str(project.id)
        for frame in list(getattr(project, "frames", None) or []):
            frame_id = str(getattr(frame, "id", "") or "")
            if not frame_id:
                continue
            referenced: tuple[tuple[AssetType, str], ...] = (
                *(
                    ("character", str(value))
                    for value in set(getattr(frame, "character_ids", None) or [])
                    if value
                ),
                *(("scene", str(value)) for value in {getattr(frame, "scene_id", None)} if value),
                *(
                    ("prop", str(value))
                    for value in set(getattr(frame, "prop_ids", None) or [])
                    if value
                ),
            )
            for asset_type, asset_id in referenced:
                processed_reference_count += 1
                target = resolve_for_project(project, asset_type, asset_id)
                if target is None:
                    continue
                add_edge(
                    ("frame", project_id, frame_id, *target),
                    target,
                )

    # First resolve every lineage edge, then remove self-references and every
    # edge whose dependent is part of a cycle.  Cyclic lineage is invalid data
    # and must not inflate a base character's usage.
    lineage: dict[CanonicalAssetIdentity, CanonicalAssetIdentity] = {}
    for identity, asset in objects.items():
        if identity[2] != "character" or identity in ambiguous_identities:
            continue
        base_id = str(getattr(asset, "base_character_id", "") or "")
        if not base_id:
            continue
        processed_reference_count += 1
        target = resolve_for_owner(identity, base_id)
        if target is None or target == identity:
            continue
        lineage[identity] = target

    cycle_members: set[CanonicalAssetIdentity] = set()
    visited: set[CanonicalAssetIdentity] = set()
    for origin in lineage:
        if origin in visited:
            continue
        path: list[CanonicalAssetIdentity] = []
        position: dict[CanonicalAssetIdentity, int] = {}
        current: Optional[CanonicalAssetIdentity] = origin
        while current is not None and current in lineage and current not in visited:
            if current in position:
                cycle_members.update(path[position[current] :])
                break
            position[current] = len(path)
            path.append(current)
            current = lineage.get(current)
        visited.update(path)

    for dependent, target in lineage.items():
        if dependent in cycle_members:
            continue
        add_edge(("lineage", *dependent, *target), target)

    return AssetUsageIndex(
        counts=MappingProxyType(counts),
        asset_count=len(objects),
        relationship_count=len(relationship_edges),
        processed_reference_count=processed_reference_count,
        ambiguous_asset_count=len(ambiguous_identities),
        build_ms=round((perf_counter() - started) * 1000, 3),
    )
