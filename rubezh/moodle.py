"""Клиент Moodle через тот же вход, которым пользуется сам сайт.

REST API у AITU закрыт на nginx (403 на /webservice/rest/server.php), но
/lib/ajax/service.php отвечает — это эндпоинт, через который страницы Moodle
сами дёргают свои же внешние функции. С живой сессией нам доступно то же,
что видит браузер: курсы, дедлайны, оценки.

Не все функции разрешены для ajax — у каждой в db/services.php свой флаг.
Что именно открыто на этом сервере, показывает `python rubezh.py probe`.
"""
import re

BASE = "https://lms.astanait.edu.kz"


class MoodleError(RuntimeError):
    pass


class Moodle:
    def __init__(self, context):
        self.page = context.new_page()
        self.page.goto(f"{BASE}/my/", wait_until="domcontentloaded", timeout=60000)
        cfg = self.page.evaluate(
            "() => window.M && M.cfg ? {sesskey: M.cfg.sesskey, userid: M.cfg.userId} : null"
        )
        # sesskey Moodle выдаёт и гостю, так что сам по себе он ничего не значит.
        # Признак входа — userId больше нуля. Раньше проверялся только sesskey, и
        # разлогиненная сессия считалась живой: watch рапортовал «всё хорошо» и
        # никогда не присылал «пора зайти руками».
        if not cfg or not cfg.get("sesskey") or not (cfg.get("userid") or 0) > 0:
            raise MoodleError("Сессия Moodle не живая. Запусти: python rubezh.py login lms")
        self.sesskey = cfg["sesskey"]
        self.userid = cfg["userid"]

    def call(self, methodname, args=None):
        """Один вызов внешней функции Moodle. Бросает MoodleError, если нельзя."""
        response = self.page.request.post(
            f"{BASE}/lib/ajax/service.php?sesskey={self.sesskey}&info={methodname}",
            data=[{"index": 0, "methodname": methodname, "args": args or {}}],
            headers={"Content-Type": "application/json"},
            timeout=60000,
        )
        if not response.ok:
            raise MoodleError(f"HTTP {response.status}")
        try:
            payload = response.json()
        except Exception:
            raise MoodleError(f"не JSON: {response.text()[:200]}")
        if not isinstance(payload, list) or not payload:
            raise MoodleError(f"неожиданный ответ: {str(payload)[:200]}")
        item = payload[0]
        if item.get("error"):
            exc = item.get("exception") or {}
            raise MoodleError(exc.get("message") or str(item.get("error"))[:200])
        return item.get("data")

    # --- то, ради чего всё затевалось ---------------------------------------

    def courses(self):
        return self.call("core_enrol_get_users_courses", {"userid": self.userid}) or []

    def deadlines(self, limit=50):
        """Ближайшие дедлайны из блока Timeline."""
        data = self.call(
            "core_calendar_get_action_events_by_timesort",
            {"limitnum": limit, "limittononsuspendedevents": True},
        )
        return (data or {}).get("events", [])


# Что пробуем на probe: (функция, аргументы, зачем нужна).
# courseid подставляется первым реальным курсом, если он есть.
PROBES = [
    ("core_webservice_get_site_info", {}, "кто мы и что за сайт"),
    ("core_enrol_get_users_courses", {"userid": "@me"}, "список курсов"),
    ("core_calendar_get_action_events_by_timesort", {"limitnum": 5}, "дедлайны (главное)"),
    ("core_calendar_get_calendar_monthly_view", {"year": 2026, "month": 9, "courseid": 1}, "месяц календаря"),
    ("core_course_get_contents", {"courseid": "@course"}, "содержимое курса"),
    ("mod_assign_get_assignments", {"courseids": ["@course"]}, "задания с дедлайнами"),
    ("gradereport_user_get_grade_items", {"courseid": "@course", "userid": "@me"}, "оценки и веса"),
    ("gradereport_overview_get_course_grades", {"userid": "@me"}, "итоги по всем курсам"),
    ("core_message_get_messages", {"useridto": "@me", "useridfrom": 0, "type": "conversations",
                                   "read": 0, "newestfirst": True, "limitfrom": 0, "limitnum": 5},
     "сообщения от преподавателей"),
]


def probe(moodle):
    """Проверяет, какие функции реально открыты на этом сервере."""
    print(f"Сессия жива. userid={moodle.userid}\n")

    courses = []
    try:
        courses = moodle.courses()
    except MoodleError as error:
        print(f"Список курсов не отдался: {error}\n")

    if courses:
        print(f"Курсов записано: {len(courses)}")
        for course in courses:
            print(f"  [{course.get('id')}] {course.get('fullname')}")
    else:
        print("Курсов пока нет — регистрация на дисциплины 31.08–05.09.")
    print()

    course_id = courses[0]["id"] if courses else 1
    ok, blocked = [], []
    for name, args, why in PROBES:
        filled = {
            key: (moodle.userid if value == "@me"
                  else course_id if value == "@course"
                  else [course_id] if value == ["@course"]
                  else value)
            for key, value in args.items()
        }
        try:
            data = moodle.call(name, filled)
            # «Сколько данных» у разных функций считается по-разному: где-то список,
            # где-то словарь с событиями внутри. Показываем то, что осмысленно.
            if isinstance(data, dict) and isinstance(data.get("events"), list):
                size = f"{len(data['events'])} событий"
            elif isinstance(data, list):
                size = f"{len(data)} шт."
            else:
                size = "есть ответ"
            print(f"  [OK]      {name:<48} {why}  ({size})")
            ok.append(name)
        except MoodleError as error:
            print(f"  [ЗАКРЫТО] {name:<48} {str(error)[:60]}")
            blocked.append(name)

    print(f"\nДоступно {len(ok)} из {len(PROBES)}.")
    if blocked:
        print("Закрытое добираем чтением страниц через ту же сессию — данные те же.")
    return ok


# --- постоянная ссылка на календарь -----------------------------------------

def calendar_url(context):
    """Достаёт постоянный ICS-адрес календаря Moodle.

    Внутри — authtoken, привязанный к пользователю, а не к сессии: ссылка живёт
    и после того, как кука протухнет. Именно её мы потом отдаём в iCloud/Google,
    и именно поэтому её нельзя коммитить — это доступ к твоему календарю.
    """
    page = context.new_page()
    page.goto(f"{BASE}/calendar/export.php", wait_until="domcontentloaded", timeout=60000)
    page.check("input[name='events[exportevents]'][value='all']")
    page.check("input[name='period[timeperiod]'][value='recentupcoming']")
    page.click("input[name='generateurl']")
    page.wait_for_load_state("domcontentloaded", timeout=60000)

    found = re.search(r"https?://[^\s\"'<>]*export_execute\.php[^\s\"'<>]*", page.content())
    if not found:
        raise MoodleError("Moodle не отдал ссылку на календарь — форма экспорта изменилась?")
    return found.group(0).replace("&amp;", "&")
