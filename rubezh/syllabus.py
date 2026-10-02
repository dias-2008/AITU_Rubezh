"""Силлабус: вынуть из файла задания, веса и сроки.

Зачем. До того как преподаватель заведёт задание в Moodle, всё уже написано в
силлабусе — состав работ, веса и недели сдачи. Дашборд может показать это
заранее, а не ждать, пока дедлайн появится в календаре.

Что читаем: PDF (pypdf), DOCX (обычный zip с XML — распаковываем сами) и
простой текст. **Сканы не читаются вообще**: у AITU часть официальных PDF — это
картинки, и текста в них нет. Про это честно говорим, а не отдаём пустой список.

Разбирает локальная модель, тем же правилом, что и почта: backend зашит в коде,
а не берётся из config.json, где однажды может оказаться `auto` и учебные файлы
уедут в облако. Дальше ответ модели проверяется руками: даты — регулярным
выражением, вес — числом от 0 до 100. Модель тут переводчик из прозы в таблицу,
а не источник правды: что не разобралось, то выбрасывается.

Два сорта записей в syllabus.json, и дашборд их различает по полю `verified`:

  "model" — то, что вытащила модель: плоский список `items` с весом и неделей.
  "hand"  — прочитано человеком по страницам PDF: код курса, преподаватели,
            баллы по аттестациям (`periods`), план по неделям (`weeks`),
            правила курса (`rules`) и `warnings` — места, где сам силлабус
            противоречит себе или устарел. Такую запись модель не перезапишет.

Второй сорт появился не от хорошей жизни: четыре из шести силлабусов осени
2026 — сканы, и модель их не прочтёт вообще, а в пятом она вернула все веса
пустыми. План курса с неверными весами хуже, чем без весов.
"""
import json
import re
import sys
import zipfile
from datetime import date
from pathlib import Path

import paths

ROOT = paths.ROOT
STORE = ROOT / "syllabus.json"

# llm.py лежит в соседнем tg-digest и намеренно общий на два инструмента.
_SHARED = paths.DIGEST
if _SHARED.is_dir() and str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
try:
    import llm
except ImportError:
    llm = None

# Локальная модель, зашита намеренно: см. докстринг модуля.
# Какая именно — решает machine.model(): уже скачанная или подходящая по железу.
LOCAL = {"backend": "ollama"}

PROMPT = (
    "Ниже текст силлабуса университетского курса. Выпиши из него список работ, "
    "которые студент должен сдать: задания, лабораторные, квизы, midterm, endterm, "
    "финальный экзамен, проекты, эссе.\n"
    "Ответь ТОЛЬКО строками формата:\n"
    "название | вес в процентах или - | неделя или дата или -\n"
    "Одна работа на строку, без нумерации, без заголовков, без пояснений. "
    "Если работ нет, ответь ровно: нет"
)

MIN_TEXT = 400          # меньше — это скан или обложка, разбирать нечего
FOCUS_LIMIT = 7000      # столько текста уходит в модель

# По каким словам ищем в силлабусе ту самую таблицу с работами и весами.
FOCUS_WORDS = (
    "assignment", "assessment", "grading", "grade", "weight", "midterm", "endterm",
    "final", "exam", "quiz", "lab", "project", "essay", "presentation", "deadline",
    "due", "week", "rubric", "задани", "оценив", "вес", "рубеж", "экзамен", "квиз",
    "неделя", "срок",
)


def focus(text):
    """Оставить куски, где вообще может быть состав работ.

    Силлабус — это девять страниц, из которых нужны полторы: политика посещения,
    цели курса и список литературы модели только мешают. Целиком отданный текст
    ещё и считается минутами: локальная модель не облако, контекст ей дорог.
    """
    blocks, keep, budget = re.split(r"\n\s*\n|\r\n\s*\r\n", text), [], FOCUS_LIMIT
    for block in blocks:
        low = block.lower()
        if not any(word in low for word in FOCUS_WORDS) and "%" not in block:
            continue
        block = block.strip()[:1200]
        if len(block) < 20:
            continue
        keep.append(block)
        budget -= len(block)
        if budget <= 0:
            break
    return "\n\n".join(keep) if keep else text[:FOCUS_LIMIT]


def _from_pdf(path):
    try:
        import pypdf
    except ImportError:
        raise SystemExit("Для PDF нужен pypdf: python -m pip install pypdf")
    reader = pypdf.PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _from_docx(path):
    """DOCX — это zip с XML. Отдельная библиотека ради одного файла не нужна."""
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def read(path):
    """Текст файла. Пусто у скана — и это надо говорить вслух."""
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Нет файла: {path}")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _from_pdf(path)
    elif suffix == ".docx":
        text = _from_docx(path)
    elif suffix in (".txt", ".md"):
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise SystemExit(f"Не знаю, как читать {suffix or 'файл без расширения'}. "
                         "Умею: pdf, docx, txt, md.")
    text = re.sub(r"[ \t]+", " ", text)
    if len(text.strip()) < MIN_TEXT:
        raise SystemExit(
            f"В файле почти нет текста ({len(text.strip())} символов). "
            "Скорее всего это скан: страницы — картинки, и вытащить из них "
            "нечего. Нужен PDF с текстом или docx."
        )
    return text


def _weight(raw):
    """Вес работы в процентах. Не число или бессмыслица — значит, веса нет."""
    match = re.search(r"\d+(?:[.,]\d+)?", raw or "")
    if not match:
        return None
    value = float(match.group().replace(",", "."))
    return value if 0 < value <= 100 else None


def _when(raw, year):
    """Срок: либо дата, либо номер недели, либо ничего.

    Возвращаем словарём, а не строкой: интерфейсу нужен факт «дата такая-то»
    или «неделя такая-то», а не фраза, которую снова разбирать.
    """
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return {}
    iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if iso:
        return {"due": iso.group(0)}
    day = re.search(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b", raw)
    if day:
        d, m = int(day.group(1)), int(day.group(2))
        y = int(day.group(3) or year)
        y = y + 2000 if y < 100 else y
        try:
            return {"due": date(y, m, d).isoformat()}
        except ValueError:
            pass
    week = re.search(r"\b(\d{1,2})\b", raw)
    if week and int(week.group(1)) <= 20:
        return {"week": int(week.group(1))}
    return {}


def _ask(text):
    """Спросить локальную модель, подняв ollama, если он спит.

    Ollama намеренно не висит в памяти круглые сутки — девять с лишним гигабайт
    ради работы раз в неделю. Он стартует по первому обращению, но не мгновенно:
    первый запрос в свежую систему упирается в «connection refused». Поэтому
    будим его явно и пробуем ещё раз, а не показываем человеку трассировку.
    """
    import machine
    cfg = dict(LOCAL, ollama_model=machine.model())
    try:
        return llm.summarize(PROMPT, text, cfg)
    except Exception as error:
        if "11434" not in str(error) and "refused" not in str(error).lower():
            raise
    import subprocess
    print("Поднимаю ollama, первый запуск занимает до минуты...")
    try:
        subprocess.run(["ollama", "list"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(
            "Не нашёл ollama. Разбор идёт локальной моделью — установи ollama "
            f"и модель {cfg['ollama_model']}, либо разбери силлабус руками."
        )
    return llm.summarize(PROMPT, text, cfg)


def parse(text, year=None):
    """Работы из текста силлабуса. Модель переводит, проверяем мы."""
    if llm is None:
        raise SystemExit(
            "Нет llm.py — он лежит в соседней папке tg-digest. Без него "
            "разбирать силлабус нечем."
        )
    year = year or date.today().year
    answer = (_ask(focus(text)) or "").strip()
    if not answer or answer.lower().strip(" .") in ("нет", "no", "ничего"):
        return []

    out, seen = [], set()
    for raw in answer.splitlines():
        line = re.sub(r"^[\s*\-–—•\d.)]+", "", raw).strip()
        if "|" not in line:
            continue                    # не наша строка: пояснение или заголовок
        parts = [p.strip() for p in line.split("|")]
        name = parts[0][:120]
        if len(name) < 3 or name.lower() in seen:
            continue
        seen.add(name.lower())
        item = {"name": name, "weight": _weight(parts[1] if len(parts) > 1 else "")}
        item.update(_when(parts[2] if len(parts) > 2 else "", year))
        out.append(item)
    return out[:40]


def load():
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(course, source, items):
    """Разобранное — по курсам, чтобы второй файл не затирал первый.

    Запись, прочитанную человеком, модель не трогает: её ответ заведомо беднее
    и, как показал Calculus 1, может прийти вовсе без весов.
    """
    data = load()
    if data.get(course, {}).get("verified") == "hand":
        raise SystemExit(
            f"Курс «{course}» уже прочитан вручную (verified: hand) — модель его "
            "не перезапишет. Удали запись из syllabus.json, если правда нужно."
        )
    data[course] = {"source": source, "at": date.today().isoformat(),
                    "verified": "model", "items": items}
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return STORE


def for_dashboard(today=None):
    """Силлабусы плюс то, без чего «неделя 5» на экране ничего не значит.

    В силлабусе сроки заданы номером недели. Даты им даёт академический
    календарь: неделя 1 начинается с первого дня теоретического обучения, и
    РК1 = 05–10.10 в самом деле пятая неделя, РК2 = 09–14.11 — десятая.
    Midterm/endterm без номера недели (Psychology, Sociology) садятся в окна
    рубежных контролей — они общеуниверситетские.
    """
    import aitu
    today = today or date.today()
    term = aitu.current_term(today)
    week1 = term["study"][0]
    week_of = lambda day: (day - week1).days // 7 + 1
    return {
        "week1": week1.isoformat(),
        "rk_weeks": {"midterm": week_of(term["rk1"][0]), "endterm": week_of(term["rk2"][0])},
        "courses": load(),
    }
