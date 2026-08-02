'use client';

import { useState, useRef, useEffect, useMemo } from 'react';
import { ChevronDown } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { usePlaygroundStore } from './usePlaygroundStore';
import { getModelsForMode, getModelDisplayInfo, type PlaygroundModelOption } from './playgroundModels';
import { getModelTranslationKey } from '@/lib/newApiModels';

export default function ModelSelector() {
  const mode = usePlaygroundStore((s) => s.mode);
  const modelId = usePlaygroundStore((s) => s.modelId);
  const setModelId = usePlaygroundStore((s) => s.setModelId);
  const t = useTranslations('playground');
  const tm = useTranslations('models');

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const availableModels = useMemo(() => getModelsForMode(mode), [mode]);

  const selected = useMemo(() => {
    const info = getModelDisplayInfo(modelId);
    if (info) return { ...info, id: modelId };
    if (availableModels.length > 0) return availableModels[0];
    return null;
  }, [modelId, availableModels]);

  const localizeModelName = (id: string, fallback: string) => {
    const key = getModelTranslationKey(id);
    return key ? tm(`${key}.name`) : fallback;
  };
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  function handleSelect(id: string) {
    setModelId(id);
    setOpen(false);
  }

  // Group models by family for visual grouping
  const groupedModels = useMemo(() => {
    const groups: { family: string; models: PlaygroundModelOption[] }[] = [];
    let currentFamily = '';
    for (const m of availableModels) {
      if (m.family !== currentFamily) {
        currentFamily = m.family;
        groups.push({ family: currentFamily, models: [m] });
      } else {
        groups[groups.length - 1].models.push(m);
      }
    }
    return groups;
  }, [availableModels]);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className={`flex items-center gap-[10px] w-full text-left cursor-pointer glass-input hover:border-foreground/30 bg-surface-inset rounded-[14px] ${
          open ? 'shadow-[var(--glow-primary)]' : ''
        }`}
      >
        <span className="w-2 h-2 rounded-full bg-primary shrink-0" />
        <span className="flex-1 text-[0.8125rem] font-medium text-foreground truncate">
          {selected ? localizeModelName(selected.id, selected.displayName) : t('model.selectPlaceholder')}
        </span>
        <ChevronDown
          className={`w-3 h-3 text-text-muted shrink-0 transition-transform ${
            open ? 'rotate-180' : ''
          }`}
        />
      </button>

      {open && (
        <div className="absolute top-full mt-1 w-full bg-elevated atelier-card border border-glass-border rounded-lg shadow-xl z-20 max-h-60 overflow-y-auto">
          {availableModels.length === 0 && (
            <div className="px-3 py-2 text-[0.75rem] text-text-muted">{t('model.noModels')}</div>
          )}
          {groupedModels.map((group, gi) => (
            <div key={group.family}>
              {gi > 0 && <div className="border-t border-border-subtle mx-2" />}
              {groupedModels.length > 1 && group.family !== 'newapi' && (
                <div className="px-3 pt-2 pb-1">
                  <span className="font-mono text-[0.5625rem] text-text-muted uppercase tracking-[0.15em]">
                    {group.family}
                  </span>
                </div>
              )}
              {group.models.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => handleSelect(m.id)}
                  className={`flex items-center gap-[10px] w-full px-3 py-2 text-left transition-colors hover:bg-hover-bg ${
                    m.id === modelId ? 'bg-elevated text-foreground' : 'text-foreground/80'
                  }`}
                >
                  <span className="flex-1 text-[0.8125rem] font-medium truncate">
                    {localizeModelName(m.id, m.displayName)}
                  </span>
                  {m.recommended && (
                    <span className="text-[0.5rem] font-mono text-primary bg-primary/10 px-1.5 py-0.5 rounded uppercase tracking-wider shrink-0">
                      {t('model.recommended')}
                    </span>
                  )}
                  {m.id === modelId && (
                    <span className="text-primary shrink-0">✓</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
