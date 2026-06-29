"""Сгенерировать иконку приложения assets/UnlimitedMC.ico из знака-логотипа.

ICO собирается вручную из PNG-кадров нескольких размеров (без зависимостей,
кроме PySide6, которая и так нужна лаунчеру). Запуск:

    python tools/make_icon.py
"""
import os
import sys
import struct
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QBuffer, QByteArray
from ui_kit import icon_pixmap

SIZES = [16, 24, 32, 48, 64, 128, 256]


def png_bytes(size: int) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    icon_pixmap(size).save(buf, "PNG")
    buf.close()
    return bytes(ba)


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 — нужен для QPixmap
    frames = [(s, png_bytes(s)) for s in SIZES]

    header = struct.pack("<HHH", 0, 1, len(frames))  # reserved, type=icon, count
    entries = b""
    data = b""
    offset = 6 + 16 * len(frames)
    for s, png in frames:
        wh = 0 if s >= 256 else s        # 0 в ICO означает 256
        entries += struct.pack("<BBBBHHII", wh, wh, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png

    out = ROOT / "assets" / "UnlimitedMC.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(header + entries + data)
    # превью для глаз
    icon_pixmap(256).save(str(ROOT / "assets" / "icon_preview.png"), "PNG")
    print(f"wrote {out} ({out.stat().st_size} bytes), sizes={SIZES}")


if __name__ == "__main__":
    main()
