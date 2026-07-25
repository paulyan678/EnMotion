import type { AssetUnit, Character, ImageAsset, Prop, Scene } from "@/store/projectStore";
import { selectedVariantUrl } from "@/lib/characterImage";
import { getAssetUrl } from "@/lib/utils";

export type AssetImageKind = "character" | "scene" | "prop";
export type ImageBearingAsset = Character | Scene | Prop;

type CharacterWithAssetUnitFallback = Character & {
  full_body?: AssetUnit;
  three_views?: AssetUnit;
  head_shot?: AssetUnit;
};
type AssetWithReferenceUrl = (Scene | Prop) & { reference_image_url?: string };

export function assetUnitAsImageAsset(unit?: AssetUnit | null): ImageAsset | undefined {
  if (!unit?.image_variants?.length) return undefined;
  return { selected_id: unit.selected_image_id, variants: unit.image_variants };
}

/**
 * Return the canonical image container for an asset.
 *
 * Characters are special because persisted records exist in two schema eras:
 * `reference_sheet` is canonical while `full_body_asset` is the legacy
 * fallback. Scenes and props both use `image_asset`.
 */
export function primaryAssetImage(
  asset: ImageBearingAsset,
  kind: AssetImageKind,
): ImageAsset | undefined {
  if (kind === "character") {
    const character = asset as CharacterWithAssetUnitFallback;
    return assetUnitAsImageAsset(character.reference_sheet)
      || assetUnitAsImageAsset(character.full_body)
      || character.full_body_asset
      || assetUnitAsImageAsset(character.three_views)
      || character.three_view_asset
      || assetUnitAsImageAsset(character.head_shot)
      || character.headshot_asset;
  }
  return (asset as Scene | Prop).image_asset;
}

/** Resolve the selected variant, falling back to the first variant and legacy URL fields. */
export function primaryAssetImageUrl(
  asset: ImageBearingAsset,
  kind: AssetImageKind,
): string | undefined {
  if (kind === "character") {
    const character = asset as Character;
    return selectedVariantUrl(primaryAssetImage(character, kind))
      || character.image_url
      || character.full_body_image_url
      || character.avatar_url
      || character.headshot_image_url
      || character.three_view_image_url;
  }

  const media = asset as AssetWithReferenceUrl;
  return selectedVariantUrl(media.image_asset) || media.image_url || media.reference_image_url;
}

/** Resolve an asset image and normalize persisted relative paths for browser display. */
export function primaryAssetDisplayUrl(
  asset: ImageBearingAsset,
  kind: AssetImageKind,
): string {
  return getAssetUrl(primaryAssetImageUrl(asset, kind));
}
