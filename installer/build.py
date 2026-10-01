"""Собрать установщик Windows: python installer/build.py [--no-installer]

Четыре шага, каждый можно повторить отдельно:

  1. PyInstaller по installer/rubezh.spec  ->  installer/dist/AITU Rubezh/
     четыре exe + _internal/ с Python, пакетами и драйвером Playwright
  2. Chromium для Playwright                ->  .../AITU Rubezh/browsers/
     тот же `playwright install chromium`, но в папку рядом с exe: paths.py
     выставляет PLAYWRIGHT_BROWSERS_PATH туда, и у студента ничего не качается
  3. cloudflared                            ->  .../AITU Rubezh/cloudflared.exe
     туннель для кнопки «Телефон» (phone.py): чтобы QR-код появлялся сразу,
     а не после скачивания 55 МБ при первом нажатии. Apache-2.0, лицензия рядом
  4. Inno Setup по installer/setup.iss      ->  installer/out/AITU-Rubezh-Setup-<версия>.exe

Версия берётся из файла VERSION в корне репозитория (или --version). Так же
это делает GitHub Actions на тег v* — см. .github/workflows/release.yml.

Что нужно на машине сборки: Python 3.12 с зависимостями обоих инструментов,
`pip install -r installer/requirements-build.txt`, Inno Setup 6
(winget install JRSoftware.InnoSetup). Установщик собирается только на Windows;
сам PyInstaller-шаг работает и на macOS, но .app и .dmg отсюда не выходят.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlretrieve

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DIST = HERE / "dist" / "AITU Rubezh"
OUT = HERE / "out"
CLOUDFLARED = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
CLOUDFLARED_LICENSE = "https://raw.githubusercontent.com/cloudflare/cloudflared/master/LICENSE"

ISCC_CANDIDATES = [
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
]


def run(args, **kw):
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


def version(explicit=None):
    if explicit:
        return explicit
    return (REPO / "VERSION").read_text(encoding="utf-8").strip()


def step_pyinstaller():
    shutil.rmtree(HERE / "dist", ignore_errors=True)
    shutil.rmtree(HERE / "build", ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", HERE / "rubezh.spec", "--noconfirm",
         "--distpath", HERE / "dist", "--workpath", HERE / "build"])
    # Иконка нужна как файл: на неё смотрят ярлык и схема rubezh:// в реестре.
    shutil.copy2(REPO / "rubezh" / "icon.ico", DIST / "icon.ico")
    for name in ("rubezh.exe", "rubezhw.exe", "digest.exe", "digestw.exe"):
        if not (DIST / name).exists():
            raise SystemExit(f"PyInstaller не собрал {name}")


def step_browsers():
    target = DIST / "browsers"
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(target))
    # --no-shell: без chromium_headless_shell, session.py и так берёт полный Chromium.
    run([sys.executable, "-m", "playwright", "install", "chromium", "--no-shell"], env=env)
    if not any(target.glob("chromium-*")):
        raise SystemExit("Chromium не установился в " + str(target))


def step_cloudflared():
    exe = DIST / "cloudflared.exe"
    print("+ cloudflared ->", exe, flush=True)
    urlretrieve(CLOUDFLARED, exe)
    urlretrieve(CLOUDFLARED_LICENSE, DIST / "cloudflared-LICENSE.txt")
    run([exe, "--version"])


def step_installer(ver):
    iscc = shutil.which("ISCC") or next((str(p) for p in ISCC_CANDIDATES if p.exists()), None)
    if not iscc:
        raise SystemExit("Не нашёл ISCC.exe — поставь Inno Setup 6: winget install JRSoftware.InnoSetup")
    OUT.mkdir(exist_ok=True)
    run([iscc, f"/DVersion={ver}", f"/O{OUT}", HERE / "setup.iss"])
    built = sorted(OUT.glob("*.exe"), key=lambda p: p.stat().st_mtime)
    print("\nГотово:", built[-1] if built else "(файл не найден)")


def smoke():
    """Собранный exe должен хотя бы показать список команд и собрать демо."""
    run([DIST / "rubezh.exe", "build", "--demo"], cwd=DIST)
    page = DIST / "rubezh" / "build" / "dashboard.html"
    if not page.exists():
        raise SystemExit("rubezh.exe build --demo не создал дашборд")
    shutil.rmtree(DIST / "rubezh", ignore_errors=True)     # данные в сборку не входят


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # вывод Inno и наш — в одну консоль cp1252
    parser = argparse.ArgumentParser()
    parser.add_argument("--version")
    parser.add_argument("--no-installer", action="store_true", help="только PyInstaller, Chromium и cloudflared")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="dist/ уже собран")
    args = parser.parse_args()

    ver = version(args.version)
    if not args.skip_pyinstaller:
        step_pyinstaller()
        step_browsers()
        step_cloudflared()
        smoke()
    if not args.no_installer:
        if sys.platform != "win32":
            raise SystemExit("Inno Setup есть только на Windows.")
        step_installer(ver)


if __name__ == "__main__":
    main()
