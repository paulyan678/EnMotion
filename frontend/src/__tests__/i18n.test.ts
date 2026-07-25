import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import {
    APP_LOCALE,
    getMessages,
    MESSAGE_CATALOG_LOCALES,
} from '@/lib/i18n';
import { APPROVED_NEWAPI_MODELS, getModelTranslationKey } from '@/lib/newApiModels';

const canonicalModelNamePaths = new Set(
    APPROVED_NEWAPI_MODELS.flatMap((model) => {
        const translationKey = getModelTranslationKey(model.id);
        return translationKey ? [`models.${translationKey}.name`] : [];
    }),
);

function visitMessageStrings(
    value: unknown,
    visit: (message: string, keyPath: string) => void,
    keyPath = '',
) {
    if (typeof value === 'string') {
        visit(value, keyPath);
        return;
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) return;
    for (const [key, child] of Object.entries(value)) {
        visitMessageStrings(child, visit, keyPath ? `${keyPath}.${key}` : key);
    }
}

function tsxFilesUnder(root: string): string[] {
    return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
        const entryPath = path.join(root, entry.name);
        if (entry.isDirectory()) return tsxFilesUnder(entryPath);
        return entry.name.endsWith('.tsx') ? [entryPath] : [];
    });
}

function duplicateJsonKeys(file: string): string[] {
    const sourceFile = ts.parseJsonText(file, readFileSync(file, 'utf8'));
    const duplicates: string[] = [];

    const visit = (node: ts.Node, keyPath = ''): void => {
        if (ts.isObjectLiteralExpression(node)) {
            const seen = new Set<string>();
            for (const property of node.properties) {
                if (!ts.isPropertyAssignment(property)) {
                    visit(property, keyPath);
                    continue;
                }
                const key = ts.isStringLiteralLike(property.name) || ts.isIdentifier(property.name)
                    ? property.name.text
                    : property.name.getText(sourceFile);
                const propertyPath = keyPath ? `${keyPath}.${key}` : key;
                if (seen.has(key)) duplicates.push(propertyPath);
                seen.add(key);
                visit(property.initializer, propertyPath);
            }
            return;
        }
        if (ts.isArrayLiteralExpression(node)) {
            node.elements.forEach((element) => visit(element, keyPath));
            return;
        }
        ts.forEachChild(node, (child) => visit(child, keyPath));
    };

    visit(sourceFile);
    return duplicates;
}

function messageAtPath(messages: unknown, keyPath: string): unknown {
    return keyPath.split('.').reduce<unknown>((value, key) => {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
        return (value as Record<string, unknown>)[key];
    }, messages);
}

describe('i18n configuration', () => {
    it('ships the application in Simplified Chinese', () => {
        expect(APP_LOCALE).toBe('zh');
    });

    it('getMessages returns messages for zh', () => {
        const messages = getMessages('zh');
        expect(messages).toBeDefined();
        expect(messages.common.save).toBe('保存');
        expect(messages.nav.workspace).toBe('工作区');
        expect(messages.workspace.title).toBe('工作区');
        expect(messages.settings.title).toBe('设置');
        expect(messages.models.seriesGenSettings).toBe('系列生成设置');
    });

    it('getMessages returns messages for en', () => {
        const messages = getMessages('en');
        expect(messages).toBeDefined();
        expect(messages.common.save).toBe('Save');
        expect(messages.nav.workspace).toBe('Workspace');
        expect(messages.workspace.title).toBe('Workspace');
        expect(messages.settings.title).toBe('Settings');
        expect(messages.models.seriesGenSettings).toBe('Series Generation Settings');
    });

    it('zh and en have identical key structure', () => {
        const zh = getMessages('zh');
        const en = getMessages('en');

        const getKeys = (obj: Record<string, unknown>, prefix = ''): string[] => {
            return Object.entries(obj).flatMap(([key, value]) => {
                const path = prefix ? `${prefix}.${key}` : key;
                if (typeof value === 'object' && value !== null) {
                    return getKeys(value as Record<string, unknown>, path);
                }
                return [path];
            });
        };

        const zhKeys = getKeys(zh).sort();
        const enKeys = getKeys(en).sort();
        expect(zhKeys).toEqual(enKeys);
    });

    it('does not retain decorative top-level panel overline messages', () => {
        const removedOverlineKeys = [
            'ui.workspace.eyebrow',
            'ui.library.eyebrow',
            'ui.playground.freeformStudio',
            'apiCalls.eyebrow',
            'settings.eyebrowGeneral',
            'settings.eyebrowModels',
            'settings.eyebrowPrompts',
            'settings.eyebrowApikeys',
            'settings.settingsEyebrow',
            'playground.header.eyebrowAccent',
        ];

        for (const locale of MESSAGE_CATALOG_LOCALES) {
            const messages = getMessages(locale);
            for (const keyPath of removedOverlineKeys) {
                expect(messageAtPath(messages, keyPath), `${locale}:${keyPath}`).toBeUndefined();
            }
        }
    });

    it('locale JSON files contain no duplicate keys', () => {
        for (const locale of MESSAGE_CATALOG_LOCALES) {
            const file = path.join(process.cwd(), 'messages', `${locale}.json`);
            expect(duplicateJsonKeys(file)).toEqual([]);
        }
    });

    it('Chinese mode preserves canonical English model names', () => {
        const zhModels = getMessages('zh').models;

        for (const model of APPROVED_NEWAPI_MODELS) {
            const translationKey = getModelTranslationKey(model.id);
            expect(translationKey).toBeDefined();
            const localizedModel = zhModels[translationKey! as keyof typeof zhModels];
            expect(localizedModel).toMatchObject({ name: model.name });
        }
    });

    it('Chinese messages contain no English prose', () => {
        const unexpectedEnglish: string[] = [];
        visitMessageStrings(getMessages('zh'), (message, keyPath) => {
            if (canonicalModelNamePaths.has(keyPath)) return;
            if (keyPath === 'ui.brand.nameStart' || keyPath === 'ui.brand.nameEnd') return;
            // Preserve API/file-format identifiers while rejecting natural-language English.
            const proseOnly = message
                .replace(/\{[^}]+\}/g, '')
                .replace(/https?:\/\/\S+/gi, '')
                .replace(/oss-cn-beijing\.aliyuncs\.com/gi, '')
                .replace(/EnMotion|gpt-image-2/gi, '')
                .replace(/JPG|PNG|WebP|MP4|MP3|WAV|MB|px/gi, '')
                .replace(/character\d*|camera/gi, '')
                .replace(/\.(?:txt|md|pdf|mp4)\b/gi, '');
            if (/[A-Za-z]{2,}/.test(proseOnly)) {
                unexpectedEnglish.push(`${keyPath}: ${message}`);
            }
        });
        expect(unexpectedEnglish).toEqual([]);
    });

    it('visible TSX literals contain no unlocalized English UI copy', () => {
        const visibleAttributes = new Set([
            'placeholder', 'title', 'aria-label', 'alt', 'label', 'subtitle',
            'englishName', 'statusLabel', 'data-tip', 'desc', 'hint',
        ]);
        const allowedTechnicalLiterals = new Set([
            'EnMotion 工作室', 'EnMotion', 'En', 'Motion', '&rsaquo;', '&quot;',
            'https://example.com/v1', 'enmotion',
        ]);
        const unexpectedEnglish: string[] = [];
        const roots = [path.join(process.cwd(), 'src/app'), path.join(process.cwd(), 'src/components')];

        const check = (file: string, sourceFile: ts.SourceFile, node: ts.Node, text: string) => {
            const normalized = text.replace(/\s+/g, ' ').trim();
            if (
                !/[A-Za-z]/.test(normalized)
                || allowedTechnicalLiterals.has(normalized)
                || /^\d+p$/i.test(normalized)
            ) return;
            const { line } = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
            unexpectedEnglish.push(`${path.relative(process.cwd(), file)}:${line + 1}: ${normalized}`);
        };

        const checkAttributeExpression = (file: string, sourceFile: ts.SourceFile, node: ts.Node): void => {
            // Translation calls contain English key names, not rendered copy.
            if (ts.isCallExpression(node)) return;
            if (ts.isStringLiteralLike(node)) {
                if (
                    ts.isBinaryExpression(node.parent) &&
                    [
                        ts.SyntaxKind.EqualsEqualsToken,
                        ts.SyntaxKind.EqualsEqualsEqualsToken,
                        ts.SyntaxKind.ExclamationEqualsToken,
                        ts.SyntaxKind.ExclamationEqualsEqualsToken,
                    ].includes(node.parent.operatorToken.kind)
                ) return;
                check(file, sourceFile, node, node.text);
                return;
            }
            if (ts.isTemplateExpression(node)) {
                check(file, sourceFile, node.head, node.head.text);
                for (const span of node.templateSpans) {
                    checkAttributeExpression(file, sourceFile, span.expression);
                    check(file, sourceFile, span.literal, span.literal.text);
                }
                return;
            }
            ts.forEachChild(node, (child) => checkAttributeExpression(file, sourceFile, child));
        };

        for (const file of roots.flatMap(tsxFilesUnder)) {
            const sourceFile = ts.createSourceFile(
                file,
                readFileSync(file, 'utf8'),
                ts.ScriptTarget.Latest,
                true,
                ts.ScriptKind.TSX,
            );
            const visit = (node: ts.Node) => {
                if (ts.isJsxText(node)) check(file, sourceFile, node, node.getText(sourceFile));
                if (ts.isJsxAttribute(node) && visibleAttributes.has(node.name.getText(sourceFile))) {
                    const initializer = node.initializer;
                    if (initializer && ts.isStringLiteral(initializer)) {
                        check(file, sourceFile, initializer, initializer.text);
                    } else if (
                        initializer && ts.isJsxExpression(initializer) &&
                        initializer.expression
                    ) {
                        checkAttributeExpression(file, sourceFile, initializer.expression);
                    }
                }
                ts.forEachChild(node, visit);
            };
            visit(sourceFile);
        }

        expect(unexpectedEnglish).toEqual([]);
    });

    it('uses Chinese units in visible motion-duration options', () => {
        for (const component of [
            'src/components/modules/CharacterWorkbench.tsx',
            'src/components/modules/ScenePropWorkbench.tsx',
        ]) {
            const source = readFileSync(path.join(process.cwd(), component), 'utf8');
            expect(source).not.toMatch(/label:\s*["'`]\d+s["'`]/);
            for (const duration of [5, 10, 15]) {
                expect(source).toContain(`label: "${duration}秒"`);
            }
        }
    });

    it('getMessages falls back to zh for unknown locale', () => {
        // @ts-expect-error testing invalid input
        const messages = getMessages('fr');
        expect(messages.common.save).toBe('保存');
    });

    it('application providers and settings expose no runtime language switch', () => {
        const providers = readFileSync(path.join(process.cwd(), 'src/components/Providers.tsx'), 'utf8');
        const settings = readFileSync(path.join(process.cwd(), 'src/components/settings/SettingsPage.tsx'), 'utf8');
        const store = readFileSync(path.join(process.cwd(), 'src/store/settingsStore.ts'), 'utf8');

        expect(providers).toContain('locale={APP_LOCALE}');
        expect(providers).not.toContain('s.locale');
        expect(settings).not.toMatch(/\bsetLocale\b|languageField|languageDesc/);
        expect(store).not.toMatch(/\bsetLocale\b|\blocale:/);
    });
});
