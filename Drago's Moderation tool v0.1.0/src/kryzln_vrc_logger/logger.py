import argparse

from .config import Config
from .engine import VRCLogger
from .printing import safe_print
from .webhook import Discord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drago's Moderation Tool")
    parser.add_argument("--console", action="store_true", help="Run in console mode instead of GUI")
    parser.add_argument("--webhook-test", action="store_true", help="Send a webhook test message and exit")
    return parser.parse_args()


def run_webhook_test():
    Config.init()
    discord = Discord(Config.DISCORD_WEBHOOK, printer=safe_print)
    if not discord.enabled:
        safe_print("[!] Webhook is disabled. Set one in GUI Webhook URL field or KRYZLN_DISCORD_WEBHOOK.")
        return

    fields = [
        {"name": "VRChat Logs", "value": f"`{Config.VRCHAT_LOG_DIR}`", "inline": False},
        {"name": "VRCX DB", "value": f"`{Config.VRCX_DB_FILE}`", "inline": False},
    ]
    ok = discord.send_embed(
        "Webhook Test",
        "Manual webhook test from Drago's Moderation Tool.",
        0x35D2FF,
        fields,
    )
    if ok:
        safe_print("[+] Webhook test sent successfully.")
    else:
        safe_print("[!] Webhook test failed.")


def run_console():
    VRCLogger().run()


def run_gui_with_console_fallback():
    try:
        import tkinter as tk
        from .gui import ModerationToolGUI
    except ImportError as exc:
        safe_print(f"[!] GUI import failed: {exc}")
        safe_print("[!] Falling back to console mode.")
        run_console()
        return

    try:
        ModerationToolGUI().run()
    except (OSError, tk.TclError) as exc:
        safe_print(f"[!] GUI launch failed: {exc}")
        safe_print("[!] Falling back to console mode.")
        run_console()


def main():
    args = parse_args()

    if args.webhook_test:
        run_webhook_test()
        return

    if args.console:
        run_console()
        return

    run_gui_with_console_fallback()


if __name__ == "__main__":
    main()
