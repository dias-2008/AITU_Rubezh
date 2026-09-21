"""Журнал Moodle: оценки и посещаемость со страницы, а не через API.

Через ajax журнал закрыт — `gradereport_user_get_grade_items` и всё остальное
отвечают «Web service is not available» (см. `rubezh.py probe`). Но обычная
страница отчёта студенту открыта и отдаётся по той же куке, что и всё
остальное: `/grade/report/user/index.php?id=<курс>`. Браузер для неё не нужен.

Что в ней лежит у AITU (проверено на живом курсе):

    Register Midterm   0-100   — рубежный контроль 1
    Register Endterm   0-100   — рубежный контроль 2
    Register Final     0-100   — итоговый экзамен
    Register Term      0-100   — сводная за триместр, считает сам Moodle
    Attendance         0-100   — посещаемость, уже в процентах

Register-строки и есть аттестации из формулы университета (0,3 / 0,3 / 0,4),
поэтому наружу отдаём именно их, а не отдельные задания: задания внутри рубежа
уже сложены преподавателем в эту цифру, и складывать их второй раз значило бы
удвоить максимум.
"""
import re
from html.parser import HTMLParser

import requests

import fast

REPORT = "https://lms.astanait.edu.kz/grade/report/user/index.php"

# Строка журнала -> чем она является в формуле AITU.
PERIODS = {
    "register midterm": 1,
    "register endterm": 2,
    "register final": "final",
}
ATTENDANCE = "attendance"


class _Rows(HTMLParser):
    """Все строки всех таблиц как списки текстов ячеек.

    Стороннего парсера в проекте нет и заводить его ради одной страницы не
    хочется: зависимость ставится всем, кто клонирует репозиторий.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _number(text):
    """«78,5», «100.00», «0–100» -> число. Прочерк и пустое -> None."""
    if not text:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text.replace("–", "-").replace("−", "-"))
    return float(match.group().replace(",", ".")) if match else None


def _max_of(range_text):
    """«0–100» -> 100.

    Знак у чисел не читаем намеренно: тире между границами само выглядит как
    минус, и «0–100» превращалось в максимум −100.
    """
    numbers = re.findall(r"\d+(?:[.,]\d+)?", range_text or "")
    return float(numbers[-1].replace(",", ".")) if numbers else None


# Moodle клеит к названию тип строки — для читалок с экрана. В тексте ячейки
# это выглядит как «Calculated gradeRegister Midterm», и по такому названию
# ничего не найти.
TYPE_LABELS = ("calculated grade", "attendance", "assignment", "quiz", "workshop",
               "manual item", "category total", "course total", "forum", "lesson")


def _clean(name):
    for label in TYPE_LABELS:
        if name.lower().startswith(label):
            return name[len(label):].strip() or name
    return name


def rows(courseid):
    """Строки отчёта: (название, оценка, максимум, процент)."""
    jar = fast._cookies("lms", "astanait")
    if "MoodleSession" not in jar:
        raise fast.Stale("нет куки MoodleSession")
    response = requests.get(REPORT, params={"id": courseid},
                            headers={"User-Agent": fast.UA}, cookies=jar,
                            timeout=fast.TIMEOUT)
    if response.status_code == 403:
        raise fast.Stale("WAF ответил 403")
    if not response.ok:
        raise fast.Stale(f"HTTP {response.status_code}")
    if "login/index.php" in response.url or "You are logged in" not in response.text:
        raise fast.Stale("вместо отчёта пришла страница входа")

    parser = _Rows()
    parser.feed(response.text)

    # Колонки ищем по шапке: их состав зависит от настроек курса, и надеяться
    # на «третья слева — оценка» нельзя.
    columns = {}
    for row in parser.rows:
        lowered = [c.lower() for c in row]
        if "grade item" in lowered and "grade" in lowered:
            for name in ("grade", "range", "percentage"):
                if name in lowered:
                    columns[name] = lowered.index(name)
            break
    if "grade" not in columns:
        raise fast.Stale("в отчёте нет колонки Grade — вёрстка изменилась")

    out = []
    for row in parser.rows:
        need = max(columns.values())
        if len(row) <= need or not row[0]:
            continue
        if row[0].lower() in ("grade item", ""):
            continue
        out.append({
            "name": _clean(row[0]),
            "score": _number(row[columns["grade"]]),
            "max": _max_of(row[columns["range"]]) if "range" in columns else None,
            "percent": _number(row[columns["percentage"]]) if "percentage" in columns else None,
        })
    return out


def marks(courseid):
    """Аттестации и посещаемость одного курса в том виде, в каком их ждёт grade.

    Возвращает ({items}, посещаемость в процентах). Ненайденное — не ноль, а
    None: «ещё не выставили» и «поставили ноль» это разные вещи, и рисовать
    вторым первое нельзя.
    """
    items, attendance = [], None
    titles = {1: "Рубежный контроль 1", 2: "Рубежный контроль 2",
              "final": "Итоговый экзамен"}
    seen = {}
    for row in rows(courseid):
        key = row["name"].strip().lower()
        if key in PERIODS and key not in seen:
            seen[key] = True
            items.append({
                "name": titles[PERIODS[key]],
                "period": PERIODS[key],
                "max": row["max"] or 100,
                "score": row["score"],
                "kind": "register",
            })
        elif key == ATTENDANCE and attendance is None:
            # Посещаемость Moodle держит как оценку 0-100, это уже проценты.
            attendance = row["percent"] if row["percent"] is not None else row["score"]
    return items, attendance
