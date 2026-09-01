# Sayri

Agent assistant for Pulsar OS. Like Openclaw, but with less token consumption.

A reactive orb pinned to the **top-left** corner of the screen reacts to your
voice:

- **STT**: [whisper.cpp](https://github.com/ggml-org/whisper.cpp) transcribes what you say.
- The live transcript appears in an Apple-intelligence style **box** (with
  a chroma-ring animated border) right next to the orb, at the same height.
- When you pause (silence) or press **Enter**, the query is sent to the provider.
- **LLM**: any **OpenAI-compatible** API (`/v1/chat/completions`) — OpenAI, Ollama,
  LM Studio, OpenClaw, etc. — configurable endpoint, key, model and options.
- **TTS**: [Piper](https://github.com/rhasspy/piper) speaks the answer back while
  the orb animates, and the text is shown in the box.

**Clicking the orb toggles the microphone on/off.** The box is both the
assistant's reply and the text input (enter to send). You can also type your
query directly. Activation is configurable: always listening, wake word
("hey sayri" / "hey siri", configurable), or manual.

The assistante its in GTK4, settings and appindicator in gtk3.

## Package layout

```
sayri/
├── DEBIAN/control              Debian package metadata
├── etc/xdg/autostart/          Autostart the orb after login
├── usr/bin/sayri               Entry wrapper (bash)
├── usr/share/applications/     .desktop launcher
├── usr/share/icons/            App icon (SVG)
├── usr/share/sayri/
│   ├── lib/sayri/              Python package (GTK4 + WebKitGTK 6.0)
│   └── web/                    Committed static build (Expo web)
├── web/                        Expo/React Native source: the orb and the box
│                               (reacticx `unstable_siri_orb` + `chroma-ring`)
├── tests/                      Unit tests (tests/run-tests.sh)
└── prepare-assets.sh           Rebuilds web/ into usr/share/sayri/web if node exists
```

## The web build (orb + box)

Two windows are rendered from the same Expo web bundle (selected via
`?mode=orb` / `?mode=bubble`):

- the **orb** is the `unstable_siri_orb` component from
  [reacticx](https://www.reacticx.com/docs/components/siri-orb) (a Skia
  shader);
- the **box** is the apple-intelligence chat card with the `chroma-ring`
  animated border (also from reacticx) and a `TextInput`.

Both components are vendored under `web/component/organisms/` (the equivalent
of `npx reactic add <component>`, which is interactive).

On web, `react-native-skia` needs `global.CanvasKit` to exist before any Skia
module is evaluated (shaders are compiled at import time), so `App.js` imports
`LoadSkiaWeb` from `@shopify/react-native-skia/lib/module/web` and **dynamically
imports** the components once CanvasKit is ready. The exported bundle +
`canvaskit.wasm` are served by WebKitGTK through a custom `sayri://` URI scheme
(no `file://` CORS issues).

## Testing

```bash
cd tests
./run-tests.sh
```

The WebKit bridge test needs a display. GTK4 dropped the Broadway backend, so
use X11/Wayland (or `xvfb-run` if only a session is available):

```bash
xvfb-run -a ./run-tests.sh
```

## Running without installing (development)

```bash
cd PKG/sayri

# The web build auto-detected from the package structure.
# Layer-shell is optional; if your compositor supports it,
# set LD_PRELOAD for reliable WebKit+layer-shell coexistence:
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so \
  PYTHONPATH="$PWD/usr/share/sayri/lib" \
  python3 -m sayri
```

To keep your real configuration untouched while testing:

```bash
SAYRI_CONFIG_DIR=/tmp/sayri-config SAYRI_STATE_DIR=/tmp/sayri-state python3 -m sayri
```

You only need the runtime dependencies installed (see "Installing
dependencies"); the whisper/piper binaries and models are downloaded on first
use from Settings → Speech.

## Building

```bash
# Debian
./package-and-deploy.sh sayri

# Arch
cd arch && ./package-and-deploy.sh sayri
```

`prepare-assets.sh` rebuilds the web orb with `npx expo export` when Node.js is
available; otherwise the committed static build is used.

## Installing dependencies

### Debian / Ubuntu

```bash
sudo apt install -y \
  python3 python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0 \
  libgtk4-layer-shell0 python3-httpx pipewire

# optional: distro STT binary (Sayri auto-downloads it otherwise)
sudo apt install -y whisper.cpp
```

> Note: the **Piper TTS is not packaged in Debian** (the `piper` package is a
> different app, for gaming mice). Sayri downloads Piper's static binary on
> first use from Settings → Speech → "Install piper binary".

### Arch Linux / CachyOS / Manjaro

```bash
sudo pacman -S --needed \
  python python-gobject gtk4 webkitgtk-6.0 \
  gtk4-layer-shell python-httpx pipewire-audio

# optional (Sayri auto-downloads them otherwise)
sudo pacman -S --needed whisper-cpp        # STT (extra repo)
paru -S piper-tts-bin                       # TTS (AUR)
```

### Runtime binaries and models

`whisper-cli` (whisper.cpp) and `piper` are detected on `PATH`. If missing,
the Settings → Speech window downloads static builds (whisper.cpp and Piper)
plus the matching models/voices (HuggingFace) on first use.

## Configuration

Stored in `~/.config/sayri/sayri.conf` (GLib key file). Models and voices live in
`~/.local/share/sayri/`. Overridable at runtime:

| Env var             | Default                  |
|---------------------|--------------------------|
| `SAYRI_PYTHONPATH`  | `/usr/share/sayri/lib`   |
| `SAYRI_DATA_DIR`    | `/usr/share/sayri/web`   |
| `SAYRI_CONFIG_DIR`  | `~/.config/sayri`        |
| `SAYRI_STATE_DIR`   | `~/.local/share/sayri`   |
