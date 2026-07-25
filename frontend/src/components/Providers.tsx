"use client";

import { useEffect } from 'react';
import { NextIntlClientProvider } from 'next-intl';
import { useSettingsStore, THEME_PRESETS } from '@/store/settingsStore';
import { APP_LOCALE, getMessages } from '@/lib/i18n';
import { LightboxProvider } from '@/components/shared/preview/LightboxProvider';
import ToastContainer from '@/components/shared/ToastContainer';
import { MotionConfig } from 'framer-motion';
import { AuthProvider } from '@/components/auth/AuthProvider';
import AuthGate from '@/components/auth/AuthGate';
import { UpdaterProvider } from '@/components/update/UpdaterProvider';

export function Providers({ children }: { children: React.ReactNode }) {
    const theme = useSettingsStore((s) => s.theme);
    const animations = useSettingsStore((s) => s.animations);
    const messages = getMessages(APP_LOCALE);

    useEffect(() => {
        const html = document.documentElement;
        // 移除全部 5 个预设 class + 旧版遗留的 dark/light，再加当前主题
        html.classList.remove(...THEME_PRESETS, 'dark', 'light');
        html.classList.add(theme);
    }, [theme]);

    useEffect(() => {
        // animations=false → 挂 html.no-motion，CSS 据此降低/禁用过渡动画
        document.documentElement.classList.toggle('no-motion', !animations);
    }, [animations]);

    useEffect(() => {
        document.documentElement.lang = 'zh-CN';
        document.title = messages.ui.brand.documentTitle;
        document.querySelector('meta[name="description"]')?.setAttribute('content', messages.ui.brand.documentDescription);
    }, [messages]);

    return (
        <NextIntlClientProvider locale={APP_LOCALE} messages={messages} timeZone="Asia/Shanghai">
            <UpdaterProvider>
                <AuthProvider>
                    {/* MotionConfig: respect OS prefers-reduced-motion ("user"); when the
                     *  in-app 动效 toggle is off, force-reduce Framer animations ("always"). */}
                    <MotionConfig reducedMotion={animations ? "user" : "always"}>
                        {/* LightboxProvider must wrap any subtree that uses PreviewImage /
                         *  PreviewVideo. Singleton portal — see Issue 14 design notes in
                         *  LightboxProvider.tsx. */}
                        <LightboxProvider>
                            <AuthGate>{children}</AuthGate>
                            <ToastContainer />
                        </LightboxProvider>
                    </MotionConfig>
                </AuthProvider>
            </UpdaterProvider>
        </NextIntlClientProvider>
    );
}
