"""AITU Rubezh — дашборд студента AITU. Точка входа.

    python rubezh.py setup            мастер первого запуска в браузере: всё ниже по шагам
    python rubezh.py setup --cli      ... то же вопросами в консоли
    python rubezh.py login lms        логин в Moodle (один раз)
    python rubezh.py login outlook    логин в Outlook (один раз)
    python rubezh.py status           живы ли сессии
    python rubezh.py probe            что именно отдаёт Moodle этого универа
    python rubezh.py ics              постоянная ссылка на календарь Moodle
    python rubezh.py build [--demo]   собрать дашборд (--demo — на выдуманных данных)
    python rubezh.py open             пересобрать и открыть в браузере
    python rubezh.py shortcut         положить ярлык на рабочий стол (на Windows — и в «Пуск»)
    python rubezh.py register         включить кнопки «Войти» на дашборде
    python rubezh.py serve [порт]     собрать и раздать (только этот компьютер)
    python rubezh.py serve --lan      ... и открыть для телефона в этой же сети
    python rubezh.py phone            открыть дашборд на телефоне по QR-коду (туннель)
    python rubezh.py telegram         запомнить, кому слать уведомления
    python rubezh.py telegram --chat-id <id>   указать получателя явно
    python rubezh.py syllabus <файл>  вынуть задания и веса из силлабуса
    python rubezh.py sections         что лежит в курсах Moodle по неделям
    python rubezh.py watch [--dry]    проверить изменения и написать в Telegram
    python rubezh.py schedule         проверять раз в час самому
    python rubezh.py unregister       снять схему rubezh:// и задачу планировщика
"""
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

NL = chr(10)

import paths                      # до session: выставляет путь к Chromium
import session

SECRETS = paths.ROOT / "secrets.json"
LOCK = paths.ROOT / ".sessions" / "protocol.lock"
LOCK_STALE_SEC = 15 * 60          # заведомо дольше окна входа (10 минут)


def _log(line):
    """Одна строка в watch.log — туда же, куда пишет фоновая проверка."""
    log = paths.ROOT / "watch.log"
    try:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M')}  вход: {line}{NL}")
    except OSError:
        pass                      # лог не повод ронять вход


@contextmanager
def _single_run():
    """Один вход за раз: `True` — можно работать, `False` — уже идёт другой.

    Клик по «Войти» ничем себя не проявляет: обработчик запускается под
    pythonw, а Python с Playwright и холодным Chromium стартуют секунд
    двадцать. Человек, естественно, жмёт ещё раз — и второй процесс лезет в тот
    же профиль Chromium, где уже сидит первый. Профиль они делят плохо, и не
    выигрывает никто: окно то не открывается, то открывается втроём.
    """
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - LOCK.stat().st_mtime > LOCK_STALE_SEC:
            LOCK.unlink()         # прошлый запуск умер, не убрав за собой
    except OSError:
        pass                      # файла нет — так и должно быть
    try:
        os.close(os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


def cmd_login(args):
    if not args:
        raise SystemExit(f"Какой сервис? {', '.join(session.SERVICES)}")
    return 0 if session.login(args[0]) else 1


def cmd_status(_args):
    for name, spec in session.SERVICES.items():
        alive = session.is_logged_in(name) if session.profile_dir(name).exists() else False
        mark = "вошёл" if alive else "нет сессии"
        if spec.get("unused"):
            mark += " — не используется, данные отсюда не читаются"
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


def cmd_sections(args):
    """Показать, что читается со страниц курсов: секции, тексты, материалы.

        python rubezh.py sections            только текущая неделя каждого курса
        python rubezh.py sections --all      все секции

    Это то, чего нет в календаре Moodle: «подготовьте к семинару…» текстом в
    блоке недели. Заодно проверка парсера: если вёрстка курса изменилась, тут
    это видно сразу, а не через пропущенный семинар. Маркер перед активностью —
    отметка Moodle о выполнении: ✓ сделано, ☐ надо сделать, • отслеживания нет.
    """
    import fast
    import sections
    try:
        courses, _ = fast.moodle_snapshot()
    except fast.Stale as error:
        raise SystemExit(f"Сессия Moodle не живая ({error}): python rubezh.py login lms")
    if not courses:
        print("В Moodle пока нет курсов.")
        return 0
    for cid, title in courses.items():
        print(NL + f"== {title}")
        try:
            secs = sections.read(cid)
        except Exception as error:
            print(f"   не прочитался: {str(error)[:120]}")
            continue
        for sec in secs:
            if "--all" not in args and not sec["current"]:
                continue
            print(f"-- {sec['name']}" + ("   [текущая]" if sec["current"] else ""))
            if sec["summary"]:
                print("   " + sec["summary"][:300].replace(NL, NL + "   "))
            for item in sec["items"]:
                if item["type"] == "label":
                    print("   ¶ " + item["text"][:300].replace(NL, NL + "     "))
                else:
                    when = item["dates"].get("due") or item["dates"].get("closes") or ""
                    mark = {"done": "✓", "todo": "☐"}.get(item["done"], "•")
                    print(f"   {mark} [{item['type']}] {item['name']}" + (f"  — {when}" if when else "")
                          + ("  🔒" if item["restricted"] else ""))
                    if item["text"]:
                        print("     " + item["text"][:300].replace(NL, NL + "     "))
        if "--all" not in args and not any(s["current"] for s in secs):
            print(f"   секций: {len(secs)}, текущая не помечена — смотри --all")
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


def cmd_protocol(args):
    """Обработчик ссылок rubezh://login/<сервис> с самого дашборда.

    Дашборд — обычный файл на диске, запускать из него ничего нельзя. Поэтому
    кнопка «Войти» открывает ссылку своей схемы, Windows отдаёт её сюда, мы
    показываем окно входа и сразу пересобираем дашборд свежими данными.
    """
    url = (args[0] if args else "").strip()
    prefix = "rubezh://"
    if not url.startswith(prefix):
        raise SystemExit(f"Не наша ссылка: {url[:60]}")

    parts = [p for p in url[len(prefix):].strip("/").split("/") if p]
    if parts == ["phone"]:
        import phone
        return phone.run()
    if len(parts) != 2 or parts[0] != "login":
        raise SystemExit(f"Не понимаю, что делать: {url[:60]}")

    # Сервис берём только из известного списка. Ссылку может подсунуть кто
    # угодно — в запуск не должно попасть ничего, кроме заранее известных имён.
    target = parts[1]
    if target == "all":
        # Дашборду нужны оба источника. Логинимся подряд в одном запуске, чтобы
        # пересборка была одна на все входы, а не своя после каждого.
        import fast
        services = [s for s in ("lms", "du") if not fast.alive(s)]
    elif target in session.SERVICES:
        services = [target]
    else:
        raise SystemExit(f"Неизвестный сервис: {target[:40]}")

    with _single_run() as first:
        if not first:
            return 0            # уже открываем окно входа, второе тут лишнее
        try:
            ok = all(session.login(s) for s in services) if services else True
            if ok:
                import web
                web.build()
                # Вкладку намеренно НЕ открываем: страница, с которой пришёл
                # клик, сама перечитает файл. Иначе копится по новой вкладке.
        except BaseException as error:
            # Обработчик работает под pythonw: без файла падение не видно вообще
            # ниоткуда, а с дашборда это выглядит как «кнопка не работает».
            _log(f"{url[:60]} — {type(error).__name__}: {str(error)[:200]}")
            raise
    return 0 if ok else 1


def cmd_register(_args):
    """Включить кнопки «Войти» на дашборде: ссылки rubezh:// отдаются нам."""
    import desktop
    where = desktop.register_protocol()
    print(f"Схема rubezh:// зарегистрирована ({where}) — кнопки «Войти» на дашборде работают.")
    print("Только для твоей учётной записи, права администратора не нужны.")
    print("Убрать: python rubezh.py unregister")
    return 0


def cmd_unregister(_args):
    """Снять всё, что оставили register и schedule. Данные не трогает."""
    import desktop
    desktop.unregister_protocol()
    try:
        desktop.schedule_off(TASK_NAME, TASK_LABEL)
    except RuntimeError:
        pass                                  # задачи и не было
    print("Схема rubezh:// снята, задача планировщика удалена.")
    print(f"Сессии и ключи остались в {paths.ROOT} — удали папку сам, если нужно.")
    return 0


def cmd_shortcut(_args):
    """Положить ярлык на рабочий стол, а на Windows — ещё и в меню «Пуск»."""
    import desktop
    print("Ярлык на рабочем столе создан:", desktop.shortcut())
    if paths.WINDOWS:
        print("Ярлык в меню «Пуск» создан:", desktop.start_menu_shortcut())
    return 0


def cmd_serve(args):
    import web
    ports = [a for a in args if a.isdigit()]
    web.serve(int(ports[0]) if ports else 8000, lan="--lan" in args,
              demo="--demo" in args)
    return 0


def cmd_phone(_args):
    import phone
    return phone.run()


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


TASK_NAME = "AITU Rubezh"                 # имя задачи в планировщике Windows
TASK_LABEL = "kz.aitu.rubezh.watch"       # id агента launchd на macOS


def cmd_schedule(args):
    """Проверять изменения раз в час без участия человека.

        python rubezh.py schedule           включить
        python rubezh.py schedule --off     выключить
        python rubezh.py schedule --status  посмотреть, стоит ли задача

    Уведомления в Telegram шлёт `watch`, но кто-то должен его запускать. Пока
    задачи в планировщике нет, проверка идёт только когда её запустят руками —
    то есть почти никогда, и «новое задание» так и не приходит.

    Как именно — в desktop.py: на Windows это schtasks и запуск без окна
    консоли, на macOS — агент launchd.
    """
    import desktop
    if "--status" in args:
        lines = desktop.schedule_status(TASK_NAME, TASK_LABEL)
        if lines is None:
            print("Задачи нет — проверка раз в час не настроена.")
            print("Включить: python rubezh.py schedule")
            return 1
        for line in lines:
            print("  " + line)
        return 0

    if "--off" in args:
        try:
            desktop.schedule_off(TASK_NAME, TASK_LABEL)
        except RuntimeError as error:
            print(error)
            return 1
        print("Задача удалена — проверок больше не будет.")
        return 0

    desktop.schedule_on(TASK_NAME, ["watch"], TASK_LABEL)
    print(f"Готово: «{TASK_NAME}» проверяет изменения раз в час, скрытно.")
    print("Новый курс, новое задание, перенос срока и протухшая сессия —")
    print("всё это придёт в Telegram, если там что-то поменялось.")
    print(NL + "Проверить: python rubezh.py schedule --status")
    print("Выключить: python rubezh.py schedule --off")
    return 0


def cmd_syllabus(args):
    """Разобрать силлабус и запомнить, что по нему сдавать.

        python rubezh.py syllabus "Syllabus Calc1.pdf" --course "Calculus 1"
    """
    import syllabus
    files = [a for a in args if not a.startswith("--")]
    if not files:
        raise SystemExit("Какой файл разбирать? python rubezh.py syllabus <файл.pdf>")

    course = None
    if "--course" in args:
        position = args.index("--course")
        if position + 1 >= len(args):
            raise SystemExit("После --course нужно название курса.")
        course = args[position + 1]

    path = Path(files[0])
    course = course or path.stem
    print(f"Читаю {path.name}...")
    text = syllabus.read(path)
    print(f"Текста: {len(text)} символов. Разбираю локальной моделью, это небыстро.")
    items = syllabus.parse(text)
    if not items:
        print("Модель не нашла в этом файле ни одной работы.")
        return 1

    syllabus.save(course, path.name, items)
    print(f"Курс «{course}»: работ {len(items)}")
    for item in items:
        mark = item.get("due") or (f"неделя {item['week']}" if item.get("week") else "срок не указан")
        weight = f"{item['weight']:g}%" if item.get("weight") else "вес не указан"
        print(f"  • {item['name'][:60]:62} {weight:>14}  {mark}")
    print(NL + "Записано в syllabus.json. Появится на странице «Силлабус» после сборки.")
    import web
    web.build()
    return 0


def cmd_watch(args):
    import watch
    return watch.run(dry="--dry" in args)


def cmd_setup(args):
    if "--cli" in args:
        import wizard
        return wizard.run()
    import wizard_web
    return wizard_web.run()


COMMANDS = {"setup": cmd_setup, "open": cmd_open, "shortcut": cmd_shortcut, "syllabus": cmd_syllabus,
            "schedule": cmd_schedule, "protocol": cmd_protocol, "register": cmd_register,
            "unregister": cmd_unregister, "watch": cmd_watch, "telegram": cmd_telegram,
            "build": cmd_build, "serve": cmd_serve, "phone": cmd_phone, "login": cmd_login, "status": cmd_status,
            "probe": cmd_probe, "ics": cmd_ics, "sections": cmd_sections}


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
    if not argv and paths.FROZEN:
        argv = ["setup"]          # двойной клик по rubezh.exe — это первый запуск
    if not argv or argv[0] not in COMMANDS:
        print(__doc__)
        return 1
    code = COMMANDS[argv[0]](argv[1:])
    # Из ярлыка окно консоли свёрнуто и закрывается само; при ошибке — держим,
    # иначе её никто не увидит (то же делал `if errorlevel 1 pause` в .bat).
    if code and argv[0] in ("open", "setup") and sys.stdin and sys.stdin.isatty():
        try:
            input(NL + "Нажми Enter, чтобы закрыть.")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
