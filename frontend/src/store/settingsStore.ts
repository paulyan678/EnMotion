import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * 5 预设主题（Tasty Sam 主题系统）。
 * 3 暗（atelier-dark 默认 / bridge-dark / brand-dark）+ 2 亮（atelier-light / brand-light）。
 * 与 globals.css 的 html.<id> block、Providers/layout 切换逻辑一一对应。
 */
export type ThemePreset =
    | 'atelier-dark'
    | 'bridge-dark'
    | 'brand-dark'
    | 'atelier-light'
    | 'brand-light';

export const THEME_PRESETS: ThemePreset[] = [
    'atelier-dark',
    'bridge-dark',
    'brand-dark',
    'atelier-light',
    'brand-light',
];

export const DEFAULT_THEME: ThemePreset = 'atelier-dark';

interface SettingsStore {
    theme: ThemePreset;
    // 全局动效开关。true = 启用 motion（默认）；false = 降低动效，
    // 由 Providers 挂载 html.no-motion 类来落地（无障碍/性能偏好）。
    animations: boolean;
    setTheme: (theme: ThemePreset) => void;
    setAnimations: (animations: boolean) => void;
}

export const useSettingsStore = create<SettingsStore>()(
    persist(
        (set) => ({
            theme: DEFAULT_THEME,
            animations: true,
            setTheme: (theme: ThemePreset) => set({ theme }),
            setAnimations: (animations: boolean) => set({ animations }),
        }),
        {
            name: 'enmotion-settings',
            version: 2,
            // v0→v1：旧版只有 'dark' | 'light'。按产品决策，统一升级到新默认
            // atelier-dark（不保留旧观感）。v2 移除语言偏好，客户端固定使用
            // 简体中文；迁移时只保留当前仍受支持的设置，避免旧的英文偏好回流。
            migrate: (persisted: unknown) => {
                const state = (persisted ?? {}) as Partial<SettingsStore>;
                const animations = typeof state.animations === 'boolean' ? state.animations : true;
                const theme = THEME_PRESETS.includes(state.theme as ThemePreset)
                    ? state.theme as ThemePreset
                    : DEFAULT_THEME;
                return { theme, animations } as SettingsStore;
            },
        }
    )
);
