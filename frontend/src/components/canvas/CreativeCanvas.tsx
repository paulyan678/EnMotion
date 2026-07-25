"use client";

import dynamic from "next/dynamic";
import { useSettingsStore } from "@/store/settingsStore";

const RichCreativeCanvas = dynamic(
    () => import("./RichCreativeCanvas"),
    { ssr: false },
);

/**
 * Keep the common Atelier path free of Three.js, react-three, and animation
 * packages. The rich renderer is fetched only after a user selects a theme
 * that actually uses it.
 */
export default function CreativeCanvas() {
    const theme = useSettingsStore((state) => state.theme);
    const isAtelier = theme.startsWith("atelier");

    if (isAtelier) {
        return (
            <div className="absolute inset-0 z-0 h-full w-full overflow-hidden bg-background" />
        );
    }

    return <RichCreativeCanvas isDark={theme.endsWith("-dark")} />;
}
