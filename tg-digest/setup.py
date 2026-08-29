"""Мастер настройки модели. Запусти один раз: python setup.py

Спросит, чем суммаризировать, при необходимости поставит Gemini CLI,
проведёт через вход гугл-аккаунтом и проверит, что всё работает.
Вход сохраняется самим Gemini CLI (папка ~/.gemini), второй раз логиниться не нужно.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import llm

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"


def ask(question, default=""):
    suffix = f" [{default}]" if default else ""
    answer = input(f"{question}{suffix}: ").strip()
    return answer or default


def yes(question):
    return ask(f"{question} (y/n)", "y").lower().startswith(("y", "д"))


def install_gemini_cli():
    npm = shutil.which("npm")
    if not npm:
        print("\nNode.js/npm не найден. Поставь Node с https://nodejs.org и запусти setup заново.")
        return False
    print("\nСтавлю Gemini CLI (npm install -g @google/gemini-cli)...\n")
    if subprocess.run([npm, "install", "-g", "@google/gemini-cli"]).returncode != 0:
        print("\nУстановка не прошла. Поставь вручную: npm install -g @google/gemini-cli")
        return False
    if not shutil.which("gemini"):
        print("\nПоставилось, но gemini не виден в PATH. Перезапусти терминал и запусти setup заново.")
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
    print("\nПроверяю, выполнен ли вход...")
    if gemini_logged_in():
        print("Вход уже есть.")
        if not yes("Войти заново другим аккаунтом?"):
            return
    print(
        "\nСейчас откроется Gemini CLI. Выбери вход через Google (Login with Google),\n"
        "в браузере войди своим гугл-аккаунтом и разреши доступ.\n"
        "Потом выйди из Gemini CLI: команда /quit или Ctrl+C.\n"
        "Логин сохранится, повторно вводить не придётся.\n"
    )
    input("Нажми Enter, когда будешь готов...")
    # stdio наследуется: нужен живой терминал, иначе OAuth-флоу не отработает.
    subprocess.run([shutil.which("gemini")])


def save_backend(backend):
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        config = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        print(f"\nСоздал {CONFIG_PATH.name} из примера — не забудь вписать свои чаты.")
    config["backend"] = backend
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Записал в config.json: backend = {backend}")
    return config


def main():
    print("\n=== Настройка модели для дайджеста ===\n")
    print("Провайдеры (что уже доступно на этой машине):\n")
    for name in llm.PREFERENCE:
        ok, _, note = llm.PROVIDERS[name]
        print(f"  [{'✓' if ok() else ' '}] {name:<12} {note}")

    print(
        "\nЧто выбрать:\n"
        "  auto        — первое доступное, с автопереходом на следующее, если что-то отвалится\n"
        "  claude-cli  — если есть подписка Claude Code\n"
        "  gemini-api  — если подписки нет: бесплатный ключ с aistudio.google.com/apikey,\n"
        "                без карты, положи его в .env как GEMINI_API_KEY\n"
        "  ollama      — бесплатно, локально, без лимитов и без интернета\n"
        "  gemini-cli  — вход гугл-аккаунтом, НО бесплатный тир для физлиц Google закрыл\n"
        "                в августе 2026, скорее всего не заработает\n"
    )
    backend = ask("Backend", "auto")
    if backend not in llm.PROVIDERS and backend != "auto":
        sys.exit(f"Нет такого backend: {backend}")

    if backend == "gemini-cli":
        if not shutil.which("gemini"):
            if not yes("Gemini CLI не установлен. Поставить?"):
                sys.exit("Без Gemini CLI этот backend работать не будет.")
            if not install_gemini_cli():
                sys.exit(1)
        login_gemini_cli()

    config = save_backend(backend)

    print("\n=== Проверка ===\n")
    return llm.check(config)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
