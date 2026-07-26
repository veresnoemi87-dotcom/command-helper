#!/usr/bin/env python3
"""
cmdh — a tiny, dependency-free build/command runner ("mini gradle").

Works unmodified on Windows, Linux, and Android/Termux because it just
hands each configured command to the system shell (cmd.exe on Windows,
sh on everything else) and reports back what happened.

Config format (config.json), keys are the *execution order*, not IDs:

    {
        "1": "javac -d build src/Main.java",
        "2": "aapt2 compile -o build res",
        "3": "not_a_real_command foo"
    }

Steps run in ascending numeric order of the keys (so "2" always runs
before "10" — plain string sort would get that wrong, which is why we
sort by int(key) instead).

Each value can also be an object instead of a plain string, if you want
a friendly label or a per-step override:

    "1": { "cmd": "javac -d build src/Main.java", "name": "Compile Java" }

Usage:
    cmdh -init                     interactively create ./config.json
    cmdh -build                    run the steps in ./config.json
    cmdh -build -c other.json      run a specific config file
    cmdh -build -i filename        fetch config.json from Code Hub before building
    cmdh -build --continue-on-error   don't stop after a failing step
    cmdh -build --dry-run          print the plan, run nothing
    cmdh -build -q                 quiet console (log file still full)

`-i / --import` pulls a config from the public Code Hub API
(https://codyhub.lovable.app) instead of using a local file. It always
requests id="cmdh" and whatever name you pass:

    GET https://codyhub.lovable.app/api/get?id=cmdh&name=<filename>

The fetched text is written to the path given by -c/--config (default
config.json), overwriting it, then the normal build proceeds from that
file. Requires the third-party `requests` library (pip install requests).
"""

import argparse
import json
import os
import subprocess
import sys
import time
import requests
from datetime import datetime
from pathlib import Path


# --------------------------------------------------------------------------
# Code Hub — fetch a config.json from https://codyhub.lovable.app
# --------------------------------------------------------------------------

CODYHUB_BASE_URL = "https://codyhub.lovable.app/api/get"
CODYHUB_ID = "cmdh"  # fixed file ID this tool publishes/reads under


def fetch_config_from_hub(name: str, log: "Logger") -> str:
    """Fetch a single file's raw text from Code Hub (id is always 'cmdh')."""
    params = {"id": CODYHUB_ID, "name": name}
    log.info(f"Fetching config from Code Hub: {CODYHUB_BASE_URL}?id={CODYHUB_ID}&name={name}")
    try:
        resp = requests.get(CODYHUB_BASE_URL, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        log.error(f"Code Hub: network error fetching '{name}': {e}")
        sys.exit(2)

    if resp.status_code == 200:
        return resp.text
    elif resp.status_code == 404:
        log.error(f"Code Hub: no file named '{name}' under id '{CODYHUB_ID}' (404).")
    elif resp.status_code == 400:
        log.error(f"Code Hub: bad request — id or name malformed (400).")
    else:
        log.error(f"Code Hub: HTTP {resp.status_code} fetching '{name}'.")
    sys.exit(2)


# --------------------------------------------------------------------------
# Colors — degrade to plain text anywhere ANSI isn't available/wanted.
# --------------------------------------------------------------------------

class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.name == "nt":
        # Try to switch the console into ANSI mode (Windows 10+).
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
        except Exception:
            return False
    return sys.stdout.isatty()


USE_COLOR = _supports_color()


def paint(text: str, color: str) -> str:
    if not USE_COLOR:
        return text
    return f"{color}{text}{Color.RESET}"


# --------------------------------------------------------------------------
# Logger — mirrors everything to console (unless -q) and to a log file.
# --------------------------------------------------------------------------

class Logger:
    def __init__(self, log_path: Path, quiet: bool = False):
        self.quiet = quiet
        self.log_path = log_path
        self._fh = open(log_path, "a", encoding="utf-8")
        self._write_raw(f"\n===== cmdh run started {datetime.now().isoformat(timespec='seconds')} =====\n")

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _write_raw(self, text: str):
        self._fh.write(text)
        self._fh.flush()

    def _log(self, level: str, msg: str, color: str = None, console: bool = True):
        line = f"[{self._timestamp()}] [{level}] {msg}"
        self._write_raw(line + "\n")
        if console and not self.quiet:
            print(paint(line, color) if color else line)

    def info(self, msg):
        self._log("INFO", msg, Color.BLUE)

    def step(self, msg):
        self._log("STEP", msg, Color.CYAN)

    def ok(self, msg):
        self._log("OK", msg, Color.GREEN)

    def warn(self, msg):
        self._log("WARN", msg, Color.YELLOW)

    def error(self, msg):
        self._log("ERROR", msg, Color.RED)

    def raw(self, text: str, stream: str = "stdout"):
        # Command output — always goes to the file; echoed to console
        # unless quiet, prefixed so it's visually distinct from our own logs.
        prefix = "    | " if stream == "stdout" else paint("    ! ", Color.RED)
        for line in text.splitlines():
            self._write_raw(f"    | {line}\n")
            if not self.quiet:
                print(f"{prefix}{line}")

    def close(self):
        self._fh.close()


# --------------------------------------------------------------------------
# Config loading
# --------------------------------------------------------------------------

def load_config(path: Path):
    if not path.exists():
        print(paint(f"Config file not found: {path}", Color.RED), file=sys.stderr)
        print("Run 'cmdh -init' first to create one.", file=sys.stderr)
        sys.exit(2)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(paint(f"Invalid JSON in {path}: {e}", Color.RED), file=sys.stderr)
        sys.exit(2)

    settings = raw.get("_settings", {}) if isinstance(raw.get("_settings"), dict) else {}

    steps = []
    for key, value in raw.items():
        if key == "_settings" or not key.isdigit():
            continue  # ignore metadata / non-numeric keys
        if isinstance(value, str):
            cmd, name = value, value
        elif isinstance(value, dict):
            cmd = value.get("cmd", "")
            name = value.get("name", cmd)
        else:
            print(paint(f"Step \"{key}\" has an unsupported value type; skipping.", Color.YELLOW))
            continue
        if not cmd:
            print(paint(f"Step \"{key}\" has no command; skipping.", Color.YELLOW))
            continue
        steps.append((int(key), key, name, cmd))

    steps.sort(key=lambda s: s[0])  # numeric order, not string order
    return steps, settings


# --------------------------------------------------------------------------
# Step execution
# --------------------------------------------------------------------------

def run_step(order: int, key: str, name: str, cmd: str, log: Logger, timeout: float = None):
    log.step(f"[{key}] {name}")
    log.info(f"$ {cmd}")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            shell=True,           # cmd.exe on Windows, /bin/sh on Linux/Termux
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        log.error(f"[{key}] timed out after {elapsed:.1f}s")
        return False, elapsed
    except FileNotFoundError as e:
        elapsed = time.monotonic() - start
        log.error(f"[{key}] could not launch a shell: {e}")
        return False, elapsed
    except Exception as e:
        elapsed = time.monotonic() - start
        log.error(f"[{key}] unexpected failure: {e}")
        return False, elapsed

    elapsed = time.monotonic() - start

    if proc.stdout:
        log.raw(proc.stdout.rstrip("\n"), "stdout")
    if proc.stderr:
        log.raw(proc.stderr.rstrip("\n"), "stderr")

    if proc.returncode == 0:
        log.ok(f"[{key}] done in {elapsed:.2f}s")
        return True, elapsed
    else:
        log.error(f"[{key}] failed (exit code {proc.returncode}) after {elapsed:.2f}s")
        return False, elapsed


# --------------------------------------------------------------------------
# `cmdh -init` — interactively build a config.json in the current directory
# --------------------------------------------------------------------------

def _ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        answer = input(prompt + suffix).strip().lower()
        if answer == "":
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer 'y' or 'n'.")


def init_config(config_path: Path):
    if config_path.exists():
        if not _ask_yes_no(f"{config_path} already exists. Overwrite it?", default=False):
            print("Aborted — existing config left untouched.")
            return

    print(paint("cmdh init — let's build your config.json", Color.CYAN))
    print("Enter the commands to run, in order. Leave the command blank when you're done.\n")

    steps = {}
    step_num = 1
    while True:
        cmd = input(f"  [{step_num}] command: ").strip()
        if not cmd:
            break
        name = input(f"  [{step_num}] label (optional, press enter to reuse the command): ").strip()
        if name:
            steps[str(step_num)] = {"cmd": cmd, "name": name}
        else:
            steps[str(step_num)] = cmd
        step_num += 1

    if not steps:
        print(paint("No commands entered — nothing to write.", Color.YELLOW))
        return

    print()
    stop_on_error = _ask_yes_no("Stop the whole run if a step fails?", default=True)

    config = dict(steps)
    config["_settings"] = {"stop_on_error": stop_on_error}

    config_path.write_text(json.dumps(config, indent=4) + "\n", encoding="utf-8")
    print(paint(f"\nWrote {config_path.resolve()} with {len(steps)} step(s).", Color.GREEN))
    print("Run 'cmdh -build' to execute it.")


# --------------------------------------------------------------------------
# `cmdh -build` — run the steps in config.json
# --------------------------------------------------------------------------

def build(args):
    config_path = Path(args.config)
    log_path = Path(args.log)
    log = Logger(log_path, quiet=args.quiet)

    log.info(f"Platform: {sys.platform} | Python: {sys.version.split()[0]}")

    if args.import_name:
        remote_text = fetch_config_from_hub(args.import_name, log)
        try:
            json.loads(remote_text)  # validate before clobbering the local file
        except json.JSONDecodeError as e:
            log.error(f"Code Hub returned invalid JSON for '{args.import_name}': {e}")
            log.close()
            sys.exit(2)
        config_path.write_text(remote_text, encoding="utf-8")
        log.ok(f"Imported '{args.import_name}' from Code Hub -> {config_path.resolve()}")

    log.info(f"Config: {config_path.resolve()}")

    steps, settings = load_config(config_path)

    if not steps:
        log.warn("No numbered steps found in config — nothing to do.")
        log.close()
        sys.exit(0)

    # Precedence: explicit CLI flag > config "_settings" > default (stop on error)
    stop_on_error = settings.get("stop_on_error", True)
    if args.continue_on_error:
        stop_on_error = False
    if args.stop_on_error:
        stop_on_error = True

    log.info(f"{len(steps)} step(s) loaded, stop_on_error={stop_on_error}")

    if args.dry_run:
        log.info("Dry run — plan only:")
        for order, key, name, cmd in steps:
            print(f"  {key}. {name}  ->  {cmd}")
        log.close()
        sys.exit(0)

    results = []
    total_start = time.monotonic()

    for order, key, name, cmd in steps:
        success, elapsed = run_step(order, key, name, cmd, log, timeout=args.timeout)
        results.append((key, name, success, elapsed))
        if not success and stop_on_error:
            log.error("Stopping: stop_on_error is enabled and a step failed.")
            break

    total_elapsed = time.monotonic() - total_start

    # ---- Summary ----
    passed = sum(1 for r in results if r[2])
    failed = sum(1 for r in results if not r[2])
    skipped = len(steps) - len(results)

    log.info("----- Summary -----")
    for key, name, success, elapsed in results:
        status = paint("PASS", Color.GREEN) if success else paint("FAIL", Color.RED)
        log._write_raw(f"  [{key}] {name}: {'PASS' if success else 'FAIL'} ({elapsed:.2f}s)\n")
        if not args.quiet:
            print(f"  [{key}] {name}: {status} ({elapsed:.2f}s)")
    if skipped:
        log.warn(f"{skipped} step(s) skipped after a failure.")

    summary_line = f"{passed} passed, {failed} failed, {skipped} skipped, total {total_elapsed:.2f}s"
    if failed:
        log.error(summary_line)
    else:
        log.ok(summary_line)

    log.close()
    sys.exit(1 if failed else 0)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="cmdh",
        description="cmdh — a tiny command/build runner driven by a config.json.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-init", action="store_true", help="interactively create ./config.json")
    mode.add_argument("-build", "-built", dest="build", action="store_true",
                       help="run the steps in ./config.json")

    parser.add_argument("-c", "--config", default="config.json", help="path to config.json (default: ./config.json)")
    parser.add_argument("-i", "--import", dest="import_name", default=None,
                         help="fetch this filename from Code Hub (id=cmdh) and use it as the config, overwriting -c/--config")
    parser.add_argument("-l", "--log", default="cmdh.log", help="path to log file (default: ./cmdh.log)")
    parser.add_argument("--continue-on-error", action="store_true", help="keep running remaining steps after a failure")
    parser.add_argument("--stop-on-error", action="store_true", help="stop at the first failing step (overrides config)")
    parser.add_argument("--dry-run", action="store_true", help="print the execution plan without running anything")
    parser.add_argument("-q", "--quiet", action="store_true", help="suppress console output (log file still gets everything)")
    parser.add_argument("--timeout", type=float, default=None, help="per-step timeout in seconds (default: none)")

    args = parser.parse_args()

    if args.init:
        init_config(Path(args.config))
        sys.exit(0)

    build(args)


if __name__ == "__main__":
    main()
