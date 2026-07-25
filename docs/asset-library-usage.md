# Asset Library usage contract

The Asset Library feed derives `usage_count` from one coherent workspace JSON
snapshot. It is not stored on `Character`, `Scene`, or `Prop`, and PostgreSQL
does not mirror or own this value.

## Canonical identity and resolution

Every asset is addressed internally as:

```text
(owner_kind, owner_id, asset_type, asset_id)
```

`owner_kind` is `project`, `series`, or `global`; the global owner ID is always
`global`. The feed exposes `project` as the canonical API name while accepting
the legacy `episode` source/filter alias.

References from a project resolve with the same precedence as runtime asset
lookup:

```text
project override -> parent series -> global
```

Only the winning canonical asset receives the relationship. IDs are scoped by
asset type. Missing targets, ambiguous duplicate identities, and dangling
references count toward nothing.

## Counted relationships

One use is counted for each distinct persisted logical edge:

- A storyboard frame's `scene_id`, each distinct `character_ids` entry, and
  each distinct `prop_ids` entry.
- A valid, non-self, non-cyclic `Character.base_character_id` lineage edge.

A frame can count a given canonical asset only once, while ten different frames
count ten uses. Relationship keys include the owning project/frame or dependent
character plus the resolved canonical target, so retries and duplicate array
entries cannot inflate a count.

The current persisted schema has no trustworthy owner-qualified external
generation-input relationship. Consequently generation jobs, task attempts,
retries, motion-reference history, `VideoTask.asset_id`, prompts, URLs, selected
variants, and media history do not count. A future generation input may count
only after it persists both a stable logical request ID and a complete canonical
input-asset identity.

Membership in a project, series, or the global library is never usage.
Favorites, views, searches, downloads, textual mentions, imports, promotions,
and forks are also excluded unless they retain one of the explicit relationships
above. `last_used_at` is intentionally absent because no trustworthy
relationship-specific timestamp exists.

## Read model, caching, and invalidation

`GET /library/feed` extends the existing flat, bounded Asset Library read model.
The server builds one immutable `AssetUsageIndex` in
`O(assets + frames + persisted reference edges)`, then joins counts into feed
DTOs. It never scans all projects per card and never reads durable jobs per
asset.

Server workspaces publish versioned, atomically switched sidecar snapshots
fingerprinted to `projects.json`, `series.json`, and `library_assets.json`.
Missing, corrupt, stale, or schema-incompatible sidecars rebuild automatically;
they are caches, never sources of truth. Desktop mode keeps the same immutable
projection in memory until an authoritative JSON save advances its revision.
Worker startup republishes snapshots when authoritative JSON changes.

Successful reference-bearing frontend mutations emit one workspace-scoped,
coalesced invalidation. Active views keep the last validated list visible while
the backend snapshot revalidates. No component adjusts counts optimistically.

## Feed ordering and performance budget

Search and filters run before sorting, and sorting runs before pagination.
Usage ties are deterministic:

1. `usage_count` in the requested direction
2. case-normalized name ascending
3. owner kind
4. owner ID
5. asset type
6. asset ID

`usage` defaults to descending; the application's normal default sort is
unchanged. The regression fixture covers 10,000 assets and 100,000 persisted
frame edges. Warm server-side filtering/sorting/pagination targets p95 at or
below 500 ms and a bounded 50-item response.
