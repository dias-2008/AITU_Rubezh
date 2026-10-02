# -*- mode: python ; coding: utf-8 -*-
"""Сборка PyInstaller: четыре exe в одной папке.

    rubezh.exe    дашборд, консольный — команды, мастер setup, вход
    rubezhw.exe   то же без окна консоли — планировщик и обработчик rubezh://
                  (замена pythonw.exe из исходников)
    digest.exe    tg-digest, консольный
    digestw.exe   tg-digest без окна — планировщик

Все четыре — тонкие загрузчики над общей папкой _internal/: Python, пакеты,
драйвер Playwright, шаблон дашборда. Запускать напрямую: python -m PyInstaller
installer/rubezh.spec, но обычно это делает installer/build.py.
"""
from pathlib import Path

HERE = Path(SPECPATH)
REPO = HERE.parent
RUBEZH = REPO / "rubezh"
DIGEST = REPO / "tg-digest"
ICON = str(RUBEZH / "icon.ico")

# Файлы, которые код читает с диска: шаблоны дашборда, мастер и пример конфига дайджеста.
# Кладутся под теми же именами папок, что в репозитории, — paths.ASSETS и
# llm.ASSETS смотрят именно туда.
DATAS = [
    (str(RUBEZH / "templates"), "rubezh/templates"),
    (str(DIGEST / "config.example.json"), "tg-digest"),
    (str(DIGEST / ".env.example"), "tg-digest"),
    (str(DIGEST / "setup.html"), "tg-digest"),
]

# Модули, которые импортируются внутри функций или через try/except и которых
# статический анализ может не увидеть. Перечислить лишнее не страшно.
HIDDEN = [
    "aitu", "demo", "desktop", "du", "fast", "grade", "journal", "live", "machine", "moodle",
    "notify", "outlook", "paths", "phone", "session", "syllabus", "watch", "web", "wizard", "wizard_web",
    "llm", "digest", "setup", "setup_web", "pypdf", "segno",
]

common = dict(
    pathex=[str(RUBEZH), str(DIGEST)],
    binaries=[],
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[str(HERE / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "test", "unittest", "PIL"],
    noarchive=False,
)

rubezh_a = Analysis([str(RUBEZH / "rubezh.py")], **common)
digest_a = Analysis([str(DIGEST / "digest.py")], **common)

# Общая папка: у двух анализов одни и те же зависимости, их не надо класть дважды.
MERGE((rubezh_a, "rubezh", "rubezh"), (digest_a, "digest", "digest"))

rubezh_pyz = PYZ(rubezh_a.pure)
digest_pyz = PYZ(digest_a.pure)


def exe(a, pyz, name, console):
    return EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        icon=ICON,
    )


rubezh_exe = exe(rubezh_a, rubezh_pyz, "rubezh", console=True)
rubezhw_exe = exe(rubezh_a, rubezh_pyz, "rubezhw", console=False)
digest_exe = exe(digest_a, digest_pyz, "digest", console=True)
digestw_exe = exe(digest_a, digest_pyz, "digestw", console=False)

coll = COLLECT(
    rubezh_exe, rubezhw_exe, digest_exe, digestw_exe,
    rubezh_a.binaries, rubezh_a.datas,
    digest_a.binaries, digest_a.datas,
    strip=False,
    upx=False,
    name="AITU Rubezh",
)
