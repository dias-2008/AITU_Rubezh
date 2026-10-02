"""Потянет ли этот компьютер локальную модель для почты — и какую.

Почту (Outlook) разбирает Ollama на этом же компьютере. Раньше мастер всем
предлагал одну модель gemma4:e4b: на ноутбуке с 8 ГБ памяти она либо не
запустится, либо будет разбирать письма минутами, а 7–10 ГБ скачивания для
шага «по желанию» — это много. Поэтому сначала смотрим на железо и только
потом советуем: модель покрупнее, поменьше или честно «пропусти шаг».

Пороги — по памяти, которую модель занимает при работе, с запасом на
браузер и систему. Размеры скачивания — со страниц ollama.com/library на
октябрь 2026 (сборка q4_K_M; на Mac у Ollama сборка MLX, она тяжелее).
Семейство одно — gemma4: на нём проверены подсказки в outlook.py.
"""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.request import urlopen

import paths

OLLAMA = "http://localhost:11434"

# (модель, скачивать ГБ, нужно ОЗУ ГБ, или видеопамяти ГБ) — от лучшей к меньшей.
TIERS = [
    ("gemma4:e4b", 7, 16, 8),
    ("gemma4:e2b", 5, 12, 6),
]
DISK_SPARE_GB = 2
# «16 ГБ» на коробке Windows показывает как 15,7, а «8 ГБ» видеопамяти — как 7,99:
# часть занята системой. Без допуска ноутбук с 16 ГБ получал бы модель поменьше.
SLACK = 0.9


def ram_gb():
    if paths.WINDOWS:
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return status.ullTotalPhys / 2**30
    if paths.MACOS:
        out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True)
        return int(out.stdout.strip() or 0) / 2**30
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 2**20
    except OSError:
        pass
    return 0.0


def vram_gb():
    """Видеопамять NVIDIA — её Ollama использует напрямую.

    Встроенная графика и Apple Silicon берут обычную память, её уже посчитал
    ram_gb(). Видеокарты AMD Windows честно не отдаёт (WMI обрезает объём на
    4 ГБ), поэтому их не учитываем — совет выйдет осторожнее, но не неверным.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0.0
    try:
        out = subprocess.run([exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if paths.WINDOWS else 0)
        return max((int(x) for x in out.stdout.split() if x.isdigit()), default=0) / 1024
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def disk_free_gb():
    """Место там, куда Ollama кладёт модели (по умолчанию в папке пользователя)."""
    return shutil.disk_usage(Path.home()).free / 2**30


def installed():
    """Модели, которые уже скачаны в Ollama, или None, если Ollama нет.

    Сервер Ollama спит, пока его не позовут, — тогда спрашиваем `ollama list`:
    он и поднимет сервер, и ответит списком.
    """
    try:
        with urlopen(OLLAMA + "/api/tags", timeout=3) as reply:
            return [m["name"] for m in json.load(reply).get("models", [])]
    except (OSError, ValueError):
        pass
    exe = shutil.which("ollama")
    if not exe:
        return None
    # Вывод — во временный файл, не в трубу: `ollama list` поднимает сервер
    # дочерним процессом, тот наследует трубу и держит её открытой, и чтение
    # «до конца» ждало бы, пока сервер не выключится.
    import tempfile
    with tempfile.TemporaryFile() as sink:
        try:
            done = subprocess.run([exe, "list"], stdout=sink, stderr=subprocess.DEVNULL,
                                  stdin=subprocess.DEVNULL, timeout=60,
                                  creationflags=0x08000000 if paths.WINDOWS else 0)
        except (OSError, subprocess.SubprocessError):
            return None
        if done.returncode != 0:
            return None
        sink.seek(0)
        text = sink.read().decode("utf-8", "replace")
    return [line.split()[0] for line in text.splitlines()[1:] if line.strip()]


def _have(name, models):
    return any(m == name or m == name + ":latest" for m in models or [])


def advice():
    """Что сказать человеку о шаге «Почта» — всё, что нужно мастеру."""
    ram, vram, disk = ram_gb(), vram_gb(), disk_free_gb()
    models = installed()
    pick = next((t for t in TIERS if ram >= t[2] * SLACK or vram >= t[3] * SLACK), None)
    have = next((t[0] for t in TIERS if _have(t[0], models)), None)
    return {
        "ram_gb": round(ram), "vram_gb": round(vram, 1), "disk_gb": round(disk),
        "model": pick[0] if pick else None,
        "size_gb": pick[1] if pick else None,
        "disk_ok": bool(pick) and disk >= pick[1] + DISK_SPARE_GB,
        "ollama": models is not None or bool(shutil.which("ollama")),
        "running": models is not None,
        "have": have,                    # уже скачанная модель из списка — её и возьмём
    }


def ready():
    """Скачанная модель из списка или None — тогда почту не разбираем вовсе."""
    models = installed()
    return next((name for name, *_ in TIERS if _have(name, models)), None)


def model():
    """Какой моделью разбирать почту: скачанная из списка, иначе совет по железу."""
    models = installed()
    for name, *_ in TIERS:
        if _have(name, models):
            return name
    ram, vram = ram_gb(), vram_gb()
    pick = next((t for t in TIERS if ram >= t[2] * SLACK or vram >= t[3] * SLACK), TIERS[-1])
    return pick[0]
