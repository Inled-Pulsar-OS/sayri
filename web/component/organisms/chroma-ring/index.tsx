import React, { memo, useEffect, useMemo, useState } from "react";
import { View, StyleSheet } from "react-native";
import {
  Canvas,
  Shader,
  Skia,
  Fill,
} from "@shopify/react-native-skia";
import { type IChromaRing } from "./types";
import { SHADER_SOURCE } from "./conf";
import { hexToRgb } from "./helper";

export const ChromaRing: React.FC<IChromaRing> = memo<IChromaRing>(
  ({
    width = 300,
    height = 56,
    borderRadius: customBorderRadius,
    borderWidth = 2,
    speed = 1.0,
    base = "#333340",
    glow = "#c0c8e0",
    background = "#0a0a0a",
    children,
    style,
  }) => {
    const borderRadius = customBorderRadius ?? height / 2;
    const baseColorRgb = hexToRgb<typeof base>(base);
    const glowColorRgb = hexToRgb<typeof glow>(glow);

    const [time, setTime] = useState(0);

    useEffect(() => {
      let animId: number;
      let start = performance.now();
      const loop = (now: number) => {
        setTime(((now - start) / 1000.0) * speed);
        animId = requestAnimationFrame(loop);
      };
      animId = requestAnimationFrame(loop);
      return () => cancelAnimationFrame(animId);
    }, [speed]);

    const shader = useMemo(() => {
      try {
        return Skia.RuntimeEffect.Make(SHADER_SOURCE);
      } catch (err) {
        console.error("Shader compilation failed:", err);
        return null;
      }
    }, []);

    const uniforms = useMemo(
      () => ({
        iResolution: [width, height],
        iTime: time,
        borderWidth: borderWidth,
        borderRadius: borderRadius,
        speed: speed,
        baseColor: baseColorRgb,
        glowColor: glowColorRgb,
      }),
      [width, height, time, borderWidth, borderRadius, speed, baseColorRgb, glowColorRgb]
    );

    return (
      <View style={[styles.container, { width, height, borderRadius }, style]}>
        {shader && (
          <Canvas style={[StyleSheet.absoluteFill, { borderRadius }]}>
            <Fill>
              <Shader source={shader} uniforms={uniforms} />
            </Fill>
          </Canvas>
        )}

        <View
          style={[
            styles.innerBackground,
            {
              backgroundColor: background,
              borderRadius: Math.max(0, borderRadius - borderWidth),
              margin: borderWidth,
            },
          ]}
        />

        <View style={[styles.contentContainer, { borderRadius }]}>
          {children}
        </View>
      </View>
    );
  }
);

const styles = StyleSheet.create({
  container: {
    position: "relative",
    overflow: "hidden",
  },
  innerBackground: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
  },
  contentContainer: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: "center",
    alignItems: "center",
    overflow: "hidden",
  },
});

export default memo<React.FC<IChromaRing>>(ChromaRing);
