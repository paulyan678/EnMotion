"use client";

import SharedAssetEditor from "@/components/assets/SharedAssetEditor";
import type { AssetOwnerKind, EditableAssetType } from "@/lib/api";

/**
 * Backwards-compatible launcher retained for external imports.
 *
 * It intentionally owns no editor state or API orchestration. New call sites
 * should render SharedAssetEditor directly with an AssetRef.
 */
export interface LibraryAssetEditorProps {
  open: boolean;
  sourceKind: AssetOwnerKind;
  sourceId: string;
  assetType: EditableAssetType;
  assetId: string;
  onClose: () => void;
  onSaved: () => void;
}

export default function LibraryAssetEditor({
  open,
  sourceKind,
  sourceId,
  assetType,
  assetId,
  onClose,
  onSaved,
}: LibraryAssetEditorProps) {
  return (
    <SharedAssetEditor
      open={open}
      assetRef={{
        ownerKind: sourceKind,
        ownerId: sourceId,
        assetType,
        assetId,
      }}
      onClose={onClose}
      onMutated={() => onSaved()}
      onConverted={() => onSaved()}
    />
  );
}
