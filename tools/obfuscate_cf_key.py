"""
Обфускатор встроенного ключа CurseForge.

Зачем: чтобы ключ НЕ лежал в исходниках/сборке открытым текстом (его не найдут
простым grep'ом по строкам). Это барьер от случайных утечек, а НЕ настоящая
криптозащита — кто целенаправленно дизассемблирует клиент или перехватит
HTTP-заголовок, всё равно достанет ключ. Надёжно прятать ключ можно только на
сервере-релее.

Использование (ключ вводится локально, в чат/историю не попадает):

    python tools/obfuscate_cf_key.py
    # вставь ключ по запросу, нажми Enter

Скопируй полученную строку в umc_core.py → CF_BUNDLED_KEY_OBF.
"""
from __future__ import annotations

import sys
import base64
import getpass

# Та же «вуаль», что и в umc_core._deobf. Это не секрет и не безопасность —
# просто чтобы блоб не совпадал с чистым base64 ключа.
_VEIL = b"UnlimitedMC::cf::v1"


def obfuscate(key: str) -> str:
    raw = key.encode("utf-8")
    xored = bytes(b ^ _VEIL[i % len(_VEIL)] for i, b in enumerate(raw))
    return base64.b64encode(xored).decode("ascii")


def _deobfuscate(blob: str) -> str:
    raw = base64.b64decode(blob.encode("ascii"))
    return bytes(b ^ _VEIL[i % len(_VEIL)] for i, b in enumerate(raw)).decode("utf-8")


def _read_key() -> str:
    # 1) аргументом командной строки
    if len(sys.argv) > 1:
        return sys.argv[1].strip()
    # 2) скрытый ввод (обычный терминал)
    try:
        return getpass.getpass("CurseForge key (ввод скрыт): ").strip()
    except Exception:
        pass
    # 3) запасной видимый ввод — для Run-консоли PyCharm, где getpass не работает
    try:
        return input("CurseForge key: ").strip()
    except EOFError:
        return ""


def main() -> None:
    key = _read_key()
    if not key:
        print("Пустой ключ — нечего обфусцировать.")
        return
    blob = obfuscate(key)
    assert _deobfuscate(blob) == key, "round-trip failed"
    print("\nВставь это в umc_core.py:\n")
    print(f'CF_BUNDLED_KEY_OBF = "{blob}"')


if __name__ == "__main__":
    main()
