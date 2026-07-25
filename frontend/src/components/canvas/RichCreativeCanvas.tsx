"use client";

import { Canvas } from "@react-three/fiber";
import { Grid, Stars } from "@react-three/drei";
import { motion } from "framer-motion";
import {
    Component,
    Suspense,
    useEffect,
    useState,
    type ReactNode,
} from "react";

function detectWebGL(): boolean {
    try {
        const canvas = document.createElement("canvas");
        return !!(
            canvas.getContext("webgl2")
            || canvas.getContext("webgl")
            || canvas.getContext("experimental-webgl")
        );
    } catch {
        return false;
    }
}

function useDecorativeMotion(): boolean {
    const [enabled, setEnabled] = useState(false);

    useEffect(() => {
        const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
        const update = () => {
            setEnabled(
                document.visibilityState === "visible"
                && !reducedMotion.matches,
            );
        };
        update();
        document.addEventListener("visibilitychange", update);
        reducedMotion.addEventListener("change", update);
        return () => {
            document.removeEventListener("visibilitychange", update);
            reducedMotion.removeEventListener("change", update);
        };
    }, []);

    return enabled;
}

function Background({
    isDark,
    motionEnabled,
}: {
    isDark: boolean;
    motionEnabled: boolean;
}) {
    return (
        <>
            <color attach="background" args={[isDark ? "#050508" : "#f0f1f3"]} />
            {isDark ? (
                <Stars
                    radius={100}
                    depth={50}
                    count={5000}
                    factor={4}
                    saturation={0}
                    fade
                    speed={motionEnabled ? 1 : 0}
                />
            ) : null}
            <Grid
                infiniteGrid
                fadeDistance={50}
                sectionColor={isDark ? "#646cff" : "#b4b8ff"}
                cellColor={isDark ? "#ffffff" : "#d0d5dd"}
                sectionSize={10}
                cellSize={1}
                sectionThickness={1}
                cellThickness={0.5}
            />
            <ambientLight intensity={isDark ? 0.5 : 0.8} />
            <pointLight position={[10, 10, 10]} />
        </>
    );
}

class CanvasErrorBoundary extends Component<
    { children: ReactNode },
    { failed: boolean }
> {
    state = { failed: false };

    static getDerivedStateFromError() {
        return { failed: true };
    }

    render() {
        if (this.state.failed) return null;
        return this.props.children;
    }
}

const DARK_GRADIENTS = [
    "radial-gradient(circle at 50% 50%, rgba(100, 108, 255, 0.1) 0%, transparent 50%)",
    "radial-gradient(circle at 60% 40%, rgba(100, 108, 255, 0.15) 0%, transparent 50%)",
    "radial-gradient(circle at 40% 60%, rgba(100, 108, 255, 0.1) 0%, transparent 50%)",
    "radial-gradient(circle at 50% 50%, rgba(100, 108, 255, 0.1) 0%, transparent 50%)",
] as const;
const LIGHT_GRADIENTS = [
    "radial-gradient(circle at 50% 50%, rgba(100, 108, 255, 0.05) 0%, transparent 50%)",
    "radial-gradient(circle at 60% 40%, rgba(100, 108, 255, 0.08) 0%, transparent 50%)",
    "radial-gradient(circle at 40% 60%, rgba(100, 108, 255, 0.05) 0%, transparent 50%)",
    "radial-gradient(circle at 50% 50%, rgba(100, 108, 255, 0.05) 0%, transparent 50%)",
] as const;

export default function RichCreativeCanvas({ isDark }: { isDark: boolean }) {
    const [canRender3D] = useState(detectWebGL);
    const motionEnabled = useDecorativeMotion();
    const gradients = isDark ? DARK_GRADIENTS : LIGHT_GRADIENTS;

    return (
        <div className="absolute inset-0 z-0 h-full w-full overflow-hidden bg-background">
            {canRender3D ? (
                <CanvasErrorBoundary>
                    <Canvas
                        camera={{ position: [0, 5, 10], fov: 60 }}
                        frameloop={motionEnabled ? "always" : "demand"}
                    >
                        <Suspense fallback={null}>
                            <Background
                                isDark={isDark}
                                motionEnabled={motionEnabled}
                            />
                        </Suspense>
                    </Canvas>
                </CanvasErrorBoundary>
            ) : null}

            <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-background/20 via-transparent to-background/50" />
            <motion.div
                className={`pointer-events-none absolute inset-0 opacity-30 ${
                    isDark ? "mix-blend-screen" : "mix-blend-multiply"
                }`}
                animate={{
                    background: motionEnabled ? [...gradients] : gradients[0],
                }}
                transition={motionEnabled
                    ? { duration: 10, repeat: Infinity, ease: "linear" }
                    : { duration: 0 }}
            />
        </div>
    );
}
