import type { AssetOwnerKind, EditableAssetType } from "@/lib/api";
import type {
  ArtDirection,
  Character,
  ModelSettings,
  Project,
  Prop,
  Scene,
} from "@/store/projectStore";

export type EditableAsset = Character | Scene | Prop;

/**
 * Stable identity for an editable domain asset.
 *
 * Asset ids are only unique inside an owner. Keeping the owner in the public
 * editor contract prevents a series/global asset from being accidentally
 * mutated through a project route when the same id exists at multiple levels.
 */
export interface AssetRef {
  ownerKind: AssetOwnerKind;
  ownerId: string;
  assetType: EditableAssetType;
  assetId: string;
  revision?: string | number;
  projectId?: string;
  seriesId?: string;
}

export interface AssetEditorCapabilities {
  staticGeneration: boolean;
  motionGeneration: boolean;
  motionDisabledReason?: string | null;
  uploadPrimaryImage?: boolean;
}

export interface AssetEditorContext {
  ownerLabel?: string;
  ownerScope: AssetOwnerKind;
  affectedEpisodeCount: number;
  artDirection?: ArtDirection | null;
  modelSettings?: ModelSettings | null;
  capabilities: AssetEditorCapabilities;
}

export type AssetEditorResponse = Partial<EditableAsset> & {
  id: string;
  source?: AssetOwnerKind;
  source_id?: string;
  _editor_context?: AssetEditorContext;
};

export interface AssetGenerationOptions {
  modelName?: string;
  aspectRatio?: string;
  templateId?: string;
}

export function assetRefKey(ref: AssetRef): string {
  return `${ref.ownerKind}:${ref.ownerId}:${ref.assetType}:${ref.assetId}`;
}

export function sameAssetRef(left: AssetRef, right: AssetRef): boolean {
  return assetRefKey(left) === assetRefKey(right);
}

/** Normalize resolved Episode assets to the backend's canonical "project" owner. */
export function assetRefFromResolvedAsset(
  asset: EditableAsset,
  assetType: EditableAssetType,
  fallback: { projectId: string; seriesId?: string | null },
): AssetRef {
  if (asset.source === "global") {
    return {
      ownerKind: "global",
      ownerId: asset.source_id || "global",
      assetType,
      assetId: asset.id,
      projectId: fallback.projectId,
      seriesId: fallback.seriesId || undefined,
    };
  }
  if (asset.source === "series") {
    const ownerId = asset.source_id || asset.series_id || fallback.seriesId;
    if (!ownerId) {
      throw new Error(`Series owner is missing for ${assetType} ${asset.id}`);
    }
    return {
      ownerKind: "series",
      ownerId,
      assetType,
      assetId: asset.id,
      projectId: fallback.projectId,
      seriesId: ownerId,
    };
  }
  return {
    ownerKind: "project",
    ownerId: asset.source_id || asset.episode_id || fallback.projectId,
    assetType,
    assetId: asset.id,
    projectId: fallback.projectId,
    seriesId: asset.series_id || fallback.seriesId || undefined,
  };
}

/**
 * Patch one resolved project view without refetching the complete project.
 * The composite identity check is essential when owners reuse the same id.
 */
export function patchResolvedProjectAsset(
  project: Project,
  updated: EditableAsset,
  nextRef: AssetRef,
  previousRef?: AssetRef,
): Project {
  const next: Project = {
    ...project,
    characters: [...(project.characters || [])],
    scenes: [...(project.scenes || [])],
    props: [...(project.props || [])],
  };
  const fallback = {
    projectId: project.id,
    seriesId: project.series_id,
  };
  const matches = (
    candidate: EditableAsset,
    type: EditableAssetType,
    ref: AssetRef,
  ) => sameAssetRef(assetRefFromResolvedAsset(candidate, type, fallback), ref);

  if (previousRef) {
    if (previousRef.assetType === "character") {
      next.characters = next.characters.filter(
        (candidate) => !matches(candidate, "character", previousRef),
      );
    } else if (previousRef.assetType === "scene") {
      next.scenes = next.scenes.filter(
        (candidate) => !matches(candidate, "scene", previousRef),
      );
    } else {
      next.props = next.props.filter(
        (candidate) => !matches(candidate, "prop", previousRef),
      );
    }
  }

  if (nextRef.assetType === "character") {
    const index = next.characters.findIndex((candidate) =>
      matches(candidate, "character", nextRef)
    );
    if (index >= 0) next.characters[index] = updated as Character;
    else next.characters.push(updated as Character);
  } else if (nextRef.assetType === "scene") {
    const index = next.scenes.findIndex((candidate) =>
      matches(candidate, "scene", nextRef)
    );
    if (index >= 0) next.scenes[index] = updated as Scene;
    else next.scenes.push(updated as Scene);
  } else {
    const index = next.props.findIndex((candidate) =>
      matches(candidate, "prop", nextRef)
    );
    if (index >= 0) next.props[index] = updated as Prop;
    else next.props.push(updated as Prop);
  }
  return next;
}
