'use client';

import { useRef, useState } from 'react';
import { ImagePlus, Film, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { playgroundApi } from '@/lib/api';
import { getAssetUrl } from '@/lib/utils';
import PreviewImage from '@/components/shared/preview/PreviewImage';
import { usePlaygroundStore, type PlaygroundMode } from './usePlaygroundStore';
import AssetPickerModal from './AssetPickerModal';
import { toast } from '@/store/toastStore';

// ---------------------------------------------------------------------------
// Mode config
// ---------------------------------------------------------------------------

interface ModeConfig {
  labelKey: string;
  accept: string;
  hintKey: string;
  multiple: boolean;
  maxFiles: number;
  icon: 'image' | 'video';
}

const MODE_CONFIG: Partial<Record<PlaygroundMode, ModeConfig>> = {
  t2i: {
    labelKey: 'media.labelReferenceOptional',
    accept: 'image/*',
    hintKey: 't2i',
    multiple: true,
    maxFiles: 9,
    icon: 'image',
  },
  i2i: {
    labelKey: 'compose.mediaReference',
    accept: 'image/*',
    hintKey: 'i2i',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
  i2v: {
    labelKey: 'compose.mediaFirstFrame',
    accept: 'image/*',
    hintKey: 'i2v',
    multiple: false,
    maxFiles: 1,
    icon: 'image',
  },
};

// ---------------------------------------------------------------------------
// Shared style tokens (Line B — semantic tokens only, theme-safe)
// ---------------------------------------------------------------------------

// Neutral glass action button (本地上传 / 替换文件 / 从资产库选取). Replaces the
// old `border-primary/30 text-primary` accent so the panel reads quiet in Line B.
const ACTION_BTN_CLASS =
  'flex-1 px-3 py-1.5 rounded-full text-xs border border-border-subtle ' +
  'text-foreground/80 hover:bg-hover-bg hover:text-foreground ' +
  'transition-colors disabled:opacity-40';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getFileName(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

function isVideoPath(path: string): boolean {
  return /\.(mp4|mov|webm|avi|mkv)$/i.test(path);
}

function isOwnedPlaygroundUpload(path: string): boolean {
  if (/^(https?:|data:|blob:)/i.test(path)) return false;
  return /(^|\/)playground\/uploads\//.test(path);
}

async function deleteOwnedPlaygroundUpload(path: string): Promise<void> {
  await playgroundApi.deleteUpload(path);
}

// ---------------------------------------------------------------------------
// Single-reference preview — Line B media-preview-row (thumb + name + meta).
//
// Used for maxFiles=1 modes (i2i / i2v first-frame). Mirrors
// the mockup's `.media-preview-row`: a larger thumbnail on the left, file name +
// "W × H · FORMAT" meta on the right. Dimensions are read from the loaded media
// (onLoad / onLoadedMetadata); format is derived from the extension. File size is
// intentionally omitted — it is not persisted past the upload moment (and is
// absent entirely for asset-library picks), so showing it would be inconsistent.
// Keyed by `path` at the call site so dims reset when the reference changes.
// ---------------------------------------------------------------------------

function SingleRefPreview({
  path,
  onRemove,
  removing,
}: {
  path: string;
  onRemove: () => void;
  removing?: boolean;
}) {
  const [meta, setMeta] = useState<string | null>(null);
  const ext = (path.split('.').pop() || '').toUpperCase();
  const video = isVideoPath(path);

  return (
    <div className="flex items-center gap-3 p-3 rounded-[14px] bg-surface-inset border border-border-subtle">
      <div className="group relative w-20 h-20 shrink-0 rounded-[12px] overflow-hidden bg-elevated border border-border-subtle">
        {video ? (
          <video
            src={getAssetUrl(path)}
            className="w-full h-full object-cover"
            muted
            onLoadedMetadata={(e) =>
              setMeta(
                `${e.currentTarget.videoWidth} × ${e.currentTarget.videoHeight} · ${ext}`
              )
            }
          />
        ) : (
          <PreviewImage
            src={path}
            alt=""
            noLightbox
            className="h-full w-full"
            imgClassName="object-cover"
            onLoad={(e) =>
              setMeta(
                `${e.currentTarget.naturalWidth} × ${e.currentTarget.naturalHeight} · ${ext}`
              )
            }
            diagnosticContext="playground-reference-preview"
          />
        )}

        {/* Remove badge on hover (functional black corner scrim) */}
        <button
          type="button"
          onClick={onRemove}
          disabled={removing}
          aria-label="移除参考素材"
          className="absolute right-1 top-1 flex h-10 w-10 items-center justify-center rounded-full bg-black/70 text-white opacity-100 transition-opacity disabled:cursor-wait disabled:opacity-40 sm:h-8 sm:w-8 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100"
        >
          <X className="w-3 h-3" />
        </button>
      </div>

      <div className="min-w-0 flex-1">
        <div className="text-sm text-foreground truncate" title={getFileName(path)}>
          {getFileName(path)}
        </div>
        <div className="font-mono text-[0.6875rem] text-text-muted mt-1">
          {meta ?? ext}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MediaInput() {
  const mode = usePlaygroundStore((s) => s.mode);
  const inputMedia = usePlaygroundStore((s) => s.inputMedia);
  const setInputMedia = usePlaygroundStore((s) => s.setInputMedia);
  const t = useTranslations('playground');

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [deletingPaths, setDeletingPaths] = useState<Set<string>>(() => new Set());
  const [dragOver, setDragOver] = useState(false);
  const [showAssetPicker, setShowAssetPicker] = useState(false);

  const config = MODE_CONFIG[mode];

  // Don't render for t2v mode (no input media needed)
  if (!config) return null;

  const hasMedia = inputMedia.length > 0;
  const canAddMore = config.multiple && inputMedia.length < config.maxFiles;

  // -------------------------------------------------------------------------
  // Upload handler
  // -------------------------------------------------------------------------

  const handleFiles = async (files: FileList | File[]) => {
    const fileArray = Array.from(files);
    if (fileArray.length === 0) return;

    // Respect max file limit
    const available = config.multiple
      ? config.maxFiles - inputMedia.length
      : config.maxFiles;
    const toUpload = fileArray.slice(0, available);

    setUploading(true);
    const newPaths: string[] = [];
    try {
      for (const file of toUpload) {
        const result = await playgroundApi.uploadMedia(file);
        newPaths.push(result.path);
      }

      if (config.multiple) {
        setInputMedia([...inputMedia, ...newPaths]);
      } else {
        const previousPath = inputMedia[0];
        if (previousPath && isOwnedPlaygroundUpload(previousPath)) {
          await deleteOwnedPlaygroundUpload(previousPath);
        }
        setInputMedia(newPaths);
      }
    } catch (err) {
      console.error('[MediaInput] upload failed:', err);
      await Promise.allSettled(
        newPaths
          .filter(isOwnedPlaygroundUpload)
          .map((path) => deleteOwnedPlaygroundUpload(path))
      );
      toast.error(t('media.uploadFailed'));
    } finally {
      setUploading(false);
    }
  };

  // -------------------------------------------------------------------------
  // Event handlers
  // -------------------------------------------------------------------------

  const handleClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      handleFiles(e.target.files);
    }
    // Reset so re-selecting the same file works
    e.target.value = '';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files) {
      handleFiles(e.dataTransfer.files);
    }
  };

  const handleRemove = async (index: number) => {
    const path = inputMedia[index];
    if (!path || deletingPaths.has(path)) return;
    setDeletingPaths((current) => new Set(current).add(path));
    try {
      const latestMedia = usePlaygroundStore.getState().inputMedia;
      const hasAnotherLocalReference = latestMedia.some(
        (candidate, candidateIndex) => candidate === path && candidateIndex !== index,
      );
      if (isOwnedPlaygroundUpload(path) && !hasAnotherLocalReference) {
        await deleteOwnedPlaygroundUpload(path);
      }
      const currentMedia = usePlaygroundStore.getState().inputMedia;
      const currentIndex = currentMedia.indexOf(path);
      if (currentIndex >= 0) {
        setInputMedia(currentMedia.filter((_, candidateIndex) => candidateIndex !== currentIndex));
      }
    } catch (err) {
      console.error('[MediaInput] delete failed:', err);
      toast.error(t('media.deleteFailed'));
    } finally {
      setDeletingPaths((current) => {
        const next = new Set(current);
        next.delete(path);
        return next;
      });
    }
  };

  const handleReplace = () => {
    fileInputRef.current?.click();
  };

  const handleAssetSelect = async (path: string) => {
    if (config.multiple) {
      if (inputMedia.length < config.maxFiles) setInputMedia([...inputMedia, path]);
      return;
    }
    const previousPath = inputMedia[0];
    try {
      if (previousPath && isOwnedPlaygroundUpload(previousPath)) {
        await deleteOwnedPlaygroundUpload(previousPath);
      }
      setInputMedia([path]);
    } catch (err) {
      console.error('[MediaInput] replacement cleanup failed:', err);
      toast.error(t('media.deleteFailed'));
    }
  };

  // Determine accept type for AssetPickerModal
  const acceptType: 'image' | 'video' | 'all' = config.icon === 'video' ? 'video' : 'image';

  // -------------------------------------------------------------------------
  // Render: hidden file input
  // -------------------------------------------------------------------------

  const fileInput = (
    <input
      ref={fileInputRef}
      type="file"
      accept={config.accept}
      multiple={config.multiple}
      onChange={handleFileChange}
      className="hidden"
    />
  );

  // -------------------------------------------------------------------------
  // Render: empty state — Line B reference slot (recessed drop target)
  //
  // The section label is provided by the parent SectionCard (PlaygroundPage),
  // so this component renders only the slot + actions to avoid a double header.
  // -------------------------------------------------------------------------

  if (!hasMedia) {
    return (
      <div className="space-y-2">
        <div
          role="button"
          tabIndex={uploading ? -1 : 0}
          aria-disabled={uploading}
          aria-label={t('media.localUpload')}
          onClick={handleClick}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              handleClick();
            }
          }}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`
            border border-dashed rounded-[14px] p-6 bg-input-bg
            flex flex-col items-center gap-3 text-center cursor-pointer
            transition-colors
            ${
              dragOver
                ? 'border-primary/60 bg-primary/8 shadow-[var(--glow-primary)]'
                : 'border-border-subtle hover:border-foreground/30 hover:bg-hover-bg'
            }
            ${uploading ? 'pointer-events-none opacity-60' : ''}
          `}
        >
          {config.icon === 'video' ? (
            <Film className="w-8 h-8 text-text-muted" />
          ) : (
            <ImagePlus className="w-8 h-8 text-text-muted" />
          )}

          <span className="text-xs text-text-secondary">
            {uploading ? t('media.uploading') : t('media.dragOrClick')}
          </span>

          <span className="text-[0.6875rem] text-text-muted">{t(`media.hints.${config.hintKey}`)}</span>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleClick}
            disabled={uploading}
            className={ACTION_BTN_CLASS}
          >
            {t('media.localUpload')}
          </button>
          <button
            type="button"
            onClick={() => setShowAssetPicker(true)}
            className={ACTION_BTN_CLASS}
          >
            {t('media.pickFromLibrary')}
          </button>
        </div>

        {fileInput}

        <AssetPickerModal
          isOpen={showAssetPicker}
          onClose={() => setShowAssetPicker(false)}
          onSelect={handleAssetSelect}
          accept={acceptType}
        />
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Render: has media state
  //
  // Optional multi-reference T2I uses a thumbnail grid. I2I/I2V use a single
  // media-preview row.
  // (larger thumb + file name + dimensions·format), per the mockup.
  // -------------------------------------------------------------------------

  return (
    <div className="space-y-2">
      {config.multiple ? (
        <div className="space-y-3">
          {/* Thumbnail row */}
          <div className="flex flex-wrap gap-2">
            {inputMedia.map((path, index) => (
              <div
                key={path + index}
                className="group relative w-[72px] h-[72px] rounded-[14px] overflow-hidden bg-elevated border border-border-subtle"
              >
                {isVideoPath(path) ? (
                  <video
                    src={getAssetUrl(path)}
                    className="w-full h-full object-cover"
                    muted
                  />
                ) : (
                  <PreviewImage
                    src={path}
                    alt=""
                    noLightbox
                    className="h-full w-full"
                    imgClassName="object-cover"
                    diagnosticContext="playground-reference-thumbnail"
                  />
                )}

                {/* Remove badge on hover (functional black corner scrim) */}
                <button
                  type="button"
                  onClick={() => { void handleRemove(index); }}
                  disabled={deletingPaths.has(path)}
                  aria-label="移除参考素材"
                  className="
                    absolute top-1 right-1
                    w-10 h-10 rounded-full sm:w-8 sm:h-8
                    bg-black/70 text-white
                    flex items-center justify-center
                    opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100
                    transition-opacity
                    disabled:cursor-wait disabled:opacity-40
                  "
                >
                  <X className="w-3 h-3" />
                </button>

                {/* File name — bottom gradient scrim (functional, theme-agnostic) */}
                <div className="absolute bottom-0 left-0 right-0 px-1 py-0.5 bg-gradient-to-t from-black/75 to-transparent text-[0.5625rem] text-white truncate">
                  {getFileName(path)}
                </div>
              </div>
            ))}

            {/* Add another optional image reference. */}
            {canAddMore && (
              <button
                type="button"
                onClick={handleClick}
                disabled={uploading}
                aria-label={t('media.localUpload')}
                className="
                  w-[72px] h-[72px] rounded-[14px] bg-input-bg
                  border border-dashed border-border-subtle
                  flex items-center justify-center
                  text-text-muted hover:text-foreground hover:border-foreground/30 hover:bg-hover-bg
                  transition-colors disabled:opacity-40
                "
              >
                <ImagePlus className="w-5 h-5" />
              </button>
            )}
          </div>

          {/* File count for optional image references. */}
          <div className="font-mono text-[0.6875rem] text-text-muted">
            {t('media.fileCount', { current: inputMedia.length, max: config.maxFiles })}
          </div>
        </div>
      ) : (
        <SingleRefPreview
          key={inputMedia[0]}
          path={inputMedia[0]}
          onRemove={() => { void handleRemove(0); }}
          removing={deletingPaths.has(inputMedia[0])}
        />
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleReplace}
          disabled={uploading}
          className={ACTION_BTN_CLASS}
        >
          {uploading ? t('media.uploading') : t('media.replaceFile')}
        </button>
        <button
          type="button"
          onClick={() => setShowAssetPicker(true)}
          className={ACTION_BTN_CLASS}
        >
          {t('media.pickFromLibrary')}
        </button>
      </div>

      {fileInput}

      <AssetPickerModal
        isOpen={showAssetPicker}
        onClose={() => setShowAssetPicker(false)}
        onSelect={(path) => { void handleAssetSelect(path); }}
        accept={acceptType}
      />
    </div>
  );
}
