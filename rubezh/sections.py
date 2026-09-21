"""Содержимое курса Moodle по неделям — со страницы курса, а не из календаря.

Зачем. Календарь Moodle знает только про активности со сроком: задание с
датой, тест с окном. А преподаватель пишет «к практике подготовьте темы…»
прямо в блоке недели, текстом, без всякого срока — и в календаре этого нет.
Так студент сдаёт всё, что видел на дашборде, и не видит семинар, к которому
надо было готовиться. Единственное место, где это лежит, — сама страница
курса `/course/view.php?id=<курс>`: там секции «Week N (dd.mm-dd.mm)»,
плашка «Current week», тексты и ссылки на материалы.

`core_course_get_contents` через ajax на сервере AITU закрыт (см. `probe`),
поэтому страницу читаем как HTML по той же куке, что и журнал. Парсер свой,
на html.parser: сторонней зависимости ради одной страницы не заводим.

Вёрстка Moodle 4/5 (у AITU — 5.x, тема classic), на что опираемся:

    <li id="section-2" class="section ... current">
      <h3 class="sectionname">Week 2 (14.09-20.09)</h3>   + badge «Current week»
      <div class="summarytext">…описание секции…</div>
      <li class="activity modtype_label"  data-id="123"> текст преподавателя
      <li class="activity modtype_assign" data-id="124"> <a href=…/mod/assign/…>Название</a>
                                                     .activity-dates  «Opened: … Due: …»
                                                     .availabilityinfo «Not available unless…»
                                                     [data-region=completion-info]
                                                       кнопка «Mark as done» / «Done» / «To do»

Отметка о выполнении — то, что делает список «что сделать», а не «что лежит».
Ручная — кнопка с data-toggletype (manual:mark-done / manual:undo), автоматическая —
«To do: View, Receive a grade» / «Done: …». Где преподаватель отслеживание не
включил, отметки нет вовсе — тогда done пустой, и это честнее, чем гадать.

Всё, что не нашлось, отдаём пустым, а не падаем: страница курса меняется от
версии к версии, и лучше показать секцию без дат, чем не показать ничего.
"""
import re
from html.parser import HTMLParser

import requests

import fast

VIEW = "https://lms.astanait.edu.kz/course/view.php"
TEXT_LIMIT = 4000        # символов на один текстовый блок — семинар на страницу влезает

VOID = {"br", "img", "input", "hr", "meta", "link", "area", "base", "col", "embed",
        "param", "source", "track", "wbr"}
BLOCK = {"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br",
         "table", "section", "article", "blockquote", "pre"}
HIDDEN = ("accesshide", "sr-only", "visually-hidden")


class _Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    @property
    def classes(self):
        return (self.attrs.get("class") or "").split()

    def has(self, *names):
        cls = self.classes
        return any(n in cls for n in names)

    def walk(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.walk()

    def find(self, pred):
        for node in self.walk():
            if pred(node):
                return node
        return None

    def find_all(self, pred):
        return [node for node in self.walk() if pred(node)]


class _Tree(HTMLParser):
    """Дерево из HTML. Незакрытые теги закрываем по ближайшему открытому —
    html.parser сам этого не делает, а Moodle иногда забывает </p>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.cur = node.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(data)


def text_of(node, skip=lambda n: False):
    """Текст узла с переносами по блочным тегам и маркерами у пунктов списка."""
    out = []

    def rec(n):
        if isinstance(n, str):
            out.append(n)
            return
        if skip(n) or n.has(*HIDDEN) or n.tag in ("script", "style"):
            return
        block = n.tag in BLOCK
        if block:
            out.append("\n")
        if n.tag == "li":
            out.append("• ")
        for child in n.children:
            rec(child)
        if block:
            out.append("\n")

    rec(node)
    lines = [" ".join(line.split()) for line in "".join(out).split("\n")]
    return "\n".join(l for l in lines if l).strip()


def _dates(node):
    """«Opened: Monday, 6 September 2026, 11:00 PM Due: …» -> словарь по меткам."""
    box = node.find(lambda n: n.has("activity-dates"))
    if not box:
        return {}
    text = text_of(box)
    out = {}
    for match in re.finditer(r"(Opened|Opens|Due|Closes|Closed|Open until|Expected completion)\s*:\s*([^\n]+?)(?=(?:\s+(?:Opened|Opens|Due|Closes|Closed)\s*:)|$)",
                             text, flags=re.S):
        out[match.group(1).lower()] = " ".join(match.group(2).split())
    return out


def _completion(li):
    """«done» — отмечено выполненным, «todo» — ещё нет, «» — отслеживания нет."""
    box = li.find(lambda n: n.attrs.get("data-region") == "completion-info"
                  or n.has("activity-completion", "completion-dropdown", "automatic-completion-conditions"))
    if not box:
        return ""
    toggle = box.find(lambda n: n.attrs.get("data-toggletype"))
    if toggle:
        return "done" if toggle.attrs["data-toggletype"].endswith("undo") else "todo"
    if box.find(lambda n: n.has("btn-success", "badge-success", "text-success")):
        return "done"
    text = text_of(box).lower()
    if "to do" in text or "failed" in text or "mark as done" in text:
        return "todo"
    if text.startswith("done"):
        return "done"
    return ""


def _activity(li):
    modtype = next((c[len("modtype_"):] for c in li.classes if c.startswith("modtype_")), "")
    cmid = li.attrs.get("data-id") or (li.attrs.get("id") or "").replace("module-", "")
    item_box = li.find(lambda n: n.has("activity-item")) or li
    name = item_box.attrs.get("data-activityname") or ""
    if not name:
        inst = li.find(lambda n: n.has("instancename"))
        name = text_of(inst) if inst else ""
    link = li.find(lambda n: n.tag == "a" and "/mod/" in (n.attrs.get("href") or ""))
    url = (link.attrs.get("href") or "") if link else ""
    restricted = li.find(lambda n: n.has("availabilityinfo"))
    row = {
        "id": str(cmid),
        "type": modtype,
        "name": " ".join(name.split()),
        "url": url if url.startswith(fast.LMS + "/") else "",
        "dates": _dates(li),
        "restricted": text_of(restricted) if restricted else "",
        "done": _completion(li),
        "text": "",
    }
    if modtype == "label":
        # Текстовый блок — то самое, чего нет в календаре. Берём его целиком.
        body = li.find(lambda n: n.has("activity-altcontent", "contentafterlink")) or item_box
        row["text"] = text_of(body, skip=lambda n: n.has("activity-dates", "availabilityinfo"))[:TEXT_LIMIT]
        row["name"] = ""
    return row


def parse(html):
    """Секции курса: номер, название, текущая ли, описание, содержимое."""
    tree = _Tree()
    tree.feed(html)
    out = []
    for li in tree.root.find_all(lambda n: n.tag == "li" and (n.attrs.get("id") or "").startswith("section-")):
        number = re.sub(r"\D", "", li.attrs.get("id") or "")
        title = li.find(lambda n: n.has("sectionname"))
        badge = li.find(lambda n: n.has("badge") and "current" in text_of(n).lower())
        summary = li.find(lambda n: n.has("summarytext"))
        # Активности лежат в своём <ul>; вложенные li секции с id section-… не путаем
        items = [_activity(a) for a in li.find_all(
            lambda n: n.tag == "li" and n.has("activity") and any(c.startswith("modtype_") for c in n.classes))]
        out.append({
            "n": int(number) if number.isdigit() else 0,
            "name": text_of(title, skip=lambda n: n.has("badge")) if title else f"Секция {number}",
            "current": bool(badge) or li.has("current"),
            "summary": text_of(summary)[:TEXT_LIMIT] if summary else "",
            "items": items,
        })
    return out


def fetch(courseid):
    jar = fast._cookies("lms", "astanait")
    if "MoodleSession" not in jar:
        raise fast.Stale("нет куки MoodleSession")
    response = requests.get(VIEW, params={"id": courseid},
                            headers={"User-Agent": fast.UA}, cookies=jar, timeout=fast.TIMEOUT)
    if response.status_code == 403:
        raise fast.Stale("WAF ответил 403")
    if not response.ok:
        raise fast.Stale(f"HTTP {response.status_code}")
    if "login/index.php" in response.url or "You are logged in" not in response.text:
        raise fast.Stale("вместо курса пришла страница входа")
    return response.text


def read(courseid):
    return parse(fetch(courseid))


def digest(courseid, course_title, sections):
    """Плоский словарь «ключ -> что это» для сравнения между проверками.

    Ключ активности — её cmid: он не меняется при правке текста, и так видно
    и новое, и изменившееся. Описание секции своего id не имеет — ключ по
    номеру секции.
    """
    out = {}
    for sec in sections:
        if sec["summary"]:
            out[f"{courseid}:s{sec['n']}"] = {"course": course_title, "section": sec["name"],
                                              "text": sec["summary"][:600]}
        for item in sec["items"]:
            if not item["id"]:
                continue
            out[f"{courseid}:{item['id']}"] = {
                "course": course_title, "section": sec["name"], "type": item["type"],
                "name": item["name"], "text": item["text"][:600], "url": item["url"],
            }
    return out
