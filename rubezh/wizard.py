"""Мастер первого запуска в консоли: python rubezh.py setup --cli

Обычный `setup` открывает тот же мастер страницей в браузере (wizard_web.py);
этот остался для запуска без браузера, например по SSH.

Собирает в одну цепочку всё, что раньше было десятком команд из README:
браузер, два входа университетским аккаунтом, ярлык, кнопка «Войти»,
уведомления в Telegram, первая сборка дашборда. Каждый шаг — Enter, чтобы
сделать, `s`, чтобы пропустить. Всё, что уже сделано, мастер видит и не
предлагает второй раз, так что его можно перезапускать сколько угодно.
"""
import os
import sys
from pathlib import Path

import paths

NL = chr(10)


class Quit(Exception):
    """Человек нажал q — выходим тихо, без трейсбека."""


def _ask(prompt, default="y"):
    """Enter — да, s — пропустить, q — выйти. Возвращает True, если делать."""
    hint = "[Enter — да, s — пропустить, q — выйти]" if default == "y" else "[y — да, Enter — пропустить, q — выйти]"
    try:
        answer = input(f"{prompt} {hint} ").strip().lower()
    except EOFError:
        raise Quit
    if answer in ("q", "quit", "й"):
        raise Quit
    if not answer:
        return default == "y"
    return answer in ("y", "yes", "д", "да")


def _text(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        raise Quit


def _title(number, total, text):
    print(NL + f"[{number}/{total}] {text}")
    print("-" * (len(text) + 8))


# --- Шаги -------------------------------------------------------------------

def _browsers_dir():
    """Куда Playwright кладёт браузеры на этой системе — есть ли там Chromium."""
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return Path(custom)
    if paths.WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if paths.MACOS:
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def step_browser():
    if any(_browsers_dir().glob("chromium-*")):
        print("Chromium для входа уже есть.")
        return True
    if paths.FROZEN:
        print("Chromium не найден рядом с программой — установка повреждена, переустанови.")
        return False
    print("Для входа в Moodle и портал нужен Chromium (~150 МБ, один раз).")
    if not _ask("Скачать сейчас?"):
        return False
    import subprocess
    done = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    return done.returncode == 0


def step_login(service, title, why):
    import fast
    import session
    if fast.alive(service):
        print(f"{title}: сессия уже живая, входить не надо.")
        return True
    print(why)
    print("Откроется окно браузера — войди университетским аккаунтом, окно закроется само.")
    if not _ask("Открыть окно входа?"):
        return False
    return session.login(service)


def step_outlook():
    print("Почту (Outlook) инструмент читает локальной моделью через Ollama:")
    print("нужна установленная Ollama и модель gemma4:e4b (~10 ГБ). Без этого шаг не нужен.")
    if not _ask("Настроить вход в Outlook?", default="n"):
        return False
    import session
    return session.login("outlook")


def step_group():
    """Расписание и силлабусы группы из groups/<группа>/ — для одногруппников."""
    import fast
    if paths.FROZEN:
        print("Установщик положил данные группы, если ты отметил галочку.")
        return True
    groups = sorted(d for d in (paths.APP / "groups").glob("*") if d.is_dir())
    if not groups:
        print("Папки groups/ нет — пропускаем.")
        return False
    have = [f.name for g in groups for f in g.glob("*.json") if (paths.ROOT / f.name).exists()]
    if have:
        print("Уже лежат:", ", ".join(sorted(set(have))))
        return True
    mine = fast.secrets().get("du_group") or ""
    print("В репозитории есть данные групп:", ", ".join(g.name for g in groups))
    group = next((g for g in groups if g.name.lower() == mine.lower()), None)
    if group is None:
        name = _text("Твоя группа (пусто — пропустить): ")
        group = next((g for g in groups if g.name.lower() == name.lower()), None)
        if group is None:
            return False
    elif not _ask(f"Портал говорит, что ты из {group.name}. Положить расписание и силлабусы?"):
        return False
    import shutil
    for src in group.glob("*.json"):
        shutil.copy2(src, paths.ROOT / src.name)
        print("Положил:", src.name)
    return True


def step_shortcut():
    import desktop
    if paths.FROZEN and paths.WINDOWS:
        print("Установщик уже положил ярлык (если ты не снял галочку).")
        return True
    try:
        print("Ярлык:", desktop.shortcut())
        return True
    except desktop.Unsupported as error:
        print(error)
        return False


def step_start_menu():
    import desktop
    print("Ярлык в меню «Пуск»: дашборд найдётся поиском по «Rubezh», а правым кликом")
    print("по ярлыку его можно закрепить на начальном экране или на панели задач.")
    if not _ask("Добавить?"):
        return False
    print("Ярлык:", desktop.start_menu_shortcut())
    return True


def step_protocol():
    import desktop
    if paths.FROZEN and paths.WINDOWS:
        print("Установщик уже включил кнопку «Войти» на дашборде.")
        return True
    print("Когда сессия истечёт, на дашборде появится кнопка «Войти» — она откроет окно")
    print("входа прямо из браузера. Для этого системе надо знать про ссылки rubezh://.")
    if not _ask("Включить?"):
        return False
    try:
        desktop.register_protocol()
        return True
    except (desktop.Unsupported, RuntimeError) as error:
        print(error)
        return False


def step_telegram():
    import desktop
    import notify
    print("Раз в час инструмент может сам проверять Moodle и портал и писать в Telegram,")
    print("если появилось задание, перенесли срок или сессия истекла.")
    if not _ask("Настроить уведомления?"):
        return False

    notify.load_env()
    if not os.environ.get("BOT_TOKEN"):
        print(NL + "Нужен свой бот — это две минуты:")
        print("  1. В Telegram открой @BotFather, отправь /newbot, придумай имя.")
        print("  2. Скопируй токен вида 12345678:AAF... и вставь сюда.")
        while True:
            token = _text("Токен бота (пусто — пропустить шаг): ")
            if not token:
                return False
            if ":" in token and len(token) > 30:
                break
            print("Не похоже на токен. Он длинный, с двоеточием посередине.")
        print("Записал в", notify.save_token(token))

    if not notify.chat_id():
        print(NL + "Теперь открой своего бота в Telegram, нажми Start и отправь ему любое сообщение.")
        while True:
            if not _ask("Отправил?"):
                return False
            try:
                notify.link()
                break
            except SystemExit as error:
                print(error)

    try:
        desktop.schedule_on("AITU Rubezh", ["watch"], "kz.aitu.rubezh.watch")
    except (desktop.Unsupported, RuntimeError) as error:
        print(f"Проверку раз в час включить не вышло: {error}")
        print("Запускать руками: python rubezh.py watch")
        return False
    print("Проверка раз в час включена. Первое сообщение придёт через минуту-другую.")
    desktop.run_now("AITU Rubezh", "kz.aitu.rubezh.watch")
    return True


def step_build():
    import web
    import webbrowser
    out = web.build()
    print("Собрано:", out)
    webbrowser.open(out.resolve().as_uri())
    return True


# --- Цепочка ----------------------------------------------------------------

def run():
    print(NL + "=== AITU Rubezh: первый запуск ===")
    print("Минут пять: два входа университетским аккаунтом, дальше всё само.")
    print("Мастер можно перезапускать — сделанное он пропустит.")

    steps = [
        ("Браузер для входа", step_browser),
        ("Вход в Moodle", lambda: step_login(
            "lms", "Moodle", "Из Moodle берутся курсы, дедлайны, оценки и посещаемость.")),
        ("Вход в портал AITU", lambda: step_login(
            "du", "Портал", "С портала — имя, группа и расписание.")),
        ("Почта Outlook (по желанию)", step_outlook),
        ("Расписание и силлабусы группы", step_group),
        ("Ярлык на рабочем столе", step_shortcut),
        *([("Ярлык в меню «Пуск»", step_start_menu)] if paths.WINDOWS and not paths.FROZEN else []),
        ("Кнопка «Войти» на дашборде", step_protocol),
        ("Уведомления в Telegram раз в час", step_telegram),
        ("Собрать и открыть дашборд", step_build),
    ]
    done = {}
    try:
        for number, (title, step) in enumerate(steps, 1):
            _title(number, len(steps), title)
            try:
                done[title] = bool(step())
            except Quit:
                raise
            except SystemExit as error:
                print(f"Не вышло: {error}")
                done[title] = False
            except Exception as error:          # один упавший шаг не должен ронять остальные
                print(f"Не вышло: {type(error).__name__}: {str(error)[:200]}")
                done[title] = False
    except Quit:
        print(NL + "Ок, выходим. Продолжить: python rubezh.py setup")
        return 1

    print(NL + "=== Итог ===")
    for title, ok in done.items():
        print(f"  [{'✓' if ok else ' '}] {title}")
    print(NL + "Дальше: дашборд открывается ярлыком на рабочем столе.")
    print("Что-то поменять — python rubezh.py (без команды покажет список).")
    return 0
