"""
UnlimitedMC — ядро (вся логика без интерфейса).

Зависит от: minecraft-launcher-lib, requests.
Можно использовать отдельно от GUI (для тестов).
"""
from __future__ import annotations

import os
import sys
import json
import uuid
import base64
import struct
import random
import shutil
import hashlib
import zipfile
import tempfile
import platform
import subprocess
from pathlib import Path

import requests
import minecraft_launcher_lib as mll

UA = "UnlimitedMC/0.2 (https://umclaunch.net)"
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


# --------------------------------------------------------------------------- #
#  Пути и данные приложения
# --------------------------------------------------------------------------- #
def app_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "UnlimitedMC"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = app_dir()
SHARED_MC = DATA / "minecraft"
INSTANCES_DIR = DATA / "instances"
CONFIG_FILE = DATA / "config.json"
SHARED_MC.mkdir(parents=True, exist_ok=True)
INSTANCES_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
#  Конфиг / локальный профиль
# --------------------------------------------------------------------------- #
DEFAULT_CONFIG = {
    "profile": {"username": "Player", "uuid": "", "display_name": "", "bio": ""},
    "ram_mb": 4096,
    "java_path": "",
    "curseforge_api_key": "",
    "proxy": "",            # http://host:port  или  socks5://host:port (для РФ/Modrinth)
    "accent": "#4C8DFF",    # акцентный цвет интерфейса
    "theme": "dark",        # dark | light
    "lang": "ru",           # ru | en
    "reactive": True,       # реактивный фон под источник (зелёный Modrinth, оранжевый CurseForge)
    "onboarded": False,
    "instances": [],
}


def offline_uuid(username: str) -> str:
    digest = bytearray(hashlib.md5(("OfflinePlayer:" + username).encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    out = json.loads(json.dumps(DEFAULT_CONFIG))
    for key in out:
        if key in cfg and key != "profile":
            out[key] = cfg[key]
    prof = DEFAULT_CONFIG["profile"].copy()
    prof.update(cfg.get("profile", {}))
    out["profile"] = prof
    if not out["profile"].get("uuid"):
        out["profile"]["uuid"] = offline_uuid(out["profile"]["username"] or "Player")
    return out


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("Не удалось сохранить конфиг:", e)


def set_username(cfg: dict, username: str) -> None:
    username = (username or "Player").strip() or "Player"
    cfg["profile"]["username"] = username
    cfg["profile"]["uuid"] = offline_uuid(username)


# --------------------------------------------------------------------------- #
#  Прокси (для РФ: Modrinth заблокирован)
# --------------------------------------------------------------------------- #
def apply_proxy(cfg: dict) -> None:
    """Применяет прокси ко всему сетевому слою (наши запросы + загрузки игры)."""
    px = (cfg.get("proxy") or "").strip()
    if px:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ[k] = px
        _session.proxies = {"http": px, "https": px}
    else:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(k, None)
        _session.proxies = {}


# --------------------------------------------------------------------------- #
#  Сборки (instances)
# --------------------------------------------------------------------------- #
def new_instance(name: str, mc_version: str, loader: str = "vanilla") -> dict:
    return {
        "id": uuid.uuid4().hex[:12],
        "name": name.strip() or "Новая сборка",
        "mc_version": mc_version.strip(),
        "loader": (loader or "vanilla").lower(),
        "launch_id": "",
        "mods": [],
    }


def instance_dir(inst: dict) -> Path:
    d = INSTANCES_DIR / inst["id"]
    (d / "mods").mkdir(parents=True, exist_ok=True)
    return d


def add_instance(cfg: dict, inst: dict) -> None:
    cfg["instances"].append(inst)


def remove_instance(cfg: dict, inst_id: str) -> None:
    cfg["instances"] = [i for i in cfg["instances"] if i["id"] != inst_id]


def find_instance(cfg: dict, inst_id: str) -> dict | None:
    return next((i for i in cfg["instances"] if i["id"] == inst_id), None)


# --------------------------------------------------------------------------- #
#  Установка и запуск игры
# --------------------------------------------------------------------------- #
def _callback(progress_cb):
    state = {"status": "", "value": 0, "max": 0}

    def set_status(s):
        state["status"] = s
        progress_cb(s, state["value"], state["max"])

    def set_progress(v):
        state["value"] = v
        progress_cb(state["status"], v, state["max"])

    def set_max(m):
        state["max"] = m
        progress_cb(state["status"], state["value"], m)

    return {"setStatus": set_status, "setProgress": set_progress, "setMax": set_max}


def installed_version_ids() -> set:
    try:
        return {v["id"] for v in mll.utils.get_installed_versions(str(SHARED_MC))}
    except Exception:
        return set()


def get_release_versions(limit: int = 80) -> list[str]:
    out = []
    for v in mll.utils.get_version_list():
        if v.get("type") == "release":
            out.append(v["id"])
        if len(out) >= limit:
            break
    return out


def install_for_instance(inst: dict, progress_cb) -> str:
    mc = inst["mc_version"]
    loader = (inst.get("loader") or "vanilla").lower()
    before = installed_version_ids()

    if loader == "vanilla":
        mll.install.install_minecraft_version(mc, str(SHARED_MC), callback=_callback(progress_cb))
    elif loader == "fabric":
        if hasattr(mll.fabric, "is_minecraft_version_supported") and not mll.fabric.is_minecraft_version_supported(mc):
            raise RuntimeError(f"Fabric не поддерживает версию {mc}")
        mll.fabric.install_fabric(mc, str(SHARED_MC), callback=_callback(progress_cb))
    elif loader == "quilt":
        mll.quilt.install_quilt(mc, str(SHARED_MC), callback=_callback(progress_cb))
    elif loader == "forge":
        fv = mll.forge.find_forge_version(mc)
        if not fv:
            raise RuntimeError(f"Для {mc} не нашлось версии Forge с авто-установкой")
        if hasattr(mll.forge, "supports_automatic_install") and not mll.forge.supports_automatic_install(fv):
            raise RuntimeError(f"Forge {fv} нельзя установить автоматически")
        mll.forge.install_forge_version(fv, str(SHARED_MC), callback=_callback(progress_cb))
    else:
        raise RuntimeError(f"Загрузчик «{loader}» пока не поддерживается")

    if loader == "vanilla":
        launch_id = mc
    else:
        new_ids = installed_version_ids() - before
        new_ids.discard(mc)
        if new_ids:
            launch_id = max(new_ids, key=len)
        else:
            key = {"fabric": "fabric", "quilt": "quilt", "forge": "forge"}[loader]
            cands = [v for v in installed_version_ids() if key in v.lower() and mc in v]
            if not cands:
                raise RuntimeError("Не удалось определить версию для запуска")
            launch_id = max(cands, key=len)

    inst["launch_id"] = launch_id
    return launch_id


def build_command(inst: dict, cfg: dict) -> list[str]:
    launch_id = inst.get("launch_id") or inst["mc_version"]
    prof = cfg["profile"]
    # RAM сборки важнее глобальной (0/пусто = брать глобальную из Настроек)
    ram = int(inst.get("ram_mb") or cfg.get("ram_mb", 4096))
    options = {
        "username": prof.get("username", "Player"),
        "uuid": prof.get("uuid") or offline_uuid(prof.get("username", "Player")),
        "token": "0",
        "gameDirectory": str(instance_dir(inst)),
        "jvmArguments": [f"-Xmx{ram}M", f"-Xms{max(512, ram // 2)}M"],
    }
    if cfg.get("java_path"):
        options["executablePath"] = cfg["java_path"]
    return mll.command.get_minecraft_command(launch_id, str(SHARED_MC), options)


def ensure_and_launch(inst: dict, cfg: dict, progress_cb):
    launch_id = inst.get("launch_id")
    if not launch_id or launch_id not in installed_version_ids():
        install_for_instance(inst, progress_cb)
    progress_cb("Запуск игры…", 0, 0)
    cmd = build_command(inst, cfg)
    return subprocess.Popen(cmd, cwd=str(instance_dir(inst)))


# --------------------------------------------------------------------------- #
#  Скачивание
# --------------------------------------------------------------------------- #
def _content_subfolder(project_type: str) -> str:
    return {"mod": "mods", "modpack": "mods", "resourcepack": "resourcepacks",
            "shader": "shaderpacks", "datapack": "mods"}.get(project_type, "mods")


def already_installed(inst: dict, filename: str) -> bool:
    if any(m.get("filename") == filename for m in inst.get("mods", [])):
        return True
    # подстраховка: проверяем файл на диске
    for sub in ("mods", "resourcepacks", "shaderpacks"):
        if (instance_dir(inst) / sub / filename).exists():
            return True
    return False


def _project_pid(source: str, project: dict):
    """Идентификатор проекта в его источнике (для сопоставления со списком модов сборки)."""
    if source == "modrinth":
        return project.get("project_id") or project.get("id") or project.get("slug")
    return project.get("id")


def installed_entry(inst: dict | None, source: str, project: dict) -> dict | None:
    """Запись установленного мода в сборке по источнику+id проекта (или None)."""
    if not inst:
        return None
    pid = _project_pid(source, project)
    if pid is None:
        return None
    for m in inst.get("mods", []):
        if m.get("source") == source and m.get("project_id") == pid:
            return m
    return None


def remove_mod(inst: dict, entry: dict, progress_cb=None) -> dict:
    """Удалить установленный мод: файл с диска и запись из списка сборки."""
    fn = entry.get("filename")
    if fn:
        for sub in ("mods", "resourcepacks", "shaderpacks"):
            p = instance_dir(inst) / sub / fn
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass  # файла уже нет / занят — запись всё равно убираем
    inst["mods"] = [m for m in inst.get("mods", [])
                    if not (m.get("source") == entry.get("source")
                            and m.get("project_id") == entry.get("project_id")
                            and m.get("filename") == entry.get("filename"))]
    return entry


# Папки контента сборки → тип проекта (для отображения и подсчёта).
CONTENT_FOLDERS = {"mods": "mod", "resourcepacks": "resourcepack", "shaderpacks": "shader"}


def count_instance_mods(inst: dict) -> int:
    """Сколько модов реально лежит в папке mods сборки (а не в списке лаунчера)."""
    folder = instance_dir(inst) / "mods"
    if not folder.exists():
        return 0
    return sum(1 for p in folder.iterdir() if p.is_file())


def instance_mods_on_disk(inst: dict) -> list[dict]:
    """Все моды/паки/шейдеры, реально лежащие в папках сборки.

    Истина — файлы на диске (включая добавленные пользователем вручную). Имя и
    источник подтягиваем из списка лаунчера по имени файла, если запись есть;
    для ручных файлов источник — ``manual``.
    """
    tracked = {m.get("filename"): m for m in inst.get("mods", []) if m.get("filename")}
    base = instance_dir(inst)
    out: list[dict] = []
    for sub, ptype in CONTENT_FOLDERS.items():
        folder = base / sub
        if not folder.exists():
            continue
        for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_file():
                continue
            meta = tracked.get(p.name) or {}
            out.append({
                "filename": p.name,
                "subfolder": sub,
                "type": ptype,
                "name": meta.get("name") or p.name,
                "source": meta.get("source") or "manual",
                "project_id": meta.get("project_id"),
                "icon": meta.get("icon"),
            })
    return out


def instance_icon_entries(inst: dict, limit: int = 5) -> list[dict]:
    """До `limit` файлов сборки для коллажа логотипов — по файлам на диске
    (моды раньше паков/шейдеров). Стабильно-случайный порядок (seed = id сборки)."""
    files = instance_mods_on_disk(inst)
    mods = [f for f in files if f["type"] == "mod"]
    others = [f for f in files if f["type"] != "mod"]
    rnd = random.Random(str(inst.get("id", "")))
    rnd.shuffle(mods)
    rnd.shuffle(others)
    return (mods + others)[:limit]


def file_sha1(path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def modrinth_files_by_hashes(hashes: list[str]) -> dict:
    """Опознать файлы по SHA1 через Modrinth: {sha1: version}. Пусто при ошибке."""
    if not hashes:
        return {}
    try:
        r = _session.post(f"{M_BASE}/version_files",
                          json={"hashes": hashes, "algorithm": "sha1"}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def modrinth_projects(ids: list) -> dict:
    """Несколько проектов Modrinth разом: {id: project}. Пусто при ошибке."""
    if not ids:
        return {}
    try:
        r = _session.get(f"{M_BASE}/projects", params={"ids": json.dumps(ids)}, timeout=20)
        r.raise_for_status()
        return {p["id"]: p for p in r.json()}
    except Exception:
        return {}


def instance_icon_map(inst: dict) -> dict:
    """{имя_файла: url_логотипа} для контента сборки.

    Сначала логотипы из записей лаунчера; остальные файлы (включая добавленные
    вручную) опознаём по SHA1 через Modrinth — всего два запроса на сборку.
    Сетевой вызов: запускать в фоне.
    """
    result = {}
    for m in inst.get("mods", []):
        if m.get("filename") and m.get("icon"):
            result[m["filename"]] = m["icon"]
    base = instance_dir(inst)
    need = {}  # sha1 -> filename
    for sub in CONTENT_FOLDERS:
        folder = base / sub
        if not folder.exists():
            continue
        for p in folder.iterdir():
            if not p.is_file() or p.name in result:
                continue
            try:
                need[file_sha1(p)] = p.name
            except OSError:
                continue
    if need:
        versions = modrinth_files_by_hashes(list(need.keys()))
        proj_of = {h: v.get("project_id") for h, v in versions.items()
                   if isinstance(v, dict) and v.get("project_id")}
        projects = modrinth_projects(list(set(proj_of.values())))
        for h, fn in need.items():
            proj = projects.get(proj_of.get(h))
            if proj and proj.get("icon_url"):
                result[fn] = proj["icon_url"]
    return result


def delete_content_file(inst: dict, filename: str, subfolder: str) -> None:
    """Удалить файл контента сборки с диска и убрать запись из списка лаунчера."""
    p = instance_dir(inst) / subfolder / filename
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass  # файл занят/уже удалён — запись всё равно вычистим
    inst["mods"] = [m for m in inst.get("mods", []) if m.get("filename") != filename]


# --------------------------------------------------------------------------- #
#  Менеджер сборки: миры, сервера, скриншоты, открыть папку
# --------------------------------------------------------------------------- #
def open_in_os(path) -> None:
    """Открыть файл/папку штатным средством ОС (проводник, просмотрщик и т.п.)."""
    p = str(path)
    try:
        if sys.platform == "win32":
            os.startfile(p)            # type: ignore[attr-defined]  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


def _dedup_dir(folder: Path, name: str) -> Path:
    """Вернуть несуществующий путь folder/name, добавляя (2), (3)… при коллизии."""
    dest = folder / name
    i = 2
    while dest.exists():
        dest = folder / f"{name} ({i})"
        i += 1
    return dest


# ---- Миры ----
def list_worlds(inst: dict) -> list[dict]:
    """Миры сборки — папки в saves/ (мир = папка, обычно с level.dat)."""
    saves = instance_dir(inst) / "saves"
    if not saves.exists():
        return []
    out = []
    for d in sorted(saves.iterdir(), key=lambda x: x.name.lower()):
        if d.is_dir():
            out.append({"name": d.name, "folder": d.name,
                        "has_level": (d / "level.dat").exists()})
    return out


def export_world(inst: dict, world_folder: str, dest_zip) -> Path:
    """Запаковать мир в zip (внутри — папка мира, как ждут лаунчеры/импорт)."""
    src = instance_dir(inst) / "saves" / world_folder
    if not src.is_dir():
        raise RuntimeError("Мир не найден")
    dest = Path(dest_zip)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, Path(world_folder) / f.relative_to(src))
    return dest


def import_world(inst: dict, src_zip) -> str:
    """Распаковать zip с миром в saves/. Возвращает имя добавленного мира."""
    saves = instance_dir(inst) / "saves"
    saves.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpd = Path(tmp)
        with zipfile.ZipFile(src_zip) as z:
            z.extractall(tmpd)
        # ищем папку с level.dat — это корень мира
        level = next((p for p in tmpd.rglob("level.dat")), None)
        if level is not None:
            world_root = level.parent
            name = world_root.name if world_root != tmpd else Path(src_zip).stem
        else:
            # нет level.dat: берём единственную папку верхнего уровня, иначе сам zip
            tops = [p for p in tmpd.iterdir() if p.is_dir()]
            world_root = tops[0] if len(tops) == 1 else tmpd
            name = world_root.name if world_root != tmpd else Path(src_zip).stem
        dest = _dedup_dir(saves, name or "Импортированный мир")
        shutil.copytree(world_root, dest)
    return dest.name


def delete_world(inst: dict, world_folder: str) -> None:
    src = instance_dir(inst) / "saves" / world_folder
    if src.is_dir():
        shutil.rmtree(src, ignore_errors=True)


# ---- Сервера (servers.dat — несжатый NBT) ----
def _read_nbt(data: bytes):
    """Минимальный разбор NBT (big-endian, несжатый). Возвращает (имя_корня, значение)."""
    pos = 0

    def u1():
        nonlocal pos
        v = data[pos]; pos += 1
        return v

    def take(n):
        nonlocal pos
        b = data[pos:pos + n]; pos += n
        return b

    def s2():
        return struct.unpack(">H", take(2))[0]

    def read_str():
        return take(s2()).decode("utf-8", "replace")

    def read_payload(tag):
        nonlocal pos
        if tag == 1:   return struct.unpack(">b", take(1))[0]
        if tag == 2:   return struct.unpack(">h", take(2))[0]
        if tag == 3:   return struct.unpack(">i", take(4))[0]
        if tag == 4:   return struct.unpack(">q", take(8))[0]
        if tag == 5:   return struct.unpack(">f", take(4))[0]
        if tag == 6:   return struct.unpack(">d", take(8))[0]
        if tag == 7:   return take(struct.unpack(">i", take(4))[0])           # byte array
        if tag == 8:   return read_str()
        if tag == 9:                                                          # list
            it = u1(); ln = struct.unpack(">i", take(4))[0]
            return [read_payload(it) for _ in range(ln)]
        if tag == 10:                                                         # compound
            obj = {}
            while True:
                t = u1()
                if t == 0:
                    break
                nm = read_str()
                obj[nm] = read_payload(t)
            return obj
        if tag == 11:  return [struct.unpack(">i", take(4))[0] for _ in range(struct.unpack(">i", take(4))[0])]
        if tag == 12:  return [struct.unpack(">q", take(8))[0] for _ in range(struct.unpack(">i", take(4))[0])]
        raise ValueError(f"Неизвестный NBT-тег {tag}")

    root_tag = u1()
    if root_tag != 10:
        raise ValueError("Не NBT-compound в корне")
    name = read_str()
    return name, read_payload(10)


def list_servers(inst: dict) -> list[dict]:
    """Сохранённые сервера из servers.dat: список {name, ip}. Пусто при любой ошибке."""
    path = instance_dir(inst) / "servers.dat"
    if not path.exists():
        return []
    try:
        _, root = _read_nbt(path.read_bytes())
        servers = root.get("servers", []) if isinstance(root, dict) else []
        return [{"name": s.get("name", ""), "ip": s.get("ip", "")}
                for s in servers if isinstance(s, dict)]
    except Exception:
        return []


# ---- Скриншоты ----
_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def list_screenshots(inst: dict) -> list[Path]:
    """Скриншоты сборки (новые сверху) — файлы из screenshots/."""
    folder = instance_dir(inst) / "screenshots"
    if not folder.exists():
        return []
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _IMG_EXT]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def delete_path(path) -> None:
    """Удалить файл (скриншот и т.п.); без исключений наружу."""
    try:
        p = Path(path)
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def fetch_bytes(url: str, timeout: int = 20) -> bytes | None:
    """Скачать небольшой ресурс целиком (иконку мода и т.п.). None при любой ошибке.

    Идёт через общий ``_session`` — значит, учитывает прокси (важно для РФ: иконки
    Modrinth лежат на том же заблокированном CDN, что и сам сайт).
    """
    if not url:
        return None
    try:
        r = _session.get(url, timeout=timeout)
        r.raise_for_status()
        return r.content
    except Exception:
        return None


def download_file(url: str, dest: Path, progress_cb=None) -> None:
    with _session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb("Загрузка…", done, total)


# --------------------------------------------------------------------------- #
#  Modrinth API (v2) — без ключа, с авто-зависимостями
# --------------------------------------------------------------------------- #
M_BASE = "https://api.modrinth.com/v2"


def modrinth_search(query: str, project_type: str = "mod",
                    loader: str | None = None, mc_version: str | None = None,
                    limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """Возвращает (результаты, всего_найдено) — total нужен для постраничной навигации."""
    facets = [[f"project_type:{project_type}"]]
    if loader and loader != "vanilla" and project_type in ("mod", "modpack"):
        facets.append([f"categories:{loader}"])
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    params = {"query": query or "", "limit": limit, "offset": offset,
              "facets": json.dumps(facets),
              # без запроса сортируем по загрузкам → «самые популярные» сразу
              "index": "relevance" if query else "downloads"}
    r = _session.get(f"{M_BASE}/search", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("hits", []), int(data.get("total_hits", 0))


def _modrinth_get_version(project_id: str, loader: str | None, mc_version: str | None,
                          project_type: str) -> dict | None:
    params = {}
    if loader and loader != "vanilla" and project_type in ("mod", "modpack"):
        params["loaders"] = json.dumps([loader])
    if mc_version:
        params["game_versions"] = json.dumps([mc_version])
    r = _session.get(f"{M_BASE}/project/{project_id}/version", params=params, timeout=20)
    r.raise_for_status()
    versions = r.json()
    return versions[0] if versions else None


def _modrinth_project_title(project_id: str) -> str:
    try:
        r = _session.get(f"{M_BASE}/project/{project_id}", timeout=15)
        r.raise_for_status()
        return r.json().get("title", project_id)
    except Exception:
        return project_id


def modrinth_install(inst: dict, project: dict, project_type: str, progress_cb,
                     _seen: set | None = None, _is_dep: bool = False) -> dict | None:
    """Ставит мод и его обязательные зависимости. Уже установленное пропускает."""
    pid = project.get("project_id") or project.get("id") or project.get("slug")
    if _seen is None:
        _seen = set()
    if pid in _seen:
        return None
    _seen.add(pid)

    title = project.get("title") or (_modrinth_project_title(pid) if _is_dep else pid)
    ver = _modrinth_get_version(pid, inst.get("loader"), inst["mc_version"], project_type)
    if not ver:
        if _is_dep:
            return None  # зависимость без подходящей версии — пропускаем, не валим всё
        raise RuntimeError("Нет подходящего файла под версию/загрузчик этой сборки")

    files = ver.get("files", [])
    file = next((f for f in files if f.get("primary")), files[0] if files else None)
    entry = None
    if file and not already_installed(inst, file["filename"]):
        progress_cb(f"Установка: {title}", 0, 0)
        folder = instance_dir(inst) / _content_subfolder(project_type)
        download_file(file["url"], folder / file["filename"], progress_cb)
        entry = {"source": "modrinth", "project_id": pid, "name": title,
                 "filename": file["filename"], "type": project_type, "dep": _is_dep,
                 "icon": project.get("icon_url")}
        inst.setdefault("mods", []).append(entry)

    # обязательные зависимости
    for dep in ver.get("dependencies", []):
        if dep.get("dependency_type") != "required":
            continue
        dep_pid = dep.get("project_id")
        if not dep_pid or dep_pid in _seen:
            continue
        try:
            modrinth_install(inst, {"project_id": dep_pid}, project_type, progress_cb, _seen, True)
        except Exception as e:
            print("Зависимость не установилась:", e)

    return entry


# --------------------------------------------------------------------------- #
#  CurseForge API (v1) — нужен ключ
# --------------------------------------------------------------------------- #
CF_BASE = "https://api.curseforge.com/v1"
CF_GAME = 432
CF_CLASS = {"mod": 6, "modpack": 4471, "resourcepack": 12, "shader": 6552, "world": 17}
CF_LOADER = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}

# --------------------------------------------------------------------------- #
#  Встроенный («бандл») ключ CurseForge — замаскирован
# --------------------------------------------------------------------------- #
# Чтобы CurseForge работал у всех пользователей без возни с личным ключом, сюда
# зашит общий ключ с https://console.curseforge.com — но НЕ открытым текстом,
# а как XOR+base64 блоб, который собирается обратно в рантайме (_bundled_key).
# В исходниках и в строках сборки самого ключа не видно.
#
# ⚠️ Это барьер от СЛУЧАЙНЫХ утечек (grep по коду/строкам), а НЕ криптозащита:
#    кто целенаправленно дизассемблирует клиент или перехватит заголовок
#    x-api-key при запросе — всё равно достанет ключ. Надёжно прятать ключ
#    можно только на сервере-релее (umclaunch.net из ТЗ). При злоупотреблении
#    CurseForge может отозвать ключ — тогда отвалится у всех сразу.
#
# Сменить ключ:  python tools/obfuscate_cf_key.py  → вставить новую строку ниже.
# Приоритет: личный ключ пользователя из Настроек важнее встроенного.
_VEIL = b"UnlimitedMC::cf::v1"
CF_BUNDLED_KEY_OBF = "not_today_bruh"


def _bundled_key() -> str:
    if not CF_BUNDLED_KEY_OBF:
        return ""
    try:
        raw = base64.b64decode(CF_BUNDLED_KEY_OBF.encode("ascii"))
        return bytes(b ^ _VEIL[i % len(_VEIL)] for i, b in enumerate(raw)).decode("utf-8")
    except Exception:
        return ""


def cf_key(cfg: dict) -> str:
    """Эффективный ключ: личный ключ пользователя, иначе встроенный (распакованный)."""
    return (cfg.get("curseforge_api_key") or "").strip() or _bundled_key()


def _cf_headers(key: str) -> dict:
    return {"x-api-key": key, "Accept": "application/json"}


class CurseForgeAuthError(RuntimeError):
    """Ключ CurseForge не принят (нет ключа / недействителен / закончилась квота)."""


def _cf_get(url: str, key: str, **kwargs):
    """GET к CurseForge с понятной ошибкой вместо сырого «403 Forbidden».

    Встроенный ключ может быть отозван или упереться в лимит — тогда отдаём
    дружелюбное сообщение со ссылкой на получение своего бесплатного ключа,
    а лаунчер продолжает работать (см. ТЗ: ошибки не роняют программу).
    """
    r = _session.get(url, headers=_cf_headers(key), timeout=20, **kwargs)
    if r.status_code in (401, 403):
        raise CurseForgeAuthError(
            "CurseForge отклонил запрос: ключ недействителен или закончилась квота.\n\n"
            "Возьми бесплатный ключ на console.curseforge.com и впиши его в "
            "Настройки → Ключ CurseForge."
        )
    if r.status_code == 429:
        raise RuntimeError("CurseForge: слишком много запросов, попробуй чуть позже.")
    r.raise_for_status()
    return r


def curseforge_search(key: str, query: str, project_type: str = "mod",
                      loader: str | None = None, mc_version: str | None = None,
                      limit: int = 20, offset: int = 0) -> tuple[list[dict], int]:
    """Возвращает (результаты, всего_найдено). Уже отсортировано по популярности."""
    if not key:
        raise CurseForgeAuthError(
            "Нет ключа CurseForge. Возьми бесплатный на console.curseforge.com и "
            "впиши его в Настройки → Ключ CurseForge.")
    params = {"gameId": CF_GAME, "classId": CF_CLASS.get(project_type, 6),
              "searchFilter": query or "", "pageSize": limit, "index": offset,
              "sortField": 2, "sortOrder": "desc"}
    if mc_version:
        params["gameVersion"] = mc_version
    if loader and loader in CF_LOADER and project_type in ("mod", "modpack"):
        params["modLoaderType"] = CF_LOADER[loader]
    r = _cf_get(f"{CF_BASE}/mods/search", key, params=params)
    body = r.json()
    total = int(body.get("pagination", {}).get("totalCount", 0))
    return body.get("data", []), total


def _cf_pick_file(key: str, mod_id: int, loader: str | None, mc_version: str | None) -> dict | None:
    params = {"pageSize": 50}
    if mc_version:
        params["gameVersion"] = mc_version
    if loader and loader in CF_LOADER:
        params["modLoaderType"] = CF_LOADER[loader]
    r = _cf_get(f"{CF_BASE}/mods/{mod_id}/files", key, params=params)
    files = r.json().get("data", [])
    for f in files:
        if f.get("downloadUrl"):
            return f
    if files:
        raise RuntimeError("Этот мод запретил скачивание вне сайта CurseForge")
    return None


def curseforge_install(key: str, inst: dict, project: dict, project_type: str, progress_cb) -> dict:
    mod_id = project.get("id")
    file = _cf_pick_file(key, mod_id, inst.get("loader"), inst["mc_version"])
    if not file:
        raise RuntimeError("Нет подходящего файла под версию/загрузчик этой сборки")
    if already_installed(inst, file["fileName"]):
        return {"source": "curseforge", "project_id": mod_id, "name": project.get("name"),
                "filename": file["fileName"], "type": project_type}
    folder = instance_dir(inst) / _content_subfolder(project_type)
    download_file(file["downloadUrl"], folder / file["fileName"], progress_cb)
    entry = {"source": "curseforge", "project_id": mod_id, "name": project.get("name") or file["fileName"],
             "filename": file["fileName"], "type": project_type, "dep": False,
             "icon": (project.get("logo") or {}).get("url")}
    inst.setdefault("mods", []).append(entry)
    return entry
