"""Всё, что зависит от операционной системы: ярлык, схема rubezh://, планировщик.

Ядро инструмента (Playwright, HTTP, дашборд) одинаково везде. Различается только
«обвязка»: как положить ярлык на рабочий стол, как заставить систему отдавать
нам ссылки rubezh:// и как запускать проверку раз в час без человека. Это и
собрано здесь, чтобы rubezh.py и tg-digest не знали про реестр и launchd.

    Windows   ярлык .lnk через PowerShell, схема в HKCU\\Software\\Classes,
              задача в schtasks (pythonw / rubezhw.exe — без окна консоли)
    macOS     файл .command на рабочем столе, .app на AppleScript для схемы
              (URL приходит Apple Event'ом, обычному скрипту его не отдают),
              launchd-агент в ~/Library/LaunchAgents

macOS-ветка написана по документации и не проверена на живой машине —
см. README, раздел «macOS». Всё остальное (Linux) честно отвечает
«не поддерживается» и подсказывает, что сделать руками.
"""
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

import paths

NL = chr(10)


class Unsupported(RuntimeError):
    """На этой системе такого нет — в тексте сказано, как обойтись."""


def _need():
    if sys.platform not in ("win32", "darwin"):
        raise Unsupported(
            f"На {sys.platform} это не поддерживается. Поддерживаются Windows и macOS."
        )


def _run(args, encoding="utf-8"):
    done = subprocess.run(args, capture_output=True)
    text = (done.stdout + done.stderr).decode(encoding, "replace").strip()
    return done.returncode, text


def _quote(part):
    return f'"{part}"' if " " in part else part


# --- Ярлык на рабочем столе ------------------------------------------------

def shortcut(title="AITU Rubezh", args=("open",), description="Пересобрать дашборд и открыть"):
    """Положить ярлык на рабочий стол. Возвращает путь к нему."""
    _need()
    if paths.WINDOWS:
        return _shortcut_windows(title, args, description)
    return _shortcut_macos(title, args)


def start_menu_shortcut(title="AITU Rubezh", args=("open",), description="Пересобрать дашборд и открыть"):
    """Тот же ярлык в меню «Пуск» (только Windows). Закрепить плиткой программно
    Windows не даёт — ярлык появится во «Все приложения» и в поиске, а закрепить
    можно правым кликом. Возвращает путь к ярлыку."""
    if not paths.WINDOWS:
        raise Unsupported("Меню «Пуск» есть только на Windows.")
    return _shortcut_windows(title, args, description, folder="Programs")


def _shortcut_windows(title, args, description, folder="Desktop"):
    # WindowStyle 7 — свёрнутое окно консоли: оно нужно только чтобы показать
    # ошибку, если сборка упадёт, а в обычной жизни мелькать не должно.
    target, *rest = paths.python()
    icon = paths.ICON
    script = (
        f"$d=[Environment]::GetFolderPath('{folder}');"
        f"$l=Join-Path $d '{title}.lnk';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($l);"
        f"$s.TargetPath='{target}';"
        f"$s.Arguments='{' '.join(_quote(a) for a in (*rest, *args))}';"
        f"$s.WorkingDirectory='{paths.ROOT}';"
        + (f"$s.IconLocation='{icon}';" if icon.exists() else "")
        + f"$s.Description='{description}';"
        "$s.WindowStyle=7;$s.Save();$l"
    )
    code, text = _run(["powershell", "-NoProfile", "-Command", script])
    if code != 0:
        raise RuntimeError(f"Не вышло создать ярлык: {text[:300]}")
    return Path(text.splitlines()[-1].strip())


def _shortcut_macos(title, args):
    # .command открывается двойным кликом в Terminal. Файл создан локально, без
    # атрибута карантина, поэтому Gatekeeper его не останавливает.
    desktop = Path.home() / "Desktop"
    desktop.mkdir(exist_ok=True)
    path = desktop / f"{title}.command"
    cmd = " ".join(shlex.quote(p) for p in (*paths.python(), *args))
    path.write_text(f"#!/bin/sh{NL}cd {shlex.quote(str(paths.ROOT))} && {cmd}{NL}",
                    encoding="utf-8")
    path.chmod(0o755)
    return path


# --- Схема rubezh:// --------------------------------------------------------

SCHEME = "rubezh"
BUNDLE_ID = "kz.aitu.rubezh"


def register_protocol():
    """Сделать так, чтобы ссылки rubezh:// открывали наш обработчик."""
    _need()
    if paths.WINDOWS:
        return _register_windows()
    return _register_macos()


def unregister_protocol():
    _need()
    if paths.WINDOWS:
        import winreg
        try:
            _delete_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}")
        except OSError:
            pass
        return
    app = _macos_app_path()
    if app.exists():
        import shutil
        shutil.rmtree(app, ignore_errors=True)


def _register_windows():
    import winreg
    command = paths.command("protocol", windowless=True) + ' "%1"'
    base = rf"Software\Classes\{SCHEME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:AITU Rubezh")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
    if paths.ICON.exists():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\DefaultIcon") as key:
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, str(paths.ICON))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, command)
    return rf"HKCU\{base}"


def _delete_tree(root, path):
    import winreg
    with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
        while True:
            try:
                child = winreg.EnumKey(key, 0)
            except OSError:
                break
            _delete_tree(root, path + "\\" + child)
    winreg.DeleteKey(root, path)


def _macos_app_path():
    return Path.home() / "Applications" / "AITU Rubezh.app"


def _register_macos():
    """Минимальное .app на AppleScript: только оно умеет принимать URL.

    Схему на macOS регистрирует Launch Services по Info.plist приложения, а сам
    URL приходит не аргументом, а событием `open location`. Поэтому обработчик —
    AppleScript-приложение, собранное osacompile, которое передаёт ссылку нашему
    скрипту.
    """
    app = _macos_app_path()
    app.parent.mkdir(parents=True, exist_ok=True)
    cmd = " ".join(shlex.quote(p) for p in (*paths.python(), "protocol"))
    script = (
        f'on open location this_URL{NL}'
        f'  do shell script "cd {shlex.quote(str(paths.ROOT))} && {cmd} " '
        f'& quoted form of this_URL & " >/dev/null 2>&1 &"{NL}'
        f'end open location{NL}'
    )
    source = paths.ROOT / ".sessions" / "protocol.applescript"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(script, encoding="utf-8")
    code, text = _run(["osacompile", "-o", str(app), str(source)])
    if code != 0:
        raise RuntimeError(f"osacompile не собрал приложение: {text[:300]}")

    plist = app / "Contents" / "Info.plist"
    with plist.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleIdentifier"] = BUNDLE_ID
    info["CFBundleURLTypes"] = [{"CFBundleURLName": "AITU Rubezh",
                                 "CFBundleURLSchemes": [SCHEME]}]
    with plist.open("wb") as handle:
        plistlib.dump(info, handle)

    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                  "LaunchServices.framework/Support/lsregister")
    _run([lsregister, "-f", str(app)])
    return app


# --- Окно входа поверх остальных ------------------------------------------

def raise_window(exe_marker):
    """Вытащить на передний план окно Chromium, запущенного нами.

    Окно входа открывает процесс без фокуса — мастер в свёрнутой консоли или
    обработчик rubezh:// под pythonw. Windows таким процессам не даёт забирать
    передний план: новое окно не появляется, а только мигает на панели задач,
    и человек ждёт перед пустым экраном. Особенно без своего Chrome: тогда
    Chromium открывается впервые и его значок никто не узнаёт.

    Окно ищем по пути exe: в нём есть `exe_marker` (папка браузеров Playwright).
    Нажатие Alt перед SetForegroundWindow — известный способ снять запрет:
    система считает, что пользователь только что работал с клавиатурой.
    На macOS и Linux такого запрета нет — ничего не делаем.
    """
    if not paths.WINDOWS:
        return False
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    marker = exe_marker.lower()
    found = []

    def exe_of(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            kernel32.CloseHandle(handle)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if (cls.value == "Chrome_WidgetWin_1" and user32.IsWindowVisible(hwnd)
                and user32.GetWindowTextLengthW(hwnd) and marker in exe_of(hwnd).lower()):
            found.append(hwnd)
        return True

    user32.EnumWindows(each, 0)
    for hwnd in found:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)                              # SW_RESTORE
        user32.keybd_event(0x12, 0, 0, 0)                           # Alt вниз
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)                           # Alt вверх
        # Даже если фокус не дали — хотя бы поверх остальных окон.
        flags = 0x0001 | 0x0002 | 0x0040                            # NOSIZE | NOMOVE | SHOWWINDOW
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)            # HWND_TOPMOST
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, flags)            # HWND_NOTOPMOST
    return bool(found)


# --- Раз в час без человека -------------------------------------------------

def schedule_on(name, args, label, tool="rubezh"):
    """Запускать `args` раз в час. `name` — имя задачи Windows, `label` — id для launchd."""
    _need()
    if paths.WINDOWS:
        command = paths.command(*args, windowless=True, tool=tool)
        code, text = _run(["schtasks", "/create", "/tn", name, "/tr", command,
                           "/sc", "hourly", "/f"], encoding="cp866")
        if code != 0:
            raise RuntimeError(f"Планировщик отказал: {text[:300]}")
        return name
    return _launchd_on(label, [*paths.python(tool=tool), *args], paths.TOOLS[tool].parent)


def schedule_off(name, label):
    _need()
    if paths.WINDOWS:
        code, text = _run(["schtasks", "/delete", "/tn", name, "/f"], encoding="cp866")
        if code != 0:
            raise RuntimeError(f"Не вышло удалить: {text[:200]}")
        return
    plist = _launchd_plist(label)
    _run(["launchctl", "unload", str(plist)])
    plist.unlink(missing_ok=True)


def schedule_status(name, label):
    """Список строк о задаче или None, если её нет."""
    _need()
    if paths.WINDOWS:
        code, text = _run(["schtasks", "/query", "/tn", name, "/v", "/fo", "list"],
                          encoding="cp866")
        if code != 0:
            return None
        keep = ("Имя задачи", "TaskName", "Состояние", "Status", "Время прошлого",
                "Last Run", "Результат", "Last Result", "Время следующего", "Next Run")
        return [" ".join(line.split()) for line in text.splitlines()
                if any(line.strip().startswith(k) for k in keep)]
    plist = _launchd_plist(label)
    if not plist.exists():
        return None
    code, text = _run(["launchctl", "list", label])
    if code != 0:
        return [f"агент: {plist}", f"не загружен: launchctl load {plist}"]
    return [f"агент: {plist}"] + [line.strip() for line in text.splitlines()
                                  if "PID" in line or "LastExitStatus" in line]


def run_now(name, label):
    _need()
    if paths.WINDOWS:
        return _run(["schtasks", "/Run", "/TN", name], encoding="cp866")
    return _run(["launchctl", "start", label])


def _launchd_plist(label):
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _launchd_on(label, argv, workdir):
    plist = _launchd_plist(label)
    plist.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "Label": label,
        "ProgramArguments": argv,
        "WorkingDirectory": str(workdir),
        "StartInterval": 3600,
        "RunAtLoad": True,
        "StandardOutPath": str(workdir / "launchd.log"),
        "StandardErrorPath": str(workdir / "launchd.log"),
        # launchd не наследует окружение оболочки: без PATH не найдётся ollama.
        "EnvironmentVariables": {"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
    }
    _run(["launchctl", "unload", str(plist)])       # перезапись без дубля
    with plist.open("wb") as handle:
        plistlib.dump(data, handle)
    code, text = _run(["launchctl", "load", str(plist)])
    if code != 0:
        raise RuntimeError(f"launchctl не загрузил агент: {text[:300]}")
    return plist
