"""Где лежат данные и файлы приложения — в одном месте на оба способа запуска.

Из исходников всё лежит рядом со скриптами: `rubezh/secrets.json`,
`rubezh/.sessions/`, а соседняя папка `../tg-digest` — второй инструмент.

Из установщика (PyInstaller + Inno Setup) exe-файлы лежат в корне папки
установки, а данные — в тех же подпапках `rubezh/` и `tg-digest/` рядом с ними.
Раскладка та же, что в репозитории, поэтому всё, что ищет соседа через
`ROOT.parent / "tg-digest"`, работает одинаково в обоих случаях.

Установка идёт в папку пользователя (`%LOCALAPPDATA%/Programs`), а не в
Program Files: туда можно писать без прав администратора, и сессии, ключи и
собранный дашборд остаются там же, где сама программа, — как и из исходников.

Chromium для Playwright в установленной версии лежит в `browsers/` рядом с exe:
переменная окружения выставляется здесь, ДО первого импорта playwright, поэтому
`import paths` должен стоять раньше `import session` во всех точках входа.
"""
import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP = Path(sys.executable).resolve().parent      # папка установки, с exe
    ROOT = APP / "rubezh"                             # данные rubezh
    ASSETS = Path(getattr(sys, "_MEIPASS", APP)) / "rubezh"   # шаблоны внутри сборки
    ICON = APP / "icon.ico"
    browsers = APP / "browsers"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
else:
    ROOT = Path(__file__).resolve().parent
    APP = ROOT.parent
    ASSETS = ROOT
    ICON = ROOT / "icon.ico"

ROOT.mkdir(parents=True, exist_ok=True)

DIGEST = APP / "tg-digest"          # соседний инструмент: его .env и профили

WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"


TOOLS = {"rubezh": ROOT / "rubezh.py", "digest": DIGEST / "digest.py"}


def python(windowless=False, tool="rubezh"):
    """Чем запускать инструмент без человека: планировщик, обработчик rubezh://.

    Возвращает список аргументов до имени команды. Из установщика это
    `rubezhw.exe` / `digestw.exe` — та же программа без окна консоли; из
    исходников на Windows — `pythonw.exe`, чтобы раз в час не мигало окно.
    На macOS окон у фоновых процессов и так нет.
    """
    if FROZEN:
        exe = APP / (f"{tool}w.exe" if windowless and WINDOWS else f"{tool}.exe")
        if not exe.exists():
            exe = Path(sys.executable)
        return [str(exe)]
    exe = Path(sys.executable)
    if windowless and WINDOWS:
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            exe = quiet
    return [str(exe), str(TOOLS[tool])]


def command(*args, windowless=False, tool="rubezh"):
    """Та же команда одной строкой, с кавычками — для реестра и планировщика."""
    return " ".join(f'"{part}"' if " " in part else part
                    for part in (*python(windowless, tool), *args))
