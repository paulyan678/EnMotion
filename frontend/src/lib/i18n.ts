import zh from '../../messages/zh.json';
import en from '../../messages/en.json';

/**
 * EnMotion's shipped application UI is Simplified Chinese only.  The English
 * catalogue remains available to the test harness while legacy bilingual
 * behavior is retired, but application code must always use APP_LOCALE.
 */
export const APP_LOCALE = 'zh' as const;
export type MessageLocale = 'zh' | 'en';
export const MESSAGE_CATALOG_LOCALES: MessageLocale[] = ['zh', 'en'];

const messages: Record<MessageLocale, typeof zh> = { zh, en };

export function getMessages(locale: MessageLocale) {
    return messages[locale] ?? messages.zh;
}
