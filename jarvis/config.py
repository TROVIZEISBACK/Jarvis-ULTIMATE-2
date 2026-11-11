"""Runtime settings. Everything can be overridden with environment variables (or a .env file)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UI_DIR = ROOT / "ui"


def read_dotenv() -> dict[str, str]:
    """The non-empty entries of .env, read now (so keys added while JARVIS runs are picked up)."""
    env, out = ROOT / ".env", {}
    if not env.exists():
        return out
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if value:  # an empty entry must not shadow other credential sources
            out[key] = value
    return out


def _load_dotenv() -> None:
    for key, value in read_dotenv().items():
        os.environ.setdefault(key, value)


_load_dotenv()


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


MODEL = os.environ.get("JARVIS_MODEL", "claude-opus-5-5")
# Which brain runs the model:
#   claude-code - Claude Code in headless mode, billed to your Claude Pro/Max plan (no API credits)
#   api         - the Anthropic API directly, billed to API credits (needs ANTHROPIC_API_KEY)
#   auto        - claude-code when a recent Claude Code is installed, otherwise api
BRAIN = os.environ.get("JARVIS_BRAIN", "auto").lower()
# low | medium | high | xhigh | max. Opus 5.5 defaults to medium; voice wants snappy answers.
EFFORT = os.environ.get("JARVIS_EFFORT", "medium")
# Fast mode: same model, up to 2.5x faster output, 2x price.
FAST_MODE = _flag("JARVIS_FAST", False)
MAX_TOKENS = int(os.environ.get("JARVIS_MAX_TOKENS", "32000"))
MAX_STEPS = int(os.environ.get("JARVIS_MAX_STEPS", "60"))  # tool round-trips per request

# Ask out loud before destructive shell/AppleScript commands. Set to 0 for no confirmations.
CONFIRM_DANGEROUS = _flag("JARVIS_CONFIRM_DANGEROUS", True)

HOST = os.environ.get("JARVIS_HOST", "127.0.0.1")
PORT = int(os.environ.get("JARVIS_PORT", "8765"))
REMOTE_PORT = int(os.environ.get("JARVIS_REMOTE_PORT", "8766"))  # HTTPS for phones, tablets and PCs (when switched on)

# Voice
WAKE_WORDS = [w.strip().lower() for w in os.environ.get("JARVIS_WAKE_WORDS", "jarvis,javis,jarvas,jervis").split(",")]
ALERTS = os.environ.get("JARVIS_ALERTS", "1") != "0"  # speak up about battery, runaway apps, disk, meetings
MEETING_HEADS_UP_MIN = int(os.environ.get("JARVIS_MEETING_HEADS_UP", "10"))

# Voice engine: "kokoro" (neural, local, models/ folder) or "say" (macOS built-in voices)
MODELS_DIR = ROOT / "models"
TTS = os.environ.get("JARVIS_TTS", "kokoro" if (MODELS_DIR / "voices-v1.0.bin").exists() else "say").lower()
KOKORO_VOICE = os.environ.get("JARVIS_KOKORO_VOICE", "bm_george")  # bm_george, bm_lewis, bm_fable, bm_daniel
KOKORO_SPEED = float(os.environ.get("JARVIS_KOKORO_SPEED", "1.0"))
KOKORO_THREADS = int(os.environ.get("JARVIS_KOKORO_THREADS", "4"))
KOKORO_MODEL = os.environ.get("JARVIS_KOKORO_MODEL", "int8")  # int8 (fast) or full (slightly richer, slower)
VOICE = os.environ.get("JARVIS_VOICE", "Daniel")  # `say` voice: fallback, or the voice when JARVIS_TTS=say
VOICE_RATE = int(os.environ.get("JARVIS_VOICE_RATE", "190"))
STT_LANGUAGE = os.environ.get("JARVIS_STT_LANGUAGE", "en-US")
FOLLOW_UP_SECONDS = float(os.environ.get("JARVIS_FOLLOW_UP_SECONDS", "8"))  # no wake word needed right after a reply
MIC_ENABLED = _flag("JARVIS_MIC", True)
# push = mic opens only while you press ⌘H (one command per press); wake = always listening for "Jarvis"
MAX_RECORDING = float(os.environ.get("JARVIS_MAX_RECORDING", "300"))  # seconds; ⌘H recordings stop and send after this
MIC_MODE = os.environ.get("JARVIS_MIC_MODE", "push").lower()
OPEN_BROWSER = _flag("JARVIS_OPEN_BROWSER", True)

OWNER = os.environ.get("JARVIS_OWNER", "sir")
IDLE_RESET_MINUTES = float(os.environ.get("JARVIS_IDLE_RESET_MINUTES", "20"))
