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


def cmd_wake(args: argparse.Namespace) -> int:
    """Always-listening mode: wake word plus owner-voice filtering."""
    config = load_config(args.config)
    from .voice import Speaker, Transcriber, VoiceUnavailable
    from .voiceid import VoiceIdUnavailable
    from .wake import WakeLoop, load_verifier

    agent = Agent(config, approver=None)
    try:
        transcriber = Transcriber(config.voice)
        speaker = Speaker(config.voice)
        verifier = load_verifier(config, print) if not args.any_voice else None
    except (VoiceUnavailable, VoiceIdUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.any_voice:
        print("(--any-voice: speaker verification is off for this run)")

    loop = WakeLoop(
        config,
        agent=agent,
        transcriber=transcriber,
        verifier=verifier,
        speaker=speaker,
        on_event=print,
    )
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        loop.stop()
        if loop.source is not None:
            loop.source.close()
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    """Record a voiceprint so only you can give commands."""
    config = load_config(args.config)
    from .enrollment import run_enrollment
    from .voiceid import VoiceIdUnavailable

    try:
        run_enrollment(config, samples_wanted=args.samples, label=args.label)
    except VoiceIdUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled")
        return 1
    print("\nNow run: python -m nic wake")
    return 0


def cmd_qr(args: argparse.Namespace) -> int:
    """Print the web UI address, as a QR code when qrcode is installed."""
    config = load_config(args.config)
    from .web.server import _local_ip

    host = _local_ip() if config.web.host in {"0.0.0.0", ""} else config.web.host
    url = f"http://{host}:{config.web.port}"
    try:
        import qrcode
    except ImportError:
        print(url)
        print("\n(pip install qrcode for a scannable code here)")
        return 0
    code = qrcode.QRCode(border=1)
    code.add_data(url)
    code.make(fit=True)
    code.print_ascii(invert=True)
    print(url)
    if config.web.access_token:
        print(f"token: {config.web.access_token}")
    return 0


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
        try:
            import sounddevice  # noqa: F401

            print("  ok   microphone backend present")
        except (ImportError, OSError):
            ok = False
            print("  FAIL pip install sounddevice (and check a mic is connected)")

    print("voice id:")
    from .config import expand as _expand

    voiceprint_path = _expand(config.voice.voiceprint)
    if voiceprint_path.exists():
        try:
            from .voiceid import Voiceprint

            voiceprint = Voiceprint.load(voiceprint_path)
            print(
                f"  ok   '{voiceprint.label}' enrolled via {voiceprint.embedder},"
                f" threshold {voiceprint.threshold}"
            )
            if voiceprint.embedder == "mfcc":
                print("       (pip install resemblyzer and re-enroll for better accuracy)")
        except Exception as exc:  # noqa: BLE001 - a corrupt print must not hide
            ok = False
            print(f"  FAIL could not read {voiceprint_path}: {exc}")
    elif config.voice.require_enrolled_voice and config.voice.enabled:
        ok = False
        print(f"  FAIL no voiceprint at {voiceprint_path}. Run: python -m nic enroll")
    else:
        print("  skip no voiceprint enrolled (any voice would be accepted)")

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

    wake = subparsers.add_parser(
        "wake", help="Always-listening wake-word mode (owner's voice only)"
    )
    wake.add_argument(
        "--any-voice",
        action="store_true",
        help="Skip speaker verification for this run",
    )
    wake.set_defaults(func=cmd_wake)

    enroll = subparsers.add_parser("enroll", help="Record your voiceprint")
    enroll.add_argument("--samples", type=int, default=5, help="Clips to record")
    enroll.add_argument("--label", default="owner", help="Name for this voiceprint")
    enroll.set_defaults(func=cmd_enroll)

    serve = subparsers.add_parser("serve", help="Start the phone-friendly web UI")
    serve.set_defaults(func=cmd_serve)

    qr = subparsers.add_parser("qr", help="Show the web UI address for your phone")
    qr.set_defaults(func=cmd_qr)

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
