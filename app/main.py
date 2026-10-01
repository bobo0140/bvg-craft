"""
main.py — пуска прозореца на BVG Craft.

Интерфейсът е HTML/CSS/JS в собствен прозорец (pywebview), а цялата
логика е на Python. При затваряне сървърът се спира чисто, за да не се
повреди светът.
"""

import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    __package__ = "app"

import webview  # noqa: E402

from app import paths  # noqa: E402
from app.api import Api  # noqa: E402


def main():
    api = Api()
    window = webview.create_window(
        "BVG Craft", os.path.join(paths.UI, "index.html"), js_api=api,
        width=1320, height=860, min_size=(1080, 700),
        background_color="#0b0d12")
    api._window = window

    def on_closing():
        try:
            api._shutdown()
        except Exception:
            pass
        return True

    window.events.closing += on_closing
    webview.start(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
