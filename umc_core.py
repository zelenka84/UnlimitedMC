"""
UnlimitedMC — ядро (вся логика без интерфейса).

Зависит от: minecraft-launcher-lib, requests.
Можно использовать отдельно от GUI (для тестов).
"""
from __future__ import annotations

import os
import json
import uuid
import base64
import hashlib
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
    ram = int(cfg.get("ram_mb", 4096))
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
                    limit: int = 20) -> list[dict]:
    facets = [[f"project_type:{project_type}"]]
    if loader and loader != "vanilla" and project_type in ("mod", "modpack"):
        facets.append([f"categories:{loader}"])
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    params = {"query": query or "", "limit": limit, "facets": json.dumps(facets)}
    r = _session.get(f"{M_BASE}/search", params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("hits", [])


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
                 "filename": file["filename"], "type": project_type, "dep": _is_dep}
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
CF_BUNDLED_KEY_OBF = "cVwNTVxZUAYQNxsCa1o2D3c0Rw8PVCgXBwYOSwIyXnYPHmJQM1cTQT1QDzgHF1cgC3IVNyFeCwVcYgMZ"


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
                      limit: int = 20) -> list[dict]:
    if not key:
        raise CurseForgeAuthError(
            "Нет ключа CurseForge. Возьми бесплатный на console.curseforge.com и "
            "впиши его в Настройки → Ключ CurseForge.")
    params = {"gameId": CF_GAME, "classId": CF_CLASS.get(project_type, 6),
              "searchFilter": query or "", "pageSize": limit, "sortField": 2, "sortOrder": "desc"}
    if mc_version:
        params["gameVersion"] = mc_version
    if loader and loader in CF_LOADER and project_type in ("mod", "modpack"):
        params["modLoaderType"] = CF_LOADER[loader]
    r = _cf_get(f"{CF_BASE}/mods/search", key, params=params)
    return r.json().get("data", [])


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
             "filename": file["fileName"], "type": project_type, "dep": False}
    inst.setdefault("mods", []).append(entry)
    return entry
