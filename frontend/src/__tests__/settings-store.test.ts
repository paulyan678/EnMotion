// @vitest-environment happy-dom

import { describe, it, expect, beforeEach } from 'vitest';
import { useSettingsStore, THEME_PRESETS, DEFAULT_THEME } from '@/store/settingsStore';

describe('settingsStore', () => {
    beforeEach(() => {
        localStorage.clear();
        useSettingsStore.setState({ theme: DEFAULT_THEME });
    });

    it('has correct default values', () => {
        const state = useSettingsStore.getState();
        expect(state.theme).toBe(DEFAULT_THEME);
    });

    it('setTheme updates theme', () => {
        useSettingsStore.getState().setTheme('brand-light');
        expect(useSettingsStore.getState().theme).toBe('brand-light');
    });

    it('does not expose a locale switch', () => {
        const state = useSettingsStore.getState() as unknown as Record<string, unknown>;
        expect(state.locale).toBeUndefined();
        expect(state.setLocale).toBeUndefined();
    });

    it('setTheme accepts every theme preset', () => {
        // All five presets must be settable (guards against enum drift)
        for (const preset of THEME_PRESETS) {
            useSettingsStore.getState().setTheme(preset);
            expect(useSettingsStore.getState().theme).toBe(preset);
        }
    });

    it('exposes exactly the five expected presets', () => {
        expect(THEME_PRESETS).toEqual([
            'atelier-dark',
            'bridge-dark',
            'brand-dark',
            'atelier-light',
            'brand-light',
        ]);
        expect(DEFAULT_THEME).toBe('atelier-dark');
    });
});
