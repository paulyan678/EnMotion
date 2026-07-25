/**
 * Tests for Phase 2: Custom prompt configuration logic.
 * - PromptConfig defaults and fallback
 * - API payload construction for prompt config
 * - Polish API script_id passthrough
 */
import { describe, it, expect } from 'vitest';

// ── PromptConfig fallback logic (mirrors backend get_effective_prompt) ──

function getEffectivePrompt(customValue: string, systemDefault: string): string {
    return customValue.trim() ? customValue : systemDefault;
}

describe('getEffectivePrompt (fallback logic)', () => {
    const SYSTEM_DEFAULT = "You are an expert video prompt engineer...";

    it('should return system default when custom is empty', () => {
        expect(getEffectivePrompt("", SYSTEM_DEFAULT)).toBe(SYSTEM_DEFAULT);
    });

    it('should return system default when custom is whitespace', () => {
        expect(getEffectivePrompt("   ", SYSTEM_DEFAULT)).toBe(SYSTEM_DEFAULT);
    });

    it('should return custom value when provided', () => {
        const custom = "My custom prompt for video generation";
        expect(getEffectivePrompt(custom, SYSTEM_DEFAULT)).toBe(custom);
    });

    it('should preserve custom value with leading/trailing whitespace', () => {
        const custom = "  My custom prompt  ";
        expect(getEffectivePrompt(custom, SYSTEM_DEFAULT)).toBe(custom);
    });
});

// ── Placeholder substitution (mirrors llm.py template.replace()) ──

function substituteStoryboardPlaceholders(template: string, assets: string, draft: string): string {
    return template.replace("{ASSETS}", assets).replace("{DRAFT}", draft);
}

describe('substituteStoryboardPlaceholders', () => {
    it('should replace both {ASSETS} and {DRAFT}', () => {
        const template = "Assets: {ASSETS}\nDraft: {DRAFT}";
        const result = substituteStoryboardPlaceholders(template, "Image 1: Hero", "A hero stands");
        expect(result).toBe("Assets: Image 1: Hero\nDraft: A hero stands");
    });

    it('should handle template with no placeholders', () => {
        const template = "No placeholders here";
        const result = substituteStoryboardPlaceholders(template, "assets", "draft");
        expect(result).toBe("No placeholders here");
    });

    it('should handle empty assets and draft', () => {
        const template = "Assets: {ASSETS}\nDraft: {DRAFT}";
        const result = substituteStoryboardPlaceholders(template, "", "");
        expect(result).toBe("Assets: \nDraft: ");
    });
});

// ── Video polish payload with script_id ──

function buildVideoPolishPayload(draftPrompt: string, feedback: string = "", scriptId: string = "") {
    return {
        draft_prompt: draftPrompt,
        feedback: feedback,
        script_id: scriptId,
    };
}

describe('buildVideoPolishPayload with scriptId', () => {
    it('should include empty script_id by default', () => {
        const payload = buildVideoPolishPayload("prompt");
        expect(payload.script_id).toBe("");
    });

    it('should include script_id when provided', () => {
        const payload = buildVideoPolishPayload("prompt", "", "project-123");
        expect(payload.script_id).toBe("project-123");
    });

    it('should include all fields together', () => {
        const payload = buildVideoPolishPayload("prompt", "feedback text", "project-123");
        expect(payload).toEqual({
            draft_prompt: "prompt",
            feedback: "feedback text",
            script_id: "project-123",
        });
    });
});

// ── PromptConfig update payload ──

function buildPromptConfigPayload(config: {
    storyboard_polish?: string;
    video_polish?: string;
}) {
    return {
        storyboard_polish: config.storyboard_polish ?? "",
        video_polish: config.video_polish ?? "",
    };
}

describe('buildPromptConfigPayload', () => {
    it('should default all fields to empty string', () => {
        const payload = buildPromptConfigPayload({});
        expect(payload).toEqual({
            storyboard_polish: "",
            video_polish: "",
        });
    });

    it('should preserve provided values', () => {
        const payload = buildPromptConfigPayload({
            video_polish: "Custom video prompt",
        });
        expect(payload.video_polish).toBe("Custom video prompt");
        expect(payload.storyboard_polish).toBe("");
    });

    it('should handle all fields provided', () => {
        const payload = buildPromptConfigPayload({
            storyboard_polish: "A",
            video_polish: "B",
        });
        expect(payload).toEqual({
            storyboard_polish: "A",
            video_polish: "B",
        });
    });
});

// ── _get_custom_prompt helper logic (mirrors api.py helper) ──

function getCustomPrompt(
    scriptId: string,
    field: string,
    projects: Record<string, { prompt_config?: Record<string, string> }>,
): string {
    if (!scriptId) return "";
    const script = projects[scriptId];
    if (script && script.prompt_config) {
        return script.prompt_config[field] || "";
    }
    return "";
}

describe('getCustomPrompt (backend helper mirror)', () => {
    const projects: Record<string, { prompt_config?: Record<string, string> }> = {
        "proj-1": {
            prompt_config: {
                storyboard_polish: "Custom storyboard",
                video_polish: "",
            }
        },
        "proj-2": {}
    };

    it('should return empty string when scriptId is empty', () => {
        expect(getCustomPrompt("", "video_polish", projects)).toBe("");
    });

    it('should return custom value when set', () => {
        expect(getCustomPrompt("proj-1", "storyboard_polish", projects)).toBe("Custom storyboard");
    });

    it('should return empty string when field is empty', () => {
        expect(getCustomPrompt("proj-1", "video_polish", projects)).toBe("");
    });

    it('should return empty string when project has no prompt_config', () => {
        expect(getCustomPrompt("proj-2", "video_polish", projects)).toBe("");
    });

    it('should return empty string when project does not exist', () => {
        expect(getCustomPrompt("nonexistent", "video_polish", projects)).toBe("");
    });
});
