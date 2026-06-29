"""
UnlimitedMC — дизайн-система (перенос макета v6 на PySide6).

Здесь живёт всё «оформление»: палитры тем, акцент→монохромный градиент,
логотип-знак, реактивный ambient-фон, иконки навигации, базовые виджеты
(переключатель, кликабельная карточка, flow-сетка) и сборщик QSS.

Логики лаунчера тут нет — только внешний вид и мелкие виджеты.
"""
from __future__ import annotations

from PySide6.QtCore import (
    Qt, QByteArray, QTimer, QSize, QRect, QPoint, QRectF, QPointF, Signal, QEvent,
    QPropertyAnimation, QVariantAnimation, QEasingCurve, QAbstractAnimation, QObject,
)
from PySide6.QtGui import (
    QColor, QIcon, QPixmap, QPainter, QRadialGradient, QLinearGradient, QBrush, QPen,
    QCursor,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QWidget, QFrame, QLayout, QSizePolicy, QAbstractButton, QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect,
)

import math
import functools


# --------------------------------------------------------------------------- #
#  Цвет: помощники (эмуляция CSS color-mix / lighten / darken)
# --------------------------------------------------------------------------- #
def mix(a: str, b: str, t: float) -> str:
    """Смешать цвет a с цветом b на долю t (0..1)."""
    ca, cb = QColor(a), QColor(b)
    r = round(ca.red() * (1 - t) + cb.red() * t)
    g = round(ca.green() * (1 - t) + cb.green() * t)
    bl = round(ca.blue() * (1 - t) + cb.blue() * t)
    return QColor(r, g, bl).name()


def lighten(c: str, t: float) -> str:
    return mix(c, "#FFFFFF", t)


def darken(c: str, t: float) -> str:
    return mix(c, "#000000", t)


def rgba(hexc: str, a: float) -> str:
    c = QColor(hexc)
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


def grad(accent: str) -> str:
    """Монохромный диагональный градиент из акцента (светлее→темнее)."""
    return (f"qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {accent},stop:1 {darken(accent, 0.38)})")


def lerp_color(a: QColor, b: QColor, t: float) -> QColor:
    """Линейная интерполяция двух цветов (для плавных кроссфейдов)."""
    return QColor(
        round(a.red()   + (b.red()   - a.red())   * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue()  + (b.blue()  - a.blue())  * t),
    )


# --------------------------------------------------------------------------- #
#  Анимации: плавное появление виджетов (Qt QSS не умеет transition/keyframes,
#  поэтому «как в макете» делаем через QPropertyAnimation + opacity-эффект)
# --------------------------------------------------------------------------- #
def fade_widget(widget, *, duration=240, delay=0, start=0.0, end=1.0,
                easing=QEasingCurve.OutCubic, disable_at_rest=True):
    """Плавно проявить (или скрыть) виджет через opacity-эффект.

    В покое (после полного проявления) эффект отключается, чтобы не мешать
    нативной отрисовке и не нагружать перерисовку. Безопасно к удалению виджета
    во время задержки: вызовы на уже удалённый C++-объект гасятся.
    """
    eff = widget.graphicsEffect()
    if not isinstance(eff, QGraphicsOpacityEffect):
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
    eff.setEnabled(True)
    eff.setOpacity(start)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setEasingCurve(easing)

    def _finish():
        try:
            if disable_at_rest and end >= 0.999:
                eff.setEnabled(False)
        except RuntimeError:
            pass
    anim.finished.connect(_finish)
    widget._fade_anim = anim  # держим ссылку, чтобы не собрался GC

    def _start():
        try:
            anim.start()
        except RuntimeError:
            pass  # виджет/анимация уже удалены — ничего не делаем

    if delay > 0:
        QTimer.singleShot(int(delay), _start)
    else:
        _start()
    return anim


def stagger_in(widgets, *, step=45, base=0, max_steps=12, duration=300,
               easing=QEasingCurve.OutCubic):
    """Каскадное проявление списка карточек (как `rise` в макете)."""
    for i, w in enumerate(widgets):
        fade_widget(w, duration=duration, delay=base + min(i, max_steps) * step,
                    start=0.0, easing=easing)


class HoverRaise(QObject):
    """Лёгкий «подъём» карточки при наведении (translateY, как :hover в макете).

    Двигает позицию виджета, а не graphics-effect, поэтому спокойно сочетается с
    проявлением через opacity. Уход курсора на дочерний виджет не считается
    «уходом» (проверяем реальное положение курсора), чтобы не было мерцания.
    """
    def __init__(self, widget, lift=4, duration=160):
        super().__init__(widget)
        self._w = widget
        self._lift = lift
        self._base = None
        self._anim = QPropertyAnimation(widget, b"pos", self)
        self._anim.setDuration(duration)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        widget.installEventFilter(self)

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Enter:
            self._set(True)
        elif t == QEvent.Leave:
            if not self._w.rect().contains(self._w.mapFromGlobal(QCursor.pos())):
                self._set(False)
        return False

    def _set(self, up):
        cur = self._w.pos()
        if up:
            if self._base is None:
                self._base = QPoint(cur)
            target = QPoint(self._base.x(), self._base.y() - self._lift)
        else:
            if self._base is None:
                return
            target = QPoint(self._base)
            self._base = None
        self._anim.stop()
        self._anim.setStartValue(cur)
        self._anim.setEndValue(target)
        self._anim.start()


class PressGlow(QObject):
    """Анимированное свечение-тень кнопки: растёт при наведении, проседает при
    нажатии (аналог box-shadow + translateY у `.btn-play:hover` в макете)."""
    def __init__(self, widget, color="#4C8DFF", hover=26, alpha=0.5):
        super().__init__(widget)
        self._w = widget
        self._hover = hover
        self._alpha = alpha
        self._eff = QGraphicsDropShadowEffect(widget)
        self._eff.setOffset(0, 6)
        self._eff.setBlurRadius(0)
        self.set_color(color)
        widget.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"blurRadius", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        widget.installEventFilter(self)

    def set_color(self, color):
        c = QColor(color)
        c.setAlphaF(self._alpha)
        self._eff.setColor(c)

    def _to(self, blur):
        self._anim.stop()
        self._anim.setEndValue(float(blur))
        self._anim.start()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Enter:
            self._to(self._hover)
        elif t == QEvent.Leave:
            self._to(0)
        elif t == QEvent.MouseButtonPress:
            self._to(self._hover * 0.35)
        elif t == QEvent.MouseButtonRelease:
            inside = self._w.rect().contains(self._w.mapFromGlobal(QCursor.pos()))
            self._to(self._hover if inside else 0)
        return False


def raise_on_hover(widget, lift=4):
    """Навесить эффект подъёма карточки при наведении."""
    return HoverRaise(widget, lift=lift)


def glow_on_hover(widget, color="#4C8DFF", hover=26, alpha=0.5):
    """Навесить эффект свечения/нажатия на кнопку. Возвращает PressGlow (его
    `set_color` можно дёргать при смене акцента)."""
    return PressGlow(widget, color=color, hover=hover, alpha=alpha)


# --------------------------------------------------------------------------- #
#  Палитры тем и акценты
# --------------------------------------------------------------------------- #
ACCENTS = ["#4C8DFF", "#7C5CFF", "#2FD06E", "#FB8C3C", "#FF5C7A"]
SOURCE_ACCENT = {"modrinth": "#2FD06E", "curseforge": "#FB8C3C"}

THEMES = {
    "dark": {
        "bg": "#090A0F", "panel": "#0b0d13", "elevated": "#0e111a",
        "text": "#E7EAF2", "muted": "#8A92A6",
        "surface": "rgba(255,255,255,0.035)", "surface2": "rgba(255,255,255,0.06)",
        "border": "rgba(255,255,255,0.07)", "border_strong": "rgba(255,255,255,0.12)",
        "ambient": 0.26, "shadow": "rgba(0,0,0,0.40)",
    },
    "light": {
        "bg": "#EDF0F6", "panel": "#FFFFFF", "elevated": "#FFFFFF",
        "text": "#0F1320", "muted": "#5C6376",
        "surface": "rgba(0,0,0,0.04)", "surface2": "rgba(0,0,0,0.06)",
        "border": "rgba(0,0,0,0.10)", "border_strong": "rgba(0,0,0,0.17)",
        "ambient": 0.42, "shadow": "rgba(0,0,0,0.18)",
    },
}


# --------------------------------------------------------------------------- #
#  SVG: логотип и иконки навигации
# --------------------------------------------------------------------------- #
def logo_svg(accent: str) -> str:
    """Знак: две скруглённые капсулы под 18° с общим диагональным градиентом."""
    c1, c2 = lighten(accent, 0.20), darken(accent, 0.42)
    return (
        '<svg viewBox="0 0 96 96" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<defs><linearGradient id="lg" gradientUnits="userSpaceOnUse" '
        'x1="18" y1="8" x2="82" y2="90">'
        f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/>'
        '</linearGradient></defs>'
        '<rect x="24" y="12" width="24" height="76" rx="12" '
        'transform="rotate(18 36 50)" fill="url(#lg)"/>'
        '<rect x="50" y="47" width="24" height="40" rx="12" '
        'transform="rotate(18 62 67)" fill="url(#lg)"/></svg>'
    )


# inner-пути иконок навигации (из макета v6). fill=True — заливка, иначе обводка.
NAV_ICONS = {
    "home":  ('<path d="M3 11l9-8 9 8M5 10v10h14V10"/>', False),
    "play":  ('<path d="M8 5v14l11-7z"/>', True),
    "community": ('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>'
                  '<circle cx="9" cy="7" r="4"/>'
                  '<path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>', False),
    "modrinth": ('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', False),
    "curseforge": ('<path d="M4 7l8-3 8 3-8 3-8-3zM4 7v6l8 3 8-3V7"/>', False),
    "settings": ('<circle cx="12" cy="12" r="3"/>'
                 '<path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.3 1a7 7 0 0 0-1.7-1l-.3-2.5h-4'
                 'l-.3 2.5a7 7 0 0 0-1.7 1l-2.3-1-2 3.5 2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.5 2.3-1'
                 'a7 7 0 0 0 1.7 1l.3 2.5h4l.3-2.5a7 7 0 0 0 1.7-1l2.3 1 2-3.5-2-1.5a7 7 0 0 0 .1-1z"/>', False),
    "search": ('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>', False),
}


def _icon_svg(inner: str, color: str, fill: bool) -> str:
    if fill:
        return (f'<svg viewBox="0 0 24 24" fill="{color}" '
                f'xmlns="http://www.w3.org/2000/svg">{inner}</svg>')
    return (f'<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'xmlns="http://www.w3.org/2000/svg">{inner}</svg>')


def render_svg(svg: str, size: int) -> QPixmap:
    r = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    dpr = 2
    px = size * dpr
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    # рендерим в ЯВНЫЙ прямоугольник во всю физическую площадь пиксмапа; без него
    # (и при заранее выставленном devicePixelRatio) SVG рисовался в 2× и вылезал/
    # обрезался по верх-лево. dpr выставляем ПОСЛЕ — для чёткости при показе.
    r.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


@functools.lru_cache(maxsize=256)
def _nav_icon_cached(key: str, color: str, size: int) -> QIcon:
    inner, fill = NAV_ICONS[key]
    return QIcon(render_svg(_icon_svg(inner, color, fill), size))


def nav_icon(key: str, color: str, size: int = 18) -> QIcon:
    # кэш по (ключ, цвет, размер): иконки пересоздаются на каждой навигации/смене
    # темы, без кэша это лишний рендер SVG→pixmap и просадка по плавности
    return _nav_icon_cached(key, color, size)


# inner-пути иконок управления окном (кастомная рамка)
WIN_ICONS = {
    "min": '<path d="M5 12h14"/>',
    "max": '<rect x="5.5" y="5.5" width="13" height="13" rx="2.5"/>',
    "restore": '<rect x="8" y="5.5" width="10.5" height="10.5" rx="2"/>'
               '<path d="M5.5 9v7.5a1.5 1.5 0 0 0 1.5 1.5h7.5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
}


@functools.lru_cache(maxsize=64)
def win_icon(name: str, color: str, size: int = 14) -> QIcon:
    """Векторная иконка управления окном (чёткая на любом шрифте/масштабе)."""
    return QIcon(render_svg(_icon_svg(WIN_ICONS[name], color, False), size))


# Иконки интерфейса (кнопки/разделы). Векторные — чтобы не зависеть от того,
# рисует ли система цветные эмодзи (🧩/🗑 на части машин не отображались).
UI_ICONS = {
    "manager":  '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
                '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
                '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
                '<line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/>'
                '<line x1="18" y1="16" x2="22" y2="16"/>',
    "settings": NAV_ICONS["settings"][0],
    "content":  '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8'
                'a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
                '<path d="M3.27 6.96 12 12.01l8.73-5.05"/><path d="M12 22.08V12"/>',
    "worlds":   '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
                '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10'
                ' 15.3 15.3 0 0 1 4-10z"/>',
    "servers":  '<rect x="2" y="2" width="20" height="8" rx="2"/>'
                '<rect x="2" y="14" width="20" height="8" rx="2"/>'
                '<line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
    "shots":    '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
                '<path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
    "trash":    '<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4'
                'a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/>'
                '<line x1="14" y1="11" x2="14" y2="17"/>',
    "folder":   '<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2'
                'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
    "export":   '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "import":   '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "open":     '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
                '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
}


@functools.lru_cache(maxsize=256)
def ui_icon(key: str, color: str, size: int = 16) -> QIcon:
    """Векторная иконка интерфейса нужного цвета (кэш по ключ/цвет/размер)."""
    return QIcon(render_svg(_icon_svg(UI_ICONS[key], color, False), size))


# --------------------------------------------------------------------------- #
#  Логотип-виджет (перерисовывается при смене акцента)
# --------------------------------------------------------------------------- #
class Logo(QSvgWidget):
    def __init__(self, accent: str, size: int = 22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.set_accent(accent)

    def set_accent(self, accent: str):
        self.load(QByteArray(logo_svg(accent).encode("utf-8")))


# --------------------------------------------------------------------------- #
#  Реактивный ambient-фон (мягкие размытые пятна акцента, чуть «дышат»)
# --------------------------------------------------------------------------- #
class Ambient(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # ВНИМАНИЕ: этот виджет — нижняя подложка, которая СОДЕРЖИТ весь интерфейс
        # (сайдбар + контент лежат внутри него). Поэтому НЕЛЬЗЯ ставить
        # WA_TransparentForMouseEvents: Qt при hit-тесте пропускает прозрачный для
        # мыши виджет вместе со ВСЕМИ его детьми — и тогда не работают ни клики,
        # ни hover (сайдбар не раскрывается). Фон рисуется в paintEvent и так.
        self._bg = QColor("#090A0F")
        self._accent = QColor("#4C8DFF")
        self._strength = 0.26
        self._phase = 0.0
        self._color_anim = None
        # ~30 fps вместо ~16 — заметно плавнее «дыхание» фона
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_colors(self, bg: str, accent: str, strength: float):
        """Сменить палитру с плавным кроссфейдом цвета (как transition:--accent)."""
        new_bg, new_accent = QColor(bg), QColor(accent)
        if self._color_anim:
            self._color_anim.stop()
        start_bg, start_acc, start_str = QColor(self._bg), QColor(self._accent), self._strength
        anim = QVariantAnimation(self)
        anim.setDuration(480)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_val(t):
            t = float(t)
            self._bg = lerp_color(start_bg, new_bg, t)
            self._accent = lerp_color(start_acc, new_accent, t)
            self._strength = start_str + (strength - start_str) * t

        anim.valueChanged.connect(_on_val)
        anim.finished.connect(lambda: setattr(self, "_strength", strength))
        anim.start()
        self._color_anim = anim

    def _tick(self):
        self._phase += 0.007
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), self._bg)
        # органичный дрейф из двух гармоник + лёгкое «дыхание» яркости
        ph = self._phase
        dx = (math.sin(ph) + 0.4 * math.sin(ph * 1.7)) * w * 0.022
        dy = (math.cos(ph * 0.8) + 0.4 * math.cos(ph * 1.3)) * h * 0.022
        breath = 0.90 + 0.10 * math.sin(ph * 0.6)

        full = QRectF(self.rect())

        def blob(cx, cy, rad, alpha):
            g = QRadialGradient(QPointF(cx, cy), rad)
            c0 = QColor(self._accent); c0.setAlphaF(max(0.0, min(1.0, alpha)))
            c1 = QColor(self._accent); c1.setAlphaF(0.0)
            g.setColorAt(0.0, c0)
            g.setColorAt(0.6, c1)
            # за 0.6*rad градиент полностью прозрачен — заливаем только реальный
            # bbox пятна, а не всё окно (втрое меньше overdraw → выше FPS)
            er = rad * 0.6
            area = QRectF(cx - er, cy - er, er * 2, er * 2).intersected(full)
            p.fillRect(area, QBrush(g))

        s = self._strength * breath
        blob(w * 0.10 + dx, -h * 0.08 + dy, max(w, h) * 0.85, s)
        blob(w * 1.02 - dx, h * 1.12 - dy, max(w, h) * 0.80, s * 0.7)
        p.end()


# --------------------------------------------------------------------------- #
#  Кликабельная карточка/панель
# --------------------------------------------------------------------------- #
class Clickable(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


# --------------------------------------------------------------------------- #
#  Тумблер (sliding switch) как в макете
# --------------------------------------------------------------------------- #
class Toggle(QAbstractButton):
    def __init__(self, accent: str = "#4C8DFF", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._accent = accent
        self._off = "rgba(140,146,166,0.30)"
        # положение ползунка 0..1 — анимируется, а не прыгает
        self._knob = 1.0 if self.isChecked() else 0.0
        ec = QEasingCurve(QEasingCurve.OutBack)
        ec.setOvershoot(1.1)               # лёгкая «пружинка»
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(ec)
        self._anim.valueChanged.connect(self._on_val)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(46, 26)

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    def _on_val(self, v):
        self._knob = float(v)
        self.update()

    def _animate(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._knob)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        r = h / 2
        track = QRectF(0, 0, w, h)
        k = max(0.0, min(1.0, self._knob))
        # выключенный трек рисуем всегда…
        p.setBrush(QColor(140, 146, 166, 80))
        p.drawRoundedRect(track, r, r)
        # …а акцентный — поверх с прозрачностью = положение ползунка (кроссфейд)
        if k > 0:
            p.setOpacity(k)
            g = QLinearGradient(0, 0, w, 0)
            g.setColorAt(0, QColor(self._accent))
            g.setColorAt(1, QColor(darken(self._accent, 0.38)))
            p.setBrush(QBrush(g))
            p.drawRoundedRect(track, r, r)
            p.setOpacity(1.0)
        d = h - 6
        travel = w - d - 6
        x = 3 + self._knob * travel
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, d, d))
        p.end()


# --------------------------------------------------------------------------- #
#  Flow-сетка (перенос карточек как auto-fill grid в CSS)
# --------------------------------------------------------------------------- #
class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, hspacing=14, vspacing=14):
        super().__init__(parent)
        self._items = []
        self._hs, self._vs = hspacing, vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        x = rect.x() + m.left()
        y = rect.y() + m.top()
        right = rect.right() - m.right()
        line_h = 0
        for it in self._items:
            sz = it.sizeHint()
            w, h = sz.width(), sz.height()
            if x + w > right and line_h > 0:
                x = rect.x() + m.left()
                y = y + line_h + self._vs
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sz))
            x += w + self._hs
            line_h = max(line_h, h)
        return y + line_h - rect.y() + m.bottom()


# --------------------------------------------------------------------------- #
#  Сборщик QSS из палитры и акцента
# --------------------------------------------------------------------------- #
def build_qss(t: dict, accent: str) -> str:
    bg, panel, elevated = t["bg"], t["panel"], t["elevated"]
    text, muted = t["text"], t["muted"]
    surface, surface2 = t["surface"], t["surface2"]
    border, border_strong = t["border"], t["border_strong"]
    g = grad(accent)
    accent_soft = rgba(accent, 0.16)
    accent_soft2 = rgba(accent, 0.10)
    on_accent = "#FFFFFF"

    return f"""
* {{ color:{text}; font-family:'Segoe UI','Inter',sans-serif; font-size:13px; }}

QFrame#sidebar {{ background:{panel}; border-right:1px solid {border}; }}
QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background:transparent; border:none; }}
QFrame#topbar {{ background:transparent; }}
QFrame#statusbar {{ background:{panel}; border-top:1px solid {border}; }}

QLabel#brand {{ font-size:15px; font-weight:700; letter-spacing:0.3px; }}
QLabel#pageTitle {{ font-size:22px; font-weight:700; }}
QLabel#pageSub {{ color:{muted}; font-size:13px; }}
QLabel#sideLabel {{ color:{muted}; font-size:11px; font-weight:600; }}
QLabel#h3 {{ font-size:15px; font-weight:600; }}
QLabel#muted {{ color:{muted}; }}
QLabel#seth {{ color:{muted}; font-size:12px; font-weight:700; }}
QLabel#link {{ color:{accent}; }}

/* навигация */
QToolButton#nav {{ color:{muted}; background:transparent; border:1px solid transparent;
    border-radius:10px; padding:10px; font-size:14px; font-weight:500; text-align:left; }}
QToolButton#nav:hover {{ background:{surface}; color:{text}; }}
QToolButton#nav:checked {{ background:{accent_soft}; color:{text}; border:1px solid {border_strong}; }}

/* аккаунт-чип */
QFrame#account {{ background:{surface}; border:1px solid {border}; border-radius:12px; }}
QFrame#account:hover {{ border:1px solid {border_strong}; }}
QLabel#accAvatar {{ background:{accent_soft}; border-radius:8px; color:{accent};
    font-weight:700; font-size:14px; }}
QLabel#accName {{ font-size:13px; font-weight:600; }}
QLabel#accTag {{ color:{muted}; font-size:11px; }}

/* кнопки */
QPushButton {{ background:{surface}; border:1px solid {border}; border-radius:10px;
    padding:9px 16px; font-weight:600; color:{text}; }}
QPushButton:hover {{ border:1px solid {border_strong}; }}
QPushButton:disabled {{ color:{muted}; background:{surface}; }}
QPushButton#primary {{ background:{g}; border:none; color:{on_accent}; padding:11px 22px;
    font-weight:700; }}
QPushButton#primary:hover {{ background:{g}; }}
QPushButton#mini {{ background:{accent_soft}; color:{accent}; border:1px solid {border_strong};
    border-radius:9px; padding:9px 16px; font-weight:600; }}
QPushButton#mini:hover {{ background:{accent_soft}; border:1px solid {border_strong}; }}
QPushButton#ghost {{ background:transparent; border:1px solid {border}; }}
QPushButton#ghost:hover {{ border:1px solid {border_strong}; }}
QPushButton#danger {{ color:#FF8088; background:transparent; border:1px solid {rgba('#FF5C7A',0.30)}; }}
QPushButton#danger:hover {{ background:{rgba('#FF5C7A',0.12)}; }}
QPushButton#launch {{ background:{accent_soft}; color:{accent}; border:1px solid {border_strong};
    border-radius:9px; padding:8px 14px; font-weight:600; }}
QPushButton#launch:hover {{ background:{g}; color:{on_accent}; border:none; }}
QPushButton#install {{ background:{accent_soft}; color:{accent}; border:1px solid {border_strong};
    border-radius:9px; padding:7px 14px; font-weight:600; }}
QPushButton#install:hover {{ border:1px solid {border_strong}; }}
QPushButton#remove {{ background:{rgba('#FF5C7A',0.12)}; color:#FF8088;
    border:1px solid {rgba('#FF5C7A',0.35)}; border-radius:9px; padding:7px 14px; font-weight:600; }}
QPushButton#remove:hover {{ background:{rgba('#FF5C7A',0.20)}; border:1px solid {rgba('#FF5C7A',0.55)}; }}
QPushButton#page {{ background:{surface}; border:1px solid {border}; border-radius:8px;
    min-width:20px; padding:7px 11px; color:{muted}; font-weight:600; }}
QPushButton#page:hover {{ color:{text}; border:1px solid {border_strong}; }}
QPushButton#page:disabled {{ color:{rgba(muted,0.35)}; border:1px solid {border}; }}
QPushButton#pageCur {{ background:{g}; border:none; border-radius:8px;
    min-width:20px; padding:7px 11px; color:{on_accent}; font-weight:700; }}
QPushButton#segopt {{ background:transparent; border:none; border-radius:8px; padding:8px 16px;
    color:{muted}; font-weight:600; }}
QPushButton#segopt:checked {{ background:{accent_soft}; color:{text}; }}
QPushButton#mgrNav {{ background:transparent; border:none; border-radius:9px; padding:9px 13px;
    color:{muted}; font-weight:600; text-align:left; }}
QPushButton#mgrNav:hover {{ color:{text}; background:{surface}; }}
QPushButton#mgrNav:checked {{ background:{accent_soft}; color:{text}; }}
QFrame#shot {{ background:{surface}; border:1px solid {border}; border-radius:10px; }}
QFrame#shot:hover {{ border:1px solid {border_strong}; }}
QPushButton#chip {{ background:{surface}; border:1px solid {border}; border-radius:999px;
    padding:8px 15px; color:{muted}; font-weight:500; }}
QPushButton#chip:hover {{ color:{text}; }}
QPushButton#chip:checked {{ background:{accent_soft}; border:1px solid {border_strong}; color:{text}; }}
QFrame#seg {{ background:{surface2}; border-radius:11px; }}

/* поля ввода */
QLineEdit, QComboBox, QSpinBox, QTextEdit {{ background:{surface2}; border:1px solid {border_strong};
    border-radius:11px; padding:10px 13px; color:{text}; selection-background-color:{accent_soft}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {{ border:1px solid {accent}; }}
QComboBox::drop-down {{ border:none; width:22px; }}
QComboBox QAbstractItemView {{ background:{elevated}; border:1px solid {border_strong};
    border-radius:10px; selection-background-color:{accent_soft}; outline:none; padding:4px; }}

/* карточки */
QFrame#hero {{ border:1px solid {border_strong}; border-radius:20px;
    background:{rgba(accent,0.10)}; }}
QLabel#heroTitle {{ font-size:30px; font-weight:700; }}
QFrame#select {{ background:{surface2}; border:1px solid {border_strong}; border-radius:12px; }}
QLabel#selectText {{ font-size:14px; font-weight:600; }}
QLabel#selectSub {{ color:{muted}; }}

QFrame#card, QFrame#news, QFrame#inst, QFrame#mod, QFrame#opt {{ background:{surface};
    border:1px solid {border}; border-radius:14px; }}
QFrame#news:hover, QFrame#inst:hover, QFrame#mod:hover {{ border:1px solid {border_strong}; }}
QFrame#newsCover, QFrame#instThumb {{ border-radius:0px; background:{accent_soft2}; }}
QLabel#newsTag, QLabel#modTag {{ color:{accent}; font-size:11px; font-weight:600; }}
QLabel#newsTitle {{ font-size:14px; font-weight:600; }}
QLabel#newsSum {{ color:{muted}; font-size:12px; }}
QLabel#modIcon, QLabel#instIcon {{ background:{accent_soft}; border-radius:11px; font-size:22px; }}
QLabel#optTitle {{ font-size:14px; font-weight:600; }}
QLabel#optDesc {{ color:{muted}; font-size:12px; }}

QFrame#banner {{ border:1px dashed {border_strong}; border-radius:14px; background:transparent; }}
QLabel#bannerTitle {{ font-size:15px; font-weight:700; }}

/* gate / заглушки */
QLabel#gateIcon {{ font-size:46px; }}
QLabel#gateTitle {{ font-size:22px; font-weight:700; }}
QLabel#gateSub {{ color:{muted}; font-size:14px; }}

/* статья */
QLabel#artCover {{ background:{g}; border-radius:16px; font-size:74px; color:{on_accent}; }}
QLabel#artTag {{ color:{accent}; font-size:12px; font-weight:600; }}
QLabel#artTitle {{ font-size:28px; font-weight:700; }}
QLabel#artDate {{ color:{muted}; font-size:13px; }}
QLabel#artBody {{ font-size:15px; }}

/* прогресс */
QProgressBar {{ background:{surface2}; border:none; border-radius:5px; max-height:8px;
    text-align:center; color:transparent; }}
QProgressBar::chunk {{ background:{g}; border-radius:5px; }}

/* скроллбар */
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{rgba(text,0.16)}; border-radius:5px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:{rgba(text,0.30)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* диалоги / сообщения */
QDialog, QMessageBox {{ background:{elevated}; }}
QMessageBox QLabel {{ color:{text}; }}
QToolTip {{ background:{elevated}; color:{text}; border:1px solid {accent};
    padding:6px 8px; border-radius:8px; }}
QLabel#search {{ color:{muted}; }}
QFrame#searchBox {{ background:{surface}; border:1px solid {border}; border-radius:11px; }}

/* кастомная рамка окна (под тему лаунчера) */
QFrame#titlebar {{ background:{panel}; border-bottom:1px solid {border}; }}
QPushButton#winBtn {{ background:transparent; border:none; border-radius:8px;
    color:{muted}; font-size:14px; font-weight:700; padding:0; }}
QPushButton#winBtn:hover {{ background:{surface2}; color:{text}; }}
QPushButton#winClose {{ background:transparent; border:none; border-radius:8px;
    color:{muted}; font-size:14px; font-weight:700; padding:0; }}
QPushButton#winClose:hover {{ background:#E0524A; color:#FFFFFF; }}

/* стилизованные окна-сообщения (ошибки / подтверждения) */
QFrame#msgCard {{ background:{elevated}; border:1px solid {border_strong}; border-radius:18px; }}
QLabel#msgIcon {{ background:{accent_soft}; border-radius:14px; font-size:26px; }}
QLabel#msgIconError {{ background:{rgba('#E0524A',0.16)}; border-radius:14px; font-size:26px; }}
QLabel#msgTitle {{ font-size:18px; font-weight:700; }}
QLabel#msgText {{ color:{muted}; font-size:13.5px; }}
"""
