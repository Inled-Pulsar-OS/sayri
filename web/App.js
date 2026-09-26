import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  View,
  StyleSheet,
  Pressable,
  Text,
  TextInput,
  ScrollView,
} from "react-native";

// Web entry point of react-native-skia: it does NOT evaluate the Skia API
// eagerly, so global.CanvasKit can be loaded first (see LoadSkiaWeb below).
// Importing the main "@shopify/react-native-skia" index up front would break:
// Skia.web.js captures global.CanvasKit at module-evaluation time.
import { LoadSkiaWeb } from "@shopify/react-native-skia/lib/module/web";

const DEFAULT_SIZE = 220;

// Path (served by the WebKitGTK custom "sayri://" scheme) where canvaskit.wasm
// is copied next to the exported JS bundle by prepare-assets.sh.
const CANVASKIT_PATH = "/_expo/static/js/web/";

// Which window this bundle is rendered in (set via ?mode= in the load URL):
//   "orb"    -> the Siri orb (top-left)
//   "bubble" -> the Apple-intelligence cajita (right of the orb) with chroma-ring
function currentMode() {
  try {
    const p = new URL(window.location.href).searchParams;
    const m = p.get("mode");
    if (m === "orb") return "orb";
    if (m === "bubble") return "bubble";
    return "both";
  } catch (_e) {
    return "both";
  }
}
const MODE = currentMode();

// Visual parameters per assistant state. The Python host drives these via
// window.sayriBridge.setState(name, opts) (see orb_window.py / cajita_window.py).
const VISUALS = {
  idle: {
    speed: 0.65,
    glowIntensity: 1.4,
    coreIntensity: 0.55,
    brightness: 0.95,
    primaryColor: { r: 0.282, g: 0.204, b: 0.800 },  // Azul oscuro (#4834cc)
    secondaryColor: { r: 0.498, g: 0.078, b: 0.345 }, // Rosa / Morado (#7f1458)
    ringGlow: "#4834cc",
  },
  listening: {
    speed: 2.0,
    glowIntensity: 2.2,
    coreIntensity: 0.70,
    brightness: 1.1,
    primaryColor: { r: 0.320, g: 0.240, b: 0.900 },  // Azul eléctrico (#4834cc vibrante)
    secondaryColor: { r: 0.580, g: 0.100, b: 0.420 }, // Rosa / Morado (#7f1458 vibrante)
    ringGlow: "#4834cc",
  },
  activated: {
    speed: 2.8,
    glowIntensity: 2.4,
    coreIntensity: 0.80,
    brightness: 1.2,
    primaryColor: { r: 0.550, g: 0.090, b: 0.380 },  // Rosa / Morado (#7f1458)
    secondaryColor: { r: 0.300, g: 0.220, b: 0.850 }, // Azul oscuro (#4834cc)
    ringGlow: "#7f1458",
  },
  thinking: {
    speed: 3.2,
    glowIntensity: 2.6,
    coreIntensity: 0.85,
    brightness: 1.2,
    primaryColor: { r: 0.498, g: 0.078, b: 0.345 },  // Rosa / Morado (#7f1458)
    secondaryColor: { r: 0.282, g: 0.204, b: 0.800 }, // Azul oscuro (#4834cc)
    ringGlow: "#7f1458",
  },
  speaking: {
    speed: 1.6,
    glowIntensity: 2.3,
    coreIntensity: 0.75,
    brightness: 1.15,
    primaryColor: { r: 0.282, g: 0.204, b: 0.800 },  // Azul oscuro (#4834cc)
    secondaryColor: { r: 0.498, g: 0.078, b: 0.345 }, // Rosa / Morado (#7f1458)
    ringGlow: "#4834cc",
  },
};

const LINE_COLOR = {
  user: "#60a5fa",
  assistant: "#f1f5f9",
  partial: "#94a3b8",
  hint: "#94a3b8",
  error: "#f87171",
};

// Messages sent to the host (WebKitGTK registers the "sayri" script message
// handler in both windows).
function postToHost(msg) {
  const payload = JSON.stringify(msg);
  if (
    typeof window !== "undefined" &&
    window.webkit &&
    window.webkit.messageHandlers &&
    window.webkit.messageHandlers.sayri
  ) {
    window.webkit.messageHandlers.sayri.postMessage(payload);
  } else {
    console.log("[sayri-bridge]", payload);
  }
}

// ---------------------------------------------------------------- orb mode
function OrbMode({ Orb, size, visuals, audioLevel }) {
  if (!Orb) return null; // CanvasKit / component not loaded yet
  return (
    <View style={styles.orbContainer}>
      <Pressable
        onPress={() => postToHost({ type: "click" })}
        style={styles.press}
      >
        <Orb
          size={size}
          audioLevel={audioLevel}
          speed={visuals.speed}
          glowIntensity={visuals.glowIntensity}
          coreIntensity={visuals.coreIntensity}
          brightness={visuals.brightness}
          saturation={2}
          rotationSpeed={1}
          noiseScale={3}
          noiseIntensity={1}
          edgeSoftness={0.045}
          primaryColor={visuals.primaryColor}
          secondaryColor={visuals.secondaryColor}
        />
      </Pressable>
    </View>
  );
}

// ------------------------------------------------------------ bubble mode
function BubbleMode({
  Chrome,
  state,
  visuals,
  content,
  micOn,
  inputRef,
  onSend,
  onMic,
  onSettings,
  onQuit,
}) {
  const scrollRef = useRef(null);
  useEffect(() => {
    if (scrollRef.current) {
      try {
        scrollRef.current.scrollToEnd({ animated: false });
      } catch (_e) {}
    }
  }, [content]);

  const busy = state === "thinking" || state === "speaking";
  const listening = micOn || state === "listening" || state === "activated";

  const bubbleContent = (
    <View style={styles.bubbleInner}>
      {/* content / transcript */}
      <ScrollView
        ref={scrollRef}
        style={styles.bubbleScroll}
        contentContainerStyle={styles.bubbleScrollContent}
        showsVerticalScrollIndicator={false}
      >
        {content.map((line) => (
          <Text
            key={line.id}
            style={[styles.bubbleLine, { color: line.color || "#f1f5f9" }]}
          >
            {line.text}
          </Text>
        ))}
        {content.length === 0 ? (
          <Text style={styles.bubblePlaceholder}>
            Pregúntame algo o pulsa el orbe para hablar…
          </Text>
        ) : null}
      </ScrollView>

      {/* input row */}
      <View style={styles.bubbleInputRow}>
        <Pressable
          onPress={onMic}
          style={[
            styles.iconBtn,
            listening ? styles.iconBtnActive : null,
          ]}
        >
          <Text style={[styles.iconText, listening ? { color: "#ffffff" } : null]}>
            {listening ? "●" : "🎙"}
          </Text>
        </Pressable>

        <TextInput
          ref={inputRef}
          style={styles.bubbleInput}
          placeholder="Escribe o habla…"
          placeholderTextColor="#64748b"
          editable={!busy}
          onSubmitEditing={(e) => onSend(e.nativeEvent.text)}
          blurOnSubmit={false}
        />

        <Pressable onPress={onSettings} style={styles.iconBtn}>
          <Text style={styles.iconText}>⚙</Text>
        </Pressable>
        <Pressable onPress={onQuit} style={styles.iconBtn}>
          <Text style={styles.iconText}>✕</Text>
        </Pressable>
      </View>
    </View>
  );

  return (
    <View style={styles.bubbleRoot}>
      {Chrome ? (
        <Chrome
          width={BUBBLE_WIDTH}
          height={BUBBLE_HEIGHT}
          speed={visuals.speed}
          base="#34344a"
          glow={visuals.ringGlow}
          background="#0d0f18"
          borderWidth={1.8}
        >
          {bubbleContent}
        </Chrome>
      ) : (
        <View style={[styles.bubbleInner, styles.fallbackRing]}>
          {bubbleContent}
        </View>
      )}
    </View>
  );
}

const BUBBLE_WIDTH = 420;
const BUBBLE_HEIGHT = 140;

let _lineSeq = 0;

export default function App() {
  const [Comp, setComp] = useState(null); // {Orb, Chrome}
  const [state, setState] = useState("idle");
  const [size, setSize] = useState(DEFAULT_SIZE);
  const [audioLevel, setAudioLevel] = useState(0);
  const [content, setContent] = useState([]); // bubble lines
  const [micOn, setMicOn] = useState(false);
  const stateRef = useRef(state);
  stateRef.current = state;
  const contentRef = useRef(content);
  contentRef.current = content;
  const micOnRef = useRef(false);
  const inputRef = useRef(null);
  const partialIdRef = useRef(null);
  const assistantIdRef = useRef(null);
  const busyRef = useRef(false);

  useEffect(() => {
    let mounted = true;
    LoadSkiaWeb({
      locateFile: (file) => CANVASKIT_PATH + file,
    })
      .then(async () => {
        const [orbMod, chromeMod] = await Promise.all([
          import("./component/organisms/unstable_siri_orb"),
          import("./component/organisms/chroma-ring"),
        ]);
        if (mounted)
          setComp({
            Orb: orbMod.UnstableSiriOrb || orbMod.default,
            Chrome: chromeMod.ChromaRing || chromeMod.default,
          });
      })
      .catch((err) => {
        console.error("[sayri] CanvasKit failed to load:", err);
      });
    return () => {
      mounted = false;
    };
  }, []);

  // Bridge used by the host (WebKitGTK -> run_javascript).
  useEffect(() => {
    globalThis.sayriBridge = {
      setState: (name, opts = {}) => {
        if (VISUALS[name]) {
          stateRef.current = name;
          setState(name);
        }
        if (opts && opts.size) setSize(opts.size);
      },
      setAudioLevel: (lvl) => {
        const val = Math.max(0, Math.min(1, Number(lvl) || 0));
        setAudioLevel(val);
      },
      getState: () => stateRef.current,
      clear: () => {
        contentRef.current = [];
        partialIdRef.current = null;
        assistantIdRef.current = null;
        setContent([]);
      },
      setContent: (kind, text) => {
        let next = contentRef.current.slice();
        if (kind === "user" || kind === "hint" || kind === "error") {
          next = next.filter(
            (l) => l.id !== partialIdRef.current && l.id !== assistantIdRef.current
          );
          partialIdRef.current = null;
          assistantIdRef.current = null;
          if (text) {
            const color = LINE_COLOR[kind] || "#f1f5f9";
            if (kind === "user") next.push({ id: ++_lineSeq, text, color });
            else if (kind === "hint") next.push({ id: ++_lineSeq, text, color, small: true });
            else next.push({ id: ++_lineSeq, text, color });
          }
        } else if (kind === "partial") {
          next = next.filter((l) => l.id !== partialIdRef.current);
          if (partialIdRef.current != null) assistantIdRef.current = null;
          if (text) {
            const id = partialIdRef.current != null ? partialIdRef.current : ++_lineSeq;
            partialIdRef.current = id;
            next.push({ id, text, color: LINE_COLOR.partial });
          } else {
            partialIdRef.current = null;
          }
        } else if (kind === "assistant") {
          next = next.filter((l) => l.id !== assistantIdRef.current);
          if (assistantIdRef.current != null) partialIdRef.current = null;
          const id = assistantIdRef.current != null ? assistantIdRef.current : ++_lineSeq;
          assistantIdRef.current = id;
          next.push({ id, text, color: LINE_COLOR.assistant });
        }
        contentRef.current = next;
        setContent(next);
      },
      setBusy: (busy) => {
        busyRef.current = !!busy;
      },
      setMic: (active) => {
        micOnRef.current = !!active;
        setMicOn(!!active);
      },
      getSnapshot: () => JSON.stringify({
        state: stateRef.current,
        contentLen: contentRef.current.length,
        micOn: micOnRef.current,
      }),
    };
    postToHost({ type: "ready", mode: MODE });
  }, []);

  const send = useCallback((raw) => {
    const text = (raw || "").trim();
    if (!text) return;
    if (inputRef.current) inputRef.current.clear();
    postToHost({ type: "send", text });
  }, []);

  const v = VISUALS[state] || VISUALS.idle;

  return (
    <View style={styles.root}>
      {MODE === "orb" && (
        <OrbMode Orb={Comp && Comp.Orb} size={size} visuals={v} audioLevel={audioLevel} />
      )}
      {MODE === "bubble" && (
        <BubbleMode
          Chrome={Comp && Comp.Chrome}
          state={state}
          visuals={v}
          content={content}
          micOn={micOn}
          inputRef={inputRef}
          onSend={send}
          onMic={() => postToHost({ type: "mic" })}
          onSettings={() => postToHost({ type: "settings" })}
          onQuit={() => postToHost({ type: "quit" })}
        />
      )}
      {MODE === "both" && (
        <View style={styles.bothContainer}>
          <BubbleMode
            Chrome={Comp && Comp.Chrome}
            state={state}
            visuals={v}
            content={content}
            micOn={micOn}
            inputRef={inputRef}
            onSend={send}
            onMic={() => postToHost({ type: "mic" })}
            onSettings={() => postToHost({ type: "settings" })}
            onQuit={() => postToHost({ type: "quit" })}
          />
          <OrbMode Orb={Comp && Comp.Orb} size={size} visuals={v} audioLevel={audioLevel} />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "transparent",
  },
  bothContainer: {
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    backgroundColor: "transparent",
    paddingRight: 10,
  },
  // --- orb mode ---
  orbContainer: {
    width: "100%",
    height: "100%",
    flex: 1,
    backgroundColor: "transparent",
    alignItems: "center",
    justifyContent: "center",
  },
  press: {
    borderRadius: 1000,
    alignItems: "center",
    justifyContent: "center",
  },
  // --- bubble mode ---
  bubbleRoot: {
    width: BUBBLE_WIDTH,
    height: BUBBLE_HEIGHT,
    backgroundColor: "transparent",
    alignItems: "center",
    justifyContent: "center",
  },
  bubbleInner: {
    flex: 1,
    borderRadius: 24,
    padding: 10,
    backgroundColor: "rgba(13, 15, 24, 0.94)",
  },
  bubbleScroll: {
    flex: 1,
  },
  bubbleScrollContent: {
    paddingHorizontal: 8,
    paddingTop: 6,
    flexGrow: 1,
    justifyContent: "flex-end",
  },
  bubbleLine: {
    color: "#f1f5f9",
    fontSize: 13.5,
    lineHeight: 18,
    marginBottom: 2,
  },
  bubblePlaceholder: {
    color: "#64748b",
    fontSize: 13,
    alignSelf: "center",
  },
  bubbleInputRow: {
    flexDirection: "row",
    alignItems: "center",
    marginTop: 6,
    paddingHorizontal: 2,
  },
  iconBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: "rgba(255, 255, 255, 0.08)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.12)",
    alignItems: "center",
    justifyContent: "center",
    marginHorizontal: 3,
  },
  iconBtnActive: {
    backgroundColor: "rgba(239, 68, 68, 0.85)",
    borderColor: "rgba(239, 68, 68, 0.95)",
  },
  iconText: {
    color: "#cbd5e1",
    fontSize: 14,
  },
  bubbleInput: {
    flex: 1,
    height: 32,
    paddingHorizontal: 12,
    borderRadius: 16,
    backgroundColor: "rgba(255, 255, 255, 0.08)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.14)",
    color: "#f8fafc",
    fontSize: 13,
    outlineColor: "transparent",
    outlineWidth: 0,
  },
  fallbackRing: {
    borderWidth: 1.8,
    borderColor: "rgba(110, 168, 254, 0.45)",
    borderRadius: 24,
    backgroundColor: "#0d0f18",
  },
});