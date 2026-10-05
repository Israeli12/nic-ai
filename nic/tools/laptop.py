"""Laptop control tools.

Windows is the primary target (PowerShell + a few user32 calls), but the
cross-platform bits fall back sensibly so the project is still testable
and usable on Linux/macOS.
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from ..config import Config, expand
from .registry import ToolError, ToolRegistry, int_param, object_schema, string_param

IS_WINDOWS = platform.system() == "Windows"

# Virtual-key codes we drive through keybd_event on Windows.
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

MEDIA_KEYS = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "stop": VK_MEDIA_STOP,
    "mute": VK_VOLUME_MUTE,
}


def _tap_key(code: int, repeat: int = 1) -> None:
    if not IS_WINDOWS:
        raise ToolError("this action needs Windows")
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    for _ in range(max(1, repeat)):
        user32.keybd_event(code, 0, 0, 0)
        user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)


def run_powershell(script: str, timeout: int = 60) -> str:
    """Run a PowerShell snippet and return its stdout."""
    if not IS_WINDOWS:
        raise ToolError("PowerShell is only available on Windows")
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        raise ToolError("PowerShell was not found on PATH")
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise ToolError((completed.stderr or completed.stdout or "PowerShell failed").strip())
    return completed.stdout.strip()


def build_registry(config: Config) -> ToolRegistry:
    registry = ToolRegistry()
    laptop = config.laptop

    @registry.tool(
        "laptop_status",
        "Report laptop time, OS, battery level, and free disk space.",
    )
    def laptop_status() -> dict[str, object]:
        status: dict[str, object] = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
        }
        usage = shutil.disk_usage(Path.home().anchor or "/")
        status["disk_free_gb"] = round(usage.free / 1024**3, 1)
        status["disk_total_gb"] = round(usage.total / 1024**3, 1)
        if IS_WINDOWS:
            try:
                battery = run_powershell(
                    "(Get-CimInstance Win32_Battery | Select-Object -First 1"
                    " -ExpandProperty EstimatedChargeRemaining)"
                )
                status["battery_percent"] = int(battery) if battery else None
            except (ToolError, ValueError, subprocess.SubprocessError):
                status["battery_percent"] = None
        return status

    @registry.tool(
        "laptop_open_app",
        "Launch an allowlisted desktop application by short name, e.g. 'notepad'.",
        object_schema(
            {"app": string_param("Application short name from the configured allowlist")},
            ["app"],
        ),
    )
    def laptop_open_app(app: str) -> str:
        key = app.strip().lower()
        if key not in {entry.lower() for entry in laptop.app_allowlist}:
            raise ToolError(
                f"'{app}' is not in laptop.app_allowlist; add it to config.yaml to allow it"
            )
        if IS_WINDOWS:
            subprocess.Popen(["cmd", "/c", "start", "", key], shell=False)
        elif shutil.which(key):
            subprocess.Popen([key])
        else:
            raise ToolError(f"could not find '{app}' on this system")
        return f"launched {key}"

    @registry.tool(
        "laptop_open_url",
        "Open a URL or local file path in the default browser or application.",
        object_schema({"target": string_param("URL or absolute file path")}, ["target"]),
    )
    def laptop_open_url(target: str) -> str:
        target = target.strip()
        if target.startswith(("http://", "https://")):
            webbrowser.open(target)
            return f"opened {target}"
        path = expand(target)
        if not path.exists():
            raise ToolError(f"path not found: {path}")
        if IS_WINDOWS:
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            opener = "open" if platform.system() == "Darwin" else "xdg-open"
            subprocess.Popen([opener, str(path)])
        return f"opened {path}"

    @registry.tool(
        "laptop_media_key",
        "Press a media or mute key: play_pause, next, previous, stop, mute.",
        object_schema({"key": string_param("One of: play_pause, next, previous, stop, mute")}, ["key"]),
    )
    def laptop_media_key(key: str) -> str:
        code = MEDIA_KEYS.get(key.strip().lower())
        if code is None:
            raise ToolError(f"unsupported media key: {key}")
        _tap_key(code)
        return f"pressed {key}"

    @registry.tool(
        "laptop_volume",
        "Nudge system volume up or down. Each step is about 2 percent.",
        object_schema(
            {
                "direction": string_param("'up' or 'down'"),
                "steps": int_param("How many volume steps to press (1-25)"),
            },
            ["direction"],
        ),
    )
    def laptop_volume(direction: str, steps: int = 5) -> str:
        normalized = direction.strip().lower()
        if normalized not in {"up", "down"}:
            raise ToolError("direction must be 'up' or 'down'")
        steps = max(1, min(int(steps), 25))
        _tap_key(VK_VOLUME_UP if normalized == "up" else VK_VOLUME_DOWN, steps)
        return f"volume {normalized} x{steps}"

    @registry.tool(
        "laptop_screenshot",
        "Capture the laptop screen and save it as a PNG; returns the file path.",
    )
    def laptop_screenshot() -> str:
        try:
            from PIL import ImageGrab
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ToolError("screenshots need Pillow; pip install pillow") from exc
        target_dir = expand(laptop.screenshot_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"laptop-{datetime.now():%Y%m%d-%H%M%S}.png"
        ImageGrab.grab().save(path)
        return str(path)

    @registry.tool(
        "laptop_clipboard_read",
        "Read the current text contents of the laptop clipboard.",
    )
    def laptop_clipboard_read() -> str:
        if IS_WINDOWS:
            return run_powershell("Get-Clipboard -Raw")
        if shutil.which("xclip"):
            return subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
            ).stdout
        raise ToolError("no clipboard backend available")

    @registry.tool(
        "laptop_clipboard_write",
        "Replace the laptop clipboard contents with the given text.",
        object_schema({"text": string_param("Text to place on the clipboard")}, ["text"]),
    )
    def laptop_clipboard_write(text: str) -> str:
        if IS_WINDOWS:
            # Pipe through stdin so quoting in the text cannot break the command.
            executable = shutil.which("powershell") or shutil.which("pwsh")
            if not executable:
                raise ToolError("PowerShell was not found on PATH")
            subprocess.run(
                [executable, "-NoProfile", "-NonInteractive", "-Command", "$input | Set-Clipboard"],
                input=text,
                text=True,
                check=True,
                timeout=30,
            )
        elif shutil.which("xclip"):
            subprocess.run(
                ["xclip", "-selection", "clipboard"], input=text, text=True, check=True
            )
        else:
            raise ToolError("no clipboard backend available")
        return f"copied {len(text)} characters"

    @registry.tool(
        "laptop_list_windows",
        "List the titles of currently open application windows.",
    )
    def laptop_list_windows() -> list[str]:
        if not IS_WINDOWS:
            raise ToolError("window listing is implemented for Windows only")
        output = run_powershell(
            "Get-Process | Where-Object { $_.MainWindowTitle } |"
            " Select-Object -ExpandProperty MainWindowTitle"
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    @registry.tool(
        "laptop_find_files",
        "Search a folder for files whose names match a glob pattern.",
        object_schema(
            {
                "folder": string_param("Folder to search, e.g. '~/Documents'"),
                "pattern": string_param("Glob pattern, e.g. '*.pdf'"),
                "limit": int_param("Maximum results to return (default 20)"),
            },
            ["folder", "pattern"],
        ),
    )
    def laptop_find_files(folder: str, pattern: str, limit: int = 20) -> list[str]:
        root = expand(folder)
        if not root.is_dir():
            raise ToolError(f"not a folder: {root}")
        limit = max(1, min(int(limit), 200))
        matches = []
        for path in root.rglob(pattern):
            matches.append(str(path))
            if len(matches) >= limit:
                break
        return matches

    @registry.tool(
        "laptop_lock",
        "Lock the laptop screen immediately.",
        dangerous=True,
    )
    def laptop_lock() -> str:
        if IS_WINDOWS:
            ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
            return "locked"
        if shutil.which("loginctl"):
            subprocess.run(["loginctl", "lock-session"], check=False)
            return "locked"
        raise ToolError("no lock command available on this system")

    @registry.tool(
        "laptop_power",
        "Shut down, restart, sleep, or cancel a pending shutdown.",
        object_schema(
            {
                "action": string_param("One of: shutdown, restart, sleep, cancel"),
                "delay_seconds": int_param("Delay before shutdown/restart (default 30)"),
            },
            ["action"],
        ),
        dangerous=True,
    )
    def laptop_power(action: str, delay_seconds: int = 30) -> str:
        normalized = action.strip().lower()
        if not IS_WINDOWS:
            raise ToolError("power control is implemented for Windows only")
        delay = max(0, min(int(delay_seconds), 3600))
        if normalized == "shutdown":
            subprocess.run(["shutdown", "/s", "/t", str(delay)], check=True)
            return f"shutting down in {delay}s (say 'cancel' to stop it)"
        if normalized == "restart":
            subprocess.run(["shutdown", "/r", "/t", str(delay)], check=True)
            return f"restarting in {delay}s (say 'cancel' to stop it)"
        if normalized == "cancel":
            subprocess.run(["shutdown", "/a"], check=False)
            return "cancelled any pending shutdown"
        if normalized == "sleep":
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=False
            )
            return "sleeping"
        raise ToolError(f"unsupported power action: {action}")

    if laptop.allow_shell:

        @registry.tool(
            "laptop_run_command",
            "Run a shell command on the laptop and return its output."
            " Only enabled because laptop.allow_shell is true in your config.",
            object_schema(
                {
                    "command": string_param("Command line to execute"),
                    "timeout_seconds": int_param("Timeout in seconds (default 60)"),
                },
                ["command"],
            ),
            dangerous=True,
        )
        def laptop_run_command(command: str, timeout_seconds: int = 60) -> str:
            timeout = max(1, min(int(timeout_seconds), 600))
            try:
                completed = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise ToolError(f"command timed out after {timeout}s") from exc
            output = (completed.stdout or "") + (completed.stderr or "")
            return output.strip()[:8000] or f"exit code {completed.returncode}, no output"

    return registry
