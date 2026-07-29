'use client';

import { Bookmark, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';
import type { PlaygroundLibraryCategory } from '@/lib/api';

interface LibrarySaveMenuProps {
  saved: boolean;
  saving: boolean;
  variant?: 'icon' | 'full';
  onSelect: (category: PlaygroundLibraryCategory) => void;
}

const CATEGORIES: PlaygroundLibraryCategory[] = ['character', 'scene', 'prop'];

export default function LibrarySaveMenu({
  saved,
  saving,
  variant = 'icon',
  onSelect,
}: LibrarySaveMenuProps) {
  const t = useTranslations('playground');
  const tl = useTranslations('library');
  const label = saved ? t('card.saved') : t('card.saveToLibrary');

  const categoryLabel = (category: PlaygroundLibraryCategory) => (
    category === 'character'
      ? tl('characterLabel')
      : category === 'scene'
        ? tl('sceneLabel')
        : tl('propLabel')
  );

  return (
    <div
      className={`relative focus-within:ring-2 focus-within:ring-focus-ring ${
        variant === 'full'
          ? 'w-full rounded-full'
          : 'h-10 w-10 rounded-full'
      }`}
      onClick={(event) => event.stopPropagation()}
    >
      <div
        aria-hidden="true"
        className={variant === 'full'
          ? 'inline-flex w-full items-center justify-center gap-2 rounded-full bg-primary px-4 py-2.5 text-sm font-medium text-on-accent shadow-[var(--glow-primary)] transition hover:bg-primary-hover disabled:cursor-default disabled:opacity-70'
          : `flex h-10 w-10 items-center justify-center rounded-full backdrop-blur-sm transition disabled:cursor-default ${
              saved ? 'bg-primary/15' : 'bg-elevated hover:bg-hover-bg'
            }`}
      >
        {saving
          ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
          : <Bookmark aria-hidden="true" className={`${variant === 'icon' ? 'h-3.5 w-3.5' : 'h-4 w-4'} ${saved ? 'fill-current text-primary' : variant === 'icon' ? 'text-foreground' : ''}`} />}
        {variant === 'full' ? label : null}
      </div>
      <select
        value=""
        disabled={saved || saving}
        aria-label={label}
        aria-busy={saving}
        title={label}
        onChange={(event) => {
          const category = event.target.value as PlaygroundLibraryCategory;
          if (CATEGORIES.includes(category)) onSelect(category);
        }}
        className="absolute inset-0 h-full w-full cursor-pointer appearance-none opacity-0 disabled:cursor-default"
      >
        <option value="" disabled>{label}</option>
        {CATEGORIES.map((category) => (
          <option key={category} value={category}>
            {categoryLabel(category)}
          </option>
        ))}
      </select>
    </div>
  );
}
