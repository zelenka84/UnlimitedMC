"""Собрать UnlimitedMC.exe через PyInstaller.

Запуск из корня проекта:

    python tools/build_exe.py

Нужен PyInstaller:  pip install pyinstaller
Результат:          dist/UnlimitedMC.exe  (один файл, без консоли)
"""
import subprocess
import sys
from pathlib import Path

try:                       # консоль Windows бывает cp1251 — не падаем на юникоде
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:          # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    # 1) свежая иконка из логотипа
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)

    # 2) сборка
    cmd = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile",                 # один .exe
        "--windowed",                # GUI без чёрной консоли
        "--name", "UnlimitedMC",
        "--icon", str(ROOT / "assets" / "UnlimitedMC.ico"),
        # mll грузит классы загрузчиков динамически — соберём все подмодули
        "--collect-submodules", "minecraft_launcher_lib",
        str(ROOT / "unlimitedmc.py"),
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))
    print("\nГотово ->", ROOT / "dist" / "UnlimitedMC.exe")


if __name__ == "__main__":
    main()
