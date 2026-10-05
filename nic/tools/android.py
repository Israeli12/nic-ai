"""Android control over ADB.

Everything here is local: USB cable or `adb connect` over your own Wi-Fi.
Enable Developer options -> USB debugging on the phone first, then accept
the pairing prompt. See docs/android.md.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from ..config import Config, expand
from .registry import ToolError, ToolRegistry, int_param, object_schema, string_param

# Human names for the keyevents worth exposing.
KEYEVENTS = {
    "home": 3,
    "back": 4,
    "call": 5,
    "end_call": 6,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "enter": 66,
    "delete": 67,
    "menu": 82,
    "play_pause": 85,
    "next": 87,
    "previous": 88,
    "mute": 91,
    "recent_apps": 187,
    "wake": 224,
    "sleep": 223,
}


class Adb:
    """Thin wrapper around the adb binary."""

    def __init__(self, config: Config):
        self.path = config.android.adb_path
        self.serial = config.android.serial.strip()
        self.timeout = config.android.command_timeout_seconds

    def _base(self) -> list[str]:
        binary = shutil.which(self.path) or self.path
        command = [binary]
        if self.serial:
            command += ["-s", self.serial]
        return command

    def run(self, *args: str, binary_output: bool = False, timeout: int | None = None):
        if not shutil.which(self.path) and not Path(self.path).exists():
            raise ToolError(
                "adb was not found. Install Android platform-tools and set"
                " android.adb_path in config.yaml"
            )
        try:
            completed = subprocess.run(
                self._base() + list(args),
                capture_output=True,
                text=not binary_output,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(f"adb timed out: {' '.join(args)}") from exc
        if completed.returncode != 0:
            detail = completed.stderr if not binary_output else b""
            message = (detail or b"" if binary_output else detail or "").strip()
            raise ToolError(f"adb failed: {message or ' '.join(args)}")
        return completed.stdout if binary_output else completed.stdout.strip()

    def shell(self, *args: str) -> str:
        return self.run("shell", *args)


def _escape_for_shell(text: str) -> str:
    """Quote text for `adb shell input text`, which is picky about spaces."""
    return text.replace(" ", "%s").replace("'", "'\\''")


def build_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    adb = Adb(config)

    @registry.tool(
        "android_devices",
        "List Android devices currently reachable over ADB.",
        surface="android",
    )
    def android_devices() -> list[str]:
        output = adb.run("devices", "-l")
        lines = [line.strip() for line in output.splitlines()[1:] if line.strip()]
        return lines or ["no devices attached"]

    @registry.tool(
        "android_status",
        "Report the phone's battery level, charging state, and screen state.",
        surface="android",
    )
    def android_status() -> dict[str, object]:
        battery = adb.shell("dumpsys", "battery")
        level = re.search(r"level:\s*(\d+)", battery)
        # Any of the three supplies reading true means it is charging.
        charging = any(
            match.group(1) == "true"
            for match in re.finditer(r"(?:AC|USB|Wireless) powered:\s*(\w+)", battery)
        )
        awake = "mWakefulness=Awake" in adb.shell("dumpsys", "power")
        return {
            "battery_percent": int(level.group(1)) if level else None,
            "charging": charging,
            "screen_on": awake,
            "model": adb.shell("getprop", "ro.product.model"),
        }

    @registry.tool(
        "android_open_app",
        "Open an app on the phone by package name, e.g. 'com.android.settings'.",
        object_schema({"package": string_param("Android package name")}, ["package"]),
        surface="android",
    )
    def android_open_app(package: str) -> str:
        adb.shell("monkey", "-p", package.strip(), "-c", "android.intent.category.LAUNCHER", "1")
        return f"opened {package}"

    @registry.tool(
        "android_list_apps",
        "List installed app package names, optionally filtered by a substring.",
        object_schema({"contains": string_param("Filter packages containing this text")}),
        surface="android",
    )
    def android_list_apps(contains: str = "") -> list[str]:
        output = adb.shell("pm", "list", "packages", "-3")
        packages = [line.replace("package:", "").strip() for line in output.splitlines()]
        needle = contains.strip().lower()
        if needle:
            packages = [name for name in packages if needle in name.lower()]
        return sorted(packages)[:200]

    @registry.tool(
        "android_key",
        "Press a hardware/system key on the phone: " + ", ".join(sorted(KEYEVENTS)) + ".",
        object_schema({"key": string_param("Key name from the supported list")}, ["key"]),
        surface="android",
    )
    def android_key(key: str) -> str:
        code = KEYEVENTS.get(key.strip().lower())
        if code is None:
            raise ToolError(f"unsupported key: {key}")
        adb.shell("input", "keyevent", str(code))
        return f"pressed {key}"

    @registry.tool(
        "android_tap",
        "Tap the phone screen at pixel coordinates.",
        object_schema(
            {"x": int_param("X coordinate"), "y": int_param("Y coordinate")}, ["x", "y"]
        ),
        surface="android",
    )
    def android_tap(x: int, y: int) -> str:
        adb.shell("input", "tap", str(int(x)), str(int(y)))
        return f"tapped ({x}, {y})"

    @registry.tool(
        "android_swipe",
        "Swipe on the phone screen between two points.",
        object_schema(
            {
                "x1": int_param("Start X"),
                "y1": int_param("Start Y"),
                "x2": int_param("End X"),
                "y2": int_param("End Y"),
                "duration_ms": int_param("Swipe duration in milliseconds (default 300)"),
            },
            ["x1", "y1", "x2", "y2"],
        ),
        surface="android",
    )
    def android_swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        adb.shell(
            "input", "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)),
            str(max(50, int(duration_ms))),
        )
        return f"swiped ({x1},{y1}) -> ({x2},{y2})"

    @registry.tool(
        "android_type",
        "Type text into the phone's currently focused text field.",
        object_schema({"text": string_param("Text to type")}, ["text"]),
        surface="android",
    )
    def android_type(text: str) -> str:
        adb.shell("input", "text", _escape_for_shell(text))
        return f"typed {len(text)} characters"

    @registry.tool(
        "android_screenshot",
        "Capture the phone screen and save the PNG on the laptop; returns the path.",
        surface="android",
    )
    def android_screenshot() -> str:
        data = adb.run("exec-out", "screencap", "-p", binary_output=True)
        target_dir = expand(config.laptop.screenshot_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"phone-{datetime.now():%Y%m%d-%H%M%S}.png"
        path.write_bytes(data)
        return str(path)

    @registry.tool(
        "android_notifications",
        "Summarise the phone's current notifications.",
        surface="android",
    )
    def android_notifications() -> list[str]:
        output = adb.shell("dumpsys", "notification", "--noredact")
        titles = re.findall(r"android\.title=(?:String )?\(?([^\n)]+)\)?", output)
        texts = re.findall(r"android\.text=(?:String )?\(?([^\n)]+)\)?", output)
        pairs = [f"{title.strip()}: {text.strip()}" for title, text in zip(titles, texts)]
        return pairs[:30] or ["no notifications found"]

    @registry.tool(
        "android_call",
        "Start a phone call to a number.",
        object_schema({"number": string_param("Phone number to dial")}, ["number"]),
        dangerous=True,
        surface="android",
    )
    def android_call(number: str) -> str:
        cleaned = re.sub(r"[^\d+#*]", "", number)
        if not cleaned:
            raise ToolError("that does not look like a phone number")
        adb.shell("am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{cleaned}")
        return f"calling {cleaned}"

    @registry.tool(
        "android_compose_sms",
        "Open the SMS composer pre-filled with a recipient and message."
        " You still press send on the phone.",
        object_schema(
            {
                "number": string_param("Recipient phone number"),
                "message": string_param("Message body"),
            },
            ["number", "message"],
        ),
        surface="android",
    )
    def android_compose_sms(number: str, message: str) -> str:
        cleaned = re.sub(r"[^\d+#*]", "", number)
        adb.shell(
            "am", "start", "-a", "android.intent.action.SENDTO",
            "-d", f"sms:{cleaned}", "--es", "sms_body", f"'{message}'",
        )
        return f"composed SMS to {cleaned}"

    @registry.tool(
        "android_media",
        "Control media playback on the phone: play_pause, next, previous.",
        object_schema({"action": string_param("play_pause, next, or previous")}, ["action"]),
        surface="android",
    )
    def android_media(action: str) -> str:
        code = KEYEVENTS.get(action.strip().lower())
        if code is None or action.strip().lower() not in {"play_pause", "next", "previous"}:
            raise ToolError(f"unsupported media action: {action}")
        adb.shell("input", "keyevent", str(code))
        return f"media {action}"

    @registry.tool(
        "android_pull_file",
        "Copy a file from the phone to the laptop.",
        object_schema(
            {
                "remote_path": string_param("Path on the phone, e.g. /sdcard/DCIM/photo.jpg"),
                "local_dir": string_param("Destination folder on the laptop"),
            },
            ["remote_path"],
        ),
        surface="android",
    )
    def android_pull_file(remote_path: str, local_dir: str = "~/nic-ai/pulled") -> str:
        destination = expand(local_dir)
        destination.mkdir(parents=True, exist_ok=True)
        adb.run("pull", remote_path, str(destination), timeout=300)
        return str(destination / Path(remote_path).name)

    @registry.tool(
        "android_push_file",
        "Copy a file from the laptop to the phone.",
        object_schema(
            {
                "local_path": string_param("File on the laptop"),
                "remote_dir": string_param("Destination folder on the phone (default /sdcard/Download)"),
            },
            ["local_path"],
        ),
        surface="android",
    )
    def android_push_file(local_path: str, remote_dir: str = "/sdcard/Download") -> str:
        source = expand(local_path)
        if not source.exists():
            raise ToolError(f"file not found: {source}")
        adb.run("push", str(source), remote_dir, timeout=300)
        return f"pushed to {remote_dir}/{source.name}"

    @registry.tool(
        "android_lock",
        "Lock the phone screen.",
        dangerous=True,
        surface="android",
    )
    def android_lock() -> str:
        adb.shell("input", "keyevent", str(KEYEVENTS["sleep"]))
        return "phone locked"

    return registry
