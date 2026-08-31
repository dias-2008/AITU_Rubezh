"""AITU Rubezh — дашборд студента AITU. Точка входа.

    python rubezh.py login lms        логин в Moodle (один раз)
    python rubezh.py login outlook    логин в Outlook (один раз)
    python rubezh.py status           живы ли сессии
    python rubezh.py probe            что именно отдаёт Moodle этого универа
    python rubezh.py ics              постоянная ссылка на календарь Moodle
    python rubezh.py build [--demo]   собрать дашборд (--demo — на выдуманных данных)
    python rubezh.py open             пересобрать и открыть в браузере
    python rubezh.py shortcut         положить ярлык на рабочий стол
    python rubezh.py serve [порт]     собрать и раздать (только этот компьютер)
    python rubezh.py serve --lan      ... и открыть для телефона в этой же сети
    python rubezh.py telegram         запомнить, кому слать уведомления
    python rubezh.py telegram --chat-id <id>   указать получателя явно
    python rubezh.py watch [--dry]    проверить изменения и написать в Telegram
"""
import json
import sys
from pathlib import Path

NL = chr(10)

import session

SECRETS = Path(__file__).parent / "secrets.json"


def cmd_login(args):
    if not args:
        raise SystemExit(f"Какой сервис? {', '.join(session.SERVICES)}")
    return 0 if session.login(args[0]) else 1


def cmd_status(_args):
    for name, spec in session.SERVICES.items():
        alive = session.is_logged_in(name) if session.profile_dir(name).exists() else False
        mark = "вошёл" if alive else "нет сессии"
        print(f"  [{'✓' if alive else ' '}] {name:<9} {spec['title']:<12} {mark}")
    return 0


def cmd_probe(_args):
    import moodle
    with session.browser("lms", headless=True) as context:
        try:
            client = moodle.Moodle(context)
        except moodle.MoodleError as error:
            print(error, file=sys.stderr)
            return 1
        moodle.probe(client)
    return 0


def cmd_ics(_args):
    """Постоянная ссылка на календарь Moodle — её потом отдаём в iCloud/Google."""
    import moodle
    with session.connect("lms") as (context, _page):
        url = moodle.calendar_url(context)

    data = json.loads(SECRETS.read_text(encoding="utf-8")) if SECRETS.exists() else {}
    data["moodle_ics"] = url
    SECRETS.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # В консоль токен не печатаем: логи и скриншоты имеют свойство утекать.
    masked = url.split("authtoken=")[0] + "authtoken=..." if "authtoken=" in url else url
    print(f"Ссылка получена и сохранена в {SECRETS.name}")
    print(f"  {masked}")
    print(NL + "Это доступ к твоему календарю без пароля. secrets.json уже в .gitignore —")
    print("проверь сам перед первым git push.")
    return 0


def cmd_build(args):
    import web
    print("Собрано:", web.build(demo="--demo" in args))
    return 0


def cmd_open(_args):
    """Пересобрать дашборд свежими данными и открыть в браузере."""
    import webbrowser
    import web
    out = web.build(demo="--demo" in _args)
    webbrowser.open(out.resolve().as_uri())
    print("Открыл:", out)
    return 0


def cmd_shortcut(_args):
    """Положить ярлык на рабочий стол. Windows."""
    import subprocess
    root = Path(__file__).parent.resolve()
    target, icon = root / "AITU Rubezh.bat", root / "icon.ico"
    if not target.exists():
        raise SystemExit(f"Нет {target.name} — запусти из папки проекта.")

    # WindowStyle 7 — свернуть окно консоли: она нужна только чтобы показать
    # ошибку, если сборка упадёт, а в обычной жизни мелькать не должна.
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$l=Join-Path $d 'AITU Rubezh.lnk';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($l);"
        f"$s.TargetPath='{target}';"
        f"$s.WorkingDirectory='{root}';"
        + (f"$s.IconLocation='{icon}';" if icon.exists() else "")
        + "$s.Description='Пересобрать дашборд и открыть в браузере';"
        "$s.WindowStyle=7;$s.Save();$l"
    )
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"Не вышло создать ярлык: {done.stderr.strip()[:300]}")
    print("Ярлык на рабочем столе создан.")
    return 0


def cmd_serve(args):
    import web
    ports = [a for a in args if a.isdigit()]
    web.serve(int(ports[0]) if ports else 8000, lan="--lan" in args,
              demo="--demo" in args)
    return 0


def cmd_telegram(args):
    """Один раз запомнить, кому слать уведомления."""
    import notify
    chat_id = None
    if "--chat-id" in args:
        position = args.index("--chat-id")
        if position + 1 >= len(args):
            raise SystemExit("После --chat-id нужен номер.")
        chat_id = args[position + 1]
    notify.link(chat_id)
    return 0


def cmd_watch(args):
    import watch
    return watch.run(dry="--dry" in args)


COMMANDS = {"open": cmd_open, "shortcut": cmd_shortcut, "watch": cmd_watch, "telegram": cmd_telegram,
            "build": cmd_build, "serve": cmd_serve, "login": cmd_login, "status": cmd_status, "probe": cmd_probe, "ics": cmd_ics}


def main(argv):
    # Под pythonw.exe (скрытый запуск из планировщика) консоли нет вообще:
    # sys.stdout равен None, и первый же print роняет проверку.
    if sys.stdout is None or sys.stderr is None:
        import os
        sink = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
