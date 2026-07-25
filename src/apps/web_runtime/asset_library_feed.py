"""Versioned, lightweight Asset Library read models.

The editing pipeline persists rich project records because editors need prompts,
storyboards, generation histories, and every media variant.  The Home Asset
Library needs only card metadata and the selected thumbnail.  Keeping that
projection explicit prevents accidental large responses and gives readers one
strict schema to validate.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..comic_gen.models import Character, GlobalAssetLibrary, ImageVariant, Prop, Scene
from .asset_usage import AssetUsageIndex, build_asset_usage_index
from .media_derivatives import (
    DerivativeState,
    ImageDerivativeVariant,
    resolve_image_derivatives,
)

logger = logging.getLogger(__name__)

ASSET_LIBRARY_FEED_VERSION = 2
ASSET_LIBRARY_RESPONSIVE_FEED_VERSION = 3
AssetType = Literal["character", "scene", "prop"]
AssetSourceKind = Literal["series", "project", "global"]
AssetSourceFilter = Literal["series", "project", "episode", "global"]
AssetSort = Literal["default", "name", "recent", "usage"]
AssetOrder = Literal["asc", "desc"]


class AssetLibraryThumbnail(BaseModel):
    """The single image required to render one library card."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=16_384)
    created_at: float = 0.0


class AssetLibraryFeedItem(BaseModel):
    """A compact, owner-aware card record."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=2_000)
    description: str = Field(default="", max_length=20_000)
    asset_type: AssetType
    source_kind: AssetSourceKind
    source_id: str = Field(min_length=1, max_length=256)
    source_name: str = Field(min_length=1, max_length=2_000)
    series_id: Optional[str] = Field(default=None, max_length=256)
    episode_id: Optional[str] = Field(default=None, max_length=256)
    starred: bool = False
    thumbnail: Optional[AssetLibraryThumbnail] = None
    variant_count: int = Field(default=0, ge=0)
    updated_at: float = 0.0
    usage_count: int = Field(default=0, ge=0)


class AssetLibrarySnapshot(BaseModel):
    """Immutable all-card projection stored inside one committed revision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = ASSET_LIBRARY_FEED_VERSION
    revision: int = Field(ge=0)
    generated_at: float
    items: list[AssetLibraryFeedItem]


class AssetLibraryFacets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    all: int = Field(ge=0)
    characters: int = Field(ge=0)
    scenes: int = Field(ge=0)
    props: int = Field(ge=0)
    starred: int = Field(ge=0)


class AssetLibraryPageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    count: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool
    next_offset: Optional[int] = Field(default=None, ge=0)


class AssetLibraryFeedResponse(BaseModel):
    """Strict public response returned by ``GET /library/feed``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = ASSET_LIBRARY_FEED_VERSION
    revision: int = Field(ge=0)
    generated_at: float
    items: list[AssetLibraryFeedItem]
    facets: AssetLibraryFacets
    page: AssetLibraryPageInfo


class AssetLibraryDerivative(BaseModel):
    """One immutable responsive source for a library card."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2_048)
    width: int = Field(ge=1, le=32_768)
    height: int = Field(ge=1, le=32_768)
    mime_type: Literal["image/webp"] = "image/webp"
    byte_size: int = Field(ge=1)


class AssetLibraryResponsiveThumbnail(AssetLibraryThumbnail):
    """Version 3 thumbnail contract with durable identity and dimensions."""

    media_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: Optional[int] = Field(default=None, ge=1, le=32_768)
    height: Optional[int] = Field(default=None, ge=1, le=32_768)
    aspect_ratio: Optional[float] = Field(default=None, gt=0, le=100)
    mime_type: Optional[str] = Field(default=None, max_length=128)
    byte_size: Optional[int] = Field(default=None, ge=1)
    state: DerivativeState
    derivatives: list[AssetLibraryDerivative] = Field(default_factory=list)
    failure_code: Optional[str] = Field(default=None, max_length=64)


class AssetLibraryFeedItemV3(AssetLibraryFeedItem):
    thumbnail: Optional[AssetLibraryResponsiveThumbnail] = None


class AssetLibraryFeedResponseV3(BaseModel):
    """Rolling-deploy-safe responsive feed served from a separate route."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[3] = ASSET_LIBRARY_RESPONSIVE_FEED_VERSION
    revision: int = Field(ge=0)
    generated_at: float
    items: list[AssetLibraryFeedItemV3]
    facets: AssetLibraryFacets
    page: AssetLibraryPageInfo


def _selected_variant(
    variants: Sequence[ImageVariant] | None,
    selected_id: Optional[str],
) -> Optional[ImageVariant]:
    values = list(variants or [])
    if not values:
        return None
    selected = next((item for item in values if item.id == selected_id), None)
    return selected or values[0]


def _legacy_thumbnail(url: Optional[str]) -> Optional[AssetLibraryThumbnail]:
    clean = str(url or "").strip()
    if not clean:
        return None
    digest = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]
    return AssetLibraryThumbnail(id=f"legacy-{digest}", url=clean, created_at=0.0)


def _character_thumbnail(
    asset: Character,
) -> tuple[Optional[AssetLibraryThumbnail], int]:
    units = (
        getattr(asset, "reference_sheet", None),
        getattr(asset, "full_body", None),
        getattr(asset, "three_views", None),
        getattr(asset, "head_shot", None),
    )
    for unit in units:
        variants = list(getattr(unit, "image_variants", None) or [])
        if not variants:
            continue
        selected = _selected_variant(variants, getattr(unit, "selected_image_id", None))
        assert selected is not None
        return (
            AssetLibraryThumbnail(
                id=selected.id,
                url=selected.url,
                created_at=selected.created_at,
            ),
            len(variants),
        )

    image_assets = (
        getattr(asset, "full_body_asset", None),
        getattr(asset, "three_view_asset", None),
        getattr(asset, "headshot_asset", None),
    )
    for image_asset in image_assets:
        variants = list(getattr(image_asset, "variants", None) or [])
        if not variants:
            continue
        selected = _selected_variant(variants, getattr(image_asset, "selected_id", None))
        assert selected is not None
        return (
            AssetLibraryThumbnail(
                id=selected.id,
                url=selected.url,
                created_at=selected.created_at,
            ),
            len(variants),
        )

    for value in (
        getattr(asset, "image_url", None),
        getattr(asset, "full_body_image_url", None),
        getattr(asset, "avatar_url", None),
        getattr(asset, "headshot_image_url", None),
        getattr(asset, "three_view_image_url", None),
    ):
        thumbnail = _legacy_thumbnail(value)
        if thumbnail:
            return thumbnail, 1
    return None, 0


def _scene_or_prop_thumbnail(
    asset: Scene | Prop,
) -> tuple[Optional[AssetLibraryThumbnail], int]:
    image_assets = (getattr(asset, "image_asset", None),)
    for image_asset in image_assets:
        variants = list(getattr(image_asset, "variants", None) or [])
        if not variants:
            continue
        selected = _selected_variant(variants, getattr(image_asset, "selected_id", None))
        assert selected is not None
        return (
            AssetLibraryThumbnail(
                id=selected.id,
                url=selected.url,
                created_at=selected.created_at,
            ),
            len(variants),
        )

    legacy_unit = getattr(asset, "image", None)
    variants = list(getattr(legacy_unit, "image_variants", None) or [])
    if variants:
        selected = _selected_variant(variants, getattr(legacy_unit, "selected_image_id", None))
        assert selected is not None
        return (
            AssetLibraryThumbnail(
                id=selected.id,
                url=selected.url,
                created_at=selected.created_at,
            ),
            len(variants),
        )

    for value in (
        getattr(asset, "image_url", None),
        getattr(asset, "reference_image_url", None),
    ):
        thumbnail = _legacy_thumbnail(value)
        if thumbnail:
            return thumbnail, 1
    return None, 0


def _item(
    asset: Character | Scene | Prop,
    *,
    asset_type: AssetType,
    source_kind: AssetSourceKind,
    source_id: str,
    source_name: str,
    series_id: Optional[str] = None,
    episode_id: Optional[str] = None,
    usage_index: AssetUsageIndex,
) -> AssetLibraryFeedItem:
    if asset_type == "character":
        thumbnail, variant_count = _character_thumbnail(asset)
    else:
        thumbnail, variant_count = _scene_or_prop_thumbnail(asset)
    timestamps = [
        float(getattr(asset, "updated_at", 0.0) or 0.0),
        float(getattr(thumbnail, "created_at", 0.0) or 0.0),
    ]
    return AssetLibraryFeedItem(
        id=asset.id,
        name=asset.name or asset.id,
        description=getattr(asset, "description", "") or "",
        asset_type=asset_type,
        source_kind=source_kind,
        source_id=source_id,
        source_name=source_name,
        series_id=series_id,
        episode_id=episode_id,
        starred=bool(getattr(asset, "starred", False)),
        thumbnail=thumbnail,
        variant_count=variant_count,
        updated_at=max(timestamps),
        usage_count=usage_index.count_for((source_kind, source_id, asset_type, asset.id)),
    )


def _owner_items(
    owner: Any,
    *,
    source_kind: AssetSourceKind,
    source_name: str,
    usage_index: AssetUsageIndex,
) -> Iterable[AssetLibraryFeedItem]:
    series_id = (
        owner.id
        if source_kind == "series"
        else getattr(owner, "series_id", None) if source_kind == "project" else None
    )
    episode_id = owner.id if source_kind == "project" else None
    for asset_type, values in (
        ("character", owner.characters),
        ("scene", owner.scenes),
        ("prop", owner.props),
    ):
        for asset in values:
            yield _item(
                asset,
                asset_type=asset_type,
                source_kind=source_kind,
                source_id=owner.id,
                source_name=source_name,
                series_id=series_id,
                episode_id=episode_id,
                usage_index=usage_index,
            )


def build_asset_library_snapshot(
    *,
    revision: int,
    series: Iterable[Any],
    projects: Iterable[Any],
    library: GlobalAssetLibrary,
    generated_at: Optional[float] = None,
) -> AssetLibrarySnapshot:
    """Build a deterministic card projection from one coherent metadata view."""

    series_values = list(series)
    project_values = list(projects)
    usage_index = build_asset_usage_index(
        projects=project_values,
        series=series_values,
        library=library,
    )
    logger.info(
        "Asset usage index cache=miss revision=%s assets=%s "
        "processed_references=%s relationships=%s ambiguous_assets=%s build_ms=%.3f",
        revision,
        usage_index.asset_count,
        usage_index.processed_reference_count,
        usage_index.relationship_count,
        usage_index.ambiguous_asset_count,
        usage_index.build_ms,
    )
    items: list[AssetLibraryFeedItem] = []
    for owner in series_values:
        items.extend(
            _owner_items(
                owner,
                source_kind="series",
                source_name=owner.title or owner.id,
                usage_index=usage_index,
            )
        )
    for owner in project_values:
        # Series-owned assets live in the canonical Series pool and must not be
        # duplicated under each episode. A true project override is retained,
        # however, because it shadows the shared/global identity at runtime.
        if getattr(owner, "series_id", None) and not any(
            getattr(owner, attribute, None) for attribute in ("characters", "scenes", "props")
        ):
            continue
        items.extend(
            _owner_items(
                owner,
                source_kind="project",
                source_name=owner.title or owner.id,
                usage_index=usage_index,
            )
        )
    for asset_type, values in (
        ("character", library.characters),
        ("scene", library.scenes),
        ("prop", library.props),
    ):
        for asset in values:
            items.append(
                _item(
                    asset,
                    asset_type=asset_type,
                    source_kind="global",
                    source_id="global",
                    source_name="Global",
                    usage_index=usage_index,
                )
            )
    # Duplicate ids inside one owner/type are invalid because the public
    # canonical identity cannot address them independently. The usage index
    # already treats that identity as ambiguous (zero usage); expose only the
    # first persisted card so pagination and exact-owner mutations remain
    # deterministic instead of returning duplicate composite keys.
    unique_items: list[AssetLibraryFeedItem] = []
    seen_identities: set[tuple[str, str, str, str]] = set()
    duplicate_count = 0
    for item in items:
        identity = (
            item.source_kind,
            item.source_id,
            item.asset_type,
            item.id,
        )
        if identity in seen_identities:
            duplicate_count += 1
            continue
        seen_identities.add(identity)
        unique_items.append(item)
    if duplicate_count:
        logger.warning(
            "Asset Library omitted ambiguous duplicate identities revision=%s count=%s",
            revision,
            duplicate_count,
        )
    return AssetLibrarySnapshot(
        revision=revision,
        generated_at=generated_at if generated_at is not None else time.time(),
        items=unique_items,
    )


def facets_for(items: Sequence[AssetLibraryFeedItem]) -> AssetLibraryFacets:
    characters = sum(item.asset_type == "character" for item in items)
    scenes = sum(item.asset_type == "scene" for item in items)
    props = sum(item.asset_type == "prop" for item in items)
    return AssetLibraryFacets(
        all=len(items),
        characters=characters,
        scenes=scenes,
        props=props,
        starred=sum(item.starred for item in items),
    )


def query_asset_library_snapshot(
    snapshot: AssetLibrarySnapshot,
    *,
    asset_type: Optional[AssetType] = None,
    source_kind: Optional[AssetSourceKind] = None,
    project_id: Optional[str] = None,
    series_id: Optional[str] = None,
    starred: bool = False,
    search: str = "",
    sort: AssetSort = "default",
    order: Optional[AssetOrder] = None,
    offset: int = 0,
    limit: int = 50,
) -> AssetLibraryFeedResponse:
    """Filter and page before URL signing or media resolution."""

    values = list(snapshot.items)
    query = " ".join(search.split()).casefold()
    if source_kind:
        values = [item for item in values if item.source_kind == source_kind]
    if project_id:
        values = [item for item in values if item.episode_id == project_id]
    if series_id:
        values = [item for item in values if item.series_id == series_id]
    if starred:
        values = [item for item in values if item.starred]
    if query:
        values = [
            item
            for item in values
            if query in item.name.casefold() or query in item.description.casefold()
        ]
    facet_values = values
    if asset_type:
        values = [item for item in values if item.asset_type == asset_type]
    effective_order: AssetOrder = order or ("desc" if sort in {"recent", "usage"} else "asc")
    identity_key = lambda item: (
        item.source_kind,
        item.source_id,
        item.asset_type,
        item.id,
    )
    if sort == "name":
        values.sort(key=identity_key)
        values.sort(
            key=lambda item: item.name.casefold(),
            reverse=effective_order == "desc",
        )
    elif sort == "recent":
        values.sort(key=lambda item: (item.name.casefold(), *identity_key(item)))
        values.sort(
            key=lambda item: item.updated_at,
            reverse=effective_order == "desc",
        )
    elif sort == "usage":
        # Only the usage value changes direction. Equal-count items always use
        # a deterministic, human-readable ascending tie-breaker.
        values.sort(key=lambda item: (item.name.casefold(), *identity_key(item)))
        values.sort(
            key=lambda item: item.usage_count,
            reverse=effective_order == "desc",
        )
    else:
        source_order = {"series": 0, "project": 1, "global": 2}
        type_order = {"character": 0, "scene": 1, "prop": 2}
        values.sort(
            key=lambda item: (
                source_order[item.source_kind],
                item.source_id,
                type_order[item.asset_type],
                item.id,
            ),
            reverse=effective_order == "desc",
        )

    total = len(values)
    effective_offset = min(offset, total)
    page_items = values[effective_offset : effective_offset + limit]
    next_offset = effective_offset + len(page_items)
    has_more = next_offset < total
    return AssetLibraryFeedResponse(
        revision=snapshot.revision,
        generated_at=snapshot.generated_at,
        items=page_items,
        facets=facets_for(facet_values),
        page=AssetLibraryPageInfo(
            offset=effective_offset,
            limit=limit,
            count=len(page_items),
            total=total,
            has_more=has_more,
            next_offset=next_offset if has_more else None,
        ),
    )


def _responsive_derivative(
    value: ImageDerivativeVariant,
) -> AssetLibraryDerivative:
    return AssetLibraryDerivative(
        url=value.url,
        width=value.width,
        height=value.height,
        mime_type=value.mime_type,
        byte_size=value.byte_size,
    )


def responsive_asset_library_page(
    page: AssetLibraryFeedResponse,
    *,
    output_root: str,
    schedule: bool = True,
) -> tuple[AssetLibraryFeedResponseV3, str]:
    """Attach only page-local derivative metadata.

    Originals remain the fallback while a bounded background build is pending.
    The returned fingerprint changes when a manifest becomes ready, allowing
    the v3 ETag to revalidate without changing the authoritative asset revision.
    """

    items: list[AssetLibraryFeedItemV3] = []
    fingerprint = hashlib.sha256()
    for item in page.items:
        thumbnail = item.thumbnail
        responsive_thumbnail: Optional[AssetLibraryResponsiveThumbnail] = None
        if thumbnail is not None:
            lookup = resolve_image_derivatives(
                output_root,
                thumbnail.url,
                schedule=schedule,
            )
            derivatives = [
                _responsive_derivative(value)
                for value in sorted(lookup.variants, key=lambda value: value.width)
            ]
            primary = next(
                (value for value in derivatives if value.width >= 384),
                derivatives[-1] if derivatives else None,
            )
            ready = lookup.state == "ready" and primary is not None
            width = (
                lookup.original_width
                if lookup.original_width is not None
                else primary.width if primary is not None else None
            )
            height = (
                lookup.original_height
                if lookup.original_height is not None
                else primary.height if primary is not None else None
            )
            responsive_thumbnail = AssetLibraryResponsiveThumbnail(
                id=thumbnail.id,
                # Keep an authenticated original as the <picture> fallback.
                # Supported browsers select the typed WebP srcset below;
                # legacy/unsupported clients retain the previous behavior.
                url=thumbnail.url,
                created_at=thumbnail.created_at,
                media_id=lookup.media_id,
                revision=lookup.revision,
                width=width,
                height=height,
                aspect_ratio=(width / height) if width and height else None,
                mime_type=lookup.original_mime_type,
                byte_size=lookup.original_byte_size,
                state=lookup.state,
                derivatives=derivatives,
                failure_code=lookup.failure_code,
            )
            fingerprint.update(lookup.source_key.encode("ascii"))
            fingerprint.update(b"\0")
            fingerprint.update(lookup.revision.encode("ascii"))
            fingerprint.update(b"\0")
            fingerprint.update(lookup.state.encode("ascii"))
            fingerprint.update(b"\0")
        else:
            fingerprint.update(b"none\0")
        items.append(
            AssetLibraryFeedItemV3.model_validate(
                {
                    **item.model_dump(mode="python", exclude={"thumbnail"}),
                    "thumbnail": (
                        responsive_thumbnail.model_dump(mode="python")
                        if responsive_thumbnail is not None
                        else None
                    ),
                }
            )
        )
    return (
        AssetLibraryFeedResponseV3(
            revision=page.revision,
            generated_at=page.generated_at,
            items=items,
            facets=page.facets,
            page=page.page,
        ),
        fingerprint.hexdigest(),
    )
