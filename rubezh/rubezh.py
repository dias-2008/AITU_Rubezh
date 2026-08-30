"""AITU Rubezh — дашборд студента AITU. Точка входа.

    python rubezh.py login lms        логин в Moodle (один раз)
    python rubezh.py login outlook    логин в Outlook (один раз)
    python rubezh.py status           живы ли сессии
    python rubezh.py probe            что именно отдаёт Moodle этого универа
    python rubezh.py ics              постоянная ссылка на календарь Moodle
    python rubezh.py build            собрать дашборд в build/dashboard.html
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


def cmd_build(_args):
    import web
    print("Собрано:", web.build())
    return 0


def cmd_serve(args):
    import web
    ports = [a for a in args if a.isdigit()]
    web.serve(int(ports[0]) if ports else 8000, lan="--lan" in args)
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


COMMANDS = {"watch": cmd_watch, "telegram": cmd_telegram,
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
