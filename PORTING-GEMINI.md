# Informe de Porting: Sayri a macOS y Windows

**Objetivo:** Este documento describe las tecnologías, procesos y decisiones arquitectónicas necesarias para portar Sayri (asistente de voz con IA para Linux) a macOS y Windows, manteniendo la compatibilidad con Linux.

---

## 1. Qué es Sayri

Sayri es un asistente de voz estilo Siri que corre localmente en el escritorio. Su ciclo de interacción es:

1. Detección de **wake word** ("Hey Sayri") o clic manual
2. **Speech-to-Text** local vía whisper.cpp
3. Texto transcrito se envía a cualquier LLM compatible con OpenAI (Ollama, LM Studio, OpenAI, etc.)
4. Respuesta del LLM se muestra en pantalla y se **lee en voz alta** vía Piper TTS

**Stack actual:** Python 3.14, GTK4, WebKitGTK 6.0, whisper.cpp, Piper TTS, openWakeWord (ONNX), httpx (streaming SSE), SQLite, Expo/React Native Web (Skia), Bubblewrap (sandbox), PipeWire/PulseAudio (audio).

---

## 2. Arquitectura actual (Linux)

Sayri tiene dos partes principales:

### 2.1 Backend Python (core)
El cerebro de la aplicación. Maneja STT, TTS, LLM, wake word, audio, configuración, sesiones SQLite, agentes, plugins/skills, y sistema de sandbox. Todo en Python puro con bindings a librerías nativas.

### 2.2 Frontend (UI)
Una ventana GTK4 transparente y sin decoraciones que contiene:
- **Orb:** Una esfera animada renderizada via WebKitGTK (el frontend Expo/React Native Web + Skia se carga dentro de un WebView)
- **Cajita:** Una burbuja de texto con chat, configuración y barra de entrada, dibujada con Cairo/Pango (GTK4 nativo)

La ventana se posiciona en la esquina superior derecha de la pantalla usando `gtk4-layer-shell` (Wayland) o llamadas X11 directas (X11/XWayland), que hacen que la ventana sea "override-redirect" (no gestionada por el window manager, siempre encima de todo).

---

## 3. Decisiones arquitectónicas para el porting

### 3.1 Frontend: Reemplazar GTK4 por Tauri

**Razón:** GTK4 no tiene soporte oficial ni estable en macOS ni Windows. El frontend web (Orb + Settings) ya es portable y funciona en cualquier WebView.

**Tauri** es un framework que combina un frontend web (Vite + cualquier framework JS) con un backend en Rust. Soporta Windows, macOS y Linux desde un mismo código fuente.

**Qué se mantiene:**
- El frontend Expo/React Native Web (Orb) se reutiliza tal cual como UI de Tauri
- Todo el backend Python se mantiene como proceso sidecar (proceso separado comunicado por IPC)

**Qué se elimina:**
- Toda la capa GTK4: `orb.py`, `cajita.py`, `overlay.py`, `webkit.py`, `indicator.py`, `settings_window.py`, `settings_gtk3.py`, `blur_exclusion.py`
- El wrapper bash `usr/bin/sayri`
- Las llamadas ctypes a libX11
- La dependencia de gtk4-layer-shell

**Qué se reemplaza:**
- El sistema de ventanas transparentes y flotantes lo maneja Tauri nativamente
- El system tray lo maneja Tauri nativamente (via `tray-icon` crate de Rust)
- La comunicación Python ↔ UI cambia de WebKitGTK bridge a IPC de Tauri

### 3.2 Backend Python: Mantener como sidecar

El backend Python corre como un proceso separado que Tauri lanza y con el que se comunica por stdin/stdout (JSON-lines) o named pipes.

**Ventajas:**
- No hay que reescribir el core en otro lenguaje
- Python 3.14 funciona nativo en macOS y Windows
- Las dependencias C (whisper.cpp, Piper) son binarios estáticos que existen para las tres plataformas

### 3.3 IPC: Comunicación entre Tauri y Python

El flujo de comunicación es:

```
Frontend (JS/TS) ←→ Backend Tauri (Rust) ←→ Python sidecar (stdin/stdout JSON)
```

Tauri tiene un sistema de "comandos" invoke desde el frontend. El backend Rust recibe esos comandos y los reenvía al Python sidecar. El Python sidecar puede también enviar mensajes de vuelta al Rust, que los reenvía al frontend.

---

## 4. Componentes que requieren adaptación por plataforma

### 4.1 Audio (captura y reproducción)

**Situación actual:** `audio.py` lanza subprocess (`pw-record`, `pw-play`, `parec`, `paplay`, `aplay`) detectando qué servidor de audio está disponible.

**Alternativas cross-platform:**

| Plataforma | Captura de audio | Reproducción de audio |
|---|---|---|
| **Linux** | `pw-record` / `parec` (sin cambios) | `pw-play` / `paplay` / `aplay` (sin cambios) |
| **macOS** | `sox -d -r 16000 -c 1 -b 16 -t raw -` o librería `sounddevice` | `afplay` o `sox` |
| **Windows** | `sox.exe -d` o librería `sounddevice` o `ffmpeg` | `sox.exe` o `ffmpeg` o `sounddevice` |

**Proceso:** Detectar la plataforma en `audio.py` y usar el comando/appropriate binario. El formato de audio siempre es S16LE mono 16kHz raw PCM.

### 4.2 STT (Speech-to-Text)

**Situación actual:** whisper.cpp (`whisper-cli`) como subprocess con modelos ONNX descargados de HuggingFace.

**Cross-platform:** Los binarios de whisper.cpp existen para las tres plataformas:
- Linux: `whisper-cli` via package manager
- macOS: `whisper-cpp` via Homebrew o binario estático
- Windows: binarios `.exe` de [ggerganov/whisper.cpp releases](https://github.com/ggerganov/whisper.cpp/releases)

**Proceso:** En `stt.py`, buscar el binario en paths por plataforma:
- Linux: `/usr/bin/whisper-cli`
- macOS: `/opt/homebrew/bin/whisper-cli` o `/usr/local/bin/whisper-cli`
- Windows: `%LOCALAPPDATA%\sayri\whisper-cli.exe` o junto al ejecutable

Los modelos ONNX son cross-platform (son archivos de datos).

### 4.3 TTS (Text-to-Speech)

**Situación actual:** Piper (`piper`) como subprocess con modelos ONNX de rhasspy/piper-voices.

**Cross-platform:** Los binarios de Piper existen para las tres plataformas:
- Linux: `piper` via package manager
- macOS: compilar desde fuente o binario estático
- Windows: binarios `.exe` de [rhasspy/piper releases](https://github.com/rhasspy/piper/releases)

**Proceso:** Igual que STT — buscar binario en paths por plataforma.

### 4.4 Wake Word (openWakeWord)

**Situación actual:** Modelos ONNX ejecutados via `onnxruntime` (librería Python).

**Cross-platform:** `onnxruntime` es una librería Python pura que funciona nativamente en las tres plataformas. **No requiere cambios.**

### 4.5 Sistema de ventanas y posicionamiento

**Situación actual:** gtk4-layer-shell (Wayland) + ctypes X11 (X11).

**Con Tauri:**

| Plataforma | Cómo lograr "flotante + siempre encima + posición fija" |
|---|---|
| **Linux** | Mantener GTK4 + layer-shell para Linux (o usar Tauri con `always_on_top` que funciona en la mayoría de WMs) |
| **macOS** | Tauri `always_on_top: true` + `position()` con coordenadas calculadas desde `NSScreen.visibleFrame` para evitar el Dock. Alternativa: NSPanel con level `.floating`. |
| **Windows** | Tauri `always_on_top: true` + `skip_taskbar: true` + posición fija calculada desde `GetSystemMetrics(SM_XVIRTUALSCREEN)`. Para precisión total, usar `RegisterWindowMessage("AppBarNotify")` para registrar Sayri como app bar. |

**Limitación conocida:** En macOS, el Dock y el menú bar siempre están encima de cualquier ventana. Sayri no puede tapar el Dock. La posición se calcula relativa al `visibleFrame` (área útil sin Dock).

### 4.6 Sandbox

**Situación actual:** Bubblewrap (`bwrap`) con 5 niveles de aislamiento.

**Dependencia real:** Muy baja. Solo los niveles 1 y 2 usan bwrap. El agente por defecto usa nivel 3 (ejecución directa). Si bwrap no está disponible, degrada graceful a ejecución directa.

**Alternativas por plataforma:**

| Nivel | Linux | macOS | Windows |
|---|---|---|---|
| L0 (sin ejecución) | Return directo (sin cambios) | Return directo | Return directo |
| L1 (read-only fs) | `bwrap` (sin cambios) | `sandbox-exec` con profiles `.sb` dinámicos | Job Objects + `JOB_OBJECT_UILIMIT_FS` o AppContainer |
| L2 (aislado) | `bwrap` (sin cambios) | `sandbox-exec` + `unshare` equivalentes | Job Objects + restricciones de red |
| L3 (host usuario) | Ejecución directa (sin cambios) | Ejecución directa (sin cambios) | Ejecución directa (sin cambios) |
| L4 (host root) | `pkexec` (Polkit) | `osascript -e 'do shell script "..." with administrator privileges'` | UAC via `ShellExecute` con `runas` |

**Proceso:** Detectar plataforma en `executor.py` y dispatchear al mecanismo nativo correspondiente.

### 4.7 Configuración

**Situación actual:** GLib key-file (`sayri.conf`).

**Cross-platform:** Migrar a TOML (librería Python `tomli`/`tomli_w`) o JSON. TOML es el estándar moderno para configuración en Python (PEP 680) y es legible por humanos.

**Paths por plataforma:**
- Linux: `~/.config/sayri/sayri.conf`
- macOS: `~/Library/Application Support/sayri/sayri.toml`
- Windows: `%APPDATA%\sayri\sayri.toml`

### 4.8 Autostart

**Situación actual:** Archivo `.desktop` en `~/.config/autostart/`.

**Por plataforma:**
- macOS: Crear un `LaunchAgent plist` en `~/Library/LaunchAgents/es.inled.sayri.plist`
- Windows: Clave de registro `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` o acceso directo en la carpeta de inicio (`shell:startup`)

### 4.9 System Tray

**Situación actual:** GTK3 + AppIndicator3 via D-Bus.

**Con Tauri:** El system tray es nativo y cross-platform. El menú del tray se define en Rust:
- "Sayri Orb" (mostrar/ocultar)
- "Settings" (abrir configuración)
- "About"
- "Quit"

No requiere código por plataforma.

### 4.10 SQLite

**Situación actual:** `sqlite3` de Python (incluido en stdlib).

**Cross-platform:** Funciona nativamente en las tres plataformas. **No requiere cambios.**

### 4.11 Skills, Plugins, Gateways

**Situación actual:** Directorios en `~/.config/sayri/skills/`, `~/.config/sayri/plugins/`, con `SKILL.md` y `manifest.json`.

**Cross-platform:** Adapatar paths por plataforma (igual que configuración). La lógica de scan, install, remove es 100% Python y portable. **Requiere mínimo cambio.**

---

## 5. Stack tecnológico recomendado para el porting

### Frontend (UI)
| Tecnología | Versión | Propósito |
|---|---|---|
| **Tauri** | 2.x | Framework de app de escritorio (Rust + Web) |
| **Vite** | 5.x+ | Bundler del frontend |
| **React** o **Vanilla TS** | - | Framework del frontend (puede mantener Expo si se migra el build) |
| **Expo (web export)** | 57.x | Frontend actual del Orb, se mantiene como bundle web estático |

### Backend
| Tecnología | Versión | Propósito |
|---|---|---|
| **Python** | 3.14 | Lenguaje del backend (sidecar) |
| **httpx** | - | Streaming SSE para LLM |
| **onnxruntime** | - | Ejecución de modelos ONNX (wake word) |
| **SQLite** | stdlib | Persistencia de sesiones |

### Binarios nativos (sidecar)
| Binario | Plataformas | Propósito |
|---|---|---|
| **whisper.cpp** (`whisper-cli`) | Linux, macOS, Windows | STT |
| **Piper** (`piper`) | Linux, macOS, Windows | TTS |
| **SoX** (opcional) | Linux, macOS, Windows | Audio capture/playback alternativo |

---

## 6. Estructura del proyecto portado

```
sayri/
├── src-tauri/                    # Backend Rust de Tauri
│   ├── Cargo.toml
│   ├── tauri.conf.json           # Config de Tauri (ventana, tray, sidecar)
│   ├── src/
│   │   ├── main.rs               # Setup: tray, ventana overlay, sidecar launcher
│   │   ├── tray.rs               # System tray
│   │   ├── sidecar.rs            # Lanzamiento y comunicación con Python
│   │   └── commands.rs           # Comandos IPC expuestos al frontend
│   └── icons/
├── src/                          # Frontend web (TypeScript)
│   ├── App.tsx                   # Orb + Cajita + Settings (migrado de web/)
│   ├── components/
│   ├── bridge.ts                 # Wrapper de invoke() para IPC con Python
│   └── main.tsx
├── sayri-core/                   # Backend Python (el actual, adaptado)
│   ├── sayri/
│   │   ├── app.py                # Adaptado: sin GTK, solo lógica
│   │   ├── audio.py              # Adaptado: multiplatform
│   │   ├── stt.py                # Adaptado: paths multiplatform
│   │   ├── tts.py                # Adaptado: paths multiplatform
│   │   ├── llm.py                # Sin cambios significativos
│   │   ├── config.py             # Migrado a TOML
│   │   ├── paths.py              # Adaptado: paths por plataforma
│   │   ├── domain/               # Sin cambios significativos
│   │   └── adapters/
│   │       ├── sandbox/          # Adaptado: multiplatform
│   │       └── storage/          # Sin cambios (SQLite)
│   └── requirements.txt
├── package.json
└── build/
    ├── entitlements.mac.plist    # macOS sandbox entitlements
    └── nsis/                     # Windows installer config
```

---

## 7. Flujo de build y empaquetado

### macOS
1. Python sidecar empaquetado con PyInstaller (`--onefile --target-arch universal2` para soportar Intel + Apple Silicon)
2. Binarios whisper.cpp y Piper descargados/empaquetados junto al sidecar
3. Tauri build genera `.app` bundlada
4. Code signing con certificado Apple Developer
5. Notarización con `notarytool`
6. Distribución: `.dmg` o `.pkg`

### Windows
1. Python sidecar empaquetado con PyInstaller (`--onefile`)
2. Binarios whisper.cpp y Piper como `.exe` empaquetados
3. Tauri build genera `.msi` o `.exe` (NSIS)
4. Firma con certificado (recomendado: certificado EV para evitar falsos positivos de Defender)
5. Distribución: `.msi` o `.exe` auto-contenido

### Linux (se mantiene)
1. Empaquetado existente: `.deb`, `.rpm`, `.pkg.tar.zst`, Flatpak
2. Adicional: Tauri build genera `.AppImage` y `.deb`

---

## 8. Problemas conocidos y mitigaciones

### macOS
- **Dock:** El Dock siempre está encima de Sayri. Mitigar calculando posición desde `visibleFrame`.
- **Gatekeeper:** App sin notarizar bloqueada. Mitigar con certificado Apple Developer + notarización.
- **Piper en macOS:** No hay binario pre-compilado oficial. Opciones: compilar desde fuente, usar Homebrew bottle, o distribuir el binario compilado.
- **Audio CoreAudio:** El formato raw PCM de whisper/Piper es compatible. SoX lo maneja bien.

### Windows
- **Defender:** Falsos positivos con ejecutables PyInstaller. Mitigar con firma de código.
- **Ruta de whisper/Piper:** `%LOCALAPPDATA%\sayri\` es el path correcto para binarios por usuario.
- **ShellExecute runas:** Para L4 (elevación), `ShellExecuteA(NULL, "runas", "cmd.exe", ...)` ejecuta UAC.
- **Job Objects:** Para sandbox L1/L2, usar `CreateJobObject` + `AssignProcessToJobObject` con limits.

### Cross-platform
- **PyInstaller:** El sidecar Python empaquetado pesa ~50-100MB. Alternativa: distribuir un Python embeddable (~10MB) y los .pyc puros.
- **Modelos ONNX:** Descargados en primer uso (~50MB para whisper, ~50MB para Piper por voz, ~10MB para wake word). Universal entre plataformas.
- **Tamaño total estimado:** 150-250MB por instalación (ejecutable + modelos base).

---

## 9. Orden de implementación recomendado

1. **Refactorizar el core Python** para multiplataforma (paths, config TOML, audio detection, sandbox detection). Sin dependencias de GTK.
2. **Crear el proyecto Tauri** con el frontend web migrado del build Expo actual.
3. **Implementar el sidecar** en Rust que lance y se comunique con Python por IPC.
4. **Portar la ventana overlay** con Tauri (transparencia, always_on_top, posición fija).
5. **Portar el system tray** con Tauri.
6. **Empaquetar y testear en macOS** (binarios nativos, code signing, notarización).
7. **Empaquetar y testear en Windows** (binarios nativos, firma de código, installer).
8. **Testing cross-platform** de audio, STT, TTS, LLM streaming, wake word.

---

## 10. Componentes que NO requieren cambios

- `llm.py` — streaming SSE con httpx, 100% Python portable
- `downloads.py` — URLs de HuggingFace, portable
- `domain/agent_engine.py` — lógica de agentes ReAct, portable
- `domain/skills_scanner.py` — análisis estático de skills, portable
- `domain/secrets_manager.py` — vault con machine-id salt, portable
- `domain/triggers.py` — engine de triggers, portable
- `domain/cron_scheduler.py` — scheduler de tareas, portable
- `adapters/storage/sqlite_sessions.py` — SQLite, portable
- `sound.py` — solo necesita la cadena de playback multiplatform
- `skills.py` — CLI de skills, portable
- `gateway_supervisor.py` — gateways Telegram/Discord/MCP, portable
- Todos los tests unitarios (adaptar paths si es necesario)
