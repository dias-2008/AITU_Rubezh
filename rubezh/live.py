"""Настоящие данные для дашборда: Moodle + портал, без выдумок.

Честно про то, чего здесь нет. Журнал оценок Moodle (`gradereport_*`) на сервере
AITU закрыт для ajax — см. `rubezh.py probe`. Значит, **веса заданий и баллы взять
неоткуда**, пока курсы не появятся и мы не разберём HTML журнала. Поэтому в живом
режиме курс приходит с `eval: null`, и дашборд честно пишет «журнал закрыт», а не
рисует нули, которые выглядят как двойки.

Что настоящее уже сейчас: список курсов, дедлайны из календаря Moodle, расписание
группы с портала и академический календарь.
"""
from datetime import date, datetime

import aitu
import fast
import watch

COURSES_FN = "core_course_get_enrolled_courses_by_timeline_classification"
EVENTS_FN = "core_calendar_get_action_events_by_timesort"


_refreshed = False


def _refresh_keys():
    """Поднять браузер и обновить ключи — но ровно один раз за запуск.

    Иначе каждый из четырёх вызовов ловит Stale и заново поднимает Chromium с
    попыткой молчаливого входа. При мёртвой сессии это пять браузеров подряд и
    минуты ожидания там, где ответ известен после первой попытки.
    """
    global _refreshed
    if _refreshed:
        raise fast.Stale("ключи уже пробовали обновить в этом запуске")
    _refreshed = True
    watch.snapshot()              # положит свежие ключи в secrets.json


def _moodle(method, args):
    """Быстрый путь, а если ключи протухли — обновить их браузером и повторить."""
    try:
        return fast.moodle_call(method, args)
    except fast.Stale:
        _refresh_keys()
        return fast.moodle_call(method, args)


def _du(path):
    try:
        return fast.du_get(path)
    except fast.Stale:
        _refresh_keys()
        return fast.du_get(path)


def _courses():
    data = _moodle(COURSES_FN, {"offset": 0, "limit": 50,
                                "classification": "all", "sort": "fullname"}) or {}
    out = []
    for course in data.get("courses", []):
        out.append({
            "id": str(course.get("id")),
            "code": course.get("shortname") or "",
            "title": course.get("fullname") or course.get("shortname") or "курс",
            "credits": None,          # кредитов Moodle не отдаёт, они в учебном плане портала
            "teacher": "",
            "items": [],
            "eval": None,             # журнал закрыт, считать нечего
        })
    return out


def _attach_deadlines(courses, today):
    data = _moodle(EVENTS_FN, {"limitnum": 50, "limittononsuspendedevents": True}) or {}
    by_title = {c["title"]: c for c in courses}
    loose = []
    for event in data.get("events", []):
        when = event.get("timesort")
        due = datetime.fromtimestamp(when).date() if when else today
        item = {
            "name": event.get("name") or "задание",
            "due": due.isoformat(),
            "period": aitu.period_of(due, today),
            "kind": event.get("modulename") or "assignment",
            "max": None,              # вес задания живёт в закрытом журнале
            "score": None,
        }
        title = ((event.get("course") or {}).get("fullname") or "")
        (by_title[title]["items"] if title in by_title else loose).append(item)
    return loose


def _schedule(group):
    """Расписание группы. Форму ответа портал ещё ни разу не показал непустой,
    поэтому читаем осторожно и не падаем на незнакомых ключах."""
    if not group:
        return []
    body = _du(f"/astanait-schedule-module/api/v1/schedule/groupName/{group}")
    body = body.get("body") if isinstance(body, dict) else body
    if not isinstance(body, list):
        return []
    out = []
    for row in body:
        if not isinstance(row, dict):
            continue
        out.append({
            "start": str(row.get("startTime") or row.get("start") or ""),
            "end": str(row.get("endTime") or row.get("end") or ""),
            "course": str(row.get("subjectCode") or row.get("code") or ""),
            "title": str(row.get("subjectName") or row.get("subject")
                         or row.get("title") or "занятие"),
            "kind": str(row.get("lessonType") or row.get("type") or ""),
            "room": str(row.get("room") or row.get("audience") or ""),
        })
    return out


def _student_info():
    """Имя и группа из профиля портала.

    Лежат в `student`, а не в `studentPersonalInfoDtoResponse` — там адреса,
    телефон и банковские реквизиты, ничего из этого нам не нужно и брать не надо.
    """
    try:
        profile = _du("/astanait-student-module/api/v1/student/profile/principal") or {}
    except Exception:
        return "студент", None
    student = profile.get("student") or {}
    name = "студент"
    for key in ("nameKz", "nameRu", "nameEn", "name"):
        value = student.get(key)
        if isinstance(value, str) and value.strip():
            name = value.strip().split()[0]
            break
    group = student.get("group")
    return name, (group.strip() if isinstance(group, str) and group.strip() else None)


def gather(today=None):
    """Собрать всё, что доступно. Недоступное не роняет сборку.

    Сессии протухают (у Microsoft не настроить MFA, молча переподняться нельзя),
    и дашборд обязан открываться и в этом случае — с честной строкой «нужно
    войти», а не с трассировкой стека из ярлыка на рабочем столе.
    """
    today = today or date.today()
    notes, courses, loose, schedule = [], [], [], []

    # Группа из профиля надёжнее подслушанного запроса: профиль — это факт,
    # а перехват зависит от того, успела ли страница его отправить.
    student, group = _student_info()
    group = group or fast.secrets().get("du_group")
    if group:
        fast.remember(du_group=group)

    try:
        courses = _courses()
        loose = _attach_deadlines(courses, today)
    except Exception:
        notes.append("Moodle недоступен — нужно войти: python rubezh.py login lms")

    try:
        schedule = _schedule(group)
    except Exception:
        notes.append("Портал недоступен — нужно войти: python rubezh.py login du")

    # Честно объясняем пустоту, вместо того чтобы показывать пустой экран молча.
    if not notes:
        if not courses:
            notes.append("Курсов в Moodle пока нет — появятся, когда начнётся обучение.")
        elif not any(c["items"] for c in courses):
            notes.append("Дедлайнов пока не выставили.")
        if not schedule:
            notes.append("Расписание группы на портале ещё не опубликовано.")
    notes.append("Оценки и посещаемость недоступны: журнал Moodle на этом сервере "
                 "закрыт для чтения. Появятся, когда преподаватели начнут выставлять баллы.")

    return {
        "demo": False,
        "student": student,
        "group": group or "",
        "term": aitu.current_term(today)["name"],
        "today": today.isoformat(),
        "updated": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "courses": courses,
        "loose_deadlines": loose,
        "key_dates": aitu.key_dates(today),
        "schedule": schedule,
        "notes": notes,
    }
