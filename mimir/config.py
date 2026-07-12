from __future__ import annotations

import base64
import contextlib
import json
import os
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any


APP_NAME = "Mimir"
KEYRING_SERVICE = "Mimir.OpenAI"
KEYRING_USERNAME = "default"
DEFAULT_ANALYSIS_MODEL = "gpt-5.4-mini"
DEFAULT_ANALYSIS_SERVICE_TIER = "priority"
DEFAULT_ANALYSIS_REASONING_EFFORT = "medium"
DEFAULT_SMARTER_MODEL = "gpt-5.4"
DEFAULT_SMARTER_SERVICE_TIER = "priority"
DEFAULT_SMARTER_REASONING_EFFORT = "medium"
DEFAULT_SMARTER_WEB_SEARCH_CONTEXT_SIZE = "low"
DEFAULT_VISUAL_MODEL = "gpt-5.5"
DEFAULT_VISUAL_REASONING_EFFORT = "high"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
FALLBACK_ANALYSIS_MODELS = ("gpt-4.1-mini", "gpt-4o-mini")
FALLBACK_SMARTER_MODELS = ("gpt-5.4-mini", "gpt-4.1")
FALLBACK_VISUAL_MODELS = ("gpt-5.4", "gpt-4.1", "gpt-4o")
LEGACY_DEFAULT_ANALYSIS_MODELS = {"gpt-4o-mini"}
LEGACY_DEFAULT_TRANSCRIPTION_MODELS = {"gpt-realtime-whisper"}

DEFAULT_COMPACT_WIDTH = 72
DEFAULT_COMPACT_HEIGHT = 52

SETTING_FALLBACKS = {
    "analysis_service_tier": DEFAULT_ANALYSIS_SERVICE_TIER,
    "analysis_reasoning_effort": DEFAULT_ANALYSIS_REASONING_EFFORT,
    "smarter_model": DEFAULT_SMARTER_MODEL,
    "smarter_service_tier": DEFAULT_SMARTER_SERVICE_TIER,
    "smarter_reasoning_effort": DEFAULT_SMARTER_REASONING_EFFORT,
    "smarter_web_search_context_size": DEFAULT_SMARTER_WEB_SEARCH_CONTEXT_SIZE,
    "visual_model": DEFAULT_VISUAL_MODEL,
    "visual_reasoning_effort": DEFAULT_VISUAL_REASONING_EFFORT,
}


@dataclass
class Settings:
    width: int = 680
    height: int = 520
    compact_width: int = DEFAULT_COMPACT_WIDTH
    compact_height: int = DEFAULT_COMPACT_HEIGHT
    x: int | None = None
    y: int | None = None
    start_compact: bool = True
    language: str = "en"
    analysis_model: str = DEFAULT_ANALYSIS_MODEL
    analysis_service_tier: str = DEFAULT_ANALYSIS_SERVICE_TIER
    analysis_reasoning_effort: str = DEFAULT_ANALYSIS_REASONING_EFFORT
    smarter_model: str = DEFAULT_SMARTER_MODEL
    smarter_service_tier: str = DEFAULT_SMARTER_SERVICE_TIER
    smarter_reasoning_effort: str = DEFAULT_SMARTER_REASONING_EFFORT
    smarter_web_search_enabled: bool = True
    smarter_web_search_context_size: str = DEFAULT_SMARTER_WEB_SEARCH_CONTEXT_SIZE
    visual_model: str = DEFAULT_VISUAL_MODEL
    visual_reasoning_effort: str = DEFAULT_VISUAL_REASONING_EFFORT
    ai_prompt_context: str = ""
    realtime_model: str = "gpt-realtime"
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    audio_sample_rate: int = 48000
    realtime_sample_rate: int = 24000
    audio_chunk_ms: int = 100
    transcription_commit_ms: int = 1200
    audio_vad_enabled: bool = True
    audio_vad_threshold: float = 0.004
    audio_vad_silence_ms: int = 700
    audio_vad_preroll_ms: int = 300
    microphone_enabled: bool = False
    auto_assist_interval_sec: int = 18
    notes_interval_sec: int = 45
    context_minutes: int = 8
    capture_exclusion: bool = True
    acrylic: bool = True
    shell_alpha: int = 43
    safety_identifier: str = ""
    onboarding_completed: bool = False

    def __post_init__(self) -> None:
        stale_compact = (
            (self.compact_width == 52 and self.compact_height == 52)
            or (self.compact_width <= 80 and self.compact_height <= 40)
            or (self.compact_width >= 400 and self.compact_height <= 80)
            or (self.compact_width >= 180 and self.compact_height <= 72)
        )
        if stale_compact:
            self.compact_width = DEFAULT_COMPACT_WIDTH
            self.compact_height = DEFAULT_COMPACT_HEIGHT


def app_data_dir() -> Path:
    root = os.getenv("APPDATA")
    path = Path(root) / APP_NAME if root else Path.home() / f".{APP_NAME.lower()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_path()
    defaults = Settings()
    if not path.exists():
        defaults.safety_identifier = str(uuid.uuid4())
        save_settings(defaults)
        return defaults

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}

    allowed = {field.name for field in fields(Settings)}
    data: dict[str, Any] = asdict(defaults)
    data.update({key: value for key, value in raw.items() if key in allowed})
    if raw.get("analysis_model") in LEGACY_DEFAULT_ANALYSIS_MODELS:
        data["analysis_model"] = DEFAULT_ANALYSIS_MODEL
    if raw.get("transcription_model") in LEGACY_DEFAULT_TRANSCRIPTION_MODELS:
        data["transcription_model"] = DEFAULT_TRANSCRIPTION_MODEL
    for name, fallback in SETTING_FALLBACKS.items():
        if not data.get(name):
            data[name] = fallback
    if not data.get("safety_identifier"):
        data["safety_identifier"] = str(uuid.uuid4())
    settings = Settings(**data)
    save_settings(settings)
    return settings


def save_settings(settings: Settings) -> None:
    path = settings_path()
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


class SecretStore:
    def __init__(self) -> None:
        self._fallback_path = app_data_dir() / "openai_key.dpapi"

    def get_api_key(self) -> str:
        env_key = os.getenv("OPENAI_API_KEY", "").strip()
        if env_key:
            return env_key

        try:
            import keyring

            value = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
            if value:
                return value.strip()
        except Exception:
            pass

        return self._get_dpapi()

    def set_api_key(self, api_key: str) -> None:
        api_key = api_key.strip()
        if not api_key:
            return

        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
            return
        except Exception:
            self._set_dpapi(api_key)

    def clear_api_key(self) -> None:
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except Exception:
            pass
        with contextlib.suppress(OSError):
            self._fallback_path.unlink(missing_ok=True)

    def _get_dpapi(self) -> str:
        if not self._fallback_path.exists():
            return ""
        try:
            import win32crypt

            protected = base64.b64decode(self._fallback_path.read_bytes())
            _, data = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
            return data.decode("utf-8").strip()
        except Exception:
            return ""

    def _set_dpapi(self, api_key: str) -> None:
        try:
            import win32crypt

            protected = win32crypt.CryptProtectData(
                api_key.encode("utf-8"),
                "Mimir OpenAI API key",
                None,
                None,
                None,
                0,
            )
            self._fallback_path.write_bytes(base64.b64encode(protected))
        except Exception:
            pass
