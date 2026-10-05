"""Command-line interface: `python -m nic <command>`."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .agent import Agent, Step, build_registry
from .config import load_config
from .llm import ModelUnavailable, OllamaClient

BANNER = "nic-ai - offline assistant. Type 'exit' to quit, 'reset' to clear context."


def _approve_in_terminal(name: str, arguments: dict[str, Any], summary: str) -> bool:
    print(f"\n  [confirm] {summary}")
    try:
        answer = input("  allow this? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer in {"y", "yes"}


def _print_step(step: Step) -> None:
    marker = "." if step.ok else "!"
    preview = step.result if len(step.result) <= 200 else step.result[:200] + "..."
    print(f"  {marker} {step.tool} -> {preview}")


def cmd_chat(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    agent = Agent(config, approver=_approve_in_terminal, on_step=_print_step)
    speaker = None
    if args.speak:
        from .voice import Speaker

        speaker = Speaker(config.voice)

    print(BANNER)
    print(f"model: {config.model.name} via {config.model.host}")
    while True:
        try:
            text = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            return 0
        if text.lower() == "reset":
            agent.reset()
            print("context cleared")
            continue
        try:
            turn = agent.ask(text)
        except ModelUnavailable as exc:
            print(f"error: {exc}")
            continue
        print(f"\nnic> {turn.reply}")
        if turn.pending is not None:
            if _approve_in_terminal(
                turn.pending.tool_name, turn.pending.arguments, turn.pending.summary
            ):
                follow_up = agent.run_approved(
                    turn.pending.tool_name, turn.pending.arguments
                )
                print(f"nic> {follow_up.reply}")
        if speaker:
            speaker.say(turn.reply)
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    agent = Agent(config, approver=_approve_in_terminal, on_step=_print_step)
    try:
        turn = agent.ask(" ".join(args.prompt))
    except ModelUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(turn.reply)
    return 0


def cmd_listen(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from .voice import Speaker, Transcriber, VoiceUnavailable

    agent = Agent(config, approver=_approve_in_terminal, on_step=_print_step)
    try:
        transcriber = Transcriber(config.voice)
        speaker = Speaker(config.voice)
    except VoiceUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("Voice mode. Press Enter to record, Ctrl+C to stop.")
    while True:
        try:
            input(f"\n[Enter] record {args.seconds:.0f}s> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        try:
            heard = transcriber.record_and_transcribe(args.seconds)
        except VoiceUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if not heard:
            print("heard nothing")
            continue
        print(f"you> {heard}")
        try:
            turn = agent.ask(heard)
        except ModelUnavailable as exc:
            print(f"error: {exc}")
            continue
        print(f"nic> {turn.reply}")
        speaker.say(turn.reply)


def cmd_serve(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    from .web.server import serve

    return serve(config)


def cmd_tools(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    registry = build_registry(config)
    for name in registry.names():
        tool = registry.get(name)
        flag = " [confirms]" if tool.dangerous else ""
        print(f"{name:26} {tool.surface:8}{flag} {tool.description.splitlines()[0]}")
    print(f"\n{len(registry.names())} tools available")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check that every piece of the stack is actually present."""
    config = load_config(args.config)
    ok = True

    print("model:")
    client = OllamaClient(config.model)
    try:
        models = client.available_models()
        if any(entry.split(":")[0] == config.model.name.split(":")[0] for entry in models):
            print(f"  ok   {config.model.name} is pulled")
        else:
            ok = False
            print(f"  FAIL {config.model.name} not pulled. Run: ollama pull {config.model.name}")
    except ModelUnavailable as exc:
        ok = False
        print(f"  FAIL {exc}")

    print("android:")
    if not config.android.enabled:
        print("  skip disabled in config")
    else:
        from .tools.android import Adb
        from .tools.registry import ToolError

        try:
            output = Adb(config).run("devices")
            devices = [line for line in output.splitlines()[1:] if line.strip()]
            if devices:
                print(f"  ok   {len(devices)} device(s): {devices[0].split()[0]}")
            else:
                ok = False
                print("  FAIL adb works but no device is attached (check USB debugging)")
        except ToolError as exc:
            ok = False
            print(f"  FAIL {exc}")

    print("iphone:")
    if not config.ios.enabled:
        print("  skip disabled in config (see docs/ios.md)")
    elif not config.ios.bridge_url:
        ok = False
        print("  FAIL ios.enabled is true but ios.bridge_url is empty")
    else:
        print(f"  ok   bridge configured at {config.ios.bridge_url}")

    print("voice:")
    if not config.voice.enabled:
        print("  skip disabled in config")
    else:
        try:
            import faster_whisper  # noqa: F401

            print(f"  ok   faster-whisper present (model {config.voice.stt_model})")
        except ImportError:
            ok = False
            print("  FAIL pip install faster-whisper sounddevice")

    print("web:")
    if not config.web.enabled:
        print("  skip disabled in config")
    elif not config.web.access_token:
        ok = False
        print("  FAIL set web.access_token in config.yaml before exposing the UI")
    else:
        print(f"  ok   will listen on {config.web.host}:{config.web.port}")

    print("\nall good" if ok else "\nsome checks failed - see above")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nic", description="Offline assistant for your laptop and phone"
    )
    parser.add_argument("--config", help="Path to config.yaml", default=None)
    subparsers = parser.add_subparsers(dest="command")

    chat = subparsers.add_parser("chat", help="Interactive text chat (default)")
    chat.add_argument("--speak", action="store_true", help="Read replies aloud")
    chat.set_defaults(func=cmd_chat)

    ask = subparsers.add_parser("ask", help="Run a single request and exit")
    ask.add_argument("prompt", nargs="+")
    ask.set_defaults(func=cmd_ask)

    listen = subparsers.add_parser("listen", help="Voice conversation (offline STT/TTS)")
    listen.add_argument("--seconds", type=float, default=6.0, help="Recording length")
    listen.set_defaults(func=cmd_listen)

    serve = subparsers.add_parser("serve", help="Start the phone-friendly web UI")
    serve.set_defaults(func=cmd_serve)

    tools = subparsers.add_parser("tools", help="List available tools")
    tools.set_defaults(func=cmd_tools)

    doctor = subparsers.add_parser("doctor", help="Check the setup end to end")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["chat", *(argv or [])]) if argv else parser.parse_args(["chat"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
