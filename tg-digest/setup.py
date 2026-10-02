"""Мастер настройки дайджеста — консольный: python setup.py --cli  (или digest.py setup --cli)

Обычный путь теперь — мастер в браузере (setup_web.py): python setup.py или
digest.py setup. Этот остался для SSH и для тех, кому терминал удобнее.

По шагам: ключи Telegram и токен бота в .env, чем суммаризировать, вход в
Telegram своим аккаунтом, выбор групп по номерам, задача в планировщике раз в
час. Каждый шаг — Enter, чтобы сделать, `s`, чтобы пропустить. Что уже сделано,
мастер видит и второй раз не спрашивает, так что его можно перезапускать.

Вход в Gemini CLI сохраняется самим Gemini CLI (папка ~/.gemini), сессия
Telegram — в profiles/<профиль>/telegram.session.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import llm

ROOT = llm.ROOT
EXAMPLE_PATH = llm.ASSETS / "config.example.json"
ENV_PATH = ROOT / ".env"
NL = chr(10)

# desktop.py (планировщик, без окна консоли) живёт в соседнем rubezh — общий на
# два инструмента, как и llm.py общий в обратную сторону.
_SHARED = ROOT.parent / "rubezh"
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import desktop
except ImportError:
    desktop = None

TASK_NAME = "TG Digest"
TASK_LABEL = "kz.aitu.rubezh.digest"


class Quit(Exception):
    """Человек нажал q — выходим тихо."""


def ask(question, default=""):
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError:
        raise Quit
    if answer.lower() in ("q", "quit"):
        raise Quit
    return answer or default


def yes(question, default="y"):
    hint = "Enter — да, s — пропустить" if default == "y" else "y — да, Enter — пропустить"
    answer = ask(f"{question} [{hint}, q — выйти]").lower()
    if default == "y":
        return answer not in ("s", "n", "no", "нет", "н")
    return answer in ("y", "yes", "д", "да")


def _title(number, total, text):
    print(NL + f"[{number}/{total}] {text}")
    print("-" * (len(text) + 8))


# --- Шаг 1: ключи -----------------------------------------------------------

def _env_lines():
    return ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []


def _save_env(key, value):
    lines = [line for line in _env_lines() if not line.strip().startswith(f"{key}=")]
    lines.append(f"{key}={value.strip()}")
    ENV_PATH.write_text(NL.join(lines) + NL, encoding="utf-8")
    os.environ[key] = value.strip()


def _rubezh_token():
    """Токен бота из соседнего rubezh/.env — второго бота заводить незачем."""
    path = _SHARED / ".env"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("BOT_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def step_keys():
    llm.load_env(required=False)
    if not (os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH")):
        print("Дайджест читает группы от твоего аккаунта, для этого Telegram выдаёт ключи приложения:")
        print("  1. Зайди на https://my.telegram.org → API development tools.")
        print("  2. Создай приложение (название любое) — получишь api_id и api_hash.")
        api_id = ask("api_id (пусто — пропустить шаг)")
        if not api_id:
            return False
        if not api_id.isdigit():
            print("api_id — это число.")
            return False
        api_hash = ask("api_hash")
        if len(api_hash) < 20:
            print("api_hash — длинная строка из букв и цифр.")
            return False
        _save_env("TG_API_ID", api_id)
        _save_env("TG_API_HASH", api_hash)
    else:
        print("Ключи Telegram уже есть в .env.")

    if not os.environ.get("BOT_TOKEN"):
        shared = _rubezh_token()
        if shared:
            print("Токен бота беру из rubezh/.env — тот же бот.")
            _save_env("BOT_TOKEN", shared)
        else:
            print(NL + "Дайджест приходит от бота — нужен свой:")
            print("  1. В Telegram открой @BotFather, отправь /newbot, придумай имя.")
            print("  2. Скопируй токен вида 12345678:AAF... и вставь сюда.")
            print("  3. Открой своего бота и нажми Start — иначе он не сможет тебе писать.")
            token = ask("Токен бота (пусто — пропустить)")
            if not token:
                return False
            _save_env("BOT_TOKEN", token)
    else:
        print("Токен бота уже есть.")
    return True


# --- Шаг 2: модель ------------------------------------------------------------

def install_gemini_cli():
    npm = shutil.which("npm")
    if not npm:
        print(NL + "Node.js/npm не найден. Поставь Node с https://nodejs.org и запусти setup заново.")
        return False
    print(NL + "Ставлю Gemini CLI (npm install -g @google/gemini-cli)..." + NL)
    if subprocess.run([npm, "install", "-g", "@google/gemini-cli"]).returncode != 0:
        print(NL + "Установка не прошла. Поставь вручную: npm install -g @google/gemini-cli")
        return False
    if not shutil.which("gemini"):
        print(NL + "Поставилось, но gemini не виден в PATH. Перезапусти терминал и запусти setup заново.")
        return False
    return True


def gemini_logged_in():
    """Проверяем по факту, а не по наличию папки ~/.gemini: её создаёт и Antigravity."""
    try:
        result = subprocess.run(
            [shutil.which("gemini"), "-p", "ping"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False
    if result.returncode == 0:
        return True
    blob = ((result.stdout or "") + (result.stderr or "")).lower()
    return "auth" not in blob  # не про вход — значит другая беда, вход трогать не будем


def login_gemini_cli():
    """Интерактивный вход. Gemini CLI сам откроет браузер и сохранит доступ."""
    print(NL + "Проверяю, выполнен ли вход...")
    if gemini_logged_in():
        print("Вход уже есть.")
        if not yes("Войти заново другим аккаунтом?", default="n"):
            return
    print(
        NL + "Сейчас откроется Gemini CLI. Выбери вход через Google (Login with Google)," + NL +
        "в браузере войди своим гугл-аккаунтом и разреши доступ." + NL +
        "Потом выйди из Gemini CLI: команда /quit или Ctrl+C." + NL +
        "Логин сохранится, повторно вводить не придётся." + NL
    )
    ask("Нажми Enter, когда будешь готов")
    # stdio наследуется: нужен живой терминал, иначе OAuth-флоу не отработает.
    subprocess.run([shutil.which("gemini")])


def load_config(config_path):
    if config_path.exists():
        return json.loads(config_path.read_text(encoding="utf-8"))
    config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    config["chats"] = []
    return config


def save_config(config_path, config):
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def step_backend(config_path):
    print("Провайдеры (что уже доступно на этой машине):" + NL)
    for name in llm.PREFERENCE:
        ok, _, note = llm.PROVIDERS[name]
        print(f"  [{'✓' if ok() else ' '}] {name:<12} {note}")

    print(
        NL + "Что выбрать:" + NL +
        "  auto        — первое доступное, с автопереходом на следующее, если что-то отвалится" + NL +
        "  claude-cli  — если есть подписка Claude Code" + NL +
        "  gemini-api  — если подписки нет: бесплатный ключ с aistudio.google.com/apikey," + NL +
        "                без карты, положи его в .env как GEMINI_API_KEY" + NL +
        "  ollama      — бесплатно, локально, без лимитов и без интернета" + NL +
        "  gemini-cli  — вход гугл-аккаунтом, НО бесплатный тир для физлиц Google закрыл" + NL +
        "                в августе 2026, скорее всего не заработает" + NL
    )
    config = load_config(config_path)
    backend = ask("Backend", config.get("backend", "auto"))
    if backend not in llm.PROVIDERS and backend != "auto":
        print(f"Нет такого backend: {backend}")
        return False

    if backend == "gemini-api" and not os.environ.get("GEMINI_API_KEY"):
        key = ask("GEMINI_API_KEY с aistudio.google.com/apikey (пусто — пропустить)")
        if key:
            _save_env("GEMINI_API_KEY", key)

    if backend == "gemini-cli":
        if not shutil.which("gemini"):
            if not yes("Gemini CLI не установлен. Поставить?"):
                print("Без Gemini CLI этот backend работать не будет.")
                return False
            if not install_gemini_cli():
                return False
        login_gemini_cli()

    config["backend"] = backend
    save_config(config_path, config)
    print(f"Записал в config.json: backend = {backend}")

    print(NL + "Проверка:")
    return llm.check(config) == 0


# --- Шаг 3: вход в Telegram и выбор групп ------------------------------------

def _parse_numbers(text, top):
    picked = set()
    for part in text.replace(",", " ").split():
        if "-" in part:
            low, _, high = part.partition("-")
            if low.isdigit() and high.isdigit():
                picked.update(range(int(low), int(high) + 1))
        elif part.isdigit():
            picked.add(int(part))
    return sorted(n for n in picked if 1 <= n <= top)


async def _pick_chats(directory, config):
    from telethon import TelegramClient
    import digest

    client = TelegramClient(str(directory / "telegram"),
                            int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            print("Сейчас Telegram спросит номер телефона, код из приложения и пароль 2FA, если он есть.")
            print("Это вход в твой аккаунт: файл сессии остаётся на этом компьютере.")
            await client.start()
        print(NL + "Твои группы и каналы. У групп с темами каждая тема — отдельной строкой:")
        print("бери темы своего курса, а не группу целиком." + NL)
        found = await digest.cmd_chats(client)
    finally:
        await client.disconnect()

    if not found:
        print("Групп не нашлось.")
        return False
    chosen = {(c["id"], c.get("topic_id")) for c in config.get("chats", [])}
    if chosen:
        print(NL + "Сейчас выбрано: " + ", ".join(
            c["title"] for c in config["chats"]))
    text = ask("Номера через запятую или диапазон (например 2, 5-7; пусто — оставить как есть)")
    if not text:
        return bool(config.get("chats"))
    numbers = _parse_numbers(text, len(found))
    if not numbers:
        print("Ни одного номера не разобрал.")
        return False
    config["chats"] = [found[n - 1] for n in numbers]
    print("Выбрано: " + ", ".join(c["title"] for c in config["chats"]))
    return True


def step_chats(directory, config_path):
    llm.load_env(required=False)
    if not (os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH")):
        print("Без ключей Telegram (шаг 1) сюда не войти.")
        return False
    config = load_config(config_path)
    ok = asyncio.run(_pick_chats(directory, config))
    if ok:
        save_config(config_path, config)
    return ok


# --- Шаг 4: планировщик ---------------------------------------------------------

def step_schedule(profile):
    if desktop is None:
        print("Нет ../rubezh/desktop.py — задачу в планировщике придётся завести руками.")
        return False
    print("Дайджест может собираться сам раз в час, без окна и без тебя.")
    if not yes("Включить?"):
        return False
    name = TASK_NAME if profile == "default" else f"{TASK_NAME} ({profile})"
    label = TASK_LABEL if profile == "default" else f"{TASK_LABEL}.{profile}"
    try:
        desktop.schedule_on(name, ["run", "--profile", profile], label, tool="digest")
    except (desktop.Unsupported, RuntimeError) as error:
        print(f"Не вышло: {error}")
        print(f"Запускать руками: python digest.py run --profile {profile}")
        return False
    print(f"Готово: «{name}» раз в час. Лог — в profiles/{profile}/digest.log.")
    return True


# --- Цепочка ----------------------------------------------------------------

def main(profile="default"):
    directory = ROOT / "profiles" / profile
    directory.mkdir(parents=True, exist_ok=True)
    config_path = directory / "config.json"
    llm.set_usage_log(directory / "usage.log")

    print(NL + f"=== tg-digest: настройка (профиль {profile}) ===")
    print("Мастер можно перезапускать — сделанное он пропустит.")
    steps = [
        ("Ключи Telegram и токен бота", step_keys),
        ("Чем суммаризировать", lambda: step_backend(config_path)),
        ("Вход в Telegram и выбор групп", lambda: step_chats(directory, config_path)),
        ("Дайджест раз в час", lambda: step_schedule(profile)),
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
            except Exception as error:
                print(f"Не вышло: {type(error).__name__}: {str(error)[:200]}")
                done[title] = False
    except Quit:
        print(NL + "Ок, выходим. Продолжить: python setup.py")
        return 1

    print(NL + "=== Итог ===")
    for title, ok in done.items():
        print(f"  [{'✓' if ok else ' '}] {title}")
    print(NL + "Проверить без модели и без отправки:  python digest.py run --fake --dry")
    print("Настоящий дайджест в консоль:          python digest.py run --dry")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if a != "--cli"]
    profile = args[0] if args else "default"
    if "--cli" in sys.argv:
        sys.exit(main(profile))
    import setup_web                       # по умолчанию — мастер в браузере
    sys.exit(setup_web.run(profile))
