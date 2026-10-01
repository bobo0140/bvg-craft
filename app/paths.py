"""Къде живеят файловете — работи и като скрипт, и като .exe."""
import os
import sys


def app_dir() -> str:
    """Папката до .exe-то (или корена на проекта при разработка)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    """Откъдето се четат вградените файлове (интерфейсът)."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", app_dir())
    return app_dir()


DATA = os.path.join(app_dir(), "data")
SERVER = os.path.join(app_dir(), "server")
JDK = os.path.join(app_dir(), "jdk")
CONFIG = os.path.join(DATA, "config.json")
LOG = os.path.join(DATA, "log.txt")
UI = os.path.join(bundle_dir(), "ui")

os.makedirs(DATA, exist_ok=True)
