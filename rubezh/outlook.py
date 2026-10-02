"""Чтение университетской почты. Разбор — только локальной моделью.

Два правила, которые здесь важнее удобства.

**Наружу ничего не уходит.** Письма разбирает `ollama` на этом же компьютере, и
backend задан жёстко в коде, а не в конфиге: строка в config.json может однажды
оказаться `auto`, и переписка уедет в облако. Здесь это невозможно.

**Читаем только университетские письма.** Всё остальное — GitHub, рассылки,
личное — даже не попадает в модель. Фильтр по отправителю стоит до разбора, а не
после.

Ещё одно, про побочный эффект: открытие письма в Outlook **помечает его
прочитанным** в настоящем ящике. Поэтому по умолчанию берём только те строки,
что и так видны в списке, и ничего не открываем. Полные тексты — по явному
`full=True`, зная, что непрочитанные станут прочитанными.
"""
import re
import sys
from pathlib import Path

import paths
import session

# llm.py живёт в соседнем tg-digest и намеренно общий на два инструмента: там уже
# сделан выбор провайдера с фолбэком, второй такой же слой был бы копией.
_SHARED = paths.DIGEST
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import llm
except ImportError:                     # соседней папки нет — это не повод падать целиком
    llm = None

MAIL = "https://outlook.office.com/mail/"
BODY_SELECTOR = "div[role='document']"
ROWS = "div[role='option']"

# Чьи письма вообще смотрим. Всё, что не отсюда, в модель не попадает.
UNIVERSITY = ("astana it university", "astanait", "aitu")

# Только локальная модель — намеренно: см. первое правило в докстринге модуля.
# Какая именно, решает machine.model(): уже скачанная или подходящая по железу.
LOCAL = {"backend": "ollama"}

PROMPT = (
    "Ниже письма из университета. Выпиши только то, что требует действия от студента "
    "или меняет дату: дедлайн, перенос, экзамен, обязательная регистрация, документы. "
    "Одна строка на пункт, с датой, если она есть. "
    "Игнорируй приветствия, рекламу и общие слова. "
    "Если ничего такого нет, ответь ровно: нет"
)


def _is_university(text):
    low = text.lower()
    return any(marker in low for marker in UNIVERSITY)


def fetch(limit=12, full=False):
    """Университетские письма из списка Outlook.

    full=True открывает каждое, чтобы взять текст целиком, и тем самым
    **помечает письма прочитанными**. По умолчанию не открываем ничего.
    """
    out = []
    with session.browser("outlook", headless=True, lean=True) as ctx:
        page = ctx.new_page()
        page.goto(MAIL, wait_until="domcontentloaded", timeout=90000)
        for _ in range(20):
            if session._outlook_ready(page):
                break
            page.wait_for_timeout(3000)
        else:
            raise RuntimeError("Outlook не открылся — нужно войти: python rubezh.py login outlook")

        rows = page.locator(ROWS)
        for index in range(min(rows.count(), limit)):
            try:
                title = " ".join((rows.nth(index).inner_text() or "").split())
            except Exception:
                continue
            if not title or not _is_university(title):
                continue

            body = ""
            if full:
                try:
                    rows.nth(index).click()
                    page.wait_for_timeout(4000)
                    if page.locator(BODY_SELECTOR).count():
                        body = " ".join(page.locator(BODY_SELECTOR).first.inner_text().split())
                except Exception:
                    body = ""
            out.append({"title": title[:300], "body": body[:4000]})
    return out


def digest(messages):
    """Выжимка локальной моделью. Пусто — значит ничего требующего действий."""
    if not messages:
        return []
    if llm is None:
        raise RuntimeError(
            "Нет llm.py — он лежит в соседней папке tg-digest. "
            "Без него разбирать письма нечем."
        )
    blob = "\n\n".join(
        f"[{m['title']}]" + (f"\n{m['body']}" if m["body"] else "") for m in messages
    )
    import machine
    cfg = dict(LOCAL, ollama_model=machine.model())
    answer = (llm.summarize(PROMPT, blob, cfg) or "").strip()
    if not answer or answer.lower().strip(" .") in ("нет", "no", "ничего"):
        return []
    lines = []
    for raw in answer.split("\n"):
        line = re.sub(r"^[\s*\-–—•\d.)]+", "", raw).strip()
        if len(line) > 3:
            lines.append(line[:300])
    return lines[:10]
