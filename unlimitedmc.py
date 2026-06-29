"""
UnlimitedMC — интерфейс (PySide6), дизайн по макету v6.

Запуск:  python unlimitedmc.py
Требует: PySide6, minecraft-launcher-lib, requests  (см. requirements.txt)

Перенесён полный визуал макета: тёмная/светлая темы, акцент→монохромный
градиент (вживую), логотип-знак, реактивный ambient-фон, сворачиваемый по
наведению сайдбар, локализация RU/EN, Главная с приветствием/новостями/статьёй.

Аккаунты UnlimitedMC и онлайн-сообщество пока не подключены: везде, где нужен
вход, стоит честная заглушка «Вход недоступен». Локальный игровой ник (офлайн)
с аккаунтом не связан и работает как раньше.
"""
from __future__ import annotations

import sys
import random
import inspect
import traceback

from PySide6.QtCore import (
    Qt, QThreadPool, QRunnable, QObject, Signal, Slot, QPropertyAnimation,
    QEasingCurve, QEvent, QSize, QRectF,
)
from PySide6.QtGui import QFont, QColor, QPixmap, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QComboBox, QSpinBox, QScrollArea,
    QFrame, QStackedWidget, QDialog, QFormLayout, QMessageBox, QFileDialog,
    QProgressBar, QButtonGroup, QSizePolicy, QGraphicsOpacityEffect, QSizeGrip,
    QGraphicsDropShadowEffect,
)

import umc_core as core
import ui_content as C
from ui_kit import (
    Logo, Ambient, Clickable, Toggle, FlowLayout, build_qss, nav_icon, win_icon, ui_icon,
    app_icon, fade_widget, stagger_in, raise_on_hover, glow_on_hover,
    THEMES, ACCENTS, SOURCE_ACCENT,
)

NAV_ICON_SIZE = 19  # размер иконок боковой панели (вровень с подписями)


# --------------------------------------------------------------------------- #
#  Фоновые задачи (тяжёлые операции не вешают интерфейс, ошибки не валят его)
# --------------------------------------------------------------------------- #
class WorkerSignals(QObject):
    progress = Signal(str, int, int)
    error = Signal(str)
    result = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = WorkerSignals()
        # НЕ позволяем C++ удалять раннабл за нашей спиной: его жизнь держит
        # Python-ссылка (MainWindow._workers) до сигнала finished. Иначе PySide
        # мог собрать Worker/WorkerSignals прямо во время работы потока — отсюда
        # «Signal source has been deleted» и редкие, но реальные вылеты лаунчера.
        self.setAutoDelete(False)

    def _emit(self, name, *a):
        # При закрытии лаунчера во время фоновой задачи C++-объект signals может уже
        # быть снесён — тогда emit бросает RuntimeError. Гасим: программа всё равно
        # закрывается, ронять её на выходе незачем.
        try:
            getattr(self.signals, name).emit(*a)
        except RuntimeError:
            pass

    @Slot()
    def run(self):
        try:
            params = inspect.signature(self.fn).parameters
            if "progress_cb" in params and "progress_cb" not in self.kwargs:
                self.kwargs["progress_cb"] = lambda s, v, m: self._emit("progress", s, int(v), int(m))
            self._emit("result", self.fn(*self.args, **self.kwargs))
        except Exception as e:  # noqa: BLE001 — ловим всё, лаунчер не должен падать
            traceback.print_exc()
            self._emit("error", str(e))
        finally:
            self._emit("finished")


def _pager_numbers(cur: int, pages: int) -> list[int | None]:
    """Номера страниц для пейджера: первая, последняя и окно вокруг текущей.

    None между числами означает «…» (пропуск). Напр. при cur=5, pages=20:
    0 … 4 5 6 … 19.
    """
    if pages <= 7:
        return list(range(pages))
    wanted = sorted({0, pages - 1, cur - 1, cur, cur + 1}
                    & set(range(pages)) | {0, pages - 1})
    out: list[int | None] = []
    prev = None
    for p in wanted:
        if prev is not None and p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


def rounded_icon(data: bytes, size: int = 52, radius: int = 11) -> QPixmap:
    """Собрать квадратную скруглённую иконку мода из скачанных байтов.

    Вызывать ТОЛЬКО в GUI-потоке (QPixmap/QPainter не потокобезопасны). Поддержку
    форматов (webp у Modrinth, png/jpg) даёт Qt. Пустой QPixmap — если не распознали.
    """
    src = QPixmap()
    if not data or not src.loadFromData(data):
        return QPixmap()
    dpr = 2
    px = size * dpr
    src = src.scaled(px, px, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    if src.width() != px or src.height() != px:  # центр-кроп до квадрата
        src = src.copy((src.width() - px) // 2, (src.height() - px) // 2, px, px)
    out = QPixmap(px, px)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), radius * dpr, radius * dpr)
    p.setClipPath(path)
    p.drawPixmap(0, 0, src)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out


# --------------------------------------------------------------------------- #
#  Сайдбар: сворачивается в иконки, раскрывается по наведению
# --------------------------------------------------------------------------- #
SIDE_COLLAPSED = 70
SIDE_EXPANDED = 238


class Sidebar(QFrame):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDE_COLLAPSED)
        self._anim = QPropertyAnimation(self, b"maximumWidth")
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._anim.valueChanged.connect(lambda v: self.setMinimumWidth(int(v)))

    def enterEvent(self, e):
        self._animate(SIDE_EXPANDED)
        self.win.set_sidebar_expanded(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate(SIDE_COLLAPSED)
        self.win.set_sidebar_expanded(False)
        super().leaveEvent(e)

    def _animate(self, target):
        self._anim.stop()
        self._anim.setStartValue(self.width())
        self._anim.setEndValue(target)
        self._anim.start()


# --------------------------------------------------------------------------- #
#  Кастомная рамка окна (frameless) — под тему и стилистику лаунчера
# --------------------------------------------------------------------------- #
class WinButton(QPushButton):
    """Кнопка управления окном с векторной иконкой, перекрашивающейся на ховере."""
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.name = name
        self._normal = self._hover = None
        self.setFixedSize(34, 26)
        self.setIconSize(QSize(15, 15))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def set_icons(self, normal: QIcon, hover: QIcon):
        self._normal, self._hover = normal, hover
        self.setIcon(self._hover if self.underMouse() else self._normal)

    def enterEvent(self, e):
        if self._hover:
            self.setIcon(self._hover)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._normal:
            self.setIcon(self._normal)
        super().leaveEvent(e)


class TitleBar(QFrame):
    """Заголовок окна в стиле макета: бренд слева, кнопки управления справа.

    Перетаскивание окна — нативное (`startSystemMove`, со снэппингом Windows),
    двойной клик — развернуть/восстановить. Внешний вид целиком под тему через QSS.
    """
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("titlebar")
        self.setFixedHeight(44)
        self._maximized = False
        self._muted = "#8A92A6"
        self._text = "#E7EAF2"
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 8, 0)
        h.setSpacing(10)

        self.logo = Logo(win.accent, 24)
        h.addWidget(self.logo)
        self.wordmark = QLabel()
        self.wordmark.setObjectName("brand")
        self.wordmark.setTextFormat(Qt.RichText)
        h.addWidget(self.wordmark)
        h.addStretch(1)
        # бренд не перехватывает мышь — вся полоса остаётся «ручкой» перетаскивания
        self.logo.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.wordmark.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.btn_min = WinButton("min")
        self.btn_max = WinButton("max")
        self.btn_close = WinButton("close")
        self.btn_min.setObjectName("winBtn")
        self.btn_max.setObjectName("winBtn")
        self.btn_close.setObjectName("winClose")
        self.btn_min.clicked.connect(win.showMinimized)
        self.btn_max.clicked.connect(win.toggle_max_restore)
        self.btn_close.clicked.connect(win.close)
        h.addWidget(self.btn_min)
        h.addWidget(self.btn_max)
        h.addWidget(self.btn_close)

    def apply_icons(self, muted: str, text: str):
        """Перекрасить иконки управления под тему (вызывается из apply_theme)."""
        self._muted, self._text = muted, text
        self.btn_min.set_icons(win_icon("min", muted), win_icon("min", text))
        self._refresh_max_icon()
        self.btn_close.set_icons(win_icon("close", muted), win_icon("close", "#FFFFFF"))

    def _refresh_max_icon(self):
        name = "restore" if self._maximized else "max"
        self.btn_max.set_icons(win_icon(name, self._muted), win_icon(name, self._text))

    def set_maximized(self, mx: bool):
        self._maximized = mx
        self._refresh_max_icon()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.window().isMaximized():
            wh = self.window().windowHandle()
            if wh is not None:
                wh.startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.win.toggle_max_restore()
        super().mouseDoubleClickEvent(e)


# --------------------------------------------------------------------------- #
#  Стилизованное окно-сообщение (ошибки / инфо / подтверждение) под тему
# --------------------------------------------------------------------------- #
class MessageDialog(QDialog):
    _ICONS = {"error": "✕", "question": "❓", "info": "💡", "warning": "⚠️"}

    def __init__(self, win, kind, title, text, confirm=False, danger=False):
        super().__init__(win)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        card = QFrame()
        card.setObjectName("msgCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 18)
        sc = QColor(0, 0, 0)
        sc.setAlphaF(0.5)
        shadow.setColor(sc)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(24, 22, 24, 20)
        v.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        icon = QLabel(self._ICONS.get(kind, "💡"))
        icon.setObjectName("msgIconError" if kind == "error" else "msgIcon")
        icon.setFixedSize(48, 48)
        icon.setAlignment(Qt.AlignCenter)
        head.addWidget(icon, 0, Qt.AlignTop)
        tt = QLabel(title)
        tt.setObjectName("msgTitle")
        tt.setWordWrap(True)
        head.addWidget(tt, 1)
        v.addLayout(head)

        msg = QLabel(text)
        msg.setObjectName("msgText")
        msg.setWordWrap(True)
        v.addWidget(msg)

        row = QHBoxLayout()
        row.addStretch(1)
        row.setSpacing(9)
        if confirm:
            cancel = QPushButton(win.L("Отмена", "Cancel"))
            cancel.setObjectName("ghost")
            cancel.clicked.connect(self.reject)
            row.addWidget(cancel)
        ok = QPushButton(win.L("Да", "Yes") if confirm else win.L("Понятно", "Got it"))
        ok.setObjectName("danger" if danger else "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        v.addLayout(row)
        self.setMinimumWidth(400)

    def showEvent(self, e):
        super().showEvent(e)
        par = self.parentWidget()
        if par is not None:
            c = par.frameGeometry().center()
            self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
        if not getattr(self, "_faded", False):
            self._faded = True
            fade_widget(self, duration=180, start=0.0)


# --------------------------------------------------------------------------- #
#  Создание сборки
# --------------------------------------------------------------------------- #
class CreateInstanceDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(win.L("Новая сборка", "New instance"))
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        form.setContentsMargins(24, 24, 24, 20)
        form.setSpacing(12)

        self.name = QLineEdit()
        self.name.setPlaceholderText(win.L("например, Моя сборка", "e.g. My pack"))
        self.version = QComboBox()
        self.version.setEditable(True)
        self.version.addItems(C.COMMON_VERSIONS)
        self.loader = QComboBox()
        self.loader.addItems([C.LOADER_NAMES.get(l, l.capitalize()) for l in C.LOADERS])

        form.addRow(win.L("Название", "Name"), self.name)
        form.addRow(win.L("Версия", "Version"), self.version)
        form.addRow(win.L("Загрузчик", "Loader"), self.loader)

        row = QHBoxLayout()
        cancel = QPushButton(win.L("Отмена", "Cancel"))
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        ok = QPushButton(win.L("Создать", "Create"))
        ok.setObjectName("primary")
        ok.clicked.connect(self.accept)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(ok)
        form.addRow(row)

    def values(self):
        return (self.name.text(), self.version.currentText().strip(),
                C.LOADERS[self.loader.currentIndex()])

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_faded", False):
            self._faded = True
            fade_widget(self, duration=200, start=0.0)


# --------------------------------------------------------------------------- #
#  Менеджер сборки — настройки, контент, миры, сервера, скриншоты
# --------------------------------------------------------------------------- #
class InstanceManagerDialog(QDialog):
    """Одно окно для всей сборки: настройки, установленный контент (моды/паки/
    шейдеры по файлам на диске), миры (импорт/экспорт), сохранённые сервера и
    скриншоты. Истина по файлам — диск, поэтому видно и добавленное вручную."""

    def __init__(self, win, inst):
        super().__init__(win)
        self.win = win
        self.inst = inst
        tok = THEMES[win.theme]
        self.c_muted, self.c_text, self.c_accent = tok["muted"], tok["text"], win.accent
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        card = QFrame()
        card.setObjectName("msgCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 18)
        sc = QColor(0, 0, 0)
        sc.setAlphaF(0.5)
        shadow.setColor(sc)
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        v = QVBoxLayout(card)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)

        head = QHBoxLayout()
        htext = QVBoxLayout()
        htext.setSpacing(2)
        title = QLabel(inst["name"])
        title.setObjectName("msgTitle")
        title.setWordWrap(True)
        n = core.count_instance_mods(inst)
        subt = QLabel(f'{inst["loader"]} · {inst["mc_version"]} · {n} {C.T[win.lang]["mods"]}')
        subt.setObjectName("muted")
        htext.addWidget(title)
        htext.addWidget(subt)
        head.addLayout(htext, 1)
        close = QPushButton(win.L("Закрыть", "Close"))
        close.setObjectName("ghost")
        close.clicked.connect(self.accept)
        head.addWidget(close, 0, Qt.AlignTop)
        v.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        nav = QVBoxLayout()
        nav.setSpacing(4)
        self.stack = QStackedWidget()
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        sections = [
            ("settings", "Настройки", "Settings", self._page_settings),
            ("content",  "Контент",   "Content",  self._page_content),
            ("worlds",   "Миры",      "Worlds",   self._page_worlds),
            ("servers",  "Сервера",   "Servers",  self._page_servers),
            ("shots",    "Скриншоты", "Screenshots", self._page_shots),
        ]
        for i, (key, ru, en, builder) in enumerate(sections):
            btn = QPushButton("  " + win.L(ru, en))
            btn.setObjectName("mgrNav")
            btn.setCheckable(True)
            btn.setIcon(ui_icon(key, self.c_muted, 17))
            btn.setIconSize(QSize(17, 17))
            btn.setMinimumWidth(176)
            btn.clicked.connect(lambda _=False, idx=i: self.stack.setCurrentIndex(idx))
            self._group.addButton(btn)
            nav.addWidget(btn)
            self.stack.addWidget(builder())
            if i == 0:
                btn.setChecked(True)
        nav.addStretch(1)
        nav_host = QWidget()
        nav_host.setLayout(nav)
        nav_host.setFixedWidth(186)
        body.addWidget(nav_host, 0)
        sep = QFrame()
        sep.setObjectName("vsep")
        sep.setFixedWidth(1)
        body.addWidget(sep)
        body.addWidget(self.stack, 1)
        v.addLayout(body, 1)

        self.setMinimumSize(820, 580)
        self._fill_content()
        self._fill_worlds()
        self._fill_servers()
        self._fill_shots()

    # ---------- мелкие помощники ----------
    def _logo_label(self, icon_key=None):
        """Скруглённая плитка-логотип 40×40 (с запасной векторной иконкой)."""
        lab = QLabel()
        lab.setObjectName("rowLogo")
        lab.setFixedSize(40, 40)
        lab.setAlignment(Qt.AlignCenter)
        if icon_key:
            lab.setPixmap(ui_icon(icon_key, self.c_muted, 20).pixmap(QSize(20, 20)))
        return lab

    def _icon_btn(self, key, ru, en, color, *, danger=False, primary=False):
        b = QPushButton("  " + self.win.L(ru, en))
        b.setObjectName("remove" if danger else ("primary" if primary else "ghost"))
        b.setIcon(ui_icon(key, color, 15))
        b.setIconSize(QSize(15, 15))
        return b

    def _list_section(self, intro_ru, intro_en, top_widget=None):
        """Каркас раздела со списком: заголовок-подсказка + (опц. кнопка) + скролл."""
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(2, 0, 2, 0)
        pv.setSpacing(12)
        intro = QLabel(self.win.L(intro_ru, intro_en))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        if top_widget is not None:
            head = QHBoxLayout()
            head.addWidget(intro, 1)
            head.addWidget(top_widget, 0)
            pv.addLayout(head)
        else:
            pv.addWidget(intro)
        box = QVBoxLayout()
        box.setSpacing(8)
        host = QWidget()
        host.setLayout(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        pv.addWidget(scroll, 1)
        return page, box

    @staticmethod
    def _clear(box):
        while box.count():
            it = box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _empty(self, box, ru, en):
        lab = QLabel(self.win.L(ru, en))
        lab.setObjectName("muted")
        lab.setWordWrap(True)
        box.addWidget(lab)

    def _row(self, icon_key, title, subtitle, buttons):
        return self._logo_row(self._logo_label(icon_key), title, subtitle, buttons)

    def _logo_row(self, logo, title, subtitle, buttons):
        row = QFrame()
        row.setObjectName("inst")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(13, 10, 13, 10)
        rl.setSpacing(12)
        rl.addWidget(logo, 0, Qt.AlignVCenter)
        box = QVBoxLayout()
        box.setSpacing(2)
        nm = QLabel(title)
        nm.setObjectName("newsTitle")
        nm.setWordWrap(True)
        box.addWidget(nm)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("muted")
            sub.setWordWrap(True)
            box.addWidget(sub)
        rl.addLayout(box, 1)
        for b in buttons:
            rl.addWidget(b, 0, Qt.AlignVCenter)
        return row

    # ---------- Настройки ----------
    def _page_settings(self):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(2, 4, 2, 0)
        f.setSpacing(12)
        self.s_name = QLineEdit(self.inst["name"])
        f.addRow(self.win.L("Название", "Name"), self.s_name)
        info = QLabel(f'{self.inst["loader"]} · {self.inst["mc_version"]}')
        info.setObjectName("muted")
        f.addRow(self.win.L("Загрузчик и версия", "Loader & version"), info)
        self.s_ram = QSpinBox()
        self.s_ram.setRange(0, 65536)
        self.s_ram.setSingleStep(512)
        self.s_ram.setSuffix(" MB")
        self.s_ram.setValue(int(self.inst.get("ram_mb") or 0))
        f.addRow(self.win.L("Память сборки (0 = общая)", "Instance RAM (0 = global)"), self.s_ram)

        save = QPushButton(self.win.L("Сохранить", "Save"))
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings)
        openf = self._icon_btn("folder", "Открыть папку", "Open folder", self.c_muted)
        openf.clicked.connect(lambda: core.open_in_os(core.instance_dir(self.inst)))
        r1 = QHBoxLayout()
        r1.addWidget(save)
        r1.addWidget(openf)
        r1.addStretch(1)
        f.addRow(r1)

        danger = self._icon_btn("trash", "Удалить сборку", "Delete instance", "#FF5C7A", danger=True)
        danger.clicked.connect(self._delete_instance)
        f.addRow(QLabel(""), danger)
        return page

    def _save_settings(self):
        self.inst["name"] = self.s_name.text().strip() or self.inst["name"]
        self.inst["ram_mb"] = int(self.s_ram.value())
        core.save_config(self.win.cfg)
        self.win.refresh_instances()
        self.win.toast(self.win.L("Сборка сохранена", "Instance saved"))

    def _delete_instance(self):
        if not self.win.msg_confirm(
                self.win.L("Удалить сборку", "Delete instance"),
                self.win.L(f'Удалить сборку «{self.inst["name"]}»? Это действие необратимо.',
                           f'Delete instance “{self.inst["name"]}”? This cannot be undone.')):
            return
        core.remove_instance(self.win.cfg, self.inst["id"])
        core.save_config(self.win.cfg)
        self.win.refresh_instances()
        self.accept()

    # ---------- Контент (моды/паки/шейдеры) ----------
    def _page_content(self):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(2, 0, 2, 0)
        pv.setSpacing(10)
        intro = QLabel(self.win.L(
            "Моды, ресурспаки и шейдеры — по файлам в папках сборки. Удалить можно любой, даже добавленный вручную.",
            "Mods, resource packs and shaders found on disk. You can delete any — even ones added manually."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        pv.addWidget(intro)
        # чипы-переходы к разделам (Моды / Ресурспаки / Шейдеры)
        self._content_chips = QHBoxLayout()
        self._content_chips.setSpacing(8)
        self._content_chips_host = QWidget()
        self._content_chips_host.setLayout(self._content_chips)
        pv.addWidget(self._content_chips_host)
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        host = QWidget()
        host.setLayout(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        pv.addWidget(scroll, 1)
        self._content_box = box
        self._content_scroll = scroll
        return page

    _TYPE_FALLBACK = {"mod": "content", "resourcepack": "shots", "shader": "worlds"}

    def _fill_content(self):
        self._clear(self._content_box)
        self._clear(self._content_chips)
        self._content_logos = {}
        files = core.instance_mods_on_disk(self.inst)
        if not files:
            self._content_chips_host.setVisible(False)
            self._empty(self._content_box, "В папках сборки пока нет контента.",
                        "No content in this instance’s folders yet.")
            return
        groups = [("mod", "Моды", "Mods"),
                  ("resourcepack", "Ресурспаки", "Resource packs"),
                  ("shader", "Шейдеры", "Shaders")]
        headers = {}
        for t, ru, en in groups:
            items = [f for f in files if f["type"] == t]
            if not items:
                continue
            hdr = QLabel(f'{self.win.L(ru, en).upper()} · {len(items)}')
            hdr.setObjectName("seth")
            self._content_box.addWidget(hdr)
            headers[t] = hdr
            for fobj in items:
                row, logo = self._content_row(fobj)
                self._content_logos[fobj["filename"]] = logo
                self._content_box.addWidget(row)
        self._content_box.addStretch(1)
        # чипы-переходы к присутствующим разделам (нужны, когда типов больше одного)
        for t, ru, en in groups:
            if t not in headers:
                continue
            chip = QPushButton(self.win.L(ru, en))
            chip.setObjectName("chip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, h=headers[t]: self._scroll_to(h))
            self._content_chips.addWidget(chip)
        self._content_chips.addStretch(1)
        self._content_chips_host.setVisible(len(headers) > 1)
        # настоящие логотипы (в т.ч. опознанные по хэшу для ручных модов) — в фоне
        self.win._instance_icons(self.inst, self._apply_content_logos)

    def _scroll_to(self, header):
        bar = self._content_scroll.verticalScrollBar()
        anim = QPropertyAnimation(bar, b"value", self)
        anim.setDuration(260)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(bar.value())
        anim.setEndValue(max(0, header.y() - 4))
        anim.start()
        self._scroll_anim = anim   # держим ссылку, иначе анимацию соберёт GC

    def _apply_content_logos(self, m):
        for fn, lab in getattr(self, "_content_logos", {}).items():
            url = m.get(fn)
            if url:
                try:
                    self.win._load_icon(lab, url, 40, 11)
                except RuntimeError:
                    pass

    def _content_row(self, f):
        logo = self._logo_label(self._TYPE_FALLBACK.get(f["type"], "content"))
        if f["source"] == "manual":
            src = self.win.L("добавлен вручную", "added manually")
        else:
            src = self.win.L(f"из лаунчера · {f['source']}", f"from launcher · {f['source']}")
        rm = self._icon_btn("trash", "Удалить", "Remove", "#FF5C7A", danger=True)
        rm.clicked.connect(lambda _=False, ff=f: self._del_content(ff))
        return self._logo_row(logo, f["name"], f'{src} · {f["filename"]}', [rm]), logo

    def _del_content(self, f):
        if not self.win.msg_confirm(
                self.win.L("Удалить мод?", "Remove mod?"),
                self.win.L(f"Удалить файл «{f['filename']}» из сборки?\nОн будет удалён с диска.",
                           f"Delete “{f['filename']}” from this instance?\nThe file will be removed from disk.")):
            return
        core.delete_content_file(self.inst, f["filename"], f["subfolder"])
        core.save_config(self.win.cfg)
        self.win._inst_icon_map.pop(self.inst["id"], None)   # карта логотипов устарела
        self._fill_content()
        self.win.refresh_instances()

    # ---------- Миры ----------
    def _page_worlds(self):
        imp = self._icon_btn("import", "Импорт мира", "Import world", "#FFFFFF", primary=True)
        imp.clicked.connect(self._import_world)
        page, self._worlds_box = self._list_section(
            "Сохранённые миры этой сборки. Можно экспортировать в .zip, импортировать чужой или удалить.",
            "Saved worlds of this instance. Export to .zip, import another, or delete.", top_widget=imp)
        return page

    def _fill_worlds(self):
        self._clear(self._worlds_box)
        worlds = core.list_worlds(self.inst)
        if not worlds:
            self._empty(self._worlds_box, "В этой сборке пока нет миров.",
                        "This instance has no worlds yet.")
            return
        for w in worlds:
            sub = "" if w["has_level"] else self.win.L("нет level.dat", "no level.dat")
            exp = self._icon_btn("export", "Экспорт", "Export", self.c_muted)
            exp.clicked.connect(lambda _=False, ww=w: self._export_world(ww))
            rm = self._icon_btn("trash", "Удалить", "Delete", "#FF5C7A", danger=True)
            rm.clicked.connect(lambda _=False, ww=w: self._delete_world(ww))
            self._worlds_box.addWidget(self._row("worlds", w["name"], sub, [exp, rm]))
        self._worlds_box.addStretch(1)

    def _import_world(self):
        path, _ = QFileDialog.getOpenFileName(
            self, self.win.L("Импорт мира", "Import world"), "", "Zip (*.zip)")
        if not path:
            return
        try:
            name = core.import_world(self.inst, path)
            self._fill_worlds()
            self.win.refresh_instances()
            self.win.toast(self.win.L(f"Мир добавлен: {name}", f"World added: {name}"))
        except Exception as e:  # noqa: BLE001
            self.win.on_error(str(e))

    def _export_world(self, w):
        path, _ = QFileDialog.getSaveFileName(
            self, self.win.L("Экспорт мира", "Export world"), f'{w["name"]}.zip', "Zip (*.zip)")
        if not path:
            return
        try:
            dest = core.export_world(self.inst, w["folder"], path)
            self.win.toast(self.win.L(f"Мир сохранён: {dest.name}", f"World saved: {dest.name}"))
        except Exception as e:  # noqa: BLE001
            self.win.on_error(str(e))

    def _delete_world(self, w):
        if not self.win.msg_confirm(
                self.win.L("Удалить мир?", "Delete world?"),
                self.win.L(f"Удалить мир «{w['name']}»? Это действие необратимо.",
                           f"Delete world “{w['name']}”? This cannot be undone.")):
            return
        core.delete_world(self.inst, w["folder"])
        self._fill_worlds()

    # ---------- Сервера ----------
    def _page_servers(self):
        page, self._servers_box = self._list_section(
            "Сервера, сохранённые в игре (servers.dat).",
            "Servers saved in-game (servers.dat).")
        return page

    def _fill_servers(self):
        self._clear(self._servers_box)
        servers = core.list_servers(self.inst)
        if not servers:
            self._empty(self._servers_box, "Нет сохранённых серверов.", "No saved servers.")
            return
        for s in servers:
            self._servers_box.addWidget(self._row(
                "servers", s["name"] or s["ip"] or "—", s["ip"], []))
        self._servers_box.addStretch(1)

    # ---------- Скриншоты ----------
    def _page_shots(self):
        page = QWidget()
        pv = QVBoxLayout(page)
        pv.setContentsMargins(2, 0, 2, 0)
        pv.setSpacing(12)
        intro = QLabel(self.win.L("Скриншоты из игры. Клик — открыть, корзина — удалить.",
                                  "In-game screenshots. Click to open, trash to delete."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        pv.addWidget(intro)
        self._shots_flow = FlowLayout(hspacing=12, vspacing=12)
        host = QWidget()
        host.setLayout(self._shots_flow)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(host)
        pv.addWidget(scroll, 1)
        return page

    def _fill_shots(self):
        while self._shots_flow.count():
            it = self._shots_flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        shots = core.list_screenshots(self.inst)
        if not shots:
            self._empty_flow = QLabel(self.win.L("Скриншотов пока нет.", "No screenshots yet."))
            self._empty_flow.setObjectName("muted")
            self._shots_flow.addWidget(self._empty_flow)
            return
        for p in shots:
            self._shots_flow.addWidget(self._shot_card(p))

    def _shot_card(self, path):
        card = QFrame()
        card.setObjectName("shot")
        card.setFixedWidth(184)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(8, 8, 8, 8)
        cv.setSpacing(7)
        thumb = Clickable()
        thumb.setCursor(Qt.PointingHandCursor)
        tl = QVBoxLayout(thumb)
        tl.setContentsMargins(0, 0, 0, 0)
        img = QLabel()
        pm = QPixmap(str(path))
        if not pm.isNull():
            img.setPixmap(pm.scaledToWidth(168, Qt.SmoothTransformation))
        else:
            img.setText("🖼")
        img.setAlignment(Qt.AlignCenter)
        img.setFixedHeight(98)
        img.setStyleSheet("background:transparent;")
        tl.addWidget(img)
        thumb.clicked.connect(lambda _=path: core.open_in_os(path))
        cv.addWidget(thumb)
        row = QHBoxLayout()
        nm = QLabel(path.name)
        nm.setObjectName("muted")
        nm.setWordWrap(False)
        row.addWidget(nm, 1)
        rm = QPushButton()
        rm.setObjectName("ghost")
        rm.setFixedWidth(30)
        rm.setIcon(ui_icon("trash", "#FF5C7A", 15))
        rm.setIconSize(QSize(15, 15))
        rm.clicked.connect(lambda _=False, p=path: self._delete_shot(p))
        row.addWidget(rm, 0)
        cv.addLayout(row)
        return card

    def _delete_shot(self, path):
        if not self.win.msg_confirm(
                self.win.L("Удалить скриншот?", "Delete screenshot?"),
                self.win.L(f"Удалить «{path.name}»?", f"Delete “{path.name}”?")):
            return
        core.delete_path(path)
        self._fill_shots()

    def showEvent(self, e):
        super().showEvent(e)
        par = self.parentWidget()
        if par is not None:
            c = par.frameGeometry().center()
            self.move(c.x() - self.width() // 2, c.y() - self.height() // 2)
        if not getattr(self, "_faded", False):
            self._faded = True
            fade_widget(self, duration=180, start=0.0)


# --------------------------------------------------------------------------- #
#  Онбординг (первый запуск) — в стиле темы
# --------------------------------------------------------------------------- #
class OnboardingDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.accent = win.accent
        self.theme = win.theme
        self.lang = win.lang
        self.setWindowTitle(win.L("Добро пожаловать", "Welcome"))
        self.setMinimumWidth(440)
        v = QVBoxLayout(self)
        v.setContentsMargins(30, 28, 30, 26)
        v.setSpacing(14)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.logo = Logo(self.accent, 30)
        brand.addWidget(self.logo)
        wm = QLabel('Unlimited<b style="color:%s">MC</b>' % self.accent)
        wm.setTextFormat(Qt.RichText)
        wm.setStyleSheet("font-size:20px;font-weight:700;")
        brand.addWidget(wm)
        brand.addStretch(1)
        v.addLayout(brand)

        hi = QLabel(win.L("Современный лаунчер Minecraft с модами и сборками.\nДавай быстро настроим внешний вид.",
                          "A modern Minecraft launcher with mods and modpacks.\nLet’s quickly set up the look."))
        hi.setObjectName("muted")
        v.addWidget(hi)

        v.addWidget(self._lbl(win.L("Твой ник в игре", "Your in-game name")))
        self.nick = QLineEdit("" if win.local_name in ("Гость", "Guest", "Player") else win.local_name)
        self.nick.setPlaceholderText(win.L("например, Steve", "e.g. Steve"))
        v.addWidget(self.nick)

        v.addWidget(self._lbl(win.L("Тема", "Theme")))
        trow = QHBoxLayout()
        self.theme_btns = {}
        for val, ru, en in [("dark", "Тёмная", "Dark"), ("light", "Светлая", "Light")]:
            b = QPushButton(win.L(ru, en))
            b.setObjectName("segopt")
            b.setCheckable(True)
            b.setChecked(val == self.theme)
            b.clicked.connect(lambda _=False, v=val: self._pick_theme(v))
            self.theme_btns[val] = b
            trow.addWidget(b)
        trow.addStretch(1)
        v.addLayout(trow)

        v.addWidget(self._lbl(win.L("Акцентный цвет", "Accent color")))
        sw = QHBoxLayout()
        self.sw_btns = {}
        for c in ACCENTS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(30, 30)
            b.clicked.connect(lambda _=False, col=c: self._pick_accent(col))
            self.sw_btns[c] = b
            sw.addWidget(b)
        sw.addStretch(1)
        v.addLayout(sw)
        self._refresh_swatches()

        v.addSpacing(6)
        go = QPushButton(win.L("Начать", "Start"))
        go.setObjectName("primary")
        go.clicked.connect(self.accept)
        v.addWidget(go)

    def _lbl(self, t):
        l = QLabel(t)
        return l

    def _pick_theme(self, val):
        self.theme = val
        for k, b in self.theme_btns.items():
            b.setChecked(k == val)
        self.win.apply_theme_preview(val, self.accent)

    def _pick_accent(self, col):
        self.accent = col
        self._refresh_swatches()
        self.logo.set_accent(col)
        self.win.apply_theme_preview(self.theme, col)

    def _refresh_swatches(self):
        for c, b in self.sw_btns.items():
            sel = "#FFFFFF" if c == self.accent else "transparent"
            b.setChecked(c == self.accent)
            b.setStyleSheet(f"background:{c};border-radius:8px;border:2px solid {sel};")

    def values(self):
        return self.nick.text().strip(), self.theme, self.accent

    def showEvent(self, e):
        super().showEvent(e)
        if not getattr(self, "_faded", False):
            self._faded = True
            fade_widget(self, duration=240, start=0.0)


# --------------------------------------------------------------------------- #
#  Главное окно
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = core.load_config()
        self.accent = self.cfg.get("accent", "#4C8DFF")
        self.theme = self.cfg.get("theme", "dark")
        self.lang = self.cfg.get("lang", "ru")
        self.reactive = bool(self.cfg.get("reactive", True))
        self.local_name = self.cfg["profile"].get("username") or "Player"
        self.current = "home"
        self.busy = False
        self.last_article = None
        self.back_to = "home"
        self._retrans = []          # хуки локализации
        self._glows = []            # эффекты свечения кнопок (обновляются при смене акцента)
        self._workers = set()       # живые фоновые задачи — держим ссылку до finished
        self._icon_cache = {}       # (url,size,radius) -> QPixmap (скруглённые иконки)
        self._inst_icon_map = {}    # id сборки -> {имя_файла: url логотипа} (кэш)
        self._inst_icon_inflight = {}  # id сборки -> список колбэков, ждущих карту
        self.pool = QThreadPool.globalInstance()
        core.apply_proxy(self.cfg)

        self.setWindowTitle(f"UnlimitedMC — v{C.APP_VERSION}")
        self.resize(1120, 720)
        # ---- кастомная рамка окна под стиль лаунчера ----
        self.setWindowFlag(Qt.FramelessWindowHint, True)

        # ---- ambient-фон как подложка центрального виджета ----
        self.ambient = Ambient()
        self.setCentralWidget(self.ambient)
        root = QVBoxLayout(self.ambient)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._build_titlebar()
        root.addWidget(self.titlebar)

        shell = QWidget()
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._build_sidebar()
        outer.addWidget(self.side)

        # ---- правая колонка: топбар + контент + статус ----
        right = QWidget()
        rcol = QVBoxLayout(right)
        rcol.setContentsMargins(0, 0, 0, 0)
        rcol.setSpacing(0)
        self._build_topbar()
        rcol.addWidget(self.topbar)

        self.stack = QStackedWidget()
        rcol.addWidget(self.stack, 1)
        self._build_statusbar()
        rcol.addWidget(self.statusbar)
        outer.addWidget(right, 1)
        root.addWidget(shell, 1)

        # ---- ручки изменения размера (для frameless-окна) ----
        self._grip_br = QSizeGrip(self.ambient)
        self._grip_bl = QSizeGrip(self.ambient)
        for gp in (self._grip_br, self._grip_bl):
            gp.setFixedSize(16, 16)
            gp.raise_()

        # ---- страницы ----
        self.views = {}
        self._add_view("home", self._build_home())
        self._add_view("play", self._build_play())
        self._add_view("community", self._build_community())
        self._add_view("modrinth", self._build_browse("modrinth"))
        self._add_view("curseforge", self._build_browse("curseforge"))
        self._add_view("settings", self._build_settings())
        self._add_view("article", self._build_article())

        # плавная смена страниц: opacity-эффект на стопке (в покое выключен)
        self._stack_fx = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self._stack_fx)
        self._stack_fx.setEnabled(False)
        self._stack_anim = QPropertyAnimation(self._stack_fx, b"opacity", self)
        self._stack_anim.setDuration(220)
        self._stack_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._stack_anim.finished.connect(lambda: self._stack_fx.setEnabled(False))
        self._shown_once = False

        self.set_sidebar_expanded(False)
        self.apply_theme()
        self.retranslate()
        self.switch("home")
        self.refresh_instances()

    def showEvent(self, e):
        super().showEvent(e)
        # одноразовое мягкое проявление всего окна при первом запуске
        if not self._shown_once:
            self._shown_once = True
            self._round_window_corners()
            fade_widget(self.ambient, duration=320, start=0.0)

    def _round_window_corners(self):
        """Слегка скруглить углы окна нативно через DWM (Windows 11).

        DWMWCP_ROUND даёт стандартное мягкое скругление (≈8px, «не прям сильно»),
        с антиалиасингом и без артефактов на дочерних виджетах. На других ОС —
        тихо ничего не делает.
        """
        if sys.platform != "win32":
            return
        try:
            import ctypes
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWCP_ROUND = 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:  # noqa: BLE001 — оформление не должно ронять запуск
            pass

    def _fade_stack(self):
        """Короткое проявление текущей страницы при переключении раздела."""
        if not hasattr(self, "_stack_anim"):
            return
        self._stack_fx.setEnabled(True)
        self._stack_anim.stop()
        self._stack_anim.setStartValue(0.0)
        self._stack_anim.setEndValue(1.0)
        self._stack_anim.start()

    # ----------------------------- рамка окна ----------------------------- #
    def _build_titlebar(self):
        self.titlebar = TitleBar(self)

    def toggle_max_restore(self):
        if self.isMaximized():
            self.showNormal()
            self.titlebar.set_maximized(False)
        else:
            self.showMaximized()
            self.titlebar.set_maximized(True)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._position_grips()

    def closeEvent(self, e):
        # Снимаем ещё не стартовавшие фоновые задачи, чтобы не плодить работу на
        # выходе. Уже запущенные доедут сами — ссылки на них живут в _workers,
        # а их emit при сносе сигналов погашен (см. Worker._emit).
        try:
            self.pool.clear()
        except Exception:
            pass
        super().closeEvent(e)

    def _position_grips(self):
        if not hasattr(self, "_grip_br"):
            return
        r = self.ambient.rect()
        m = 1
        self._grip_br.move(r.right() - self._grip_br.width() - m,
                           r.bottom() - self._grip_br.height() - m)
        self._grip_bl.move(m, r.bottom() - self._grip_bl.height() - m)
        self._grip_br.raise_()
        self._grip_bl.raise_()

    # ----------------------------- сайдбар ----------------------------- #
    def _build_sidebar(self):
        self.side = Sidebar(self)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(11, 14, 11, 12)
        sl.setSpacing(5)

        self.lib_label = QLabel()
        self.lib_label.setObjectName("sideLabel")
        sl.addWidget(self.lib_label)

        self.nav_meta = [
            ("home", "Главная", "Home", "Готов к запуску", "Ready to play", False),
            ("play", "Играть", "Play", "Ваши сборки", "Your instances", False),
            ("community", "Сообщество", "Community", "Друзья и поиск игроков", "Friends and player search", False),
        ]
        self.browse_label = QLabel()
        self.browse_label.setObjectName("sideLabel")
        self.nav_meta2 = [
            ("modrinth", "Modrinth", "Modrinth", "Моды, паки и шейдеры", "Mods, packs and shaders", True),
            ("curseforge", "CurseForge", "CurseForge", "Моды, паки и шейдеры", "Mods, packs and shaders", True),
            ("settings", "Настройки", "Settings", "Профиль, оформление и поведение", "Profile, appearance and behavior", False),
        ]
        self.nav_btns = {}
        self.nav_titles = {}
        self.nav_subs = {}
        self.nav_search = {}

        def add_nav(key, ru, en, sru, sen, has_search):
            b = QToolButton()
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setIconSize(QSize(NAV_ICON_SIZE, NAV_ICON_SIZE))
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _=False, k=key: self.switch(k))
            self.nav_btns[key] = b
            self.nav_titles[key] = (ru, en)
            self.nav_subs[key] = (sru, sen)
            self.nav_search[key] = has_search
            sl.addWidget(b)

        for m in self.nav_meta:
            add_nav(*m)
        sl.addWidget(self.browse_label)
        for m in self.nav_meta2:
            add_nav(*m)

        sl.addStretch(1)

        # аккаунт-чип (клик → заглушка «вход недоступен»)
        self.account = Clickable()
        self.account.setObjectName("account")
        self.account.clicked.connect(self.login_stub)
        al = QHBoxLayout(self.account)
        al.setContentsMargins(9, 8, 9, 8)
        al.setSpacing(11)
        self.acc_avatar = QLabel("?")
        self.acc_avatar.setObjectName("accAvatar")
        self.acc_avatar.setFixedSize(34, 34)
        self.acc_avatar.setAlignment(Qt.AlignCenter)
        al.addWidget(self.acc_avatar)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.acc_name = QLabel()
        self.acc_name.setObjectName("accName")
        self.acc_tag = QLabel()
        self.acc_tag.setObjectName("accTag")
        who.addWidget(self.acc_name)
        who.addWidget(self.acc_tag)
        self.acc_who = QWidget()
        self.acc_who.setLayout(who)
        al.addWidget(self.acc_who, 1)
        sl.addWidget(self.account)

    def set_sidebar_expanded(self, expanded: bool):
        was = getattr(self, "_side_expanded", None)
        self._side_expanded = expanded
        for key, b in self.nav_btns.items():
            ru, en = self.nav_titles[key]
            if expanded:
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
                b.setText("  " + self.L(ru, en))
            else:
                b.setToolButtonStyle(Qt.ToolButtonIconOnly)
                b.setText("")
        labels = (self.lib_label, self.browse_label, self.acc_who)
        for w in labels:
            w.setVisible(expanded)
        # подписи плавно проявляются при раскрытии сайдбара по наведению
        if expanded and was is False:
            for w in labels:
                fade_widget(w, duration=200, start=0.0)

    # ----------------------------- топбар ----------------------------- #
    def _build_topbar(self):
        self.topbar = QFrame()
        self.topbar.setObjectName("topbar")
        t = QHBoxLayout(self.topbar)
        t.setContentsMargins(28, 18, 28, 14)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.page_sub = QLabel()
        self.page_sub.setObjectName("pageSub")
        col.addWidget(self.page_title)
        col.addWidget(self.page_sub)
        t.addLayout(col)
        t.addStretch(1)

        self.search_box = QFrame()
        self.search_box.setObjectName("searchBox")
        sb = QHBoxLayout(self.search_box)
        sb.setContentsMargins(13, 9, 13, 9)
        self.search_hint = QLabel()
        self.search_hint.setObjectName("search")
        sb.addWidget(self.search_hint)
        self.search_box.setMinimumWidth(240)
        t.addWidget(self.search_box)

    def _build_statusbar(self):
        self.statusbar = QFrame()
        self.statusbar.setObjectName("statusbar")
        s = QHBoxLayout(self.statusbar)
        s.setContentsMargins(28, 6, 28, 6)
        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        s.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(220)
        self.progress.hide()
        s.addWidget(self.progress)
        self.statusbar.hide()

    # ----------------------------- утилиты ----------------------------- #
    def L(self, ru, en):
        return en if self.lang == "en" else ru

    def _glow(self, btn, color=None):
        """Навесить на кнопку анимированное свечение/нажатие.

        По умолчанию — под акцент раздела (запоминаем, чтобы перекрасить при смене
        акцента). Если задан ``color`` — фиксированный цвет (напр. красный у кнопки
        удаления): такое свечение в список акцентных не попадает и не зеленеет на
        Modrinth/CurseForge.
        """
        if color is None:
            self._glows.append(glow_on_hover(btn, self.effective_accent()))
        else:
            glow_on_hover(btn, color)
        return btn

    # --- стилизованные окна-сообщения (вместо стандартного QMessageBox) ---
    def _msg(self, kind, title, text, confirm=False, danger=False):
        return MessageDialog(self, kind, title, text, confirm, danger).exec()

    def msg_error(self, title, text):
        self._msg("error", title, text)

    def msg_info(self, title, text):
        self._msg("info", title, text)

    def msg_confirm(self, title, text, danger=True):
        return self._msg("question", title, text, confirm=True, danger=danger) == QDialog.Accepted

    def _reg(self, fn):
        self._retrans.append(fn)
        fn()
        return fn

    def _add_view(self, key, widget):
        self.views[key] = self.stack.addWidget(widget)

    def _scroll(self, inner: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(inner)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        return sc

    def _seth(self, ru, en):
        l = QLabel()
        l.setObjectName("seth")
        self._reg(lambda: l.setText(self.L(ru, en).upper()))
        return l

    def _section_head(self, ru, en, link_ru=None, link_en=None, on_link=None):
        row = QHBoxLayout()
        h = QLabel()
        h.setObjectName("h3")
        self._reg(lambda: h.setText(self.L(ru, en)))
        row.addWidget(h)
        row.addStretch(1)
        if link_ru is not None:
            a = Clickable()
            la = QHBoxLayout(a)
            la.setContentsMargins(0, 0, 0, 0)
            lab = QLabel()
            lab.setObjectName("link")
            self._reg(lambda: lab.setText(self.L(link_ru, link_en)))
            la.addWidget(lab)
            if on_link:
                a.clicked.connect(on_link)
            row.addWidget(a)
        w = QWidget()
        w.setLayout(row)
        return w

    # ============================ ГЛАВНАЯ ============================ #
    def _build_home(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.setSpacing(0)

        # hero
        hero = QFrame()
        hero.setObjectName("hero")
        hl = QVBoxLayout(hero)
        hl.setContentsMargins(32, 30, 32, 30)
        hl.setSpacing(0)
        self.greeting = QLabel()
        self.greeting.setObjectName("heroTitle")
        hl.addWidget(self.greeting)
        hrow = QHBoxLayout()
        hrow.setContentsMargins(0, 22, 0, 0)
        hrow.setSpacing(14)

        self.home_select = Clickable()
        self.home_select.setObjectName("select")
        self.home_select.clicked.connect(lambda: self.switch("play"))
        ssl = QHBoxLayout(self.home_select)
        ssl.setContentsMargins(15, 11, 15, 11)
        self.home_select_text = QLabel()
        self.home_select_text.setObjectName("selectText")
        ssl.addWidget(self.home_select_text)
        hrow.addWidget(self.home_select)

        self.home_play = QPushButton()
        self.home_play.setObjectName("primary")
        self.home_play.clicked.connect(lambda: self.switch("play"))
        self._reg(lambda: self.home_play.setText("▶  " + self.L("Играть", "Play")))
        self._glow(self.home_play)
        raise_on_hover(self.home_select)
        hrow.addWidget(self.home_play)
        hrow.addStretch(1)
        hl.addLayout(hrow)
        v.addWidget(hero)
        v.addSpacing(20)

        v.addWidget(self._section_head("Новости Minecraft", "Minecraft News",
                                       "Всё →", "All →"))
        self.news_mc = FlowLayout(hspacing=14, vspacing=14)
        mc_host = QWidget()
        mc_host.setLayout(self.news_mc)
        v.addWidget(mc_host)
        v.addSpacing(16)

        v.addWidget(self._section_head("Новости UnlimitedMC", "UnlimitedMC News",
                                       "Все →", "All →"))
        self.news_umc = FlowLayout(hspacing=14, vspacing=14)
        umc_host = QWidget()
        umc_host.setLayout(self.news_umc)
        v.addWidget(umc_host)
        v.addSpacing(18)

        # баннер версии (на месте бывшей рекламы — рекламы нет)
        banner = QFrame()
        banner.setObjectName("banner")
        bl = QVBoxLayout(banner)
        bl.setContentsMargins(22, 18, 22, 18)
        bl.setSpacing(4)
        self.banner_title = QLabel()
        self.banner_title.setObjectName("bannerTitle")
        self.banner_sub = QLabel()
        self.banner_sub.setObjectName("muted")
        self.banner_sub.setWordWrap(True)
        self._reg(lambda: self.banner_title.setText(
            f"🏷️ UnlimitedMC — v{C.APP_VERSION} ({self.L('дизайн', 'design')} {C.DESIGN_VERSION})"))
        self._reg(lambda: self.banner_sub.setText(self.L(
            "Версия видна в интерфейсе — удобно отличать сборки. Рекламы здесь нет.",
            "The version is shown in the UI so you can tell builds apart. No ads here.")))
        bl.addWidget(self.banner_title)
        bl.addWidget(self.banner_sub)
        v.addWidget(banner)
        v.addStretch(1)
        return self._scroll(page)

    def refresh_greeting(self):
        name = self.local_name or self.L("Игрок", "Player")
        tpl = random.choice(C.GREET[self.lang])
        self.greeting.setText(tpl.replace("{n}", name))

    def update_home_select(self):
        insts = self.cfg["instances"]
        if insts:
            i = insts[0]
            self.home_select_text.setText(f'{i["name"]}  ·  {i["loader"]} {i["mc_version"]}')
        else:
            self.home_select_text.setText(self.L("Сборок пока нет", "No instances yet"))

    def _news_card(self, n):
        card = Clickable()
        card.setObjectName("news")
        card.setFixedWidth(244)
        card.clicked.connect(lambda _=False, nid=n["id"]: self.open_article(nid))
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        cover = QFrame()
        cover.setObjectName("newsCover")
        cover.setFixedHeight(112)
        cvl = QVBoxLayout(cover)
        emo = QLabel(n["cover"])
        emo.setAlignment(Qt.AlignCenter)
        emo.setStyleSheet("font-size:34px;background:transparent;")
        cvl.addWidget(emo)
        cv.addWidget(cover)
        body = QVBoxLayout()
        body.setContentsMargins(15, 13, 15, 15)
        body.setSpacing(5)
        tag = QLabel(self.L(n["tag"], n["tagEn"]).upper())
        tag.setObjectName("newsTag")
        title = QLabel(self.L(n["title"], n["titleEn"]))
        title.setObjectName("newsTitle")
        title.setWordWrap(True)
        summ = QLabel(self.L(n["sum"], n["sumEn"]))
        summ.setObjectName("newsSum")
        summ.setWordWrap(True)
        body.addWidget(tag)
        body.addWidget(title)
        body.addWidget(summ)
        bw = QWidget()
        bw.setLayout(body)
        cv.addWidget(bw)
        raise_on_hover(card)
        return card

    def render_news(self):
        for layout, group in ((self.news_mc, "mc"), (self.news_umc, "umc")):
            while layout.count():
                it = layout.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            cards = []
            for n in C.NEWS:
                if n["group"] == group:
                    card = self._news_card(n)
                    layout.addWidget(card)
                    cards.append(card)
            stagger_in(cards, step=45)

    # ============================ СТАТЬЯ ============================ #
    def _build_article(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.setSpacing(0)
        back = QPushButton()
        back.setObjectName("ghost")
        back.setFixedWidth(120)
        back.clicked.connect(lambda: self.switch(self.back_to))
        self._reg(lambda: back.setText("←  " + self.L("Назад", "Back")))
        v.addWidget(back)
        v.addSpacing(18)

        wrap = QWidget()
        wrap.setMaximumWidth(720)
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(8)
        self.art_cover = QLabel()
        self.art_cover.setObjectName("artCover")
        self.art_cover.setAlignment(Qt.AlignCenter)
        self.art_cover.setFixedHeight(200)
        wl.addWidget(self.art_cover)
        wl.addSpacing(12)
        self.art_tag = QLabel()
        self.art_tag.setObjectName("artTag")
        self.art_title = QLabel()
        self.art_title.setObjectName("artTitle")
        self.art_title.setWordWrap(True)
        self.art_date = QLabel()
        self.art_date.setObjectName("artDate")
        self.art_body = QLabel()
        self.art_body.setObjectName("artBody")
        self.art_body.setWordWrap(True)
        self.art_body.setTextFormat(Qt.RichText)
        wl.addWidget(self.art_tag)
        wl.addWidget(self.art_title)
        wl.addWidget(self.art_date)
        wl.addSpacing(10)
        wl.addWidget(self.art_body)
        v.addWidget(wrap)
        v.addStretch(1)
        return self._scroll(page)

    def fill_article(self, nid):
        n = next((x for x in C.NEWS if x["id"] == nid), None)
        if not n:
            return
        self.last_article = nid
        self.art_cover.setText(n["cover"])
        self.art_tag.setText(self.L(n["tag"], n["tagEn"]).upper())
        self.art_title.setText(self.L(n["title"], n["titleEn"]))
        self.art_date.setText(self.L(n["date"], n["dateEn"]))
        body = self.L(n["body"], n["bodyEn"])
        self.art_body.setText("<br><br>".join(body))

    def open_article(self, nid):
        if self.current not in ("article",):
            self.back_to = self.current
        self.fill_article(nid)
        self.switch("article")

    # ============================ ИГРАТЬ ============================ #
    def _build_play(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.setSpacing(0)

        # аккаунт для игры (локальный ник — это НЕ аккаунт UnlimitedMC)
        accbox = QFrame()
        accbox.setObjectName("card")
        ab = QVBoxLayout(accbox)
        ab.setContentsMargins(20, 16, 20, 18)
        ab.setSpacing(10)
        lbl = QLabel()
        lbl.setObjectName("muted")
        self._reg(lambda: lbl.setText(self.L("Аккаунт для игры (локальный ник)", "Account to play with (local name)")))
        ab.addWidget(lbl)
        row = QHBoxLayout()
        self.username_edit = QLineEdit(self.local_name)
        self.username_edit.setMaximumWidth(280)
        row.addWidget(self.username_edit)
        save_u = QPushButton()
        save_u.setObjectName("mini")
        save_u.clicked.connect(self.save_username)
        self._reg(lambda: save_u.setText(self.L("Сохранить", "Save")))
        row.addWidget(save_u)
        row.addStretch(1)
        ab.addLayout(row)
        ms = QLabel()
        ms.setObjectName("muted")
        self._reg(lambda: ms.setText(self.L(
            "Microsoft и Ely.by появятся позже — пока запуск офлайн по нику.",
            "Microsoft and Ely.by will come later — offline launch by name for now.")))
        ab.addWidget(ms)
        v.addWidget(accbox)
        v.addSpacing(16)

        v.addWidget(self._section_head("Ваши сборки", "Your instances",
                                       "+ Создать сборку", "+ New instance", self.create_instance))
        self.inst_flow = FlowLayout(hspacing=14, vspacing=14)
        host = QWidget()
        host.setLayout(self.inst_flow)
        v.addWidget(host)
        v.addStretch(1)
        return self._scroll(page)

    def _inst_card(self, inst):
        card = QFrame()
        card.setObjectName("inst")
        card.setFixedWidth(226)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(0)
        thumb = QFrame()
        thumb.setObjectName("instThumb")
        thumb.setFixedHeight(96)
        entries = core.instance_icon_entries(inst, 5)
        if entries:
            self._build_collage(thumb, inst, entries)
        else:
            tl = QVBoxLayout(thumb)
            ph = QLabel()
            ph.setAlignment(Qt.AlignCenter)
            ph.setPixmap(ui_icon("content", THEMES[self.theme]["muted"], 30).pixmap(QSize(30, 30)))
            ph.setStyleSheet("background:transparent;")
            tl.addWidget(ph)
        cv.addWidget(thumb)
        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 14)
        body.setSpacing(3)
        name = QLabel(inst["name"])
        name.setObjectName("newsTitle")
        n = core.count_instance_mods(inst)   # реальные файлы в папке mods, не список лаунчера
        meta = QLabel(f'{inst["loader"]} · {inst["mc_version"]} · {n} {C.T[self.lang]["mods"]}')
        meta.setObjectName("muted")
        body.addWidget(name)
        body.addWidget(meta)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 9, 0, 0)
        actions.setSpacing(8)
        launch = QPushButton("▶  " + C.T[self.lang]["play"])
        launch.setObjectName("launch")
        launch.clicked.connect(lambda _=False, i=inst: self.launch_instance(i))
        self._glow(launch)
        manager = QPushButton()
        manager.setObjectName("ghost")
        manager.setFixedWidth(40)
        manager.setToolTip(self.L("Менеджер сборки", "Instance manager"))
        manager.setIcon(ui_icon("manager", THEMES[self.theme]["muted"], 17))
        manager.setIconSize(QSize(17, 17))
        manager.clicked.connect(lambda _=False, i=inst: self.open_manager(i))
        actions.addWidget(launch, 1)
        actions.addWidget(manager)
        body.addLayout(actions)
        bw = QWidget()
        bw.setLayout(body)
        cv.addWidget(bw)
        raise_on_hover(card)
        return card

    def _build_collage(self, thumb, inst, entries):
        """Коллаж из 3–5 логотипов модов на превью: скруглённые плитки веером по
        центру. До загрузки логотипов — векторная заглушка; логотипы (включая
        опознанные по хэшу для ручных модов) подставляются в фоне по имени файла."""
        n = len(entries)
        tile = 60
        step = 0 if n == 1 else min(46, (210 - tile) // (n - 1))
        total = tile + (n - 1) * step
        x0 = max(8, (226 - total) // 2)
        y = (96 - tile) // 2
        fb = ui_icon("content", THEMES[self.theme]["muted"], 26).pixmap(QSize(26, 26))
        tiles = {}
        for i, e in enumerate(entries):
            t = QLabel(thumb)
            t.setObjectName("collageTile")
            t.setFixedSize(tile, tile)
            t.move(x0 + i * step, y)
            t.setAlignment(Qt.AlignCenter)
            t.setPixmap(fb)
            t.raise_()
            tiles[e["filename"]] = t

        def apply(m, _tiles=tiles):
            for fn, lab in _tiles.items():
                url = m.get(fn)
                if url:
                    try:
                        self._load_icon(lab, url, tile - 8, 16)
                    except RuntimeError:
                        pass
        self._instance_icons(inst, apply)

    def refresh_instances(self):
        if hasattr(self, "inst_flow"):
            while self.inst_flow.count():
                it = self.inst_flow.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            insts = self.cfg["instances"]
            if not insts:
                empty = QFrame()
                empty.setObjectName("card")
                empty.setFixedWidth(360)
                el = QVBoxLayout(empty)
                el.setContentsMargins(20, 36, 20, 36)
                el.setSpacing(12)
                emo = QLabel("📦")
                emo.setAlignment(Qt.AlignCenter)
                emo.setStyleSheet("font-size:40px;background:transparent;")
                msg = QLabel(C.T[self.lang]["noInst"])
                msg.setObjectName("muted")
                msg.setAlignment(Qt.AlignCenter)
                btn = QPushButton(C.T[self.lang]["createFirst"])
                btn.setObjectName("mini")
                btn.clicked.connect(self.create_instance)
                el.addWidget(emo)
                el.addWidget(msg)
                brow = QHBoxLayout()
                brow.addStretch(1)
                brow.addWidget(btn)
                brow.addStretch(1)
                el.addLayout(brow)
                self.inst_flow.addWidget(empty)
                fade_widget(empty, duration=260, start=0.0)
            else:
                cards = []
                for inst in insts:
                    card = self._inst_card(inst)
                    self.inst_flow.addWidget(card)
                    cards.append(card)
                stagger_in(cards, step=45)
        self.update_home_select()
        if hasattr(self, "browse"):
            for b in self.browse.values():
                b["refresh_targets"]()

    def create_instance(self):
        try:
            dlg = CreateInstanceDialog(self)
            if dlg.exec() == QDialog.Accepted:
                name, version, loader = dlg.values()
                if not version:
                    self.msg_info(self.L("Версия", "Version"),
                                  self.L("Укажи версию игры.", "Please set a game version."))
                    return
                inst = core.new_instance(name, version, loader)
                core.add_instance(self.cfg, inst)
                core.save_config(self.cfg)
                self.refresh_instances()
                self.toast(self.L("Сборка создана", "Instance created"))
        except Exception as e:  # noqa: BLE001
            self.on_error(str(e))

    def open_manager(self, inst):
        InstanceManagerDialog(self, inst).exec()
        self.refresh_instances()   # имя/RAM/счётчик модов могли измениться

    def launch_instance(self, inst):
        if self.busy:
            return
        self.run_task(core.ensure_and_launch, inst, self.cfg,
                      busy=self.L(f'Подготовка «{inst["name"]}»…', f'Preparing “{inst["name"]}”…'),
                      on_result=self.after_launch)

    def after_launch(self, proc):
        core.save_config(self.cfg)
        self.toast(self.L("Игра запущена — лаунчер вернётся после выхода из игры.",
                          "Game launched — the launcher will return after you quit."))
        self.showMinimized()          # уступаем экран игре
        self._watch_game(proc)        # ждём её закрытия в фоне

    def _watch_game(self, proc):
        """В фоне дожидается выхода из игры и поднимает лаунчер обратно."""
        if proc is None:
            return
        w = Worker(lambda: proc.wait())
        w.signals.finished.connect(self._on_game_closed)
        self.pool.start(w)

    def _on_game_closed(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.toast(self.L("С возвращением! Игра закрыта.", "Welcome back! The game has closed."))

    def save_username(self):
        try:
            core.set_username(self.cfg, self.username_edit.text())
            core.save_config(self.cfg)
            self.local_name = self.cfg["profile"]["username"]
            self.refresh_account_chip()
            self.toast(self.L("Ник сохранён", "Name saved"))
        except Exception as e:  # noqa: BLE001
            self.on_error(str(e))

    # ============================ КАТАЛОГ ============================ #
    def _build_browse(self, source):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.setSpacing(12)

        # «Ставить в:» + поиск
        bar = QHBoxLayout()
        bar.setSpacing(10)
        lbl = QLabel()
        lbl.setObjectName("muted")
        self._reg(lambda: lbl.setText(self.L("Ставить в:", "Install into:")))
        bar.addWidget(lbl)
        target = QComboBox()
        target.setMinimumWidth(200)
        bar.addWidget(target)
        search = QLineEdit()
        search.setPlaceholderText(self.L("Поиск…", "Search…"))
        bar.addWidget(search, 1)
        go = QPushButton()
        go.setObjectName("primary")
        self._reg(lambda: go.setText(self.L("Искать", "Search")))
        bar.addWidget(go)
        v.addLayout(bar)

        # чипы категорий
        chips_row = QHBoxLayout()
        chips_row.setSpacing(9)
        chip_group = QButtonGroup(self)
        chip_group.setExclusive(True)
        chip_buttons = []
        for ru, en, val in C.TYPES:
            ch = QPushButton()
            ch.setObjectName("chip")
            ch.setCheckable(True)
            ch.setChecked(val == "mod")
            chip_group.addButton(ch)
            chip_buttons.append((ch, ru, en, val))
            chips_row.addWidget(ch)
            self._reg(lambda c=ch, r=ru, e=en: c.setText(self.L(r, e)))
        chips_row.addStretch(1)
        v.addLayout(chips_row)

        hint = QLabel()
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        if source == "modrinth":
            self._reg(lambda: hint.setText(self.L(
                "Modrinth в РФ заблокирован — если поиск не работает, впиши прокси в Настройках.",
                "Modrinth is blocked in some regions — set a proxy in Settings if search fails.")))
        elif core.CF_BUNDLED_KEY_OBF:
            self._reg(lambda: hint.setText(self.L(
                "Работает со встроенным ключом. Хочешь свой лимит — впиши ключ в Настройках.",
                "Works out of the box with a built-in key. Add your own in Settings for a personal quota.")))
        else:
            self._reg(lambda: hint.setText(self.L(
                "Нужен бесплатный API-ключ CurseForge (Настройки → CurseForge).",
                "A free CurseForge API key is required (Settings → CurseForge).")))
        v.addWidget(hint)

        results = FlowLayout(hspacing=14, vspacing=14)
        host = QWidget()
        host.setLayout(results)
        v.addWidget(host)

        # постраничная навигация (1 2 3 … как на сайтах)
        pager_host = QWidget()
        pager = QHBoxLayout(pager_host)
        pager.setContentsMargins(0, 8, 0, 0)
        pager.setSpacing(7)
        pager.setAlignment(Qt.AlignCenter)
        pager_host.setVisible(False)
        v.addWidget(pager_host)
        v.addStretch(1)

        # состояние страницы
        state = {"source": source, "active_cat": "mod", "target": target, "results": results,
                 "pager_host": pager_host, "auto_done": False, "page": 0, "total": 0,
                 "per_page": 20, "q": "", "loader": None, "mcv": None, "ptype": "mod"}

        def refresh_targets():
            cur = target.currentData()
            target.blockSignals(True)
            target.clear()
            for inst in self.cfg["instances"]:
                target.addItem(f'{inst["name"]} · {inst["loader"]} {inst["mc_version"]}', inst["id"])
            if cur is not None:
                idx = target.findData(cur)
                if idx >= 0:
                    target.setCurrentIndex(idx)
            target.blockSignals(False)

        def target_instance():
            iid = target.currentData()
            return core.find_instance(self.cfg, iid) if iid else None

        def do_search(page=0):
            if self.busy:
                return
            state["auto_done"] = True   # поиск был — больше не подгружаем популярное само
            if page == 0:
                # новый поиск — снимаем параметры, чтобы клики по страницам их не меняли
                inst = target_instance()
                state["q"] = search.text()
                state["loader"] = inst["loader"] if inst else None
                state["mcv"] = inst["mc_version"] if inst else None
                state["ptype"] = state["active_cat"]
            state["page"] = page
            q, loader, mcv, ptype = state["q"], state["loader"], state["mcv"], state["ptype"]
            offset = page * state["per_page"]
            if source == "modrinth":
                self.run_task(core.modrinth_search, q, ptype, loader, mcv, state["per_page"], offset,
                              busy=self.L("Поиск на Modrinth…", "Searching Modrinth…"),
                              on_result=lambda res: show_results(res, ptype))
            else:
                key = core.cf_key(self.cfg)
                self.run_task(core.curseforge_search, key, q, ptype, loader, mcv, state["per_page"], offset,
                              busy=self.L("Поиск на CurseForge…", "Searching CurseForge…"),
                              on_result=lambda res: show_results(res, ptype))

        def goto_page(p):
            if self.busy or p == state["page"]:
                return
            sc = state.get("scroll")
            if sc is not None:
                sc.verticalScrollBar().setValue(0)
            do_search(p)

        def show_results(res, ptype):
            items, total = res
            state["total"] = total
            while results.count():
                it = results.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            if not items:
                empty = QLabel(C.T[self.lang]["empty"])
                empty.setObjectName("muted")
                results.addWidget(empty)
                fade_widget(empty, duration=240, start=0.0)
                render_pager()
                return
            cards = []
            for it in items:
                card = self._mod_card(source, it, ptype, target_instance)
                results.addWidget(card)
                cards.append(card)
            stagger_in(cards, step=40)
            render_pager()

        def render_pager():
            while pager.count():
                it = pager.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            per = state["per_page"]
            # CurseForge не отдаёт страницы с offset >= 10000 → держим разумный потолок
            pages = min((state["total"] + per - 1) // per, 50)
            if pages <= 1:
                pager_host.setVisible(False)
                return
            pager_host.setVisible(True)
            cur = state["page"]

            def pbtn(label, idx, *, enabled=True, current=False):
                b = QPushButton(str(label))
                b.setObjectName("pageCur" if current else "page")
                b.setEnabled(enabled and not current)
                if enabled and not current:
                    b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(lambda _=False, i=idx: goto_page(i))
                pager.addWidget(b)

            pbtn("‹", cur - 1, enabled=cur > 0)
            for n in _pager_numbers(cur, pages):
                if n is None:
                    gap = QLabel("…")
                    gap.setObjectName("muted")
                    pager.addWidget(gap)
                else:
                    pbtn(n + 1, n, current=(n == cur))
            pbtn("›", cur + 1, enabled=cur < pages - 1)

        def show_placeholder(text):
            while results.count():
                it = results.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            lbl = QLabel(text)
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            results.addWidget(lbl)
            fade_widget(lbl, duration=240, start=0.0)

        def auto_load():
            """Подгрузить самые популярные моды при заходе на страницу.

            Modrinth — сразу (ключ не нужен). CurseForge — только когда пользователь
            вписал свой API-ключ в Настройках (бережём квоту встроенного ключа): до
            этого показываем подсказку, а ручной поиск со встроенным ключом работает.
            """
            if state["auto_done"] or self.busy:
                return
            if source == "curseforge" and not self.cfg.get("curseforge_api_key", "").strip():
                show_placeholder(self.L(
                    "Чтобы здесь сразу появились популярные моды CurseForge, впиши свой "
                    "API-ключ в Настройках → CurseForge. Поиск работает и без него.",
                    "To see popular CurseForge mods here, add your API key in "
                    "Settings → CurseForge. Search works without it too."))
                return
            do_search(0)

        go.clicked.connect(lambda: do_search(0))
        search.returnPressed.connect(lambda: do_search(0))

        def on_chip(val):
            state["active_cat"] = val
        for ch, ru, en, val in chip_buttons:
            ch.clicked.connect(lambda _=False, val=val: on_chip(val))

        if not hasattr(self, "browse"):
            self.browse = {}
        self.browse[source] = {"refresh_targets": refresh_targets,
                               "auto_load": auto_load, "state": state}
        sc = self._scroll(page)
        state["scroll"] = sc          # чтобы при смене страницы прокрутить наверх
        return sc

    def _mod_card(self, source, it, ptype, target_getter):
        if source == "modrinth":
            name = it.get("title", "—")
            author = it.get("author", "")
            desc = it.get("description", "")
            downloads = it.get("downloads", 0)
            icon_url = it.get("icon_url") or ""
        else:
            name = it.get("name", "—")
            authors = it.get("authors") or []
            author = authors[0]["name"] if authors else ""
            desc = it.get("summary", "")
            downloads = it.get("downloadCount", 0)
            logo = it.get("logo") or {}
            icon_url = logo.get("url") or logo.get("thumbnailUrl") or ""

        card = QFrame()
        card.setObjectName("mod")
        card.setFixedWidth(340)
        cl = QHBoxLayout(card)
        cl.setContentsMargins(15, 15, 15, 15)
        cl.setSpacing(13)
        icon = QLabel("🧩")
        icon.setObjectName("modIcon")
        icon.setFixedSize(52, 52)
        icon.setAlignment(Qt.AlignCenter)
        cl.addWidget(icon, 0, Qt.AlignTop)
        self._load_icon(icon, icon_url)  # настоящий логотип мода (эмодзи — заглушка)
        box = QVBoxLayout()
        box.setSpacing(5)
        top = QLabel(f'{name}  ·  {author}')
        top.setObjectName("newsTitle")
        top.setWordWrap(True)
        d = QLabel(desc)
        d.setObjectName("newsSum")
        d.setWordWrap(True)
        box.addWidget(top)
        box.addWidget(d)
        foot = QHBoxLayout()
        dl = QLabel(f"↓ {downloads:,}".replace(",", " "))
        dl.setObjectName("muted")
        foot.addWidget(dl)
        foot.addStretch(1)
        # слот действия: «Установить» либо красная «Удалить», если мод уже в сборке
        action = QHBoxLayout()
        foot.addLayout(action)

        def refresh_action():
            try:
                while action.count():
                    w = action.takeAt(0).widget()
                    if w:
                        w.deleteLater()
                entry = core.installed_entry(target_getter(), source, it)
                if entry:
                    b = QPushButton("🗑  " + C.T[self.lang]["remove"])
                    b.setObjectName("remove")
                    b.clicked.connect(lambda _=False: self.remove_project(
                        source, it, target_getter(), refresh_action))
                    self._glow(b, color="#FF5C7A")   # красное свечение под цвет кнопки
                else:
                    b = QPushButton(C.T[self.lang]["install"])
                    b.setObjectName("install")
                    b.clicked.connect(lambda _=False: self.install_project(
                        source, it, ptype, target_getter(), refresh_action))
                    self._glow(b)
                action.addWidget(b)
            except RuntimeError:
                pass  # карточку могли удалить новым поиском, пока шла установка

        refresh_action()
        box.addLayout(foot)
        cl.addLayout(box, 1)
        raise_on_hover(card)
        return card

    def install_project(self, source, project, ptype, inst, on_done=None):
        if self.busy:
            return
        if not inst:
            self.msg_info(
                self.L("Нет сборки", "No instance"),
                self.L("Сначала создай сборку (вкладка «Играть») и выбери её в «Ставить в:».",
                       "Create an instance (Play tab) first and pick it in “Install into:”."))
            return
        name = project.get("title") or project.get("name") or "мод"
        if source == "modrinth":
            self.run_task(core.modrinth_install, inst, project, ptype,
                          busy=self.L(f"Установка {name}…", f"Installing {name}…"),
                          on_result=lambda e: self.after_install(name, on_done))
        else:
            key = core.cf_key(self.cfg)
            self.run_task(core.curseforge_install, key, inst, project, ptype,
                          busy=self.L(f"Установка {name}…", f"Installing {name}…"),
                          on_result=lambda e: self.after_install(name, on_done))

    def after_install(self, name, on_done=None):
        core.save_config(self.cfg)
        self._inst_icon_map.clear()   # состав модов изменился — пересоберём карту логотипов
        self.refresh_instances()
        self.toast(self.L(f"Установлено: {name} (с зависимостями)", f"Installed: {name} (with dependencies)"))
        if on_done:
            on_done()

    def remove_project(self, source, project, inst, on_done=None):
        if self.busy:
            return
        entry = core.installed_entry(inst, source, project)
        if not entry:                 # сборку могли переключить — просто обновим кнопку
            if on_done:
                on_done()
            return
        name = entry.get("name") or project.get("title") or project.get("name") or self.L("мод", "mod")
        if not self.msg_confirm(
                self.L("Удалить мод?", "Remove mod?"),
                self.L(f"Удалить «{name}» из сборки «{inst['name']}»?\n"
                       "Файл мода будет удалён с диска.",
                       f"Remove “{name}” from “{inst['name']}”?\n"
                       "The mod file will be deleted from disk.")):
            return
        self.run_task(core.remove_mod, inst, entry,
                      busy=self.L(f"Удаление {name}…", f"Removing {name}…"),
                      on_result=lambda _=None: self.after_remove(name, on_done))

    def after_remove(self, name, on_done=None):
        core.save_config(self.cfg)
        self._inst_icon_map.clear()
        self.refresh_instances()
        self.toast(self.L(f"Удалено: {name}", f"Removed: {name}"))
        if on_done:
            on_done()

    # ============================ СООБЩЕСТВО (заглушка входа) ============================ #
    def _build_community(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.addStretch(1)
        icon = QLabel("🔒")
        icon.setObjectName("gateIcon")
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel()
        title.setObjectName("gateTitle")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel()
        sub.setObjectName("gateSub")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        sub.setMaximumWidth(440)
        note = QLabel()
        note.setObjectName("muted")
        note.setAlignment(Qt.AlignCenter)
        note.setWordWrap(True)
        self._reg(lambda: title.setText(self.L("Войдите в аккаунт UnlimitedMC", "Sign in to UnlimitedMC")))
        self._reg(lambda: sub.setText(self.L(
            "Для того чтобы просматривать сообщество войдите в аккаунт UnlimitedMC.",
            "To view the community, sign in to your UnlimitedMC account.")))
        self._reg(lambda: note.setText(self.L(
            "Вход пока недоступен — раздел заработает позже, когда подключим сервер.",
            "Sign-in is not available yet — this section will work once the server is online.")))
        btn = QPushButton()
        btn.setObjectName("primary")
        btn.clicked.connect(self.login_stub)
        self._reg(lambda: btn.setText(self.L("Войти / Зарегистрироваться", "Sign in / Register")))

        v.addWidget(icon)
        v.addSpacing(6)
        v.addWidget(title)
        # ширина sub ограничена 440 — без явного центрирования бокс прижимается
        # влево и текст «съезжает» (как было видно на скрине). Центрируем по горизонтали.
        v.addWidget(sub, 0, Qt.AlignHCenter)
        v.addSpacing(8)
        brow = QHBoxLayout()
        brow.addStretch(1)
        brow.addWidget(btn)
        brow.addStretch(1)
        v.addLayout(brow)
        v.addSpacing(10)
        v.addWidget(note)
        v.addStretch(2)
        return page

    # ============================ НАСТРОЙКИ ============================ #
    def _build_settings(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(28, 8, 28, 40)
        v.setSpacing(12)

        # --- профиль и аккаунт ---
        v.addWidget(self._seth("Профиль и аккаунт", "Profile & account"))
        v.addWidget(self._opt_button("Профиль UnlimitedMC", "UnlimitedMC profile",
                                     "Имя, @username, аватар — нужен аккаунт", "Name, @username, avatar — needs an account",
                                     "Редактировать", "Edit", self.login_stub))
        v.addWidget(self._opt_button("Локальное имя", "Local name",
                                     "Ник для офлайн-запуска (не аккаунт)", "Offline launch name (not an account)",
                                     "Сменить", "Change", self.change_local_name))
        v.addWidget(self._opt_button("Вход на umclaunch.net", "Sign in to umclaunch.net",
                                     "Аккаунты и сайт появятся позже", "Accounts and the site will come later",
                                     "Войти", "Sign in", self.login_stub))
        v.addWidget(self._opt_button("Вступительный гайд", "Intro guide",
                                     "Пройти приветственные экраны заново", "Replay the welcome screens",
                                     "Пройти заново", "Replay", self.replay_onboarding))

        # --- оформление ---
        v.addWidget(self._seth("Оформление", "Appearance"))
        v.addWidget(self._opt_seg("Язык", "Language", "Язык интерфейса", "Interface language",
                                  [("ru", "RU", "RU"), ("en", "EN", "EN")], self.lang, self.set_lang))
        v.addWidget(self._opt_seg("Тема", "Theme", "Светлый или тёмный интерфейс", "Light or dark interface",
                                  [("dark", "Тёмная", "Dark"), ("light", "Светлая", "Light")], self.theme, self.set_theme))
        v.addWidget(self._opt_swatches())
        v.addWidget(self._opt_toggle("Реактивный фон под источник", "Reactive background by source",
                                     "Зелёный на Modrinth, оранжевый на CurseForge", "Green on Modrinth, orange on CurseForge",
                                     self.reactive, self.set_reactive))

        # --- игра ---
        v.addWidget(self._seth("Игра", "Game"))
        game = QFrame()
        game.setObjectName("opt")
        gf = QFormLayout(game)
        gf.setContentsMargins(20, 18, 20, 18)
        gf.setSpacing(12)
        self.s_ram = QSpinBox()
        self.s_ram.setRange(1024, 32768)
        self.s_ram.setSingleStep(512)
        self.s_ram.setSuffix(" " + self.L("МБ", "MB"))
        self.s_ram.setValue(int(self.cfg.get("ram_mb", 4096)))
        java_w = QWidget()
        jr = QHBoxLayout(java_w)
        jr.setContentsMargins(0, 0, 0, 0)
        self.s_java = QLineEdit(self.cfg.get("java_path", ""))
        self.s_java.setPlaceholderText(self.L("пусто = системная Java", "empty = system Java"))
        jb = QPushButton(self.L("Обзор…", "Browse…"))
        jb.setObjectName("ghost")
        jb.clicked.connect(self.pick_java)
        jr.addWidget(self.s_java, 1)
        jr.addWidget(jb)
        self.s_proxy = QLineEdit(self.cfg.get("proxy", ""))
        self.s_proxy.setPlaceholderText("http://host:port  /  socks5://host:port")
        self.s_cf = QLineEdit(self.cfg.get("curseforge_api_key", ""))
        self.s_cf.setEchoMode(QLineEdit.Password)
        self.s_cf.setPlaceholderText(self.L(
            "пусто = встроенный ключ; или свой с console.curseforge.com",
            "empty = built-in key; or your own from console.curseforge.com"))
        self._reg_form_label(gf, self.s_ram, "Память (ОЗУ)", "Memory (RAM)")
        self._reg_form_label(gf, java_w, "Путь к Java", "Java path")
        self._reg_form_label(gf, self.s_proxy, "Прокси (для РФ)", "Proxy")
        self._reg_form_label(gf, self.s_cf, "Ключ CurseForge", "CurseForge key")
        v.addWidget(game)

        save = QPushButton()
        save.setObjectName("primary")
        save.setFixedWidth(220)
        save.clicked.connect(self.save_settings)
        self._reg(lambda: save.setText(self.L("Сохранить настройки игры", "Save game settings")))
        self._glow(save)
        v.addWidget(save)
        v.addStretch(1)
        return self._scroll(page)

    def _reg_form_label(self, form, field, ru, en):
        lbl = QLabel()
        self._reg(lambda: lbl.setText(self.L(ru, en)))
        form.addRow(lbl, field)

    def _opt_shell(self, ru, en, dru, den):
        opt = QFrame()
        opt.setObjectName("opt")
        row = QHBoxLayout(opt)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(3)
        t = QLabel()
        t.setObjectName("optTitle")
        d = QLabel()
        d.setObjectName("optDesc")
        d.setWordWrap(True)
        self._reg(lambda: t.setText(self.L(ru, en)))
        self._reg(lambda: d.setText(self.L(dru, den)))
        col.addWidget(t)
        col.addWidget(d)
        row.addLayout(col, 1)
        return opt, row

    def _opt_button(self, ru, en, dru, den, bru, ben, cb):
        opt, row = self._opt_shell(ru, en, dru, den)
        b = QPushButton()
        b.setObjectName("mini")
        b.clicked.connect(cb)
        self._reg(lambda: b.setText(self.L(bru, ben)))
        row.addWidget(b)
        return opt

    def _opt_seg(self, ru, en, dru, den, options, current, cb):
        opt, row = self._opt_shell(ru, en, dru, den)
        seg = QFrame()
        seg.setObjectName("seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(5, 5, 5, 5)
        sl.setSpacing(5)
        group = QButtonGroup(seg)
        group.setExclusive(True)
        for val, oru, oen in options:
            b = QPushButton()
            b.setObjectName("segopt")
            b.setCheckable(True)
            b.setChecked(val == current)
            group.addButton(b)
            b.clicked.connect(lambda _=False, v=val: cb(v))
            self._reg(lambda btn=b, r=oru, e=oen: btn.setText(self.L(r, e)))
            sl.addWidget(b)
        row.addWidget(seg)
        return opt

    def _opt_toggle(self, ru, en, dru, den, current, cb):
        opt, row = self._opt_shell(ru, en, dru, den)
        tg = Toggle(self.accent)
        tg.setChecked(current)
        tg.toggled.connect(cb)
        row.addWidget(tg)
        return opt

    def _opt_swatches(self):
        opt, row = self._opt_shell("Цвет акцента", "Accent color",
                                   "Используется в интерфейсе и градиентах", "Used across the interface and gradients")
        wrap = QHBoxLayout()
        wrap.setSpacing(9)
        self.settings_swatches = {}
        for c in ACCENTS:
            b = QPushButton()
            b.setCheckable(True)
            b.setFixedSize(28, 28)
            b.clicked.connect(lambda _=False, col=c: self.set_accent(col))
            self.settings_swatches[c] = b
            wrap.addWidget(b)
        w = QWidget()
        w.setLayout(wrap)
        row.addWidget(w)
        self._refresh_swatches()
        return opt

    def _refresh_swatches(self):
        if not hasattr(self, "settings_swatches"):
            return
        for c, b in self.settings_swatches.items():
            sel = THEMES[self.theme]["text"] if c == self.accent else "transparent"
            b.setChecked(c == self.accent)
            b.setStyleSheet(f"background:{c};border-radius:8px;border:2px solid {sel};")

    # настройки: обработчики
    def change_local_name(self):
        self.switch("play")
        self.username_edit.setFocus()
        self.username_edit.selectAll()

    def replay_onboarding(self):
        dlg = OnboardingDialog(self)
        dlg.exec()
        # вернуть применённую тему/акцент (preview мог поменять)
        self.apply_theme()
        if dlg.result() == QDialog.Accepted:
            nick, theme, accent = dlg.values()
            if nick:
                core.set_username(self.cfg, nick)
                self.local_name = self.cfg["profile"]["username"]
            self.theme, self.accent = theme, accent
            self.cfg["theme"], self.cfg["accent"] = theme, accent
            core.save_config(self.cfg)
            self.apply_theme()
            self.refresh_account_chip()
            self.refresh_greeting()

    def pick_java(self):
        path, _ = QFileDialog.getOpenFileName(self, self.L("Выберите java", "Choose java"))
        if path:
            self.s_java.setText(path)

    def save_settings(self):
        try:
            self.cfg["ram_mb"] = int(self.s_ram.value())
            self.cfg["java_path"] = self.s_java.text().strip()
            self.cfg["proxy"] = self.s_proxy.text().strip()
            self.cfg["curseforge_api_key"] = self.s_cf.text().strip()
            core.apply_proxy(self.cfg)
            core.save_config(self.cfg)
            self.toast(self.L("Настройки сохранены", "Settings saved"))
        except Exception as e:  # noqa: BLE001
            self.on_error(str(e))

    # ============================ ТЕМА / АКЦЕНТ / ЯЗЫК ============================ #
    def effective_accent(self):
        if self.reactive and self.current in SOURCE_ACCENT:
            return SOURCE_ACCENT[self.current]
        return self.accent

    def apply_theme(self):
        tok = THEMES[self.theme]
        acc = self.effective_accent()
        # apply_theme зовётся на КАЖДОЙ навигации (ради реактивного акцента). Полная
        # пересборка QSS дорогая (Qt пере-полирует все виджеты), поэтому тяжёлую часть
        # делаем только когда реально сменились тема/акцент — иначе переходы тормозили
        # и «съедали» анимацию тумблера.
        key = (self.theme, acc)
        if key != getattr(self, "_qss_key", None):
            self._qss_key = key
            QApplication.instance().setStyleSheet(build_qss(tok, acc))
            self.ambient.set_colors(tok["bg"], acc, tok["ambient"])
            self.titlebar.logo.set_accent(acc)
            self.titlebar.wordmark.setText(f'Unlimited<b style="color:{acc}">MC</b>')
            self.titlebar.apply_icons(tok["muted"], tok["text"])
            self._refresh_swatches()
            for tg in self.findChildren(Toggle):
                tg.set_accent(acc)
            # обновить цвет свечения у живых кнопок, мёртвые (пересозданные карточки) убрать
            live = []
            for g in self._glows:
                try:
                    g.set_color(acc)
                    live.append(g)
                except RuntimeError:
                    pass
            self._glows = live
        self._rebuild_nav_icons(acc, tok["muted"])

    def apply_theme_preview(self, theme, accent):
        """Живой предпросмотр из онбординга (без сохранения)."""
        tok = THEMES[theme]
        self._qss_key = None  # предпросмотр обходит кэш — сбросим, чтобы apply_theme пересобрал
        QApplication.instance().setStyleSheet(build_qss(tok, accent))
        self.ambient.set_colors(tok["bg"], accent, tok["ambient"])
        self.titlebar.logo.set_accent(accent)
        self.titlebar.wordmark.setText(f'Unlimited<b style="color:{accent}">MC</b>')
        self.titlebar.apply_icons(tok["muted"], tok["text"])

    def _rebuild_nav_icons(self, accent, muted):
        for key, b in self.nav_btns.items():
            color = accent if key == self.current else muted
            b.setIcon(nav_icon(key, color, NAV_ICON_SIZE))

    def set_accent(self, col):
        self.accent = col
        self.cfg["accent"] = col
        core.save_config(self.cfg)
        self.apply_theme()

    def set_theme(self, val):
        self.theme = val
        self.cfg["theme"] = val
        core.save_config(self.cfg)
        self.apply_theme()

    def set_reactive(self, on):
        self.reactive = bool(on)
        self.cfg["reactive"] = self.reactive
        core.save_config(self.cfg)
        self.apply_theme()

    def set_lang(self, val):
        self.lang = val
        self.cfg["lang"] = val
        core.save_config(self.cfg)
        self.retranslate()

    def retranslate(self):
        for fn in self._retrans:
            try:
                fn()
            except Exception:  # noqa: BLE001 — один кривой хук не должен ронять остальные
                traceback.print_exc()
        self.set_sidebar_expanded(getattr(self, "_side_expanded", False))
        self.refresh_titlebar()
        self.refresh_greeting()
        self.render_news()
        self.refresh_instances()
        self.refresh_account_chip()
        if self.last_article:
            self.fill_article(self.last_article)
        if self.s_ram is not None:
            self.s_ram.setSuffix(" " + self.L("МБ", "MB"))

    # ============================ НАВИГАЦИЯ ============================ #
    def switch(self, key):
        self.current = key
        self.stack.setCurrentIndex(self.views[key])
        for k, b in self.nav_btns.items():
            b.setChecked(k == key)
        # статья не имеет пункта меню — подсветим источник перехода
        if key == "article" and self.back_to in self.nav_btns:
            self.nav_btns[self.back_to].setChecked(True)
        self.apply_theme()      # реактивный акцент зависит от раздела
        self.refresh_titlebar()
        nav_has_search = self.nav_search.get(key, False)
        self.search_box.setVisible(nav_has_search)
        # верхняя панель прячется на странице профиля (профиль = заглушка, не делаем)
        sc = self.stack.currentWidget()
        if isinstance(sc, QScrollArea):
            sc.verticalScrollBar().setValue(0)
        if key == "home":
            self.refresh_greeting()
            self.render_news()
        if key in ("modrinth", "curseforge"):
            self.browse[key]["auto_load"]()
        self._fade_stack()

    def refresh_titlebar(self):
        if self.current == "article":
            self.page_title.setText(self.L("Новости", "News"))
            self.page_sub.setText("")
            return
        if self.current in self.nav_titles:
            ru, en = self.nav_titles[self.current]
            sru, sen = self.nav_subs[self.current]
            self.page_title.setText(self.L(ru, en))
            self.page_sub.setText(self.L(sru, sen))
        self._reg_search_hint()

    def _reg_search_hint(self):
        self.search_hint.setText("🔍  " + self.L("Поиск модов, паков, шейдеров…",
                                                 "Search mods, packs, shaders…"))

    # ============================ АККАУНТ-ЗАГЛУШКА ============================ #
    def refresh_account_chip(self):
        self.acc_avatar.setText("?")
        self.acc_name.setText(self.L("Войти", "Sign in"))
        self.acc_tag.setText(self.L("Аккаунт UnlimitedMC", "UnlimitedMC account"))

    def login_stub(self):
        self.msg_info(
            self.L("Вход недоступен", "Sign-in unavailable"),
            self.L(
                "Аккаунты UnlimitedMC и онлайн-сообщество появятся позже, когда подключим сервер.\n\n"
                "Локальный игровой ник (для офлайн-запуска) задаётся во вкладке «Играть» — он не связан с аккаунтом.",
                "UnlimitedMC accounts and the online community will arrive later, once the server is online.\n\n"
                "Your local in-game name (for offline launch) is set on the “Play” tab — it is not tied to an account."))

    # ============================ ЗАДАЧИ / СТАТУС ============================ #
    def _launch_worker(self, w, *, on_result=None, on_error=None, on_finished=None):
        """Запускает Worker, удерживая на него ссылку до сигнала finished.

        Без этого PySide может собрать Worker (и его WorkerSignals) сборщиком мусора,
        пока поток ещё работает или пока в очереди главного потока висят результаты, —
        отсюда «Signal source has been deleted» и редкие вылеты. Ссылку снимаем только
        после finished, когда все результаты уже доставлены.
        """
        self._workers.add(w)
        if on_result:
            w.signals.result.connect(on_result)
        if on_error:
            w.signals.error.connect(on_error)

        def _fin(_w=w):
            if on_finished:
                on_finished()
            self._workers.discard(_w)
        w.signals.finished.connect(_fin)
        self.pool.start(w)

    def run_task(self, fn, *args, busy="…", on_result=None, **kwargs):
        self.set_busy(True, busy)
        w = Worker(fn, *args, **kwargs)
        w.signals.progress.connect(self.on_progress)
        self._launch_worker(w, on_result=on_result, on_error=self.on_error,
                            on_finished=lambda: self.set_busy(False))

    def _bg(self, fn, *args, on_result=None):
        """Лёгкая фоновая задача без индикатора занятости (иконки модов и т.п.)."""
        self._launch_worker(Worker(fn, *args), on_result=on_result)

    def _load_icon(self, label, url, size=52, radius=11):
        """Подгрузить иконку в QLabel: из кэша мгновенно, иначе — фоном."""
        if not url:
            return
        ck = (url, size, radius)
        pm = self._icon_cache.get(ck)
        if pm is not None:
            if not pm.isNull():
                label.setText("")
                label.setPixmap(pm)
            return

        def done(data, _ck=ck, _label=label, _s=size, _r=radius):
            pm = rounded_icon(data, _s, _r) if data else QPixmap()
            self._icon_cache[_ck] = pm  # кэшируем и неудачу — не долбим CDN повторно
            if pm.isNull():
                return
            try:                       # карточка могла быть удалена новым поиском
                _label.setText("")
                _label.setPixmap(pm)
            except RuntimeError:
                pass
        self._bg(core.fetch_bytes, url, on_result=done)

    def _instance_icons(self, inst, on_ready):
        """Карта {имя_файла: url логотипа} для сборки — в фоне, с кэшем и дедупом.

        Логотипы берутся из записей лаунчера, а для файлов без записи (добавленных
        вручную) — опознаются по SHA1 через Modrinth. Колбэк зовётся в GUI-потоке.
        """
        iid = inst.get("id")
        cached = self._inst_icon_map.get(iid)
        if cached is not None:
            on_ready(cached)
            return
        waiters = self._inst_icon_inflight.setdefault(iid, [])
        waiters.append(on_ready)
        if len(waiters) > 1:
            return  # карта уже грузится — подождём общий результат

        def done(m, _iid=iid):
            self._inst_icon_map[_iid] = m or {}
            for cb in self._inst_icon_inflight.pop(_iid, []):
                try:
                    cb(self._inst_icon_map[_iid])
                except RuntimeError:
                    pass
        self._bg(core.instance_icon_map, inst, on_result=done)

    def set_busy(self, flag, text=""):
        self.busy = flag
        if flag:
            self.statusbar.show()
            self.progress.setRange(0, 0)
            self.progress.show()
            self.status_label.setText(text)
        else:
            self.progress.hide()
            self.status_label.setText(self.L("Готово", "Done"))

    def on_progress(self, status, value, maximum):
        if maximum > 0:
            self.progress.setRange(0, maximum)
            self.progress.setValue(value)
        else:
            self.progress.setRange(0, 0)
        if status:
            self.status_label.setText(status)

    def on_error(self, msg):
        self.msg_error(self.L("Не получилось", "Failed"), msg)
        self.statusbar.show()
        self.status_label.setText(self.L("Ошибка (лаунчер продолжает работать)",
                                         "Error (the launcher keeps running)"))

    def toast(self, text):
        self.statusbar.show()
        self.status_label.setText(text)


# --------------------------------------------------------------------------- #
#  Точка входа
# --------------------------------------------------------------------------- #
def main():
    # Windows: свой AppUserModelID, иначе панель задач берёт иконку python, а не нашу
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("UnlimitedMC.Launcher")
        except Exception:  # noqa: BLE001
            pass

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    app.setWindowIcon(app_icon())   # логотип в панели задач и в окне

    win = MainWindow()

    # онбординг при первом запуске
    if not win.cfg.get("onboarded"):
        dlg = OnboardingDialog(win)
        if dlg.exec() == QDialog.Accepted:
            nick, theme, accent = dlg.values()
            if nick:
                core.set_username(win.cfg, nick)
                win.local_name = win.cfg["profile"]["username"]
            win.theme, win.accent = theme, accent
            win.cfg["theme"], win.cfg["accent"] = theme, accent
        win.cfg["onboarded"] = True
        core.save_config(win.cfg)
        win.apply_theme()
        win.refresh_account_chip()
        win.refresh_greeting()

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
