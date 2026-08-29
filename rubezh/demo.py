"""Демонстрационные данные: осенний триместр CS-2606, конец октября.

Зачем: до 07.09 в Moodle и на портале пусто, а дашборд и движок оценок надо
довести и проверить сейчас. Момент выбран не случайно — это середина триместра,
РК1 уже закрыт, РК2 идёт. Именно тогда инструмент полезнее всего: ещё можно
что-то изменить.

Ключевые даты и структура оценивания настоящие (академический календарь
2026-2027 и силлабус). Выдуманы только баллы.
"""
GROUP = "CS-2606"
STUDENT = "Диас"
TODAY = "2026-10-26"
TERM = "Осенний триместр"

# Академический календарь 2026-2027, 1 курс — реальные даты.
KEY_DATES = [
    {"date": "2026-10-05", "title": "Рубежный контроль 1", "kind": "rk", "span": "05–10.10"},
    {"date": "2026-10-25", "title": "День Республики", "kind": "holiday", "span": "25.10"},
    {"date": "2026-11-09", "title": "Рубежный контроль 2", "kind": "rk", "span": "09–14.11"},
    {"date": "2026-11-14", "title": "Конец теоретического обучения", "kind": "term", "span": "14.11"},
    {"date": "2026-11-16", "title": "Экзаменационная сессия", "kind": "exam", "span": "16–28.11"},
    {"date": "2026-11-30", "title": "Каникулы", "kind": "break", "span": "30.11–05.12"},
]


def _course(code, title, credits, teacher, attended, total, items):
    return {"code": code, "title": title, "credits": credits, "teacher": teacher,
            "attendance": {"attended": attended, "total": total}, "items": items}


def courses():
    return [
        # Идёт ровно: РК1 закрыт хорошо, РК2 начат.
        _course("MATH 1201", "Calculus 1", 5, "А. Сериков", 22, 24, [
            {"name": "Задание 1: пределы", "period": 1, "max": 20, "score": 18, "due": "2026-09-21", "kind": "assignment"},
            {"name": "Задание 2: производные", "period": 1, "max": 20, "score": 17, "due": "2026-09-28", "kind": "assignment"},
            {"name": "Квиз 1", "period": 1, "max": 20, "score": 16, "due": "2026-10-02", "kind": "quiz"},
            {"name": "Mid Term", "period": 1, "max": 40, "score": 31, "due": "2026-10-07", "kind": "midterm"},
            {"name": "Задание 3: интегралы", "period": 2, "max": 20, "score": 19, "due": "2026-10-23", "kind": "assignment"},
            {"name": "Задание 4: ряды", "period": 2, "max": 20, "score": None, "due": "2026-10-30", "kind": "assignment"},
            {"name": "Квиз 2", "period": 2, "max": 20, "score": None, "due": "2026-11-06", "kind": "quiz"},
            {"name": "End Term", "period": 2, "max": 40, "score": None, "due": "2026-11-11", "kind": "endterm"},
            {"name": "Итоговый экзамен", "period": "final", "max": 100, "score": None, "due": "2026-11-18", "kind": "final"},
        ]),
        # Всё отлично — так выглядит спокойный курс.
        _course("CSCI 1101", "Programming Principles", 5, "Б. Нурланова", 24, 24, [
            {"name": "Лаба 1: типы и ввод", "period": 1, "max": 20, "score": 20, "due": "2026-09-18", "kind": "assignment"},
            {"name": "Лаба 2: циклы", "period": 1, "max": 20, "score": 19, "due": "2026-09-25", "kind": "assignment"},
            {"name": "Квиз 1", "period": 1, "max": 20, "score": 18, "due": "2026-10-01", "kind": "quiz"},
            {"name": "Mid Term", "period": 1, "max": 40, "score": 36, "due": "2026-10-06", "kind": "midterm"},
            {"name": "Лаба 3: функции", "period": 2, "max": 20, "score": 20, "due": "2026-10-22", "kind": "assignment"},
            {"name": "Лаба 4: файлы", "period": 2, "max": 20, "score": None, "due": "2026-10-29", "kind": "assignment"},
            {"name": "Квиз 2", "period": 2, "max": 20, "score": None, "due": "2026-11-05", "kind": "quiz"},
            {"name": "End Term", "period": 2, "max": 40, "score": None, "due": "2026-11-12", "kind": "endterm"},
            {"name": "Итоговый экзамен", "period": "final", "max": 100, "score": None, "due": "2026-11-20", "kind": "final"},
        ]),
        # Слабый РК1 — ради этого случая инструмент и делается.
        _course("CSCI 1102", "Discrete Mathematics", 5, "Д. Ахметов", 20, 24, [
            {"name": "Задание 1: множества", "period": 1, "max": 20, "score": 11, "due": "2026-09-20", "kind": "assignment"},
            {"name": "Задание 2: логика", "period": 1, "max": 20, "score": 9, "due": "2026-09-27", "kind": "assignment"},
            {"name": "Квиз 1", "period": 1, "max": 20, "score": 8, "due": "2026-10-03", "kind": "quiz"},
            {"name": "Mid Term", "period": 1, "max": 40, "score": 14, "due": "2026-10-08", "kind": "midterm"},
            {"name": "Задание 3: графы", "period": 2, "max": 20, "score": None, "due": "2026-10-27", "kind": "assignment"},
            {"name": "Задание 4: комбинаторика", "period": 2, "max": 20, "score": None, "due": "2026-11-03", "kind": "assignment"},
            {"name": "Квиз 2", "period": 2, "max": 20, "score": None, "due": "2026-11-07", "kind": "quiz"},
            {"name": "End Term", "period": 2, "max": 40, "score": None, "due": "2026-11-13", "kind": "endterm"},
            {"name": "Итоговый экзамен", "period": "final", "max": 100, "score": None, "due": "2026-11-24", "kind": "final"},
        ]),
        # Баллы приличные, но посещаемость ниже 70% — скрытый способ завалить курс.
        _course("HIST 1101", "History of Kazakhstan", 5, "Г. Сапарова", 15, 24, [
            {"name": "Эссе 1", "period": 1, "max": 20, "score": 16, "due": "2026-09-22", "kind": "assignment"},
            {"name": "Эссе 2", "period": 1, "max": 20, "score": 15, "due": "2026-09-29", "kind": "assignment"},
            {"name": "Квиз 1", "period": 1, "max": 20, "score": 14, "due": "2026-10-02", "kind": "quiz"},
            {"name": "Mid Term", "period": 1, "max": 40, "score": 28, "due": "2026-10-09", "kind": "midterm"},
            {"name": "Эссе 3", "period": 2, "max": 20, "score": None, "due": "2026-10-28", "kind": "assignment"},
            {"name": "Эссе 4", "period": 2, "max": 20, "score": None, "due": "2026-11-04", "kind": "assignment"},
            {"name": "Квиз 2", "period": 2, "max": 20, "score": None, "due": "2026-11-06", "kind": "quiz"},
            {"name": "End Term", "period": 2, "max": 40, "score": None, "due": "2026-11-10", "kind": "endterm"},
            {"name": "Итоговый экзамен", "period": "final", "max": 100, "score": None, "due": "2026-11-26", "kind": "final"},
        ]),
        _course("LANG 1201", "Professional English", 3, "E. Whitfield", 23, 24, [
            {"name": "Speaking 1", "period": 1, "max": 20, "score": 17, "due": "2026-09-19", "kind": "assignment"},
            {"name": "Writing 1", "period": 1, "max": 20, "score": 18, "due": "2026-09-26", "kind": "assignment"},
            {"name": "Квиз 1", "period": 1, "max": 20, "score": 17, "due": "2026-10-01", "kind": "quiz"},
            {"name": "Mid Term", "period": 1, "max": 40, "score": 33, "due": "2026-10-05", "kind": "midterm"},
            {"name": "Speaking 2", "period": 2, "max": 20, "score": 18, "due": "2026-10-24", "kind": "assignment"},
            {"name": "Writing 2", "period": 2, "max": 20, "score": None, "due": "2026-10-31", "kind": "assignment"},
            {"name": "Квиз 2", "period": 2, "max": 20, "score": None, "due": "2026-11-07", "kind": "quiz"},
            {"name": "End Term", "period": 2, "max": 40, "score": None, "due": "2026-11-12", "kind": "endterm"},
            {"name": "Итоговый экзамен", "period": "final", "max": 100, "score": None, "due": "2026-11-22", "kind": "final"},
        ]),
    ]


# Расписание на день: пары по 50 минут, как в силлабусе.
SCHEDULE = [
    {"start": "09:00", "end": "09:50", "course": "MATH 1201", "title": "Calculus 1", "kind": "Лекция", "room": "C1.2.315"},
    {"start": "10:00", "end": "10:50", "course": "MATH 1201", "title": "Calculus 1", "kind": "Практика", "room": "C1.2.315"},
    {"start": "11:00", "end": "11:50", "course": "CSCI 1101", "title": "Programming Principles", "kind": "Лаба", "room": "C1.3.240"},
    {"start": "13:00", "end": "13:50", "course": "CSCI 1102", "title": "Discrete Mathematics", "kind": "Лекция", "room": "C1.1.108"},
    {"start": "14:00", "end": "14:50", "course": "LANG 1201", "title": "Professional English", "kind": "Практика", "room": "C1.4.412"},
]
