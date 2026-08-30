"""Отправка в Telegram. Бот берётся тот же, что у соседнего tg-digest.

Заводить второго бота ради одних уведомлений незачем: токен уже есть в
`../tg-digest/.env`, читаем оттуда. Если у тебя только rubezh — положи
`BOT_TOKEN` в свой `.env` рядом, он имеет приоритет.

Кому слать, узнаём один раз:

    напиши что-нибудь своему боту в Telegram, потом
    python rubezh.py telegram

Telethon тут не нужен — chat_id достаём через getUpdates самого бота.
"""
import os
from pathlib import Path

import requests

ROOT = Path(__file__).parent
SECRETS = ROOT / "secrets.json"
ENV_FILES = [ROOT / ".env", ROOT.parent / "tg-digest" / ".env"]
API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 3900          # у Telegram потолок 4096 на сообщение


def load_env():
    """Простой .env-ридер, чтобы не тащить python-dotenv (как в tg-digest)."""
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


def token():
    load_env()
    value = os.environ.get("BOT_TOKEN")
    if not value:
        raise SystemExit(
            "Нет BOT_TOKEN. Он лежит в ../tg-digest/.env — проверь, что файл на месте, "
            "или положи BOT_TOKEN в rubezh/.env."
        )
    return value


def _secrets():
    import json
    if SECRETS.exists():
        try:
            return json.loads(SECRETS.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def _save_secret(key, value):
    import json
    data = _secrets()
    data[key] = value
    SECRETS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def chat_id():
    return _secrets().get("telegram_chat_id")


def link(chat_id=None):
    """Найти, кому слать уведомления.

    Раньше здесь бралась просто последняя написавшая боту переписка. Это дыра:
    имя бота попадает к другим студентам, и любой, кто напишет ему раньше, чем
    ты запустишь эту команду, тихо станет получателем твоих оценок. Поэтому
    молча выбираем только когда кандидат ровно один; иначе показываем список и
    просим указать явно.
    """
    if chat_id is not None:
        _save_secret("telegram_chat_id", int(chat_id))
        print(f"Буду писать в chat_id {chat_id}. Сохранено в secrets.json.")
        return int(chat_id)

    response = requests.get(API.format(token=token(), method="getUpdates"), timeout=30)
    if not response.ok:
        raise SystemExit(f"Telegram ответил {response.status_code}: {response.text[:200]}")

    chats = {}
    for update in response.json().get("result", []):
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = {
                "name": chat.get("first_name") or chat.get("title") or str(chat["id"]),
                "private": chat.get("type") == "private",
            }
    if not chats:
        raise SystemExit(
            "Боту никто не писал. Открой своего бота в Telegram, нажми Start, отправь любое "
            "сообщение и запусти команду ещё раз.\n"
            "Учти: Telegram хранит эти обновления около суток."
        )

    # Уведомления — в личку, а не в группу, куда бот случайно попал.
    candidates = {i: c for i, c in chats.items() if c["private"]} or chats
    if len(candidates) > 1:
        listing = "\n".join(f"  --chat-id {i}   {c['name']}" for i, c in candidates.items())
        raise SystemExit(
            "Боту писал не один человек, и угадывать тут нельзя — иначе твои оценки\n"
            "уедут чужому. Выбери себя явно:\n\n" + listing +
            "\n\n  python rubezh.py telegram --chat-id <id>"
        )

    found, info = next(iter(candidates.items()))
    _save_secret("telegram_chat_id", found)
    print(f"Буду писать сюда: {info['name']} (chat_id {found}). Сохранено в secrets.json.")
    print("Если это не ты — перезапусти с нужным --chat-id.")
    return found


def send(text):
    """Отправить сообщение. Длинное режем — у Telegram лимит на сообщение."""
    target = chat_id()
    if not target:
        raise SystemExit("Неизвестно, кому слать. Запусти: python rubezh.py telegram")

    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > LIMIT:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)

    for chunk in chunks:
        response = requests.post(
            API.format(token=token(), method="sendMessage"),
            json={"chat_id": target, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=60,
        )
        if not response.ok:
            raise SystemExit(
                f"Бот не смог отправить ({response.status_code}): {response.text[:250]}\n"
                "Чаще всего это значит, что ты не нажал Start в своём боте."
            )
    return len(chunks)
