import React, { memo, useEffect, useMemo, useRef, useState } from "react";
import { View, StyleSheet } from "react-native";
import {
  Canvas,
  Skia,
  Shader,
  Fill,
} from "@shopify/react-native-skia";
import type { IUnstableSiriORB } from "./types";
import { SHADER_SOURCE } from "./conf";

export const UnstableSiriOrb: React.FC<IUnstableSiriORB> = memo<IUnstableSiriORB>(
  ({
    size = 140,
    speed = 1,
    audioLevel = 0,
    primaryColor = { r: 0.4, g: 0.6, b: 1.0 },
    secondaryColor = { r: 0.0, g: 0.8, b: 0.8 },
    noiseIntensity = 1,
    glowIntensity = 1.5,
    saturation = 2,
    brightness = 1,
    rotationSpeed = 1,
    noiseScale = 3,
    coreIntensity = 0.5,
    edgeSoftness = 0.04,
    paused = false,
    style,
  }) => {
    const [renderState, setRenderState] = useState({
      time: 0,
      smoothedLevel: 0,
    });
    const audioLevelRef = useRef(audioLevel);
    audioLevelRef.current = audioLevel;
    const smoothedLevelRef = useRef(0);
    const speedRef = useRef(speed);
    speedRef.current = speed;

    useEffect(() => {
      if (paused) return;
      let animId: number;
      let lastTime = performance.now();
      let accumulatedTime = 0;

      const loop = (now: number) => {
        const dt = Math.min(0.1, (now - lastTime) / 1000.0);
        lastTime = now;

        // Smooth physical attack and decay for audio reaction
        const target = Math.max(0, Math.min(1, audioLevelRef.current || 0));
        const lerpFactor = target > smoothedLevelRef.current ? 0.35 : 0.12;
        smoothedLevelRef.current += (target - smoothedLevelRef.current) * lerpFactor;
        if (smoothedLevelRef.current < 0.0005) smoothedLevelRef.current = 0;

        const currentLvl = smoothedLevelRef.current;
        // Accelerate animation when speaking / listening to audio
        const currentSpeed = speedRef.current;
        const effectiveSpeed = currentSpeed + currentLvl * 3.8;
        accumulatedTime = (accumulatedTime + dt * effectiveSpeed) % (Math.PI * 2000.0);

        setRenderState({
          time: accumulatedTime,
          smoothedLevel: currentLvl,
        });

        animId = requestAnimationFrame(loop);
      };

      animId = requestAnimationFrame(loop);
      return () => cancelAnimationFrame(animId);
    }, [paused]);

    const shader = useMemo(() => {
      try {
        return Skia.RuntimeEffect.Make(SHADER_SOURCE);
      } catch (err) {
        console.error("Shader compilation failed:", err);
        return null;
      }
    }, []);

    const lvl = renderState.smoothedLevel;

    // Dynamically modulate visuals in response to audio level
    const dynamicGlow = glowIntensity * (1.0 + lvl * 0.8) + lvl * 0.9;
    const dynamicCore = coreIntensity * (1.0 + lvl * 0.75) + lvl * 0.4;
    const dynamicBrightness = brightness * (1.0 + lvl * 0.35);
    const dynamicNoise = noiseIntensity * (1.0 + lvl * 0.8);
    const dynamicRotation = rotationSpeed * (1.0 + lvl * 1.8);

    const uniforms = useMemo(
      () => ({
        iResolution: [size, size],
        iTime: renderState.time,
        primaryColor: [primaryColor.r, primaryColor.g, primaryColor.b],
        secondaryColor: [secondaryColor.r, secondaryColor.g, secondaryColor.b],
        noiseIntensity: dynamicNoise,
        glowIntensity: dynamicGlow,
        saturation,
        brightness: dynamicBrightness,
        rotationSpeed: dynamicRotation,
        noiseScale,
        coreIntensity: dynamicCore,
        edgeSoftness,
      }),
      [
        size,
        renderState.time,
        primaryColor.r,
        primaryColor.g,
        primaryColor.b,
        secondaryColor.r,
        secondaryColor.g,
        secondaryColor.b,
        dynamicNoise,
        dynamicGlow,
        saturation,
        dynamicBrightness,
        dynamicRotation,
        noiseScale,
        dynamicCore,
        edgeSoftness,
      ]
    );

    if (!shader) return null;

    return (
      <View style={[styles.container, { width: size, height: size }, style]}>
        <Canvas style={styles.canvas}>
          <Fill>
            <Shader source={shader} uniforms={uniforms} />
          </Fill>
        </Canvas>
      </View>
    );
  }
);

const styles = StyleSheet.create({
  container: {
    overflow: "hidden",
    borderRadius: 1000,
  },
  canvas: {
    flex: 1,
    width: "100%",
    height: "100%",
  },
});

export default UnstableSiriOrb;
