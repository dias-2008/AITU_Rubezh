"""Настоящие данные для дашборда: Moodle + портал, без выдумок.

Честно про то, чего здесь нет. Журнал оценок Moodle (`gradereport_*`) на сервере
AITU закрыт для ajax — см. `rubezh.py probe`. Значит, **веса заданий и баллы взять
неоткуда**, пока курсы не появятся и мы не разберём HTML журнала. Поэтому в живом
режиме курс приходит с `eval: null`, и дашборд честно пишет «журнал закрыт», а не
рисует нули, которые выглядят как двойки.

Что настоящее уже сейчас: список курсов, дедлайны из календаря Moodle, расписание
группы с портала и академический календарь.
"""
import json
from datetime import date, datetime
from pathlib import Path

import aitu
import fast
import session

COURSES_FN = "core_course_get_enrolled_courses_by_timeline_classification"
EVENTS_FN = "core_calendar_get_action_events_by_timesort"


_refreshed = set()


def _refresh_keys(service):
    """Поднять браузер и обновить ключи сервиса — ровно один раз за запуск.

    Иначе каждый вызов ловит Stale и заново поднимает Chromium с попыткой
    молчаливого входа. При мёртвой сессии это несколько браузеров подряд и
    минуты ожидания там, где ответ известен после первой попытки.

    Счёт попыток — по сервисам, а не один на всех: мёртвый портал не повод
    считать, что за ключом Moodle уже сходили.
    """
    if service in _refreshed:
        raise fast.Stale(f"ключи {service} уже пробовали обновить в этом запуске")
    _refreshed.add(service)
    try:
        session.refresh(service)
    except (Exception, SystemExit) as error:
        # SystemExit — это «зайди руками» из session.connect. Для сборки это
        # обычный отказ источника, а не повод уронить дашборд: он обязан
        # открываться и с протухшей сессией.
        raise fast.Stale(f"ключи {service} обновить не вышло: {str(error)[:120]}")


def _moodle(method, args):
    """Быстрый путь, а если ключи протухли — обновить их браузером и повторить."""
    try:
        return fast.moodle_call(method, args)
    except fast.Stale:
        _refresh_keys("lms")
        return fast.moodle_call(method, args)


def _du(path):
    try:
        return fast.du_get(path)
    except fast.Stale:
        _refresh_keys("du")
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
    # Группа приходит объектом {"id": 863, "title": "CS-2606"}, а не строкой.
    # Строку тоже принимаем: портал уже менял форму этого поля один раз.
    group = student.get("group")
    if isinstance(group, dict):
        group = group.get("title") or group.get("name")
    return name, (group.strip() if isinstance(group, str) and group.strip() else None)


def gather(today=None):
    """Собрать всё, что доступно. Недоступное не роняет сборку.

    Сессии протухают (у Microsoft не настроить MFA, молча переподняться нельзя),
    и дашборд обязан открываться и в этом случае — с честной строкой «нужно
    войти», а не с трассировкой стека из ярлыка на рабочем столе.
    """
    today = today or date.today()
    notes, courses, loose, schedule, needs = [], [], [], [], []

    # Группа из профиля надёжнее подслушанного запроса: профиль — это факт,
    # а перехват зависит от того, успела ли страница его отправить.
    student, group = _student_info()
    group = group or fast.secrets().get("du_group")
    if group:
        fast.remember(du_group=group)

    # «Войди руками» пишем только на отказ авторизации (Stale). Упавшая сеть или
    # лежащий сервер — не повод гнать человека логиниться: он войдёт, ничего не
    # изменится, и полоса «сессия истекла» останется висеть как обвинение.
    try:
        courses = _courses()
        loose = _attach_deadlines(courses, today)
    except fast.Stale:
        needs.append("lms")
    except Exception as error:
        notes.append(f"Moodle сейчас не отвечает: {str(error)[:120]}")

    try:
        schedule = _schedule(group)
    except fast.Stale:
        needs.append("du")
    except Exception as error:
        notes.append(f"Портал сейчас не отвечает: {str(error)[:120]}")

    # Честно объясняем пустоту, вместо того чтобы показывать пустой экран молча.
    if not needs:
        if not courses:
            notes.append("Курсов в Moodle пока нет — появятся, когда начнётся обучение.")
        elif not any(c["items"] for c in courses):
            notes.append("Дедлайнов пока не выставили.")
        if not schedule:
            notes.append("Расписание группы на портале ещё не опубликовано.")
    notes.append("Оценки и посещаемость недоступны: журнал Moodle на этом сервере "
                 "закрыт для чтения. Появятся, когда преподаватели начнут выставлять баллы.")

    # Находки из почты считает watch (раз в час и только при новых письмах),
    # здесь их только читаем: сборка дашборда не должна поднимать модель.
    mail = []
    try:
        state = json.loads((Path(__file__).parent / "state.json").read_text(encoding="utf-8"))
        mail = state.get("mail_findings") or []
    except (OSError, ValueError):
        pass

    return {
        "demo": False,
        "mail": mail,
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
        "needs_login": needs,
    }
