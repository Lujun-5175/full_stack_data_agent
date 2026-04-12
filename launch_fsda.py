from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UI_URL = "http://127.0.0.1:8501"
REQUIRED_MODULES = ("streamlit", "pandas", "langchain_core", "langchain", "langgraph")
AUTO_INSTALL = os.environ.get("FSDA_AUTO_INSTALL", "1").strip().lower() not in {"0", "false", "no"}


def _check_modules() -> list[str]:
    return [name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None]


def _install_dependencies() -> bool:
    print("Installing missing dependencies into the current interpreter...")
    command = [sys.executable, "-m", "pip", "install", "-e", "."]
    completed = subprocess.run(command, cwd=str(ROOT))
    if completed.returncode != 0:
        return False
    return True


def _wait_for_ui(url: str, timeout_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    return False


def _open_browser_later(url: str) -> None:
    if _wait_for_ui(url):
        webbrowser.open(url)


def main() -> int:
    missing_modules = _check_modules()
    if missing_modules:
        print("FSDA launch preflight detected missing modules:", ", ".join(missing_modules))
        if AUTO_INSTALL:
            print("Auto-install is enabled. Installing now...")
            if not _install_dependencies():
                print()
                print("Dependency installation failed.")
                print(f'You can retry manually with: "{sys.executable}" -m pip install -e .')
                return 1
            missing_modules = _check_modules()
            if missing_modules:
                print()
                print("Dependencies are still missing after installation:", ", ".join(missing_modules))
                print(f'Please retry manually with: "{sys.executable}" -m pip install -e .')
                return 1
        else:
            print()
            print("Auto-install is disabled.")
            print("Fix it by installing dependencies into the same interpreter:")
            print(f'  "{sys.executable}" -m pip install -e .')
            print()
            print("Then re-run the launcher.")
            return 1

    env = os.environ.copy()
    # Let the launcher open the browser once; prevent Streamlit from opening a second window.
    env.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    env.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")

    browser_thread = threading.Thread(target=_open_browser_later, args=(UI_URL,), daemon=True)
    browser_thread.start()

    streamlit_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "full_stack_data_agent" / "ui" / "app.py"),
        "--server.headless",
        "true",
        "--global.developmentMode",
        "false",
    ]
    print("Starting Full Stack Data Agent...")
    print(f"UI: {UI_URL}")
    print(f"Python: {sys.executable}")
    print(f"Project: {ROOT}")
    print()
    print("If the UI opens with a runtime warning, install the missing dependency listed above.")
    print()
    process = subprocess.Popen(streamlit_cmd, cwd=str(ROOT), env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
