"""Runtime paths for Sayri.

All locations can be overridden with environment variables so the app can be
developed/run without installing (see the wrapper script in usr/bin/sayri):

  SAYRI_DATA_DIR    web orb build directory
  SAYRI_CONFIG_DIR  config directory
  SAYRI_STATE_DIR   models / voices / binaries directory
"""

import os

DEFAULT_DATA_DIR = "/usr/share/sayri/web"
DEFAULT_CONFIG_DIR = "~/.config/sayri"
DEFAULT_STATE_DIR = "~/.local/share/sayri"


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or os.path.expanduser(default)


def lib_dir() -> str:
    """Directory that contains this package (…/lib)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir() -> str:
    """Directory with the exported web orb build (index.html + bundle)."""
    explicit = os.environ.get("SAYRI_DATA_DIR")
    if explicit:
        return explicit
    # auto-detect: if the package lives at …/lib/sayri, the web build is at …/web
    lib = lib_dir()                          # …/usr/share/sayri/lib
    candidate = os.path.join(os.path.dirname(lib), "web")
    if os.path.isfile(os.path.join(candidate, "index.html")):
        return candidate
    return DEFAULT_DATA_DIR


def config_dir() -> str:
    p = _env("SAYRI_CONFIG_DIR", DEFAULT_CONFIG_DIR)
    os.makedirs(p, mode=0o700, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def state_dir() -> str:
    p = _env("SAYRI_STATE_DIR", DEFAULT_STATE_DIR)
    os.makedirs(p, mode=0o700, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        pass
    return p


def config_file() -> str:
    return os.path.join(config_dir(), "sayri.conf")


def models_dir() -> str:
    return os.path.join(state_dir(), "models")


def voices_dir() -> str:
    return os.path.join(state_dir(), "voices")


def bin_dir() -> str:
    return os.path.join(state_dir(), "bin")


def tmp_dir() -> str:
    return os.path.join(state_dir(), "tmp")


def skills_dir() -> str:
    return os.path.join(config_dir(), "skills")


def agents_dir() -> str:
    return os.path.join(config_dir(), "agents")


def plugins_dir() -> str:
    return os.path.join(config_dir(), "plugins")


def triggers_dir() -> str:
    return os.path.join(config_dir(), "triggers")


def sandboxes_dir() -> str:
    return os.path.join(state_dir(), "sandboxes")


def sessions_db() -> str:
    return os.path.join(state_dir(), "sessions.db")


def memory_file() -> str:
    return os.path.join(config_dir(), "memory.md")


def user_file() -> str:
    return os.path.join(config_dir(), "USER.md")


def find_sound(name: str) -> Optional[str]:
    """Find path to a sound file (activate, thinking, etc.)."""
    for base in (
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "sounds")),
        os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")),
        os.path.join(data_dir(), "sounds"),
        "/usr/share/sayri/sounds",
    ):
        for ext in (".mp3", ".wav", ".ogg"):
            candidate = os.path.join(base, f"{name}{ext}")
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
    return None


def ensure_dirs() -> None:
    for d in (
        config_dir(),
        skills_dir(),
        agents_dir(),
        plugins_dir(),
        triggers_dir(),
        sandboxes_dir(),
        models_dir(),
        voices_dir(),
        bin_dir(),
        tmp_dir(),
    ):
        os.makedirs(d, exist_ok=True)

    uf = user_file()
    if not os.path.isfile(uf):
        try:
            import getpass
            u = getpass.getuser()
            with open(uf, "w", encoding="utf-8") as f:
                f.write(f"# User Profile\n\n- **Username**: {u}\n- **OS**: Pulsar OS\n- **Assistant**: Sayri\n")
        except Exception:
            pass

    mf = memory_file()
    if not os.path.isfile(mf):
        try:
            with open(mf, "w", encoding="utf-8") as f:
                f.write("# Sayri Long-Term Memory\n\nThis file contains persistent memories, notes, and user preferences learned by Sayri.\n")
        except Exception:
            pass


def find_binary(name: str) -> str | None:
    """Look for a binary on PATH first, then in Sayri's own bin dir."""
    import shutil

    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(bin_dir(), name)
    return candidate if os.path.isfile(candidate) and os.access(candidate, os.X_OK) else None
