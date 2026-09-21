"""Расчёт оценки по правилам AITU. Чистые функции, без сети и без файлов.

Источники правил: силлабус дисциплины (веса) и официальное письмо университета
первокурснику (пороги допуска). Формула одна на весь университет, меняется только
состав заданий внутри аттестации.

    Итог = 0,3 x РК1 + 0,3 x РК2 + 0,4 x Итоговый экзамен

Смысл модуля — не «показать оценку», а ответить на вопрос «что мне ещё нужно
набрать». Отсюда required_re() и required_final().
"""
import math

WEIGHT_RM = 0.3          # Register Midterm, он же рубежный контроль 1
WEIGHT_RE = 0.3          # Register Endterm, он же рубежный контроль 2
WEIGHT_FINAL = 0.4       # итоговый экзамен

MIN_ATTENDANCE = 70      # ниже — не допустят до экзамена
MIN_ATTESTATION = 25     # ниже по любой из аттестаций — автоматический незачёт
MIN_AVG_FOR_EXAM = 50    # среднее РК1 и РК2 для допуска к экзамену
PASS_MARK = 50           # с этого начинается «успешно»
RETAKE_FLOOR = 25        # ниже по экзамену — не пересдача, а весь курс заново

# Шкала университета: (нижняя граница процента, буква, GPA)
SCALE = [
    (95, "A", 4.00), (90, "A-", 3.67), (85, "B+", 3.33), (80, "B", 3.00),
    (75, "B-", 2.67), (70, "C+", 2.33), (65, "C", 2.00), (60, "C-", 1.67),
    (55, "D+", 1.33), (50, "D", 1.00), (30, "FX", 0.00), (0, "F", 0.00),
]


def letter(total):
    """Буква и GPA по итоговому проценту."""
    for floor, mark, gpa in SCALE:
        if total >= floor:
            return mark, gpa
    return "F", 0.0


def attestation(items):
    """Свод по одной аттестации: что уже есть и что ещё можно набрать.

    Различаем три разные величины, которые легко перепутать:
      score    — сколько баллов уже в кармане (несданное = 0)
      pace     — как ты справляешься с тем, что уже оценено
      ceiling  — максимум, если всё оставшееся сдать идеально
    """
    graded = [i for i in items if i.get("score") is not None]
    pending = [i for i in items if i.get("score") is None]

    # У дедлайна из календаря Moodle веса нет вообще (`max: None`) — он про срок,
    # а не про баллы. В сумме он должен весить ноль, а не ронять расчёт.
    earned = sum(i["score"] for i in graded)
    graded_max = sum(i.get("max") or 0 for i in graded)
    pending_max = sum(i.get("max") or 0 for i in pending)

    return {
        "items": items,
        "graded": len(graded),
        "total_items": len(items),
        "score": round(earned, 1),                                  # факт сейчас
        "ceiling": round(earned + pending_max, 1),                  # потолок
        "pace": round(earned / graded_max * 100, 1) if graded_max else None,
        "pending_max": round(pending_max, 1),
        # Пустая аттестация не «завершена», она ещё не началась. Иначе курс без
        # заданий во втором рубеже показывает «РК2 = 0, незачёт» на первой неделе.
        "complete": bool(items) and not pending,
        "started": bool(items),
    }


def required_re(rm):
    """Сколько нужно за РК2, чтобы допустили до экзамена.

    Допуск требует (РК1 + РК2) / 2 >= 50, то есть РК1 + РК2 >= 100.
    Плюс отдельный порог: сам РК2 не ниже 25.
    """
    need = max(MIN_ATTESTATION, MIN_AVG_FOR_EXAM * 2 - rm)
    return None if need > 100 else math.ceil(need)


def required_final(rm, re):
    """Сколько нужно за экзамен, чтобы итог дотянул до 50."""
    need = (PASS_MARK - WEIGHT_RM * rm - WEIGHT_RE * re) / WEIGHT_FINAL
    need = max(need, RETAKE_FLOOR)      # ниже 25 всё равно означает курс заново
    return None if need > 100 else math.ceil(need)


def attendance_pct(attendance):
    """Проценты посещаемости. Считаем из занятий либо берём готовые.

    Moodle держит посещаемость как оценку 0-100, то есть уже в процентах, и
    числа занятий не отдаёт вовсе. Пересчитывать «100 из 100 занятий» было бы
    выдумкой: занятий столько не было.
    """
    if not attendance:
        return None
    if attendance.get("percent") is not None:
        return round(float(attendance["percent"]), 1)
    if not attendance.get("total"):
        return None
    return round(attendance["attended"] / attendance["total"] * 100, 1)


def evaluate(course):
    """Полный разбор одной дисциплины: где стоишь и что ещё нужно."""
    items = course.get("items", [])
    rm = attestation([i for i in items if i.get("period") == 1])
    re = attestation([i for i in items if i.get("period") == 2])
    final = attestation([i for i in items if i.get("period") == "final"])

    rm_score, re_score, final_score = rm["score"], re["score"], final["score"]
    total_now = WEIGHT_RM * rm_score + WEIGHT_RE * re_score + WEIGHT_FINAL * final_score
    ceiling = WEIGHT_RM * rm["ceiling"] + WEIGHT_RE * re["ceiling"] + WEIGHT_FINAL * final["ceiling"]

    attend = attendance_pct(course.get("attendance"))
    average = (rm_score + re_score) / 2

    # Барьеры проверяем только когда по ним уже можно судить: незаконченная
    # аттестация не «провалена», она просто ещё идёт.
    risks = []
    if attend is not None and attend < MIN_ATTENDANCE:
        risks.append(f"Посещаемость {attend}% — нужно {MIN_ATTENDANCE}%")
    if rm["complete"] and rm_score < MIN_ATTESTATION:
        risks.append(f"РК1 {rm_score} — ниже {MIN_ATTESTATION}, это незачёт")
    if re["complete"] and re_score < MIN_ATTESTATION:
        risks.append(f"РК2 {re_score} — ниже {MIN_ATTESTATION}, это незачёт")
    if rm["complete"] and re["complete"] and average < MIN_AVG_FOR_EXAM:
        risks.append(f"Среднее РК {average:.0f} — до экзамена не допустят")

    mark, gpa = letter(total_now)
    return {
        "course": course,
        "rm": rm, "re": re, "final": final,
        "attendance": attend,
        "average_rk": round(average, 1),
        "total_now": round(total_now, 1),
        "ceiling": round(ceiling, 1),
        # Буква по набранному сейчас честна арифметически, но бессмысленна в
        # середине триместра: несданное считается нулём, и отличник видит FX.
        # Поэтому наружу отдаём и букву потолка — «если сдать всё оставшееся».
        "letter": mark, "gpa": gpa,
        "ceiling_letter": letter(ceiling)[0],
        # Совет «нужно 100 за РК2» при неоценённом РК1 — арифметика от нуля,
        # а не совет: РК1 ещё не выставили, требовать по нему нечего.
        "need_re": required_re(rm_score) if rm["complete"] and not re["complete"] else None,
        "need_final": required_final(rm_score, re_score) if not final["complete"] else None,
        "risks": risks,
        "hopeless": ceiling < PASS_MARK,
    }
