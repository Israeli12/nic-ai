"""Configuration loading.

Settings come from ``config.yaml`` next to the project root (copy
``config.example.yaml`` to start), with environment variables of the form
``NIC_<SECTION>_<KEY>`` taking precedence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional dependency
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass
class ModelConfig:
    # Ollama runs entirely on your machine; nothing leaves the laptop.
    host: str = "http://127.0.0.1:11434"
    name: str = "qwen3:4b"
    # Keep the context modest: on 8-16GB CPU-only boxes a big window is slow.
    context_tokens: int = 8192
    temperature: float = 0.2
    # Hard stop so a confused model cannot loop forever on tool calls.
    max_tool_iterations: int = 8
    request_timeout_seconds: int = 300


@dataclass
class AndroidConfig:
    enabled: bool = True
    adb_path: str = "adb"
    # Empty means "whatever single device is attached".
    serial: str = ""
    command_timeout_seconds: int = 30


@dataclass
class IosConfig:
    """iOS control is limited to Shortcuts you expose yourself.

    Apple gives no offline device-control API, so we POST to a Shortcut
    listening on your LAN. See docs/ios.md.
    """

    enabled: bool = False
    bridge_url: str = ""
    shared_secret: str = ""
    request_timeout_seconds: int = 15


@dataclass
class LaptopConfig:
    enabled: bool = True
    # Shell access is powerful; it stays behind an explicit opt-in.
    allow_shell: bool = False
    # Only these executables may be launched by name.
    app_allowlist: list[str] = field(
        default_factory=lambda: [
            "notepad",
            "calc",
            "explorer",
            "mspaint",
            "code",
            "chrome",
            "msedge",
            "firefox",
            "spotify",
        ]
    )
    screenshot_dir: str = "~/nic-ai/screenshots"


@dataclass
class SafetyConfig:
    # Every tool marked dangerous asks before it runs.
    confirm_dangerous_actions: bool = True
    # Tools listed here are refused outright, whatever the model asks for.
    blocked_tools: list[str] = field(default_factory=list)
    audit_log: str = "~/nic-ai/audit.log"


@dataclass
class VoiceConfig:
    enabled: bool = False
    # faster-whisper model size: tiny/base/small are realistic on CPU.
    stt_model: str = "base.en"
    stt_compute_type: str = "int8"
    # Path to a downloaded Piper voice (.onnx).
    piper_voice: str = ""
    piper_path: str = "piper"
    wake_word: str = "nic"


@dataclass
class WebConfig:
    enabled: bool = True
    # 0.0.0.0 so your phone can reach it over Wi-Fi.
    host: str = "0.0.0.0"
    port: int = 8713
    # Anyone on your network can reach the port, so a token is required.
    access_token: str = ""


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    android: AndroidConfig = field(default_factory=AndroidConfig)
    ios: IosConfig = field(default_factory=IosConfig)
    laptop: LaptopConfig = field(default_factory=LaptopConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    web: WebConfig = field(default_factory=WebConfig)


def _type_name(target_type: Any) -> str:
    """Field types arrive as strings because of `from __future__ import annotations`."""
    if isinstance(target_type, str):
        return target_type.strip()
    return getattr(target_type, "__name__", str(target_type))


def _coerce(value: Any, target_type: Any) -> Any:
    name = _type_name(target_type)
    if name == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if name == "int":
        return int(value)
    if name == "float":
        return float(value)
    if name == "str":
        return str(value)
    if name in {"list[str]", "List[str]", "list"}:
        if isinstance(value, str):
            # Comma-separated when it comes from an environment variable.
            return [part.strip() for part in value.split(",") if part.strip()]
        return [str(item) for item in value]
    return value


def _apply_mapping(section: Any, data: dict[str, Any]) -> None:
    known = {f.name: f.type for f in fields(section)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key: {key}")
        setattr(section, key, _coerce(value, known[key]))


def _apply_env(config: Config) -> None:
    for section_field in fields(config):
        section = getattr(config, section_field.name)
        if not is_dataclass(section):
            continue
        for leaf in fields(section):
            env_key = f"NIC_{section_field.name.upper()}_{leaf.name.upper()}"
            if env_key in os.environ:
                setattr(section, leaf.name, _coerce(os.environ[env_key], leaf.type))


def load_config(path: str | Path | None = None) -> Config:
    """Build a :class:`Config`, layering file settings then environment vars."""
    config = Config()
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        if yaml is None:
            raise RuntimeError("PyYAML is required to read config.yaml; pip install pyyaml")
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        for section_name, values in raw.items():
            if not hasattr(config, section_name):
                raise ValueError(f"unknown config section: {section_name}")
            if values:
                _apply_mapping(getattr(config, section_name), values)
    _apply_env(config)
    return config


def expand(path: str) -> Path:
    return Path(path).expanduser()
