"""Провайдер-независимая суммаризация.

Смысл файла: инструмент не должен умирать вместе с чьей-то подпиской.
Кончилась подписка на Claude — переключаешь одну строчку и работаешь дальше.
У другого студента нет ничего — он логинится своим гугл-аккаунтом в Gemini CLI.
Нет интернета или не хочешь никуда отдавать текст — работает локальная Ollama.

Использование:
    import llm
    llm.summarize(instruction, messages, cfg)   # cfg — это твой config.json

    python llm.py --check    # показать, что доступно прямо сейчас, и прогнать тест
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).parent
USAGE_LOG = ROOT / "usage.log"

SYSTEM_PROMPT = (
    "Ты выжимаешь важное из студенческих чатов и писем. "
    "Отвечай только результатом, без вступлений, без markdown-разметки."
)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Порядок автовыбора. gemini-cli стоит последним намеренно: 2026-08-30 Google закрыл
# бесплатный тир Code Assist для физлиц (IneligibleTierError / UNSUPPORTED_CLIENT),
# так что установленный бинарник ещё не значит рабочий провайдер.
PREFERENCE = ["claude-cli", "gemini-api", "ollama", "gemini-cli"]


def log_usage(line):
    """Чек за каждый вызов модели — чтобы всегда было видно, куда ушла квота."""
    with USAGE_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now():%Y-%m-%d %H:%M}  {line}\n")


# На Windows claude/gemini — это .cmd, аргументы проходят через cmd.exe. Любой из этих
# символов там оператор, а не текст: «Первый Курс | AITU 2026» рвало вызов пополам.
# Инструкция — константа без них, но проверять дешевле, чем ловить это в проде.
CMD_UNSAFE = re.compile(r"[&|<>^%]")


def _run_cli(binary, args, stdin_text, label):
    result = subprocess.run(
        [binary, *args], input=stdin_text, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{label}: код {result.returncode}. {(result.stderr or '').strip()[:500]}")
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError(f"{label}: пустой ответ. {(result.stderr or '').strip()[:300]}")
    log_usage(f"{label}  ~{len(stdin_text)} симв. на входе")
    return out


# --- claude-cli: подписка Claude Code, ключ не нужен ------------------------
def _claude_ok():
    return bool(shutil.which("claude"))


def _claude(instruction, messages, cfg):
    model = cfg.get("claude_model", "sonnet")
    return _run_cli(
        shutil.which("claude"),
        ["-p", "--model", model, "--allowed-tools", "", "--strict-mcp-config",
         "--no-session-persistence", "--system-prompt", SYSTEM_PROMPT,
         CMD_UNSAFE.sub(" ", instruction)],
        messages, f"claude-cli {model}",
    )


# --- gemini-cli: вход обычным гугл-аккаунтом, 1000 запросов в день ----------
def _gemini_cli_ok():
    return bool(shutil.which("gemini"))


def _gemini_cli(instruction, messages, cfg):
    model = cfg.get("gemini_cli_model", "gemini-2.5-flash")
    try:
        return _run_cli(
            shutil.which("gemini"),
            ["-m", model, "-p", CMD_UNSAFE.sub(" ", f"{SYSTEM_PROMPT}\n\n{instruction}")],
            messages, f"gemini-cli {model}",
        )
    except RuntimeError as error:
        text = str(error).lower()
        if "ineligible" in text or "unsupported_client" in text:
            raise RuntimeError(
                "Google закрыл бесплатный тир Gemini CLI для личных аккаунтов "
                "(IneligibleTierError). Логин тут ни при чём. Бери backend gemini-api "
                "с ключом из aistudio.google.com/apikey, либо ollama, либо claude-cli."
            ) from error
        if "auth" in text:
            raise RuntimeError(
                "Gemini CLI не залогинен. Запусти: python setup.py — он проведёт через "
                "вход гугл-аккаунтом (или выполни gemini и выбери Login with Google)."
            ) from error
        raise


# --- ollama: полностью локально, ничего никуда не уходит --------------------
def _ollama_ok():
    try:
        return requests.get(f"{OLLAMA_URL}/api/tags", timeout=2).ok
    except requests.RequestException:
        return False


def _ollama(instruction, messages, cfg):
    model = cfg.get("ollama_model", "qwen2.5:7b-instruct")
    response = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model, "stream": False, "options": {"temperature": 0.2},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"{instruction}\n\n{messages}"},
            ],
        },
        timeout=900,
    )
    if not response.ok:
        raise RuntimeError(f"ollama {response.status_code}: {response.text[:300]}")
    log_usage(f"ollama {model}  ~{len(messages)} симв. (локально, никуда не ушло)")
    return response.json().get("message", {}).get("content", "").strip()


# --- gemini-api: ключ из AI Studio -----------------------------------------
def _gemini_api_ok():
    return bool(os.environ.get("GEMINI_API_KEY"))


def _gemini_api(instruction, messages, cfg):
    model = cfg.get("gemini_model", "gemini-2.5-flash")
    response = requests.post(
        GEMINI_API_URL.format(model=model),
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": f"{SYSTEM_PROMPT}\n\n{instruction}\n\n{messages}"}]}],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=300,
    )
    if not response.ok:
        raise RuntimeError(f"gemini-api {response.status_code}: {response.text[:400]}")
    data = response.json()
    usage = data.get("usageMetadata", {})
    log_usage(
        f"gemini-api {model}  вход {usage.get('promptTokenCount', '?')} / "
        f"выход {usage.get('candidatesTokenCount', '?')} токенов"
    )
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


# --- fake: ничего не вызывает, для бесплатной отладки -----------------------
def _fake_ok():
    return True


def _fake(instruction, messages, cfg):
    preview = "\n".join(f"- {line[:120]}" for line in messages.split("\n")[:5])
    return f"🟡 Тестовый прогон, модель не вызывалась\n{preview}"


PROVIDERS = {
    "claude-cli": (_claude_ok, _claude, "подписка Claude Code, без ключа и лимитов бесплатного тира"),
    "gemini-api": (_gemini_api_ok, _gemini_api, "бесплатный ключ с aistudio.google.com/apikey, без карты"),
    "ollama": (_ollama_ok, _ollama, "локально на твоём компьютере, бесплатно и без лимитов"),
    "gemini-cli": (_gemini_cli_ok, _gemini_cli, "бесплатный тир для физлиц закрыт Google 2026-08"),
    "fake": (_fake_ok, _fake, "заглушка для отладки, модель не вызывается"),
}


def available():
    """Какие провайдеры реально готовы к работе прямо сейчас."""
    return [name for name in PREFERENCE if PROVIDERS[name][0]()]


def resolve(cfg):
    """Выбранный провайдер: явный из конфига или первый доступный."""
    backend = (cfg or {}).get("backend", "auto")
    if backend != "auto":
        if backend not in PROVIDERS:
            raise SystemExit(f"Неизвестный backend {backend!r}. Есть: {', '.join(PROVIDERS)}")
        return backend
    found = available()
    if not found:
        raise SystemExit(
            "Не найдено ни одного провайдера. Запусти: python digest.py setup"
        )
    return found[0]


def summarize(instruction, messages, cfg=None):
    cfg = cfg or {}
    backend = resolve(cfg)
    if (cfg.get("backend", "auto")) != "auto":
        return PROVIDERS[backend][1](instruction, messages, cfg)

    # В режиме auto провайдер может быть установлен и всё равно отвалиться на вызове:
    # закрыли тир, кончилась квота, пропала сеть. Поэтому идём по списку дальше.
    errors = []
    for name in available():
        try:
            return PROVIDERS[name][1](instruction, messages, cfg)
        except Exception as error:
            print(f"{name} не сработал, пробую следующий: {error}", file=sys.stderr)
            errors.append(f"  {name}: {error}")
    raise RuntimeError("Ни один провайдер не сработал:\n" + "\n".join(errors))


def check(cfg=None):
    cfg = cfg or {}
    print("Провайдеры:\n")
    for name in PREFERENCE:
        ok, _, note = PROVIDERS[name]
        print(f"  [{'✓' if ok() else ' '}] {name:<12} {note}")

    found = available()
    if not found:
        print("\nНичего не доступно. Запусти: python digest.py setup")
        return 1

    backend = resolve(cfg)
    print(f"\nБудет использован: {backend}\n")

    sample = "\n".join([
        "[ссылка1] Дана: Завтра матанализа не будет, перенос на пятницу 14:00",
        "[ссылка2] Айгерим: спасибо",
        "[ссылка3] Дана: СРС по физике сдать до 5 сентября",
        "[ссылка4] Асет: +",
    ])
    try:
        out = summarize("Выпиши важное для студента, одна строка на пункт.", sample, cfg)
    except Exception as error:  # показать студенту причину, а не трейсбек
        print(f"Тестовый вызов не прошёл: {error}")
        return 1
    print("Тестовый вызов прошёл:\n")
    print(out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")  # сюда пишет автопереход между провайдерами
    config = {}
    config_path = ROOT / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    sys.exit(check(config))
