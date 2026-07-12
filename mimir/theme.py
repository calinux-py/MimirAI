from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)


GLASS_RADII = {
    "compact": 24.0,
    "panel": 20.0,
    "onboarding": 22.0,
    "dialog": 22.0,
    "content": 15.0,
}


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def paint_glass_surface(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    variant: str = "panel",
    reduced_transparency: bool = False,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    shell = rounded_path(rect, radius)

    if reduced_transparency:
        top = QColor(30, 38, 52, 250)
        middle = QColor(21, 28, 40, 252)
        bottom = QColor(13, 19, 29, 254)
    else:
        palettes = {
            "compact": (
                QColor(56, 73, 99, 116),
                QColor(25, 36, 54, 132),
                QColor(13, 22, 37, 150),
            ),
            "panel": (
                QColor(47, 62, 85, 138),
                QColor(24, 34, 51, 150),
                QColor(12, 20, 34, 168),
            ),
            "onboarding": (
                QColor(45, 59, 81, 184),
                QColor(23, 33, 50, 205),
                QColor(12, 19, 32, 218),
            ),
            "dialog": (
                QColor(43, 56, 77, 206),
                QColor(22, 31, 47, 222),
                QColor(12, 19, 31, 232),
            ),
        }
        top, middle, bottom = palettes.get(variant, palettes["panel"])

    shadow_rect = rect.adjusted(1.2, 2.8, -1.2, 0.2)
    painter.fillPath(
        rounded_path(shadow_rect, max(1.0, radius - 0.6)), QColor(0, 6, 18, 68)
    )

    base = QLinearGradient(rect.topLeft(), rect.bottomRight())
    base.setColorAt(0.0, top)
    base.setColorAt(0.48, middle)
    base.setColorAt(1.0, bottom)
    painter.fillPath(shell, base)

    if not reduced_transparency:
        cool_light = QRadialGradient(
            QPointF(
                rect.left() + rect.width() * 0.18, rect.top() - rect.height() * 0.05
            ),
            max(rect.width(), rect.height()) * 0.78,
        )
        cool_light.setColorAt(0.0, QColor(196, 225, 255, 30))
        cool_light.setColorAt(0.42, QColor(133, 184, 241, 11))
        cool_light.setColorAt(1.0, QColor(95, 139, 214, 0))
        painter.save()
        painter.setClipPath(shell)
        painter.fillRect(rect, cool_light)

        ambient = QRadialGradient(
            QPointF(
                rect.right() + rect.width() * 0.08, rect.bottom() + rect.height() * 0.12
            ),
            max(rect.width(), rect.height()) * 0.72,
        )
        ambient.setColorAt(0.0, QColor(154, 169, 220, 15))
        ambient.setColorAt(1.0, QColor(154, 169, 220, 0))
        painter.fillRect(rect, ambient)

        caustic = QLinearGradient(
            QPointF(rect.center().x(), rect.top()),
            QPointF(rect.center().x(), rect.top() + min(92.0, rect.height() * 0.34)),
        )
        caustic.setColorAt(0.0, QColor(255, 255, 255, 31))
        caustic.setColorAt(0.22, QColor(247, 252, 255, 14))
        caustic.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, caustic)
        painter.restore()

    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    rim.setColorAt(0.0, QColor(255, 255, 255, 126 if not reduced_transparency else 164))
    rim.setColorAt(0.32, QColor(230, 242, 255, 60))
    rim.setColorAt(0.68, QColor(141, 160, 190, 30))
    rim.setColorAt(1.0, QColor(5, 10, 20, 88))
    border = QPen(rim, 1.15)
    border.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.strokePath(shell, border)

    inner_rect = rect.adjusted(1.35, 1.35, -1.35, -1.35)
    inner = rounded_path(inner_rect, max(1.0, radius - 1.35))
    painter.strokePath(inner, QPen(QColor(255, 255, 255, 17), 0.8))


def paint_content_surface(
    painter: QPainter,
    rect: QRectF,
    radius: float = GLASS_RADII["content"],
    *,
    elevated: bool = True,
) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if elevated:
        shadow_rect = rect.adjusted(0.8, 2.4, -0.8, 0.2)
        painter.fillPath(
            rounded_path(shadow_rect, max(1.0, radius - 0.5)),
            QColor(0, 7, 19, 44),
        )

    path = rounded_path(rect, radius)
    fill = QLinearGradient(rect.topLeft(), rect.bottomRight())
    fill.setColorAt(0.0, QColor(69, 88, 116, 82))
    fill.setColorAt(0.52, QColor(28, 41, 62, 104))
    fill.setColorAt(1.0, QColor(13, 23, 39, 128))
    painter.fillPath(path, fill)

    rim = QLinearGradient(rect.topLeft(), rect.bottomRight())
    rim.setColorAt(0.0, QColor(255, 255, 255, 83))
    rim.setColorAt(0.45, QColor(205, 225, 250, 37))
    rim.setColorAt(1.0, QColor(15, 24, 39, 64))
    painter.strokePath(path, QPen(rim, 1.0))

    painter.save()
    painter.setClipPath(path)
    sheen = QLinearGradient(rect.topLeft(), QPointF(rect.left(), rect.top() + 54.0))
    sheen.setColorAt(0.0, QColor(255, 255, 255, 13))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillRect(rect, sheen)
    painter.restore()


APP_STYLESHEET = r"""
QWidget {
    color: rgba(246, 249, 255, 224);
    font-family: "Segoe UI Variable Text", "Segoe UI";
    font-size: 13px;
    background: transparent;
}
QToolTip {
    color: rgba(250, 252, 255, 242);
    background-color: rgba(24, 34, 50, 244);
    border: 1px solid rgba(223, 237, 255, 72);
    border-radius: 8px;
    padding: 6px 8px;
}
QLabel#Muted {
    color: rgba(221, 231, 245, 158);
    font-size: 12px;
}
QLabel#OnboardingTitle {
    color: rgba(255, 255, 255, 242);
}
QLabel#OnboardingInfo {
    color: rgba(242, 247, 255, 232);
    background-color: rgba(222, 236, 255, 16);
    border: 1px solid rgba(235, 245, 255, 27);
    border-radius: 11px;
    padding: 12px;
}
QWidget#OnboardingCard {
    background-color: rgba(214, 231, 255, 12);
    border: 1px solid rgba(231, 242, 255, 22);
    border-radius: 12px;
}
QWidget#MaterialToolbar, QWidget#MaterialFooter {
    background-color: rgba(226, 239, 255, 11);
    border: 1px solid rgba(235, 246, 255, 22);
    border-radius: 13px;
}
QPushButton {
    background-color: rgba(235, 244, 255, 16);
    border: 1px solid rgba(238, 247, 255, 22);
    border-radius: 8px;
    padding: 5px 10px;
    color: rgba(248, 251, 255, 226);
}
QPushButton:hover {
    background-color: rgba(238, 247, 255, 31);
    border-color: rgba(241, 249, 255, 53);
    color: rgba(255, 255, 255, 244);
}
QPushButton:pressed {
    background-color: rgba(200, 224, 255, 40);
    border-color: rgba(224, 239, 255, 68);
}
QPushButton:focus {
    border-color: rgba(151, 199, 255, 100);
}
QPushButton:disabled {
    color: rgba(213, 224, 240, 88);
    background-color: rgba(224, 238, 255, 7);
    border-color: rgba(231, 243, 255, 10);
}
QPushButton[iconOnly="true"] {
    padding: 0;
    background-color: rgba(232, 243, 255, 12);
    border-radius: 8px;
}
QPushButton[iconOnly="true"]:hover {
    background-color: rgba(238, 248, 255, 29);
    border-color: rgba(245, 251, 255, 48);
}
QPushButton[iconOnly="true"]:pressed {
    background-color: rgba(196, 222, 255, 42);
}
QPushButton[iconOnly="true"][active="true"] {
    background-color: rgba(101, 220, 177, 42);
    border-color: rgba(128, 239, 199, 67);
}
QPushButton[smartAssist="true"] {
    padding: 4px 9px;
    text-align: center;
    background-color: rgba(132, 187, 255, 39);
    border-color: rgba(175, 213, 255, 57);
}
QPushButton[smartAssist="true"]:hover {
    background-color: rgba(139, 193, 255, 62);
    border-color: rgba(197, 225, 255, 86);
}
QPushButton[quickAsk="true"] {
    padding: 3px 7px;
    font-size: 11px;
}
QPushButton#panelAskSubmit, QPushButton#OnboardingPrimary {
    background-color: rgba(124, 181, 255, 56);
    border-color: rgba(172, 211, 255, 77);
    padding: 4px 11px;
}
QPushButton#panelAskSubmit:hover, QPushButton#OnboardingPrimary:hover {
    background-color: rgba(130, 187, 255, 82);
    border-color: rgba(195, 225, 255, 108);
}
QLineEdit#panelAskInput {
    padding: 5px 9px;
}
QPushButton[tab="true"] {
    background-color: rgba(224, 238, 255, 8);
    border-color: transparent;
    border-radius: 8px;
    padding: 4px 9px;
    color: rgba(229, 237, 249, 181);
}
QPushButton[tab="true"]:hover {
    background-color: rgba(233, 244, 255, 20);
    color: rgba(250, 252, 255, 230);
}
QPushButton[tab="true"][selected="true"] {
    background-color: rgba(196, 220, 255, 32);
    border-color: rgba(233, 244, 255, 31);
    color: rgba(255, 255, 255, 244);
}
QLineEdit, QTextBrowser, QPlainTextEdit, QSpinBox {
    background-color: rgba(8, 18, 34, 76);
    border: 1px solid rgba(231, 243, 255, 19);
    border-radius: 10px;
    padding: 8px;
    selection-background-color: rgba(123, 181, 255, 102);
    selection-color: rgba(255, 255, 255, 245);
    color: rgba(247, 250, 255, 229);
}
QLineEdit:hover, QTextBrowser:hover, QPlainTextEdit:hover, QSpinBox:hover {
    background-color: rgba(10, 22, 41, 88);
    border-color: rgba(235, 246, 255, 31);
}
QLineEdit:focus, QTextBrowser:focus, QPlainTextEdit:focus, QSpinBox:focus {
    background-color: rgba(11, 24, 45, 96);
    border-color: rgba(143, 195, 255, 82);
}
QLineEdit#OnboardingKey[invalid="true"] {
    border-color: rgba(255, 124, 132, 160);
    background-color: rgba(76, 24, 35, 102);
}
QWidget#Panel QTextBrowser, QWidget#Panel QPlainTextEdit {
    padding: 7px;
}
QWidget#AssistResponseBubble QTextBrowser,
QWidget#AssistResponseBubble QTextBrowser:hover,
QWidget#AssistResponseBubble QTextBrowser:focus,
QWidget#ResponseBubble QTextBrowser,
QWidget#ResponseBubble QTextBrowser:hover,
QWidget#ResponseBubble QTextBrowser:focus {
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 4px 2px;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent;
    border: 0;
    margin: 0;
    width: 8px;
    height: 8px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background: rgba(222, 236, 255, 44);
    border-radius: 4px;
    min-height: 22px;
    min-width: 22px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background: rgba(232, 244, 255, 72);
}
QScrollBar::add-line, QScrollBar::sub-line,
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
    border: 0;
}
QCheckBox {
    color: rgba(243, 247, 255, 221);
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 5px;
    background-color: rgba(225, 239, 255, 13);
    border: 1px solid rgba(232, 244, 255, 45);
}
QCheckBox::indicator:hover {
    background-color: rgba(232, 244, 255, 27);
    border-color: rgba(239, 248, 255, 72);
}
QCheckBox::indicator:checked {
    background-color: rgba(106, 218, 178, 98);
    border-color: rgba(142, 239, 205, 125);
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: rgba(230, 241, 255, 12);
    border: none;
    width: 18px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: rgba(236, 246, 255, 30);
}
QSizeGrip {
    background: transparent;
    width: 13px;
    height: 13px;
}
"""


SETTINGS_STYLESHEET = (
    APP_STYLESHEET
    + r"""
QDialog {
    background: transparent;
}
QLabel#SettingsTitle {
    color: rgba(255, 255, 255, 242);
    font-family: "Segoe UI Variable Display", "Segoe UI";
    font-size: 17px;
}
QPushButton#SettingsClose {
    padding: 0;
    font-size: 11px;
    border-radius: 8px;
}
QDialogButtonBox QPushButton {
    min-width: 72px;
    padding: 7px 14px;
}
QDialogButtonBox QPushButton:first-child {
    background-color: rgba(124, 181, 255, 56);
    border-color: rgba(172, 211, 255, 77);
}
"""
)
