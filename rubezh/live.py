"""Настоящие данные для дашборда: Moodle + портал, без выдумок.

Что откуда. Курсы и дедлайны — по API Moodle (две функции, которые тут открыты
для ajax). Оценки и посещаемость — со страницы отчёта журнала, см. journal.py:
через ajax журнал закрыт, а страница читается обычным HTTP. Расписание группы,
имя и группа — с портала. Академический календарь — свой, из aitu.py.

Невыставленное везде приходит как None, а не ноль: «ещё не оценили» и «поставили
ноль» — разные вещи, и рисовать вторым первое нельзя, нули выглядят как двойки.
"""
import json
from datetime import date, datetime
from pathlib import Path

import aitu
import fast
import grade
import journal
import sections
import paths
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


def _split_teacher(fullname):
    """AITU держит в названии курса и предмет, и преподавателя:
    «Discrete Mathematics | Mukhambetzhan Manshuk». Карточке нужно отдельно."""
    parts = [p.strip() for p in (fullname or "").split("|")]
    if len(parts) >= 2 and parts[-1]:
        return " | ".join(parts[:-1]).strip(), parts[-1]
    return (fullname or "курс"), ""


def _courses():
    data = _moodle(COURSES_FN, {"offset": 0, "limit": 50,
                                "classification": "all", "sort": "fullname"}) or {}
    out = []
    for course in data.get("courses", []):
        fullname = course.get("fullname") or course.get("shortname") or "курс"
        title, teacher = _split_teacher(fullname)
        code = (course.get("shortname") or "").strip()
        if code == fullname.strip():
            code = ""             # у AITU краткое имя совпадает с полным
        entry = {
            "id": str(course.get("id")),
            "code": code,
            "fullname": fullname,     # этим именем Moodle называет курс в дедлайнах
            "title": title,
            "credits": None,          # кредитов Moodle не отдаёт, они в учебном плане портала
            "teacher": teacher,
            "items": [],
            "eval": None,
        }
        # Журнал ajax-ом закрыт, но страница отчёта открыта — оттуда и берём
        # аттестации с посещаемостью. Не отдался — курс останется без оценок,
        # но сборку не уронит: пустая карточка лучше пустого дашборда.
        try:
            entry["items"], attendance = journal.marks(course.get("id"))
        except Exception:
            attendance = None
        if attendance is not None:
            entry["attendance"] = {"percent": attendance}
        # Страница курса по неделям: тексты преподавателя и материалы, которых
        # нет в календаре — «подготовьте темы к семинару» живёт только здесь.
        try:
            entry["sections"] = sections.read(course.get("id"))
        except Exception:
            entry["sections"] = []
        out.append(entry)
    return out


def _evaluate(courses):
    """Посчитать «где стою и что нужно» по правилам университета."""
    for course in courses:
        if not course["items"]:
            continue
        result = grade.evaluate(course)
        result.pop("course", None)      # сам курс уже рядом, незачем дублировать
        course["eval"] = result


def _attach_deadlines(courses, today):
    data = _moodle(EVENTS_FN, {"limitnum": 50, "limittononsuspendedevents": True}) or {}
    by_title = {c["fullname"]: c for c in courses}
    loose = []
    for event in data.get("events", []):
        when = event.get("timesort")
        moment = datetime.fromtimestamp(when) if when else None
        due = moment.date() if moment else today
        url = str(event.get("url") or "")
        item = {
            "name": event.get("name") or "задание",
            "due": due.isoformat(),
            # Час сдачи важен не меньше дня: «до 23:59» и «до 9:00» — это разные
            # вечера. Moodle его отдаёт, раньше мы его выбрасывали.
            "at": moment.strftime("%H:%M") if moment else "",
            "period": aitu.period_of(due, today),
            "kind": event.get("modulename") or "assignment",
            "max": None,              # вес задания живёт в закрытом журнале
            "score": None,
            # Ссылка на страницу задания в Moodle — та же, что за «Go to activity»
            # в уведомлении. Берём только адреса своего LMS: href на дашборде
            # строится из этих данных, чужому хосту там делать нечего.
            "url": url if url.startswith(fast.LMS + "/") else "",
        }
        title = ((event.get("course") or {}).get("fullname") or "")
        (by_title[title]["items"] if title in by_title else loose).append(item)
    return loose


SCHEDULE_FILE = paths.ROOT / "schedule.json"


def _schedule_file():
    """Расписание, снятое руками со скриншотов портала — см. schedule.json.

    Портал так и не отдал расписание группы по API, а в приложении оно есть.
    Поэтому недельная сетка лежит в файле: day 1–6 (Пн–Сб), пустая аудитория
    означает онлайн. Правки с дашборда хранятся в браузере поверх этого файла.
    """
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in data.get("lessons", []):
        if not isinstance(row, dict) or not row.get("start"):
            continue
        out.append({
            "day": int(row.get("day") or 0),
            "start": str(row["start"]),
            "end": str(row.get("end") or ""),
            "course": str(row.get("code") or ""),
            "title": str(row.get("title") or "занятие"),
            "kind": str(row.get("kind") or ""),
            "room": str(row.get("room") or ""),
            "teacher": str(row.get("teacher") or ""),
            "online": bool(row.get("online")) or not row.get("room"),
        })
    return out


def _schedule(group):
    """Расписание группы: с портала, а пока он молчит — из schedule.json.

    Форму ответа портал ещё ни разу не показал непустой, поэтому читаем
    осторожно и не падаем на незнакомых ключах."""
    if not group:
        return _schedule_file()
    body = _du(f"/astanait-schedule-module/api/v1/schedule/groupName/{group}")
    body = body.get("body") if isinstance(body, dict) else body
    if not isinstance(body, list) or not body:
        return _schedule_file()
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
        _evaluate(courses)
    except fast.Stale:
        needs.append("lms")
    except Exception as error:
        notes.append(f"Moodle сейчас не отвечает: {str(error)[:120]}")

    try:
        schedule = _schedule(group)
    except fast.Stale:
        needs.append("du")
        schedule = _schedule_file()
    except Exception as error:
        notes.append(f"Портал сейчас не отвечает: {str(error)[:120]}")
        schedule = _schedule_file()

    # Честно объясняем пустоту, вместо того чтобы показывать пустой экран молча.
    if not needs:
        if not courses:
            # Раньше здесь стояло «появятся, когда начнётся обучение». Обучение
            # началось, а курсов всё нет: Moodle отвечает «You're not enrolled
            # in any courses». Дело не в сроке, а в том, что на курсы не записали.
            notes.append("В Moodle вас ещё не записали ни на один курс — он так и "
                         "пишет. Пока записи нет, оттуда не будет ни заданий, ни оценок.")
        elif not any(c["items"] for c in courses):
            notes.append("Дедлайнов пока не выставили.")
        if not schedule:
            notes.append("Расписания нет: портал молчит, а schedule.json пуст.")
    # Раньше здесь безусловно стояло «журнал закрыт для чтения». Для ajax он и
    # правда закрыт, но страница отчёта студенту открыта, и оценки с
    # посещаемостью берутся оттуда. Жалуемся, только если не прочли ни одного.
    if courses and not any(c.get("eval") for c in courses):
        notes.append("Журнал Moodle не отдался — оценок и посещаемости в этой "
                     "сборке нет. Обычно помогает вход заново.")

    # Находки из почты считает watch (раз в час и только при новых письмах),
    # здесь их только читаем: сборка дашборда не должна поднимать модель.
    mail = []
    try:
        state = json.loads((paths.ROOT / "state.json").read_text(encoding="utf-8"))
        mail = state.get("mail_findings") or []
    except (OSError, ValueError):
        pass

    # Силлабусы лежат в syllabus.json — прочитанные вручную или моделью, — и
    # сборка их только показывает. Модель на сборке не поднимаем.
    try:
        import syllabus
        parsed = syllabus.for_dashboard(today)
    except Exception:
        parsed = None

    return {
        "demo": False,
        "mail": mail,
        "syllabus": parsed,
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
