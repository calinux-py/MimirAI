from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QBuffer,
    QEvent,
    QIODevice,
    QObject,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QSyntaxHighlighter,
    QTextBlockUserData,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRubberBand,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from mimir.config import Settings
from mimir.events import EventBus
from mimir import win32


class StatusDot(QWidget):
    COLORS = {
        "idle": QColor(118, 176, 255),
        "busy": QColor(64, 220, 160),
        "transcribing": QColor(230, 55, 55),
        "paused": QColor(240, 200, 55),
        "recovering": QColor(255, 120, 120),
    }

    def __init__(self, diameter: int = 16) -> None:
        super().__init__()
        self._d = max(8, int(diameter))
        self._state = "idle"
        self._error_flash_on = True
        self._error_timer = QTimer(self)
        self._error_timer.setInterval(450)
        self._error_timer.timeout.connect(self._toggle_error_flash)
        self.setFixedSize(self._d, self._d)

    def _toggle_error_flash(self) -> None:
        if self._state == "error":
            self._error_flash_on = not self._error_flash_on
            self.update()

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "error":
            if not self._error_timer.isActive():
                self._error_flash_on = True
                self._error_timer.start()
        else:
            self._error_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        if self._state == "error":
            if self._error_flash_on:
                color = QColor(255, 255, 255)
            else:
                color = QColor(255, 255, 255, 28)
        else:
            color = self.COLORS.get(self._state, self.COLORS["idle"])
        glow = QColor(color)
        glow.setAlpha(80 if self._state != "error" or self._error_flash_on else 24)
        painter.setBrush(glow)
        d = self._d
        painter.drawEllipse(1, 1, d - 2, d - 2)
        painter.setBrush(color)
        m = max(2, round(d * 0.25))
        inner = d - 2 * m
        painter.drawEllipse(m, m, inner, inner)


class _ModeStack(QStackedWidget):
    def minimumSizeHint(self) -> QSize:
        w = self.currentWidget()
        if w is not None:
            return w.minimumSizeHint()
        return super().minimumSizeHint()

    def sizeHint(self) -> QSize:
        w = self.currentWidget()
        if w is not None:
            return w.sizeHint()
        return super().sizeHint()


def _popup_shell_alpha(alpha: int) -> int:
    return max(6, min(255, int(alpha)))


def _popup_menu_stylesheet(alpha: int) -> str:
    fill_alpha = _popup_shell_alpha(alpha)
    return f"""
QMenu {{
    background-color: rgba(0, 0, 0, {fill_alpha});
    border: 1px solid rgba(170, 176, 188, 58);
    border-radius: 10px;
    padding: 4px;
    color: rgba(248, 250, 255, 214);
    font-family: Segoe UI;
    font-size: 13px;
}}
QMenu::separator {{
    height: 1px;
    background: rgba(210, 225, 255, 38);
    margin: 4px 6px;
}}
QMenu::item {{
    background-color: transparent;
    border-radius: 6px;
    padding: 5px 14px 5px 9px;
    margin: 0px 1px;
}}
QMenu::item:selected {{
    background-color: rgba(255, 255, 255, 24);
    color: rgba(255, 255, 255, 232);
}}
QMenu::item:disabled {{
    color: rgba(248, 250, 255, 90);
}}
"""


def style_context_menu(menu: QMenu, alpha: int) -> None:
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(_popup_menu_stylesheet(alpha))


class _GlassTooltip(QWidget):
    RADIUS = 8.0

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.NoDropShadowWindowHint
            | Qt.BypassGraphicsProxyWidget,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._alpha = 200
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setMaximumWidth(320)
        self._label.setStyleSheet(
            "color: rgb(248, 250, 255);"
            "font-family: Segoe UI;"
            "font-size: 12px;"
            "background: transparent;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 4, 7, 4)
        layout.addWidget(self._label)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_for(
        self, owner: QWidget, global_pos: QPoint, text: str, alpha: int
    ) -> None:
        if not text:
            self.hide_tooltip()
            return
        self._alpha = _popup_shell_alpha(alpha)
        self._label.setText(text)
        self.adjustSize()
        screen = owner.screen() if owner is not None else None
        geo = screen.availableGeometry() if screen is not None else None
        x = global_pos.x() + 16
        y = global_pos.y() + 22
        if geo is not None:
            if x + self.width() > geo.right():
                x = geo.right() - self.width()
            if y + self.height() > geo.bottom():
                y = global_pos.y() - self.height() - 14
            x = max(geo.left(), x)
            y = max(geo.top(), y)
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        win32.force_topmost(int(self.winId()))
        self._hide_timer.start(9000)

    def hide_tooltip(self) -> None:
        self._hide_timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        painter.fillPath(path, QColor(0, 0, 0, self._alpha))
        painter.strokePath(path, QPen(QColor(170, 176, 188, 58), 1))


MARKDOWN_DOCUMENT_STYLE = """
body {
    color: rgba(248, 250, 255, 218);
    font-family: Segoe UI;
    font-size: 12px;
}
h1 { font-size: 18px; }
h2 { font-size: 15px; }
h3 { font-size: 13px; }
h4, h5, h6 { font-size: 13px; font-weight: 700; }
p { margin-top: 0; margin-bottom: 6px; }
ul, ol { margin-top: 0; margin-bottom: 6px; }
blockquote {
    color: rgba(226, 232, 244, 170);
    margin-left: 0;
    padding-left: 10px;
    border-left: 2px solid rgba(210, 225, 255, 92);
}
code {
    background-color: rgba(255, 255, 255, 22);
    border-radius: 4px;
}
pre {
    background-color: rgba(7, 12, 20, 190);
    border: 1px solid rgba(210, 225, 255, 54);
    border-radius: 6px;
    padding: 8px;
    color: rgba(238, 244, 255, 232);
    font-family: Cascadia Code, Consolas, monospace;
    font-size: 11px;
}
table {
    border-collapse: collapse;
}
th, td {
    border: 1px solid rgba(210, 216, 228, 70);
    padding: 4px 7px;
}
"""


_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")


class _CodeBlockData(QTextBlockUserData):
    def __init__(self, language: str = "") -> None:
        super().__init__()
        self.language = language


def _markdown_code_blocks(markdown: str) -> list[tuple[str, int]]:
    blocks: list[tuple[str, int]] = []
    in_fence = False
    fence_marker = ""
    fence_len = 0
    language = ""
    line_count = 0

    for line in markdown.splitlines():
        if in_fence:
            stripped = line.lstrip()
            if stripped.startswith(fence_marker * fence_len):
                blocks.append((language, max(1, line_count)))
                in_fence = False
                fence_marker = ""
                fence_len = 0
                language = ""
                line_count = 0
            else:
                line_count += 1
            continue

        match = _FENCE_RE.match(line)
        if not match:
            continue
        fence = match.group(1)
        info = match.group(2).strip()
        in_fence = True
        fence_marker = fence[0]
        fence_len = len(fence)
        language = _normalize_code_language(info)
        line_count = 0

    if in_fence:
        blocks.append((language, max(1, line_count)))
    return blocks


def _normalize_code_language(info: str) -> str:
    language = info.split(maxsplit=1)[0].strip("{}[]()").lower()
    aliases = {
        "py": "python",
        "python3": "python",
        "js": "javascript",
        "jsx": "javascript",
        "mjs": "javascript",
        "cjs": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "ps1": "powershell",
        "pwsh": "powershell",
        "sh": "bash",
        "shell": "bash",
        "zsh": "bash",
        "yml": "yaml",
        "htm": "html",
        "xml": "html",
        "cs": "csharp",
        "c#": "csharp",
        "cpp": "c++",
        "cc": "c++",
        "cxx": "c++",
    }
    return aliases.get(language, language)


class MarkdownCodeHighlighter(QSyntaxHighlighter):
    _KEYWORDS = {
        "python": {
            "False",
            "None",
            "True",
            "and",
            "as",
            "assert",
            "async",
            "await",
            "break",
            "class",
            "continue",
            "def",
            "del",
            "elif",
            "else",
            "except",
            "finally",
            "for",
            "from",
            "global",
            "if",
            "import",
            "in",
            "is",
            "lambda",
            "nonlocal",
            "not",
            "or",
            "pass",
            "raise",
            "return",
            "try",
            "while",
            "with",
            "yield",
        },
        "javascript": {
            "await",
            "async",
            "break",
            "case",
            "catch",
            "class",
            "const",
            "continue",
            "debugger",
            "default",
            "delete",
            "do",
            "else",
            "export",
            "extends",
            "false",
            "finally",
            "for",
            "from",
            "function",
            "if",
            "import",
            "in",
            "instanceof",
            "let",
            "new",
            "null",
            "return",
            "switch",
            "this",
            "throw",
            "true",
            "try",
            "typeof",
            "undefined",
            "var",
            "void",
            "while",
            "yield",
        },
        "typescript": {
            "abstract",
            "any",
            "as",
            "async",
            "await",
            "boolean",
            "break",
            "case",
            "catch",
            "class",
            "const",
            "continue",
            "default",
            "else",
            "enum",
            "export",
            "extends",
            "false",
            "finally",
            "for",
            "from",
            "function",
            "if",
            "implements",
            "import",
            "in",
            "interface",
            "keyof",
            "let",
            "namespace",
            "never",
            "new",
            "null",
            "number",
            "private",
            "protected",
            "public",
            "readonly",
            "return",
            "string",
            "switch",
            "this",
            "throw",
            "true",
            "try",
            "type",
            "typeof",
            "undefined",
            "unknown",
            "var",
            "void",
            "while",
        },
        "csharp": {
            "abstract",
            "as",
            "async",
            "await",
            "base",
            "bool",
            "break",
            "case",
            "catch",
            "class",
            "const",
            "continue",
            "decimal",
            "default",
            "do",
            "double",
            "else",
            "enum",
            "false",
            "finally",
            "float",
            "for",
            "foreach",
            "if",
            "in",
            "int",
            "interface",
            "internal",
            "is",
            "long",
            "namespace",
            "new",
            "null",
            "object",
            "out",
            "override",
            "private",
            "protected",
            "public",
            "readonly",
            "record",
            "return",
            "sealed",
            "static",
            "string",
            "struct",
            "switch",
            "this",
            "throw",
            "true",
            "try",
            "using",
            "var",
            "virtual",
            "void",
            "while",
        },
        "java": {
            "abstract",
            "boolean",
            "break",
            "case",
            "catch",
            "class",
            "const",
            "continue",
            "default",
            "do",
            "double",
            "else",
            "enum",
            "extends",
            "false",
            "final",
            "finally",
            "float",
            "for",
            "if",
            "implements",
            "import",
            "instanceof",
            "int",
            "interface",
            "long",
            "new",
            "null",
            "package",
            "private",
            "protected",
            "public",
            "return",
            "static",
            "string",
            "super",
            "switch",
            "this",
            "throw",
            "throws",
            "true",
            "try",
            "void",
            "while",
        },
        "c++": {
            "auto",
            "bool",
            "break",
            "case",
            "catch",
            "char",
            "class",
            "const",
            "constexpr",
            "continue",
            "default",
            "delete",
            "do",
            "double",
            "else",
            "enum",
            "false",
            "float",
            "for",
            "if",
            "include",
            "int",
            "long",
            "namespace",
            "new",
            "nullptr",
            "private",
            "protected",
            "public",
            "return",
            "short",
            "sizeof",
            "static",
            "struct",
            "switch",
            "template",
            "this",
            "throw",
            "true",
            "try",
            "typedef",
            "typename",
            "using",
            "virtual",
            "void",
            "while",
        },
        "sql": {
            "add",
            "alter",
            "and",
            "as",
            "asc",
            "between",
            "by",
            "case",
            "create",
            "delete",
            "desc",
            "distinct",
            "drop",
            "else",
            "end",
            "from",
            "group",
            "having",
            "in",
            "insert",
            "into",
            "is",
            "join",
            "left",
            "like",
            "limit",
            "not",
            "null",
            "on",
            "or",
            "order",
            "outer",
            "right",
            "select",
            "set",
            "table",
            "then",
            "union",
            "update",
            "values",
            "when",
            "where",
        },
        "powershell": {
            "begin",
            "break",
            "catch",
            "class",
            "continue",
            "data",
            "do",
            "dynamicparam",
            "else",
            "elseif",
            "end",
            "exit",
            "filter",
            "finally",
            "for",
            "foreach",
            "from",
            "function",
            "if",
            "in",
            "param",
            "process",
            "return",
            "switch",
            "throw",
            "trap",
            "try",
            "until",
            "using",
            "var",
            "while",
        },
        "bash": {
            "case",
            "do",
            "done",
            "elif",
            "else",
            "esac",
            "fi",
            "for",
            "function",
            "if",
            "in",
            "select",
            "then",
            "until",
            "while",
        },
    }

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._base = self._format("#e6edf7")
        self._keyword = self._format("#c678dd", bold=True)
        self._builtin = self._format("#61afef")
        self._string = self._format("#98c379")
        self._number = self._format("#d19a66")
        self._comment = self._format("#7f8b9b", italic=True)
        self._function = self._format("#61afef")
        self._type = self._format("#e5c07b")
        self._tag = self._format("#e06c75")
        self._attr = self._format("#d19a66")
        self._operator = self._format("#56b6c2")

    def highlightBlock(self, text: str) -> None:
        data = self.currentBlock().userData()
        if not isinstance(data, _CodeBlockData):
            return

        if text:
            self.setFormat(0, len(text), self._base)
        language = data.language
        if language in {"text", "plaintext", "plain", "txt"}:
            return
        if language in {"html", "xml"}:
            self._highlight_markup(text)
            return
        if language == "json":
            self._highlight_json(text)
            return
        if language in {"css", "scss", "sass"}:
            self._highlight_css(text)
            return
        self._highlight_common(text, language)

    def _format(
        self, color: str, *, bold: bool = False, italic: bool = False
    ) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        fmt.setFontFamily("Cascadia Code")
        fmt.setFontFixedPitch(True)
        if bold:
            fmt.setFontWeight(QFont.Bold)
        if italic:
            fmt.setFontItalic(True)
        return fmt

    def _apply(
        self, text: str, pattern: str, fmt: QTextCharFormat, flags: int = 0
    ) -> None:
        for match in re.finditer(pattern, text, flags):
            self.setFormat(match.start(), match.end() - match.start(), fmt)

    def _highlight_common(self, text: str, language: str) -> None:
        comment_pattern = (
            r"#.*" if language in {"python", "bash", "powershell", "yaml"} else r"//.*"
        )
        if language == "sql":
            comment_pattern = r"--.*"
        self._apply(text, comment_pattern, self._comment)
        self._apply(
            text,
            r"(?<!\\)(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)",
            self._string,
        )
        self._apply(text, r"\b(0x[0-9a-fA-F]+|\d+(?:\.\d+)?)\b", self._number)
        self._apply(text, r"\b([A-Z][A-Za-z0-9_]*)(?=[\s\[{(])", self._type)
        self._apply(text, r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?=\()", self._function)
        if language == "python":
            self._apply(text, r"(?<!\w)@[A-Za-z_][A-Za-z0-9_.]*", self._builtin)
        if language in {"powershell", "bash"}:
            self._apply(text, r"[$@][A-Za-z_][A-Za-z0-9_:-]*", self._builtin)
        keywords = self._KEYWORDS.get(language, set())
        if keywords:
            self._apply(
                text,
                r"\b(" + "|".join(sorted(map(re.escape, keywords))) + r")\b",
                self._keyword,
                re.IGNORECASE if language == "sql" else 0,
            )
        self._apply(text, r"[-+*/%=!<>|&^~:]+", self._operator)

    def _highlight_json(self, text: str) -> None:
        self._apply(text, r"\"(?:\\.|[^\"\\])*\"(?=\s*:)", self._attr)
        self._apply(text, r"(?<=:\s)\"(?:\\.|[^\"\\])*\"", self._string)
        self._apply(text, r"\b(true|false|null)\b", self._keyword)
        self._apply(text, r"\b-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b", self._number)

    def _highlight_markup(self, text: str) -> None:
        self._apply(text, r"<!--.*?-->", self._comment)
        self._apply(text, r"</?[\w:-]+", self._tag)
        self._apply(text, r"\b[\w:-]+(?=\=)", self._attr)
        self._apply(text, r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", self._string)
        self._apply(text, r"/?>", self._tag)

    def _highlight_css(self, text: str) -> None:
        self._apply(text, r"/\*.*?\*/", self._comment)
        self._apply(text, r"#[0-9a-fA-F]{3,8}\b", self._number)
        self._apply(text, r"\b[a-zA-Z-]+(?=\s*:)", self._attr)
        self._apply(text, r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", self._string)
        self._apply(text, r"\b\d+(?:\.\d+)?(?:px|rem|em|%|vh|vw|s|ms)?\b", self._number)
        self._apply(text, r"[{}:;,>.#]", self._operator)


_ICON_COLOR = QColor(248, 250, 255, 226)


def _make_glyph_icon(kind: str, size: int = 16) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = QPen(_ICON_COLOR, 1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)

    cx = size / 2
    cy = size / 2

    if kind == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ICON_COLOR)
        bar_w = 2.6
        bar_h = size * 0.55
        gap = 2.4
        y = cy - bar_h / 2
        painter.drawRoundedRect(QRectF(cx - gap / 2 - bar_w, y, bar_w, bar_h), 1.0, 1.0)
        painter.drawRoundedRect(QRectF(cx + gap / 2, y, bar_w, bar_h), 1.0, 1.0)
    elif kind == "play":
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ICON_COLOR)
        pad = size * 0.26
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(pad, pad),
                    QPointF(size - pad, cy),
                    QPointF(pad, size - pad),
                ]
            )
        )
    elif kind in {"arrow_left", "arrow_right"}:
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        direction = -1 if kind == "arrow_left" else 1
        tip_x = cx + direction * size * 0.25
        tail_x = cx - direction * size * 0.25
        painter.drawLine(QPointF(tail_x, cy), QPointF(tip_x, cy))
        painter.drawLine(
            QPointF(tip_x, cy),
            QPointF(cx, cy - size * 0.25),
        )
        painter.drawLine(
            QPointF(tip_x, cy),
            QPointF(cx, cy + size * 0.25),
        )
    elif kind == "settings":
        gear_pen = QPen(_ICON_COLOR, 1.35)
        gear_pen.setCapStyle(Qt.RoundCap)
        gear_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(gear_pen)
        painter.setBrush(Qt.NoBrush)
        n_teeth = 6
        r_outer = size * 0.40
        r_inner = size * 0.28
        gear_points: list[QPointF] = []
        for i in range(n_teeth * 2):
            angle = -math.pi / 2 + i * math.pi / n_teeth
            r = r_outer if i % 2 == 0 else r_inner
            gear_points.append(
                QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
            )
        painter.drawPolygon(QPolygonF(gear_points))
        r_hole = size * 0.09
        painter.drawEllipse(QPointF(cx, cy), r_hole, r_hole)
    elif kind == "compact":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        margin = size * 0.20
        y_top = size * 0.34
        y_bot = size * 0.66
        painter.drawLine(QPointF(margin, y_top), QPointF(size - margin, y_top))
        painter.drawLine(
            QPointF(margin + size * 0.10, y_bot),
            QPointF(size - margin - size * 0.10, y_bot),
        )
    elif kind == "sparkle":
        painter.setPen(Qt.NoPen)
        painter.setBrush(_ICON_COLOR)
        outer = size * 0.46
        inner = size * 0.14
        points: list[QPointF] = []
        for i in range(8):
            angle = -math.pi / 2 + i * math.pi / 4
            r = outer if i % 2 == 0 else inner
            points.append(QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle)))
        painter.drawPolygon(QPolygonF(points))
    elif kind == "clock":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        r = size * 0.34
        painter.drawEllipse(QPointF(cx, cy), r, r)
        painter.drawLine(QPointF(cx, cy), QPointF(cx, cy - r * 0.55))
        painter.drawLine(QPointF(cx, cy), QPointF(cx + r * 0.42, cy + r * 0.12))
    elif kind == "microphone":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        capsule = QRectF(cx - size * 0.16, size * 0.18, size * 0.32, size * 0.45)
        painter.drawRoundedRect(capsule, size * 0.16, size * 0.16)
        painter.drawArc(
            QRectF(cx - size * 0.30, size * 0.32, size * 0.60, size * 0.42),
            205 * 16,
            130 * 16,
        )
        painter.drawLine(QPointF(cx, size * 0.74), QPointF(cx, size * 0.86))
        painter.drawLine(
            QPointF(cx - size * 0.16, size * 0.86),
            QPointF(cx + size * 0.16, size * 0.86),
        )
    elif kind == "export":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        tray = QRectF(size * 0.20, size * 0.61, size * 0.60, size * 0.20)
        painter.drawRoundedRect(tray, size * 0.04, size * 0.04)
        painter.drawLine(QPointF(cx, size * 0.18), QPointF(cx, size * 0.61))
        painter.drawLine(
            QPointF(cx, size * 0.18), QPointF(cx - size * 0.17, size * 0.35)
        )
        painter.drawLine(
            QPointF(cx, size * 0.18), QPointF(cx + size * 0.17, size * 0.35)
        )
    elif kind == "trash":
        trash_pen = QPen(_ICON_COLOR, 1.35)
        trash_pen.setCapStyle(Qt.RoundCap)
        trash_pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(trash_pen)
        painter.setBrush(Qt.NoBrush)
        lid_y = size * 0.29
        lid_half_w = size * 0.30
        handle = QRectF(cx - size * 0.11, size * 0.18, size * 0.22, size * 0.08)
        painter.drawRoundedRect(handle, size * 0.03, size * 0.03)
        painter.drawLine(
            QPointF(cx - lid_half_w, lid_y),
            QPointF(cx + lid_half_w, lid_y),
        )
        body_top = size * 0.37
        body_bottom = size * 0.78
        half_top = size * 0.24
        half_bottom = size * 0.18
        body = QPainterPath()
        body.moveTo(cx - half_top, body_top)
        body.lineTo(cx + half_top, body_top)
        body.lineTo(cx + half_bottom, body_bottom)
        body.lineTo(cx - half_bottom, body_bottom)
        body.closeSubpath()
        painter.drawPath(body)
        painter.drawLine(
            QPointF(cx - size * 0.08, body_top + size * 0.08),
            QPointF(cx - size * 0.06, body_bottom - size * 0.08),
        )
        painter.drawLine(
            QPointF(cx + size * 0.08, body_top + size * 0.08),
            QPointF(cx + size * 0.06, body_bottom - size * 0.08),
        )
    elif kind == "confirm_check":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        pen_chk = QPen(_ICON_COLOR, 2.0)
        pen_chk.setCapStyle(Qt.RoundCap)
        pen_chk.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen_chk)
        x0, y0 = size * 0.22, cy
        x1, y1 = size * 0.42, cy + size * 0.26
        x2, y2 = size * 0.78, size * 0.24
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif kind == "lock":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        body_w = size * 0.68
        body_h = size * 0.38
        body_x = cx - body_w / 2
        body_y = size * 0.52
        body = QRectF(body_x, body_y, body_w, body_h)
        painter.drawRoundedRect(body, 1.0, 1.0)
        shackle_w = body_w * 0.6
        shackle_x = cx - shackle_w / 2
        shackle_top = size * 0.15
        shackle_y = shackle_top + shackle_w / 2
        shackle = QPainterPath()
        shackle.moveTo(shackle_x, body_y)
        shackle.lineTo(shackle_x, shackle_y)
        shackle.arcTo(
            QRectF(shackle_x, shackle_top, shackle_w, shackle_w), 180.0, -180.0
        )
        shackle.lineTo(shackle_x + shackle_w, body_y)
        painter.drawPath(shackle)
    elif kind == "shield":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(cx - 6.0, cy - 6.0)
        path.lineTo(cx + 6.0, cy - 6.0)
        path.lineTo(cx + 6.0, cy - 1.0)
        path.quadTo(QPointF(cx + 6.0, cy + 5.0), QPointF(cx, cy + 7.5))
        path.quadTo(QPointF(cx - 6.0, cy + 5.0), QPointF(cx - 6.0, cy - 1.0))
        path.closeSubpath()
        painter.drawPath(path)
    elif kind == "eye":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(cx - 7.5, cy - 4.5, 15.0, 9.0), 0, 180 * 16)
        painter.drawArc(QRectF(cx - 7.5, cy - 4.5, 15.0, 9.0), 0, -180 * 16)
        painter.drawEllipse(QPointF(cx, cy), 2.5, 2.5)
    elif kind == "transcript":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        page = QRectF(cx - 5.5, cy - 7.0, 11.0, 14.0)
        painter.drawRoundedRect(page, 1.2, 1.2)
        for y in (cy - 3.0, cy, cy + 3.0):
            painter.drawLine(QPointF(cx - 2.8, y), QPointF(cx + 2.8, y))
    elif kind == "audio":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(cx - 4.0, cy - 3.0)
        path.lineTo(cx - 1.0, cy - 3.0)
        path.lineTo(cx + 3.0, cy - 6.0)
        path.lineTo(cx + 3.0, cy + 6.0)
        path.lineTo(cx - 1.0, cy + 3.0)
        path.lineTo(cx - 4.0, cy + 3.0)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(cx + 1.0, cy - 3.0, 4.0, 6.0), -60 * 16, 120 * 16)
    elif kind == "mouse":
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        body = QRectF(cx - 4.5, cy - 7.0, 9.0, 14.0)
        painter.drawRoundedRect(body, 4.5, 4.5)
        painter.drawLine(QPointF(cx, cy - 7.0), QPointF(cx, cy - 1.0))

    painter.end()
    return QIcon(pixmap)


class MarkdownTextBrowser(QTextBrowser):
    def __init__(self, placeholder: str = "") -> None:
        super().__init__()
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setPlaceholderText(placeholder)
        self.document().setDefaultStyleSheet(MARKDOWN_DOCUMENT_STYLE)
        self._code_highlighter = MarkdownCodeHighlighter(self.document())
        self._auto_follow_bottom = True
        self._scroll_request_pending = False
        self._content_overflows = False
        scrollbar = self.verticalScrollBar()
        self._scroll_animation = QPropertyAnimation(scrollbar, b"value", self)
        self._scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_animation.finished.connect(self._on_scroll_animation_finished)
        scrollbar.sliderPressed.connect(self._on_scrollbar_pressed)
        scrollbar.sliderReleased.connect(self._sync_auto_follow_from_scrollbar)

    def set_markdown(self, markdown: str) -> None:
        scrollbar = self.verticalScrollBar()
        previous_value = scrollbar.value()
        previous_maximum = scrollbar.maximum()
        animation_was_running = (
            self._scroll_animation.state() == QAbstractAnimation.Running
        )
        should_follow_bottom = self._auto_follow_bottom and (
            animation_was_running or previous_value >= previous_maximum - 4
        )

        self._scroll_animation.stop()
        self.viewport().setUpdatesEnabled(False)
        try:
            self.document().setDefaultStyleSheet(MARKDOWN_DOCUMENT_STYLE)
            self.document().setMarkdown(markdown, QTextDocument.MarkdownDialectGitHub)
            self._tag_code_blocks(_markdown_code_blocks(markdown))
            self._code_highlighter.rehighlight()
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        finally:
            self.viewport().setUpdatesEnabled(True)
            self.viewport().update()

        if scrollbar.maximum() == 0:
            self._auto_follow_bottom = True
        elif should_follow_bottom:
            self._auto_follow_bottom = True
            self._request_smooth_scroll_to_bottom()
        else:
            self._auto_follow_bottom = False
        self._schedule_scrollbar_visibility_update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._schedule_scrollbar_visibility_update()

    def _schedule_scrollbar_visibility_update(self) -> None:
        if getattr(self, "_scrollbar_visibility_update_pending", False):
            return
        self._scrollbar_visibility_update_pending = True
        QTimer.singleShot(0, self._update_scrollbar_visibility)

    def _update_scrollbar_visibility(self) -> None:
        self._scrollbar_visibility_update_pending = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        document_height = math.ceil(self.document().size().height())
        needs_scrollbar = document_height > self.viewport().height() + 8
        self._content_overflows = needs_scrollbar
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if needs_scrollbar else Qt.ScrollBarAlwaysOff
        )
        if not needs_scrollbar:
            self.verticalScrollBar().setValue(0)
            self._auto_follow_bottom = True

    def _tag_code_blocks(self, code_blocks: list[tuple[str, int]]) -> None:
        code_index = 0
        remaining_lines = 0
        language = ""
        block = self.document().firstBlock()
        while block.isValid():
            if block.blockFormat().nonBreakableLines():
                if remaining_lines <= 0:
                    if code_index >= len(code_blocks):
                        block = block.next()
                        continue
                    language, remaining_lines = code_blocks[code_index]
                    code_index += 1
                self._style_code_block(block, language)
                remaining_lines -= 1
            else:
                remaining_lines = 0
            block = block.next()

    def _style_code_block(self, block, language: str) -> None:
        block.setUserData(_CodeBlockData(language))
        cursor = QTextCursor(block)
        block_format = cursor.blockFormat()
        block_format.setBackground(QColor(7, 12, 20, 190))
        block_format.setLeftMargin(9)
        block_format.setRightMargin(9)
        cursor.mergeBlockFormat(block_format)

    def _request_smooth_scroll_to_bottom(self) -> None:
        if self._scroll_request_pending:
            return
        self._scroll_request_pending = True
        QTimer.singleShot(0, self._smooth_scroll_to_bottom)

    def _smooth_scroll_to_bottom(self) -> None:
        self._scroll_request_pending = False
        if not self._auto_follow_bottom:
            return

        scrollbar = self.verticalScrollBar()
        current = scrollbar.value()
        target = scrollbar.maximum()
        distance = target - current
        if distance <= 1:
            scrollbar.setValue(target)
            return

        self._scroll_animation.stop()
        self._scroll_animation.setDuration(max(80, min(180, 70 + distance // 2)))
        self._scroll_animation.setStartValue(current)
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def _on_scroll_animation_finished(self) -> None:
        if not self._auto_follow_bottom:
            return
        scrollbar = self.verticalScrollBar()
        if scrollbar.value() < scrollbar.maximum() - 1:
            self._request_smooth_scroll_to_bottom()
        else:
            scrollbar.setValue(scrollbar.maximum())

    def _on_scrollbar_pressed(self) -> None:
        self._auto_follow_bottom = False
        self._scroll_animation.stop()

    def _sync_auto_follow_from_scrollbar(self) -> None:
        scrollbar = self.verticalScrollBar()
        self._auto_follow_bottom = scrollbar.value() >= scrollbar.maximum() - 4

    def wheelEvent(self, event) -> None:
        if not self._content_overflows:
            event.accept()
            return
        self._auto_follow_bottom = False
        self._scroll_animation.stop()
        super().wheelEvent(event)
        QTimer.singleShot(0, self._sync_auto_follow_from_scrollbar)

    def keyPressEvent(self, event) -> None:
        scroll_keys = {
            Qt.Key_Up,
            Qt.Key_Down,
            Qt.Key_PageUp,
            Qt.Key_PageDown,
            Qt.Key_Home,
            Qt.Key_End,
            Qt.Key_Space,
        }
        if event.key() in scroll_keys and not self._content_overflows:
            event.accept()
            return
        if event.key() in scroll_keys:
            self._auto_follow_bottom = False
            self._scroll_animation.stop()
        super().keyPressEvent(event)
        if event.key() in scroll_keys:
            QTimer.singleShot(0, self._sync_auto_follow_from_scrollbar)


class AssistResponseBubble(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QPainterPath()
        path.addRoundedRect(rect, 13.5, 13.5)
        painter.fillPath(path, QColor(0, 0, 0, 48))

        border = QPen(QColor(210, 225, 255, 72), 1.25)
        border.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, border)


class ResponseCopyButton(QPushButton):
    def __init__(
        self, callback: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._copied = False
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Copy response")
        self.setAccessibleName("Copy response")
        self.clicked.connect(callback)

    def set_copied(self, copied: bool) -> None:
        self._copied = copied
        self.setToolTip("Copied" if copied else "Copy response")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)

        if not self.isEnabled():
            fill_alpha, border_alpha, icon_alpha = 4, 24, 70
        elif self.isDown():
            fill_alpha, border_alpha, icon_alpha = 42, 112, 238
        elif self.underMouse():
            fill_alpha, border_alpha, icon_alpha = 28, 92, 232
        else:
            fill_alpha, border_alpha, icon_alpha = 14, 50, 218

        painter.setPen(QPen(QColor(210, 225, 255, border_alpha), 1.1))
        painter.setBrush(QColor(255, 255, 255, fill_alpha))
        painter.drawRoundedRect(rect, 7.0, 7.0)

        icon_pen = QPen(QColor(248, 250, 255, icon_alpha), 1.45)
        icon_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        icon_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self._copied:
            painter.drawLine(QPointF(8.5, 14.0), QPointF(12.2, 17.7))
            painter.drawLine(QPointF(12.2, 17.7), QPointF(20.0, 9.8))
        else:
            painter.drawRoundedRect(QRectF(11.0, 7.0, 9.5, 11.0), 1.5, 1.5)
            painter.drawRoundedRect(QRectF(7.5, 10.5, 9.5, 11.0), 1.5, 1.5)


class _SvgResponseButton(QPushButton):
    _SVG = ""

    def __init__(
        self,
        callback: Callable[[], None],
        tooltip: str,
        accessible_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_renderers: dict[int, QSvgRenderer] = {}
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(accessible_name)
        self.clicked.connect(callback)

    def _renderer(self, icon_alpha: int) -> QSvgRenderer:
        if icon_alpha not in self._icon_renderers:
            svg = self._SVG.replace("{opacity}", str(icon_alpha / 255))
            self._icon_renderers[icon_alpha] = QSvgRenderer(
                QByteArray(svg.encode("utf-8"))
            )
        return self._icon_renderers[icon_alpha]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)

        if not self.isEnabled():
            fill_alpha, border_alpha, icon_alpha = 4, 24, 70
        elif self.isDown():
            fill_alpha, border_alpha, icon_alpha = 42, 112, 238
        elif self.underMouse():
            fill_alpha, border_alpha, icon_alpha = 28, 92, 232
        else:
            fill_alpha, border_alpha, icon_alpha = 14, 50, 218

        painter.setPen(QPen(QColor(210, 225, 255, border_alpha), 1.1))
        painter.setBrush(QColor(255, 255, 255, fill_alpha))
        painter.drawRoundedRect(rect, 7.0, 7.0)
        self._renderer(icon_alpha).render(painter, QRectF(5.0, 5.0, 18.0, 18.0))


class ResponseRefreshButton(_SvgResponseButton):
    _SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="rgb(248,250,255)" stroke-opacity="{opacity}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>"""

    def __init__(
        self, callback: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(callback, "Refresh Notes", "Refresh Notes", parent)


class ResponseDeeperButton(_SvgResponseButton):
    _SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="rgb(248,250,255)" stroke-opacity="{opacity}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 18h8"/><path d="M3 22h18"/><path d="M14 22a7 7 0 1 0 0-14h-1"/><path d="M9 14h2"/><path d="M9 12a2 2 0 0 1-2-2V6h6v4a2 2 0 0 1-2 2Z"/><path d="M12 6V3a1 1 0 0 0-1-1H9a1 1 0 0 0-1 1v3"/></svg>"""

    def __init__(
        self, callback: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(
            callback,
            "Deeper: expand on the most recent discussion item",
            "Deeper",
            parent,
        )


class ResponseSmarterButton(QPushButton):
    def __init__(
        self, callback: Callable[[], None], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "Smarter: revisit this response with the full transcript and web research"
        )
        self.setAccessibleName("Smarter")
        self.clicked.connect(callback)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)

        if not self.isEnabled():
            fill_alpha, border_alpha, icon_alpha = 4, 24, 70
        elif self.isDown():
            fill_alpha, border_alpha, icon_alpha = 42, 112, 238
        elif self.underMouse():
            fill_alpha, border_alpha, icon_alpha = 28, 92, 232
        else:
            fill_alpha, border_alpha, icon_alpha = 14, 50, 218

        painter.setPen(QPen(QColor(210, 225, 255, border_alpha), 1.1))
        painter.setBrush(QColor(255, 255, 255, fill_alpha))
        painter.drawRoundedRect(rect, 7.0, 7.0)

        pen = QPen(QColor(248, 250, 255, icon_alpha), 1.35)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        brain = QPainterPath()
        brain.moveTo(16.87, 14.82)
        brain.lineTo(15.1, 12.17)
        brain.lineTo(11.97, 12.79)
        brain.lineTo(11.35, 15.92)
        brain.lineTo(14.0, 17.69)
        brain.lineTo(14.0, 18.51)
        brain.lineTo(14.22, 19.61)
        brain.lineTo(14.84, 20.54)
        brain.lineTo(15.77, 21.16)
        brain.lineTo(16.87, 21.38)
        brain.lineTo(17.97, 21.16)
        brain.lineTo(18.9, 20.54)
        brain.lineTo(19.52, 19.61)
        brain.lineTo(19.74, 18.51)
        brain.lineTo(19.74, 17.03)
        brain.moveTo(11.13, 14.82)
        brain.lineTo(12.9, 12.17)
        brain.lineTo(16.03, 12.79)
        brain.lineTo(16.65, 15.92)
        brain.lineTo(14.0, 17.69)
        brain.lineTo(14.0, 18.51)
        brain.lineTo(13.78, 19.61)
        brain.lineTo(13.16, 20.54)
        brain.lineTo(12.23, 21.16)
        brain.lineTo(11.13, 21.38)
        brain.lineTo(10.03, 21.16)
        brain.lineTo(9.1, 20.54)
        brain.lineTo(8.48, 19.61)
        brain.lineTo(8.26, 18.51)
        brain.lineTo(8.26, 17.03)
        brain.moveTo(18.51, 17.28)
        brain.lineTo(19.61, 17.06)
        brain.lineTo(20.54, 16.44)
        brain.lineTo(21.16, 15.51)
        brain.lineTo(21.38, 14.41)
        brain.lineTo(21.16, 13.31)
        brain.lineTo(20.54, 12.38)
        brain.lineTo(19.61, 11.76)
        brain.lineTo(18.51, 11.54)
        brain.lineTo(18.1, 11.54)
        brain.moveTo(19.74, 11.79)
        brain.lineTo(19.74, 9.49)
        brain.lineTo(19.52, 8.39)
        brain.lineTo(18.9, 7.46)
        brain.lineTo(17.97, 6.84)
        brain.lineTo(16.87, 6.62)
        brain.lineTo(15.77, 6.84)
        brain.lineTo(14.84, 7.46)
        brain.lineTo(14.22, 8.39)
        brain.lineTo(14.0, 9.49)
        brain.moveTo(9.49, 17.28)
        brain.lineTo(8.39, 17.06)
        brain.lineTo(7.46, 16.44)
        brain.lineTo(6.84, 15.51)
        brain.lineTo(6.62, 14.41)
        brain.lineTo(6.84, 13.31)
        brain.lineTo(7.46, 12.38)
        brain.lineTo(8.39, 11.76)
        brain.lineTo(9.49, 11.54)
        brain.lineTo(9.9, 11.54)
        brain.moveTo(8.26, 11.79)
        brain.lineTo(8.26, 9.49)
        brain.lineTo(8.48, 8.39)
        brain.lineTo(9.1, 7.46)
        brain.lineTo(10.03, 6.84)
        brain.lineTo(11.13, 6.62)
        brain.lineTo(12.23, 6.84)
        brain.lineTo(13.16, 7.46)
        brain.lineTo(13.78, 8.39)
        brain.lineTo(14.0, 9.49)
        brain.lineTo(14.0, 17.69)
        painter.drawPath(brain)


class ScreenshotSelectionOverlay(QWidget):
    def __init__(
        self,
        screenshots: list[tuple[QRect, QPixmap]],
        desktop_geometry: QRect,
        on_capture: Callable[[bytes], None],
        on_cancel: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._screenshots = screenshots
        self._desktop_geometry = desktop_geometry
        self._on_capture = on_capture
        self._on_cancel = on_cancel
        self._origin: QPoint | None = None
        self._selection = QRect()
        self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 92))
        if not self._selection.isNull():
            rect = self._selection.normalized().intersected(self.rect())
            painter.setCompositionMode(QPainter.CompositionMode_Clear)
            painter.fillRect(rect, Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setPen(QPen(QColor(248, 250, 255, 230), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect.adjusted(1, 1, -1, -1))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        self._origin = event.position().toPoint()
        self._selection = QRect(self._origin, self._origin)
        self._rubber_band.setGeometry(self._selection)
        self._rubber_band.show()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is None:
            return
        self._selection = QRect(self._origin, event.position().toPoint()).normalized()
        self._rubber_band.setGeometry(self._selection)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        rect = (
            QRect(self._origin, event.position().toPoint())
            .normalized()
            .intersected(self.rect())
        )
        self._origin = None
        self._rubber_band.hide()
        if rect.width() < 8 or rect.height() < 8:
            self.close()
            if self._on_cancel is not None:
                self._on_cancel()
            return
        image_png = self._capture_selection_png(rect)
        if not image_png:
            self.close()
            if self._on_cancel is not None:
                self._on_cancel()
            return
        self.close()
        self._on_capture(image_png)

    def _capture_selection_png(self, rect: QRect) -> bytes:
        rect = rect.normalized().intersected(self.rect())
        if rect.isEmpty():
            return b""
        selection_global = rect.translated(self._desktop_geometry.topLeft())
        crop = QPixmap(rect.size())
        crop.fill(Qt.transparent)
        painter = QPainter(crop)
        for screen_geometry, screenshot in self._screenshots:
            visible_global = selection_global.intersected(screen_geometry)
            if visible_global.isEmpty():
                continue
            target = visible_global.translated(-selection_global.topLeft())
            source = self._source_rect_for_screen(
                screen_geometry, screenshot, visible_global
            )
            painter.drawPixmap(QRectF(target), screenshot, QRectF(source))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        crop.save(buffer, "PNG")
        buffer.close()
        return bytes(data)

    def _source_rect_for_screen(
        self,
        screen_geometry: QRect,
        screenshot: QPixmap,
        visible_global: QRect,
    ) -> QRectF:
        scale_x = screenshot.width() / max(1, screen_geometry.width())
        scale_y = screenshot.height() / max(1, screen_geometry.height())
        local = visible_global.translated(-screen_geometry.topLeft())
        return QRectF(
            local.x() * scale_x,
            local.y() * scale_y,
            local.width() * scale_x,
            local.height() * scale_y,
        )

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.close()
            if self._on_cancel is not None:
                self._on_cancel()
            return
        super().keyPressEvent(event)


class GlassWindow(QMainWindow):
    SHELL_BORDER_ALPHA = 58
    RESIZE_DURATION_MS = 220

    def _shell_alpha_for_mode(self, mode: str) -> int:
        if not self.settings.onboarding_completed or mode == "onboarding":
            return 230
        base = max(6, min(255, int(self.settings.shell_alpha)))
        if mode == "compact":
            return max(6, base - 2)
        return base

    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        on_save_key: Callable[[str], None],
        on_smart_assist: Callable[[], None],
        on_ask: Callable[..., None],
        on_toggle_listening: Callable[[], None],
        on_toggle_microphone: Callable[[], None],
        on_capture_visual: Callable[[], None],
        on_refresh_notes: Callable[[], None],
        on_show_settings: Callable[[], None],
        get_auto_assist_seconds: Callable[[], int] | None = None,
        on_postpone_auto_assist: Callable[[], None] | None = None,
        on_hold_auto_assist_countdown: Callable[[], None] | None = None,
        on_release_auto_assist_countdown: Callable[[], None] | None = None,
        on_clear_panel_context: Callable[[str], None] | None = None,
        on_send_current_transcript: Callable[[], None] | None = None,
        on_smarter: Callable[[str, str], None] | None = None,
        on_complete_onboarding: Callable[[], None] | None = None,
        on_set_microphone_enabled: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.bus = bus
        self._on_save_key = on_save_key
        self._on_smart_assist = on_smart_assist
        self._on_ask = on_ask
        self._on_toggle_listening = on_toggle_listening
        self._on_toggle_microphone = on_toggle_microphone
        self._on_capture_visual = on_capture_visual
        self._on_refresh_notes = on_refresh_notes
        self._on_show_settings = on_show_settings
        self._get_auto_assist_seconds = get_auto_assist_seconds
        self._on_postpone_auto_assist = on_postpone_auto_assist
        self._on_hold_auto_assist_countdown = on_hold_auto_assist_countdown
        self._on_release_auto_assist_countdown = on_release_auto_assist_countdown
        self._on_clear_panel_context = on_clear_panel_context
        self._on_send_current_transcript = on_send_current_transcript
        self._on_smarter = on_smarter
        self._on_complete_onboarding = on_complete_onboarding
        self._on_set_microphone_enabled = on_set_microphone_enabled
        self._onboarding_requires_key = not settings.onboarding_completed
        self._delete_clear_armed = False
        self._drag_offset: QPoint | None = None
        self._resize_edges: set[str] = set()
        self._resize_start_pos: QPoint | None = None
        self._resize_start_geometry: QRect | None = None
        self._suspend_geometry_save = False
        self._mode = "compact"
        self._current_transcript_items: dict[str, str] = {}
        self._transcript_lines: list[str] = []
        self._assist_pages: list[dict[str, str]] = []
        self._selected_assist_page_index: int | None = None
        self._active_assist_pages: dict[str, int] = {}
        self._assist_bubble_resize_pending = False
        self._response_bubble_resize_pending: set[str] = set()
        self._notes_stream_markdown = ""
        self._ask_entries: list[dict[str, str]] = []
        self._active_ask_indices: dict[str, int] = {}
        self._bus_status = "idle"
        self._listening = True
        self._microphone_enabled = bool(settings.microphone_enabled)
        self._speech_activity_active = False
        self._transcribing_active = False
        self._ai_streams_active = 0
        self._auto_assist_paused = False
        self._auto_assist_timer_long_pressed = False
        self._smart_assist_long_pressed = False
        self._auto_assist_pause_long_press_timer = QTimer(self)
        self._auto_assist_pause_long_press_timer.setSingleShot(True)
        self._auto_assist_pause_long_press_timer.setInterval(550)
        self._auto_assist_pause_long_press_timer.timeout.connect(
            self._hold_auto_assist_countdown_from_timer_button
        )
        self._smart_assist_pause_long_press_timer = QTimer(self)
        self._smart_assist_pause_long_press_timer.setSingleShot(True)
        self._smart_assist_pause_long_press_timer.setInterval(550)
        self._smart_assist_pause_long_press_timer.timeout.connect(
            self._pause_auto_assist_countdown_from_smart_button
        )
        self._assist_render_timer = QTimer(self)
        self._assist_render_timer.setSingleShot(True)
        self._assist_render_timer.setInterval(32)
        self._assist_render_timer.timeout.connect(self._render_assist)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.setMinimumSize(16, 16)
        self.resize(settings.compact_width, settings.compact_height)

        self._stack = _ModeStack()
        self._stack.setAttribute(Qt.WA_TranslucentBackground)
        self._stack.setAttribute(Qt.WA_NoSystemBackground)
        self._stack.setAutoFillBackground(False)
        self._compact = self._build_compact()
        self._panel = self._build_panel()
        self._onboarding = self._build_onboarding()
        self._stack.addWidget(self._compact)
        self._stack.addWidget(self._panel)
        self._stack.addWidget(self._onboarding)
        self.setCentralWidget(self._stack)

        self._dot_state = "idle"
        self._transition_dot = StatusDot(diameter=16)
        self._transition_dot.setParent(self)
        self._transition_dot.hide()
        self._dot_transition_anim: QPropertyAnimation | None = None

        self._tooltip = _GlassTooltip()
        self._tooltip_owner: QWidget | None = None

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self._apply_styles(self)
        self._wire_bus()

        self._auto_assist_timer = QTimer(self)
        self._auto_assist_timer.setInterval(1000)
        self._auto_assist_timer.timeout.connect(self._update_auto_assist_countdown)
        self._auto_assist_timer.start()
        self._update_auto_assist_countdown()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        hwnd = int(self.winId())
        win32.enable_acrylic(hwnd, self.settings.acrylic)
        win32.force_topmost(hwnd)
        if self.settings.capture_exclusion:
            win32.set_capture_exclusion(hwnd, True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        if self._mode == "compact":
            radius = min(24, int(min(rect.width(), rect.height()) // 2))
        else:
            radius = 18
        alpha = self._shell_alpha_for_mode(self._mode)

        shell = QPainterPath()
        shell.addRoundedRect(rect, radius, radius)

        painter.fillPath(shell, QColor(0, 0, 0, alpha))

        painter.strokePath(
            shell,
            QPen(QColor(170, 176, 188, self.SHELL_BORDER_ALPHA), 1),
        )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            text = watched.toolTip()
            if text:
                QToolTip.hideText()
                self._tooltip_owner = watched
                self._tooltip.show_for(
                    watched, event.globalPos(), text, self.settings.shell_alpha
                )
            else:
                self._hide_tooltip()
            return True
        if event_type == QEvent.Type.MouseButtonPress:
            self._hide_tooltip()
            if (
                isinstance(event, QMouseEvent)
                and event.button() == Qt.RightButton
                and isinstance(watched, QWidget)
                and watched.window() is self
                and not isinstance(watched, (QPlainTextEdit, QTextBrowser, QLineEdit))
            ):
                self._show_exit_context_menu(event.globalPosition().toPoint())
                return True
        elif event_type in (QEvent.Type.Leave, QEvent.Type.Hide) and (
            watched is self._tooltip_owner
        ):
            self._hide_tooltip()
        return super().eventFilter(watched, event)

    def _hide_tooltip(self) -> None:
        self._tooltip_owner = None
        self._tooltip.hide_tooltip()

    def _show_exit_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        style_context_menu(menu, self.settings.shell_alpha)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._quit_application)
        menu.addAction(exit_action)
        menu.exec(global_pos)

    def _quit_application(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _mouse_pos_in_window(self, event: QMouseEvent) -> QPoint:
        return self.mapFromGlobal(event.globalPosition().toPoint())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            resize_edges = self._hit_test_resize_edges(self._mouse_pos_in_window(event))
            if resize_edges:
                self._resize_edges = resize_edges
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
            else:
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            self._resize_edges
            and self._resize_start_pos
            and self._resize_start_geometry
        ):
            self._resize_from_edges(event.globalPosition().toPoint())
        elif self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        else:
            self._update_resize_cursor(self._mouse_pos_in_window(event))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        resized = bool(self._resize_edges)
        self._resize_edges = set()
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self._drag_offset = None
        if not resized:
            self._snap_to_edges()
        self._save_geometry()
        self._update_resize_cursor(self._mouse_pos_in_window(event))
        super().mouseReleaseEvent(event)

    def _on_panel_top_spacer_double_click(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._mode == "panel":
            self.show_compact()
            event.accept()
        else:
            QWidget.mouseDoubleClickEvent(self._panel_top_spacer, event)

    def leaveEvent(self, event) -> None:
        if not self._resize_edges:
            self.unsetCursor()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        if self._mode in {"panel", "onboarding"} and not self._suspend_geometry_save:
            self._save_geometry()
        if hasattr(self, "assist_response_bubble"):
            self._schedule_assist_bubble_resize()
        if hasattr(self, "notes_response_bubble"):
            self._schedule_response_bubble_resize("notes")
        if hasattr(self, "ask_response_bubble"):
            self._schedule_response_bubble_resize("ask")
        super().resizeEvent(event)

    def toggle_expanded(self) -> None:
        if self._mode == "compact":
            self.show_panel()
        elif self._mode == "panel":
            self.show_compact()

    def show_compact(self) -> None:
        self.setMinimumSize(40, 40)
        self.setMaximumSize(16777215, 16777215)
        self._transition_to_mode(
            "compact",
            self._compact,
            self.settings.compact_width,
            self.settings.compact_height,
        )

    def show_panel(self) -> None:
        width = max(self.settings.width, 460)
        height = max(self.settings.height, 320)
        self.setMaximumSize(16777215, 16777215)
        self._transition_to_mode(
            "panel", self._panel, width, height, on_grown=self._lock_panel_minimum_size
        )

    def _lock_panel_minimum_size(self) -> None:
        self._suspend_geometry_save = True
        self.setMinimumSize(460, 320)
        self._suspend_geometry_save = False

    def _dot_for_mode(self, mode: str) -> "StatusDot | None":
        if mode == "compact":
            return getattr(self, "compact_dot", None)
        if mode == "panel":
            return getattr(self, "panel_dot", None)
        return None

    def _dot_opacity_effect(self, dot: "StatusDot") -> QGraphicsOpacityEffect:
        effect = getattr(dot, "_fade_effect", None)
        if effect is None:
            effect = QGraphicsOpacityEffect(dot)
            dot.setGraphicsEffect(effect)
            dot._fade_effect = effect
        return effect

    def _reset_dot_transition(self) -> None:
        if self._dot_transition_anim is not None:
            self._dot_transition_anim.stop()
            self._dot_transition_anim = None
        self._transition_dot.hide()
        for attr in ("compact_dot", "panel_dot"):
            dot = getattr(self, attr, None)
            if dot is not None:
                self._dot_opacity_effect(dot).setOpacity(1.0)

    def _transition_to_mode(
        self,
        mode: str,
        widget: QWidget,
        width: int,
        height: int,
        on_grown: Callable[[], None] | None = None,
    ) -> None:
        changing_mode = mode != self._mode
        old_dot = self._dot_for_mode(self._mode) if changing_mode else None
        start_point: QPoint | None = None
        if old_dot is not None and self.isVisible():
            start_point = old_dot.mapTo(self, old_dot.rect().center())

        self._mode = mode
        self._stack.setCurrentWidget(widget)

        new_dot = self._dot_for_mode(mode) if changing_mode else None
        if start_point is not None and new_dot is not None:
            if self._dot_transition_anim is not None:
                self._dot_transition_anim.stop()
                self._dot_transition_anim = None
            for attr in ("compact_dot", "panel_dot"):
                other_dot = getattr(self, attr, None)
                if other_dot is not None and other_dot is not new_dot:
                    self._dot_opacity_effect(other_dot).setOpacity(1.0)
            diameter = self._transition_dot._d
            self._transition_dot.set_state(self._dot_state)
            self._transition_dot.move(
                start_point.x() - diameter // 2, start_point.y() - diameter // 2
            )
            self._transition_dot.show()
            self._transition_dot.raise_()
            self._dot_opacity_effect(new_dot).setOpacity(0.0)

            if mode == "panel":
                QTimer.singleShot(
                    0,
                    partial(self._run_open_animation, new_dot, width, height, on_grown),
                )
            else:
                QTimer.singleShot(
                    0, partial(self._run_close_animation, new_dot, width, height)
                )
        else:
            self._reset_dot_transition()
            self._animate_resize(width, height, on_finished=on_grown)

        self.update()

    def _run_open_animation(
        self,
        new_dot: "StatusDot",
        width: int,
        height: int,
        on_grown: Callable[[], None] | None,
    ) -> None:
        diameter = self._transition_dot._d
        start_pos = self._transition_dot.pos()
        start_size = self.size()
        end_size = (
            start_size.expandedTo(self.minimumSize())
            .boundedTo(self.maximumSize())
            .scaled(width, height, Qt.IgnoreAspectRatio)
        )

        self._panel.resize(end_size)
        panel_layout = self._panel.layout()
        if panel_layout is not None:
            panel_layout.activate()
        end_center = new_dot.mapTo(self, new_dot.rect().center())
        end_pos = QPoint(end_center.x() - diameter // 2, end_center.y() - diameter // 2)

        start_w, start_h = start_size.width(), start_size.height()
        end_w, end_h = end_size.width(), end_size.height()
        start_x, start_y = start_pos.x(), start_pos.y()
        end_x, end_y = end_pos.x(), end_pos.y()

        if self._dot_transition_anim is not None:
            self._dot_transition_anim.stop()

        anim = QVariantAnimation(self)
        anim.setDuration(self.RESIZE_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _tick(t: float) -> None:
            self.resize(
                round(start_w + (end_w - start_w) * t),
                round(start_h + (end_h - start_h) * t),
            )
            self._transition_dot.move(
                round(start_x + (end_x - start_x) * t),
                round(start_y + (end_y - start_y) * t),
            )

        def _done() -> None:
            self.resize(end_w, end_h)
            if on_grown is not None:
                on_grown()
            self._finish_dot_transition(new_dot)

        anim.valueChanged.connect(_tick)
        anim.finished.connect(_done)
        self._transition_dot.raise_()
        anim.start()
        self._animation = anim
        self._dot_transition_anim = anim

    def _run_close_animation(
        self, new_dot: "StatusDot", width: int, height: int
    ) -> None:
        pinned_pos = self._transition_dot.pos()
        start_size = self.size()
        end_size = (
            start_size.expandedTo(self.minimumSize())
            .boundedTo(self.maximumSize())
            .scaled(width, height, Qt.IgnoreAspectRatio)
        )
        start_w, start_h = start_size.width(), start_size.height()
        end_w, end_h = end_size.width(), end_size.height()

        if self._dot_transition_anim is not None:
            self._dot_transition_anim.stop()

        anim = QVariantAnimation(self)
        anim.setDuration(self.RESIZE_DURATION_MS)
        anim.setEasingCurve(QEasingCurve.InOutCubic)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _tick(t: float) -> None:
            self.resize(
                round(start_w + (end_w - start_w) * t),
                round(start_h + (end_h - start_h) * t),
            )
            self._transition_dot.move(pinned_pos)

        def _done() -> None:
            self.resize(end_w, end_h)
            self._transition_dot.move(pinned_pos)
            self._begin_dot_glide_now(new_dot, 320, QEasingCurve.InOutCubic)

        anim.valueChanged.connect(_tick)
        anim.finished.connect(_done)
        self._transition_dot.raise_()
        anim.start()
        self._animation = anim
        self._dot_transition_anim = anim

    def _begin_dot_glide_now(
        self,
        new_dot: "StatusDot",
        duration: int = 150,
        easing: QEasingCurve.Type = QEasingCurve.OutCubic,
    ) -> None:
        target_center = new_dot.mapTo(self, new_dot.rect().center())
        diameter = self._transition_dot._d
        end_pos = QPoint(
            target_center.x() - diameter // 2, target_center.y() - diameter // 2
        )
        if self._dot_transition_anim is not None:
            self._dot_transition_anim.stop()
        anim = QPropertyAnimation(self._transition_dot, b"pos", self)
        anim.setDuration(duration)
        anim.setEasingCurve(easing)
        anim.setStartValue(self._transition_dot.pos())
        anim.setEndValue(end_pos)
        anim.finished.connect(partial(self._finish_dot_transition, new_dot))
        anim.start()
        self._dot_transition_anim = anim

    def _finish_dot_transition(self, dot: "StatusDot") -> None:
        self._transition_dot.hide()
        self._dot_opacity_effect(dot).setOpacity(1.0)

    def show_assist(self) -> None:
        self.show_panel()
        self._select_tab(0)

    def show_onboarding(self, *, require_key: bool | None = None) -> None:
        self._reset_dot_transition()
        self._mode = "onboarding"
        self._onboarding_requires_key = (
            not self.settings.onboarding_completed
            if require_key is None
            else bool(require_key)
        )
        self.audio_consent_checkbox.setChecked(self.settings.onboarding_completed)
        self.onboarding_microphone_checkbox.setChecked(self.settings.microphone_enabled)
        self.key_input.clear()
        self.key_input.setPlaceholderText(
            "sk-..."
            if self._onboarding_requires_key
            else "Leave blank to keep your current key"
        )
        self._set_onboarding_key_invalid(False)
        self.onboarding_stack.setCurrentIndex(0)
        self._update_onboarding_nav()
        self._stack.setCurrentWidget(self._onboarding)
        self.setMaximumSize(16777215, 16777215)
        self.setMinimumSize(640, 500)
        self._animate_resize(720, 560)
        self.update()

    def append_transcript_final(
        self, item_id: str, text: str, completed_at: float
    ) -> None:
        import time

        stamp = time.strftime("%H:%M:%S", time.localtime(completed_at))
        line = f"[{stamp}] {text}"
        self._transcript_lines.append(line)
        if len(self._transcript_lines) > 600:
            self._transcript_lines = self._transcript_lines[-600:]
        self.transcript_box.setPlainText("\n".join(self._transcript_lines))
        self.transcript_box.verticalScrollBar().setValue(
            self.transcript_box.verticalScrollBar().maximum()
        )

    def _build_compact(self) -> QWidget:
        root = QWidget()
        root.setAttribute(Qt.WA_TranslucentBackground)
        root.setAttribute(Qt.WA_NoSystemBackground)
        root.setAutoFillBackground(False)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(0)
        self.compact_dot = StatusDot()
        layout.addStretch(1)
        layout.addWidget(self.compact_dot, 0, Qt.AlignCenter)
        layout.addStretch(1)
        root.mouseDoubleClickEvent = lambda event: self.show_panel()
        self._enable_window_mouse_handling(root)
        return root

    def _build_panel(self) -> QWidget:
        root = QWidget()
        root.setObjectName("Panel")
        root.setAttribute(Qt.WA_TranslucentBackground)
        root.setAttribute(Qt.WA_NoSystemBackground)
        root.setAutoFillBackground(False)
        self._enable_window_mouse_handling(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(3)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(3)
        self.panel_dot = StatusDot()
        top_bar.addWidget(self.panel_dot)

        self.tab_buttons: list[QPushButton] = []
        for index, name in enumerate(["Assist", "Transcript", "Notes", "Ask"]):
            button = QPushButton(name)
            button.setProperty("tab", True)
            button.clicked.connect(lambda checked=False, i=index: self._select_tab(i))
            self.tab_buttons.append(button)
            top_bar.addWidget(button)
        self._panel_top_spacer = QWidget()
        self._panel_top_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._panel_top_spacer.setMinimumHeight(26)
        self._enable_window_mouse_handling(self._panel_top_spacer)
        self._panel_top_spacer.mouseDoubleClickEvent = (
            self._on_panel_top_spacer_double_click
        )
        top_bar.addWidget(self._panel_top_spacer, 1)

        self.delete_button = self._make_icon_button(
            "trash", "", self._on_delete_clicked
        )
        self.visual_capture_button = self._make_icon_button(
            "eye",
            "Capture a selected screen area as visual context",
            self._on_capture_visual,
        )
        self.transcript_send_button = self._make_icon_button(
            "transcript",
            "Send the current live transcript to Auto Assist now",
            self._on_send_current_transcript_clicked,
        )
        self.microphone_button = self._make_icon_button(
            "microphone",
            "Also listen to your microphone (your side of the conversation)",
            self._on_toggle_microphone,
        )
        self.microphone_button.setCheckable(True)
        self._set_microphone(self._microphone_enabled)
        self.pause_button = self._make_icon_button(
            "pause", "Pause listening", self._on_toggle_listening
        )
        compact_button = self._make_icon_button(
            "compact", "Compact view", self.show_compact
        )
        settings_button = self._make_icon_button(
            "settings", "Settings", self._on_show_settings
        )
        self.export_button = self._make_icon_button(
            "export", "Export", self._on_export_clicked
        )

        self.auto_assist_snooze_button: QPushButton | None = None
        if self._on_postpone_auto_assist is not None:
            self.auto_assist_snooze_button = self._make_icon_button(
                "clock",
                "Add 5 seconds to Live Assist auto timer; hold to freeze then send",
                self._on_postpone_auto_assist_clicked,
            )
            self.auto_assist_snooze_button.pressed.connect(
                self._on_auto_assist_timer_button_pressed
            )
            self.auto_assist_snooze_button.released.connect(
                self._on_auto_assist_timer_button_released
            )

        top_bar.addWidget(self.pause_button)
        if self.auto_assist_snooze_button is not None:
            top_bar.addWidget(self.auto_assist_snooze_button)
        top_bar.addWidget(self.transcript_send_button)
        top_bar.addWidget(self.visual_capture_button)
        top_bar.addWidget(self.microphone_button)
        top_bar.addWidget(compact_button)
        top_bar.addWidget(self.export_button)
        top_bar.addWidget(self.delete_button)
        top_bar.addWidget(settings_button)

        self.smart_assist_button = QPushButton(" --")
        self.smart_assist_button.setIcon(_make_glyph_icon("sparkle"))
        self.smart_assist_button.setIconSize(QSize(14, 14))
        self.smart_assist_button.setProperty("smartAssist", True)
        self.smart_assist_button.setMinimumWidth(72)
        self.smart_assist_button.setToolTip("Smart Assist")
        self.smart_assist_button.setAccessibleName("Smart Assist")
        self.smart_assist_button.pressed.connect(self._on_smart_assist_button_pressed)
        self.smart_assist_button.released.connect(self._on_smart_assist_button_released)
        top_bar.addWidget(self.smart_assist_button)
        layout.addLayout(top_bar)

        self.content = QStackedWidget()

        self.assist_box = MarkdownTextBrowser("Live guidance appears here")
        self.assist_response_bubble = AssistResponseBubble()
        self.assist_response_bubble.setObjectName("AssistResponseBubble")
        assist_bubble_layout = QVBoxLayout(self.assist_response_bubble)
        assist_bubble_layout.setContentsMargins(10, 8, 10, 6)
        assist_bubble_layout.setSpacing(3)
        assist_bubble_layout.addWidget(self.assist_box, 1)
        assist_bubble_footer = QHBoxLayout()
        assist_bubble_footer.setContentsMargins(2, 0, 2, 0)
        assist_bubble_footer.addStretch(1)
        self.assist_deeper_button = ResponseDeeperButton(self._ask_deeper)
        assist_bubble_footer.addWidget(self.assist_deeper_button)
        self.assist_smarter_button = ResponseSmarterButton(
            self._make_current_response_smarter
        )
        assist_bubble_footer.addWidget(self.assist_smarter_button)
        self.assist_copy_button = ResponseCopyButton(self._copy_current_assist_response)
        assist_bubble_footer.addWidget(self.assist_copy_button)
        assist_bubble_layout.addLayout(assist_bubble_footer)
        self.assist_response_bubble.setVisible(False)
        self.assist_page = QWidget()
        assist_layout = QVBoxLayout(self.assist_page)
        assist_layout.setContentsMargins(10, 8, 10, 6)
        assist_layout.setSpacing(0)
        assist_bubble_row = QHBoxLayout()
        assist_bubble_row.setContentsMargins(0, 0, 0, 0)
        assist_bubble_row.setSpacing(0)
        assist_bubble_row.addWidget(self.assist_response_bubble, 1)
        assist_bubble_row.addSpacing(28)
        assist_layout.addLayout(assist_bubble_row)
        assist_layout.addStretch(1)

        self.transcript_box = QPlainTextEdit()
        self.transcript_box.setReadOnly(True)
        self.transcript_box.setPlaceholderText("Live transcript of the conversation")

        self.notes_box = MarkdownTextBrowser("Live notes")
        self.notes_refresh_button = ResponseRefreshButton(self._on_refresh_notes)
        notes_page, self.notes_copy_button = self._page_with_copy_button(
            self.notes_box,
            self._copy_notes,
            leading_buttons=[self.notes_refresh_button],
        )
        self.notes_content_page = notes_page
        self.notes_response_bubble = self.notes_box.parentWidget()

        self.ask_history = MarkdownTextBrowser(
            "Ask anything about what's happening now"
        )
        ask_page, self.ask_copy_button = self._page_with_copy_button(
            self.ask_history, self._copy_ask_history
        )
        self.ask_page = ask_page
        self.ask_response_bubble = self.ask_history.parentWidget()

        self.content.addWidget(self.assist_page)
        self.content.addWidget(self.transcript_box)
        self.content.addWidget(notes_page)
        self.content.addWidget(ask_page)
        layout.addWidget(self.content, 1)

        ask_footer = QHBoxLayout()
        ask_footer.setContentsMargins(0, 0, 0, 0)
        ask_footer.setSpacing(4)
        self.panel_footer_layout = ask_footer
        self.assist_previous_button = self._make_icon_button(
            "arrow_left",
            "Previous response",
            self._show_previous_assist_page,
        )
        self.assist_next_button = self._make_icon_button(
            "arrow_right",
            "Next response",
            self._show_next_assist_page,
        )
        self.assist_page_indicator = QLabel("0 / 0")
        self.assist_page_indicator.setObjectName("Muted")
        self.assist_page_indicator.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        ask_footer.addWidget(self.assist_previous_button)
        ask_footer.addWidget(self.assist_next_button)
        ask_footer.addWidget(self.assist_page_indicator)
        ask_footer.addSpacing(4)
        self._update_assist_page_navigation()
        follow_btn = QPushButton("Follow-ups")
        follow_btn.setProperty("quickAsk", True)
        follow_btn.setToolTip("Ask the AI for follow-up questions you could explore")
        follow_btn.clicked.connect(self._ask_followup_questions)
        nudge_btn = QPushButton("Nudge")
        nudge_btn.setProperty("quickAsk", True)
        nudge_btn.setToolTip(
            "Ask the AI what to say next, using the full transcript for context"
        )
        nudge_btn.clicked.connect(self._ask_nudge)
        recap_btn = QPushButton("Recap")
        recap_btn.setProperty("quickAsk", True)
        recap_btn.setToolTip(
            "Summarize the full transcript shown in the Transcript tab"
        )
        recap_btn.clicked.connect(self._ask_recap)
        ask_footer.addWidget(follow_btn)
        ask_footer.addWidget(nudge_btn)
        ask_footer.addWidget(recap_btn)
        ask_footer.addSpacing(4)
        self.ask_input = QLineEdit()
        self.ask_input.setObjectName("panelAskInput")
        self.ask_input.setPlaceholderText(
            "Ask about the call, a term, or what to say next"
        )
        self.ask_input.returnPressed.connect(self._submit_question)
        ask_button = QPushButton("Ask")
        ask_button.setObjectName("panelAskSubmit")
        ask_button.clicked.connect(self._submit_question)
        ask_footer.addWidget(self.ask_input, 1)
        ask_footer.addWidget(ask_button)
        panel_grip = QSizeGrip(root)
        ask_footer.addWidget(panel_grip, 0, Qt.AlignBottom)
        layout.addLayout(ask_footer)

        self._select_tab(0)
        self._apply_styles(root)
        return root

    def _build_onboarding(self) -> QWidget:
        root = QWidget()
        root.setAttribute(Qt.WA_TranslucentBackground)
        root.setAttribute(Qt.WA_NoSystemBackground)
        root.setAutoFillBackground(False)
        self._enable_window_mouse_handling(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(28, 24, 28, 22)
        main_layout.setSpacing(14)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(_make_glyph_icon("sparkle").pixmap(18, 18))
        brand_icon.setFixedSize(20, 20)
        brand_name = QLabel("Mimir")
        brand_name.setFont(QFont("Segoe UI", 13, QFont.DemiBold))
        brand_name.setStyleSheet("color: rgba(248, 251, 255, 220);")
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(brand_name)
        brand_row.addStretch(1)
        self.onboarding_brand_icon = brand_icon
        self.onboarding_brand_name = brand_name
        main_layout.addLayout(brand_row)

        self.onboarding_stack = QStackedWidget()
        self.onboarding_stack.setAttribute(Qt.WA_TranslucentBackground)
        self.onboarding_stack.setAttribute(Qt.WA_NoSystemBackground)
        self.onboarding_page_titles = [
            "Welcome",
            "Audio & consent",
            "Toolbar",
            "Actions & responses",
            "Auto Assist & shortcuts",
            "Status",
            "Settings",
            "Connect",
        ]

        def add_page(title: str, description: str) -> QVBoxLayout:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
            )
            content = QWidget()
            content.setAttribute(Qt.WA_TranslucentBackground)
            content.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            layout = QVBoxLayout(content)
            layout.setContentsMargins(8, 4, 12, 8)
            layout.setSpacing(12)
            heading = QLabel(title)
            heading.setObjectName("OnboardingTitle")
            heading.setFont(QFont("Segoe UI", 23, QFont.DemiBold))
            heading.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            intro = QLabel(description)
            intro.setWordWrap(True)
            intro.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            intro.setFont(QFont("Segoe UI", 12))
            intro.setObjectName("Muted")
            layout.addWidget(heading)
            layout.addWidget(intro)
            scroll.setWidget(content)
            self.onboarding_stack.addWidget(scroll)
            return layout

        def card(kind: str, title: str, body: str) -> QWidget:
            panel = QWidget()
            panel.setObjectName("OnboardingCard")
            panel.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            row = QHBoxLayout(panel)
            row.setContentsMargins(14, 11, 14, 11)
            row.setSpacing(12)
            icon = QLabel()
            icon.setPixmap(_make_glyph_icon(kind).pixmap(20, 20))
            icon.setFixedSize(22, 22)
            icon.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
            copy = QVBoxLayout()
            copy.setSpacing(3)
            title_label = QLabel(title)
            title_label.setFont(QFont("Segoe UI", 12, QFont.DemiBold))
            title_label.setStyleSheet("color: rgba(255, 255, 255, 238);")
            title_label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            body_label = QLabel(body)
            body_label.setWordWrap(True)
            body_label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            body_label.setFont(QFont("Segoe UI", 11))
            body_label.setObjectName("Muted")
            copy.addWidget(title_label)
            copy.addWidget(body_label)
            row.addWidget(icon)
            row.addLayout(copy, 1)
            return panel

        welcome = add_page(
            "Live help, grounded in the conversation",
            "Mimir listens, transcribes, and turns the conversation into timely guidance while staying out of your way.",
        )
        welcome.addWidget(
            card(
                "audio",
                "Listen, then understand",
                "System audio supplies most of Mimir's context. When listening is paused or there is no usable transcript, guidance will be limited.",
            )
        )
        welcome.addWidget(
            card(
                "sparkle",
                "Assist in the moment",
                "Smart Assist, notes, questions, recaps, and response tools use the transcript to help you follow and respond.",
            )
        )
        welcome.addWidget(
            card(
                "shield",
                "Private overlay, best-effort capture protection",
                "On supported Windows systems, capture exclusion can hide the overlay from many screen-sharing and recording paths. It is not a universal guarantee, so always verify your sharing setup.",
            )
        )
        welcome.addStretch(1)

        audio_page = add_page(
            "Audio powers Mimir",
            "Before continuing, understand what listening means and choose whether to include your microphone.",
        )
        audio_page.addWidget(
            card(
                "audio",
                "System audio is the primary source",
                "While listening is on, Mimir captures computer-output audio and sends audio chunks to OpenAI for transcription. The returned transcript becomes the main context for assistance. Raw audio is not saved by Mimir to local files.",
            )
        )
        audio_page.addWidget(
            card(
                "microphone",
                "Your microphone is optional",
                "Enable it to capture your side of the conversation. Microphone audio is also sent to OpenAI for transcription and appears as [USER] context. You can turn it off at any time.",
            )
        )
        audio_page.addWidget(
            card(
                "pause",
                "Pause means stop listening",
                "The pause button or Ctrl+Shift+M pauses system and microphone capture, transcription, and automatic assistance until you resume.",
            )
        )
        self.onboarding_microphone_checkbox = QCheckBox(
            "Include my microphone (optional)"
        )
        self.onboarding_microphone_checkbox.setToolTip(
            "Optional. You can change this later from the microphone toolbar button or Settings."
        )
        self.onboarding_microphone_checkbox.setAccessibleName(
            "Include microphone audio"
        )
        audio_page.addWidget(self.onboarding_microphone_checkbox)
        self.audio_consent_checkbox = QCheckBox(
            "I understand and consent to this audio processing"
        )
        self.audio_consent_checkbox.setToolTip(
            "Listening sends audio to OpenAI for transcription; Mimir depends on the returned transcript context."
        )
        self.audio_consent_checkbox.setAccessibleName("Audio processing consent")
        self.audio_consent_checkbox.toggled.connect(
            lambda checked: self._update_onboarding_nav()
        )
        audio_page.addWidget(self.audio_consent_checkbox)
        self.audio_consent_hint = QLabel("Required to continue")
        self.audio_consent_hint.setObjectName("Muted")
        audio_page.addWidget(self.audio_consent_hint)
        audio_page.addStretch(1)

        toolbar = add_page(
            "The toolbar, in the order you will see it",
            "Select a control to learn what it does. Every icon also has a hover tooltip in the main panel.",
        )
        toolbar_surface = QWidget()
        toolbar_surface.setObjectName("MaterialToolbar")
        toolbar_grid = QGridLayout(toolbar_surface)
        toolbar_grid.setContentsMargins(12, 12, 12, 10)
        toolbar_grid.setHorizontalSpacing(8)
        toolbar_grid.setVerticalSpacing(8)
        self.toolbar_info_label = QLabel("Choose any control above for details.")
        self.toolbar_info_label.setObjectName("OnboardingInfo")
        self.toolbar_info_label.setWordWrap(True)
        self.toolbar_info_label.setMinimumHeight(68)
        controls = [
            (
                "pause",
                "Pause",
                "Pause or resume all listening, transcription, and Auto Assist.",
            ),
            (
                "clock",
                "Snooze",
                "Click to add 5 seconds to Auto Assist. Hold to freeze the timer, then release to run.",
            ),
            (
                "transcript",
                "Send now",
                "Commit the current live transcript boundary and run Auto Assist immediately.",
            ),
            (
                "eye",
                "Visual AI",
                "Select a screen area. The image is sent to OpenAI and its description is added as [VISUAL] context.",
            ),
            (
                "microphone",
                "Microphone",
                "Include or exclude your microphone audio. The active style means it is on.",
            ),
            (
                "compact",
                "Compact",
                "Collapse the panel into the small always-on-top status pill.",
            ),
            (
                "export",
                "Export",
                "Export the current/all Assist pages or the Transcript as a timestamped text file.",
            ),
            (
                "trash",
                "Clear",
                "Click twice to clear the active tab. Clearing Transcript also clears transcript-based AI context.",
            ),
            (
                "settings",
                "Settings",
                "Open models, reasoning, transcription, audio, prompt, privacy, and appearance settings.",
            ),
            (
                "sparkle",
                "Smart Assist",
                "Click to run now. Hold to pause automatic sends; click again to resume and run.",
            ),
        ]
        for index, (kind, label, explanation) in enumerate(controls):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(2, 1, 2, 1)
            cell_layout.setSpacing(4)
            button = QPushButton()
            button.setIcon(_make_glyph_icon(kind))
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(34, 34)
            button.setProperty("iconOnly", True)
            button.setToolTip(f"{label}: {explanation}")
            button.setAccessibleName(label)
            button.clicked.connect(
                lambda checked=False, name=label, text=explanation: (
                    self.toolbar_info_label.setText(f"<b>{name}</b><br>{text}")
                )
            )
            caption = QLabel(label)
            caption.setAlignment(Qt.AlignCenter)
            caption.setFont(QFont("Segoe UI", 9))
            caption.setObjectName("Muted")
            cell_layout.addWidget(button, 0, Qt.AlignCenter)
            cell_layout.addWidget(caption)
            toolbar_grid.addWidget(cell, index // 5, index % 5)
        toolbar.addWidget(toolbar_surface)
        toolbar.addWidget(self.toolbar_info_label)
        toolbar.addWidget(
            card(
                "mouse",
                "Move, resize, and switch views",
                "Drag the overlay from open surfaces. Resize from panel edges or the bottom-right grip. Double-click the empty header to compact; double-click the compact pill to expand.",
            )
        )
        toolbar.addStretch(1)

        actions = add_page(
            "Navigate the conversation and act on it",
            "The four tabs share one live context, while response actions create or refine specific outputs.",
        )
        actions.addWidget(
            card(
                "sparkle",
                "Assist",
                "Smart Assist responses appear as separate pages. Use the left/right arrows and page counter to revisit them. Deeper expands a response, Smarter revisits it with the full transcript and optional web research, and Copy copies it.",
            )
        )
        actions.addWidget(
            card(
                "transcript",
                "Transcript",
                "The finalized conversation log is the core AI context. Send-now commits current speech; Export saves it; Clear removes it and the related AI memory.",
            )
        )
        actions.addWidget(
            card(
                "clock",
                "Notes",
                "Structured notes update automatically. Refresh runs an immediate pass and Copy places the current notes on the clipboard.",
            )
        )
        actions.addWidget(
            card(
                "sparkle",
                "Ask",
                "Type a question and press Enter or Ask. Follow-ups proposes useful questions, Nudge suggests what to say next, and Recap summarizes the full transcript. These results open as Assist pages.",
            )
        )
        actions.addWidget(
            card(
                "export",
                "Button availability follows context",
                "Response, navigation, copy, and export controls remain disabled until the selected tab has something they can act on.",
            )
        )
        actions.addStretch(1)

        automation = add_page(
            "Auto Assist without breaking focus",
            "Use the countdown, gestures, shortcuts, or tray depending on how much of the overlay you want visible.",
        )
        automation.addWidget(
            card(
                "clock",
                "Countdown and snooze",
                "Auto Assist runs after new transcript context arrives and the configured interval elapses. Click the clock for +5 seconds; hold it to freeze and run on release.",
            )
        )
        automation.addWidget(
            card(
                "sparkle",
                "Run or pause automatic sends",
                "Click Smart Assist to run immediately. Hold it for about half a second to pause automatic sends; click again to resume and run.",
            )
        )
        automation.addWidget(
            card(
                "compact",
                "Compact and hidden modes",
                "Compact keeps only the status pill visible. Ctrl+Shift+H or a tray-icon click hides or shows the overlay.",
            )
        )
        automation.addWidget(
            card(
                "settings",
                "Shortcuts and tray",
                "Ctrl+Enter: Smart Assist  •  Ctrl+Shift+M: pause/resume listening  •  Tray menu: show/hide, Smart Assist, listening, microphone, Settings, and Quit.",
            )
        )
        automation.addStretch(1)

        status = add_page(
            "Read Mimir at a glance",
            "The dot remains visible in compact mode and reports the highest-priority activity.",
        )
        demo_row = QHBoxLayout()
        self.demo_dot = StatusDot(diameter=26)
        self.dot_info_label = QLabel("Blue — listening and ready")
        self.dot_info_label.setObjectName("OnboardingInfo")
        self.dot_info_label.setWordWrap(True)
        demo_row.addWidget(self.demo_dot, 0, Qt.AlignTop)
        demo_row.addWidget(self.dot_info_label, 1)
        status.addLayout(demo_row)
        states = [
            ("idle", "Blue", "Listening and waiting for speech or AI work."),
            ("busy", "Green", "AI is processing or generating a response."),
            ("transcribing", "Red", "Speech is being detected or transcribed."),
            (
                "paused",
                "Yellow",
                "Listening, transcription, and automatic assistance are paused.",
            ),
            ("recovering", "Soft red", "A temporary connection problem is recovering."),
            (
                "error",
                "Flashing white",
                "Something needs attention; check the status message, connection, API key, or Settings.",
            ),
        ]
        state_grid = QGridLayout()
        state_grid.setSpacing(8)
        for index, (state_name, label, explanation) in enumerate(states):
            button = QPushButton(label)
            button.setAccessibleName(f"Preview {label} status")
            button.clicked.connect(
                lambda checked=False, state_value=state_name, name=label, text=explanation: (
                    self.demo_dot.set_state(state_value),
                    self.dot_info_label.setText(f"<b>{name}</b><br>{text}"),
                )
            )
            state_grid.addWidget(button, index // 3, index % 3)
        status.addLayout(state_grid)
        status.addWidget(
            card(
                "pause",
                "When in doubt, pause",
                "Pause immediately stops new audio capture and automatic assistance. Resume when you are ready to continue.",
            )
        )
        status.addStretch(1)

        settings_page = add_page(
            "Settings, from everyday to advanced",
            "Defaults are designed to work. Change advanced model fields only when you know the model and account support the value.",
        )
        settings_page.addWidget(
            card(
                "settings",
                "Assist models",
                "Analysis model, service tier, and reasoning control normal assistance. Smarter model, service tier, reasoning, web search, and web-search context control the Smarter response action.",
            )
        )
        settings_page.addWidget(
            card(
                "eye",
                "Visual and transcription models",
                "Visual AI model and reasoning apply only to selected screenshots. Transcription model and language control speech-to-text.",
            )
        )
        settings_page.addWidget(
            card(
                "audio",
                "Listening and automation",
                "Auto Assist countdown sets the delay. Audio cost saver enables VAD; silence threshold controls sensitivity and silence hangover controls when speech is considered finished.",
            )
        )
        settings_page.addWidget(
            card(
                "sparkle",
                "AI prompt context",
                "Add role, domain, tone, or output preferences that should guide assistance—for example, interview context or concise bullets.",
            )
        )
        settings_page.addWidget(
            card(
                "shield",
                "Privacy, appearance, and key",
                "Screen-share exclusion is best effort on Windows. Black background adjusts overlay opacity. The API key can be replaced, and Replay onboarding is available here anytime.",
            )
        )
        settings_page.addStretch(1)

        connect = add_page(
            "Connect OpenAI",
            "Mimir uses your API key for transcription and assistance requests. The key is stored locally through Windows Credential Manager, with a DPAPI-encrypted fallback.",
        )
        link = QLabel(
            "Create or manage a key at <a href='https://platform.openai.com/api-keys' style='color:#76b0ff;'>platform.openai.com/api-keys</a>. API usage is billed by OpenAI according to your account and selected models."
        )
        link.setWordWrap(True)
        link.setOpenExternalLinks(True)
        link.setObjectName("Muted")
        connect.addWidget(link)
        key_row = QHBoxLayout()
        self.key_input = QLineEdit()
        self.key_input.setObjectName("OnboardingKey")
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setPlaceholderText("sk-...")
        self.key_input.setAccessibleName("OpenAI API key")
        self.key_input.returnPressed.connect(self._save_key)
        self.key_input.textChanged.connect(
            lambda text: self._set_onboarding_key_invalid(False)
        )
        self.key_visibility_button = QPushButton("Show")
        self.key_visibility_button.setCheckable(True)
        self.key_visibility_button.setAccessibleName("Show or hide API key")
        self.key_visibility_button.toggled.connect(
            self._toggle_onboarding_key_visibility
        )
        key_row.addWidget(self.key_input, 1)
        key_row.addWidget(self.key_visibility_button)
        connect.addLayout(key_row)
        self.onboarding_key_error = QLabel("")
        self.onboarding_key_error.setWordWrap(True)
        self.onboarding_key_error.setStyleSheet("color: rgba(255, 145, 153, 230);")
        self.onboarding_key_error.setVisible(False)
        connect.addWidget(self.onboarding_key_error)
        connect.addWidget(
            card(
                "lock",
                "Stored locally, used with OpenAI",
                "Mimir does not upload your key anywhere other than presenting it to OpenAI to authenticate API requests. Mimir does not maintain its own remote key store.",
            )
        )
        connect.addWidget(
            card(
                "shield",
                "You stay in control",
                "Pause listening before sensitive audio, disable microphone when it is not needed, and verify capture exclusion in the specific sharing or recording app you use.",
            )
        )
        connect.addStretch(1)

        main_layout.addWidget(self.onboarding_stack, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(10)
        self.back_button = QPushButton("Back")
        self.back_button.setFixedSize(90, 36)
        self.back_button.clicked.connect(self._onboarding_back)
        self.onboarding_step_label = QLabel(self.onboarding_page_titles[0])
        self.onboarding_step_label.setObjectName("Muted")
        self.onboarding_step_label.setAlignment(Qt.AlignCenter)
        self.page_indicator = QLabel(f"1 / {len(self.onboarding_page_titles)}")
        self.page_indicator.setObjectName("Muted")
        self.page_indicator.setAlignment(Qt.AlignCenter)
        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("OnboardingPrimary")
        self.next_button.setFixedSize(104, 36)
        self.next_button.clicked.connect(self._onboarding_next)
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        footer.addWidget(self.onboarding_step_label)
        footer.addWidget(self.page_indicator)
        footer.addStretch(1)
        footer.addWidget(self.next_button)
        main_layout.addLayout(footer)

        self._update_onboarding_nav()
        self._apply_styles(root)
        return root

    def _onboarding_back(self) -> None:
        idx = self.onboarding_stack.currentIndex()
        if idx > 0:
            self.onboarding_stack.setCurrentIndex(idx - 1)
            self._update_onboarding_nav()

    def _onboarding_next(self) -> None:
        idx = self.onboarding_stack.currentIndex()
        if idx < self.onboarding_stack.count() - 1:
            self.onboarding_stack.setCurrentIndex(idx + 1)
            self._update_onboarding_nav()
        else:
            self._save_key()

    def _update_onboarding_nav(self) -> None:
        idx = self.onboarding_stack.currentIndex()
        total = self.onboarding_stack.count()
        self.page_indicator.setText(f"{idx + 1} / {total}")
        if hasattr(self, "onboarding_step_label"):
            self.onboarding_step_label.setText(self.onboarding_page_titles[idx])
        if hasattr(self, "onboarding_brand_icon"):
            self.onboarding_brand_icon.raise_()
            self.onboarding_brand_name.raise_()
        self.back_button.setVisible(idx > 0)
        audio_step = idx == 1
        consent_given = self.audio_consent_checkbox.isChecked()
        self.next_button.setEnabled(not audio_step or consent_given)
        if hasattr(self, "audio_consent_hint"):
            self.audio_consent_hint.setText(
                "Audio processing understood — you can continue"
                if consent_given
                else "Required to continue"
            )
        if idx == total - 1:
            self.next_button.setText(
                "Start Mimir" if not self.settings.onboarding_completed else "Done"
            )
            self.next_button.setFixedWidth(112)
        elif audio_step and not consent_given:
            self.next_button.setText("Consent required")
            self.next_button.setFixedWidth(132)
        else:
            self.next_button.setText("Next")
            self.next_button.setFixedWidth(104)

    def _make_icon_button(
        self, kind: str, tooltip: str, callback: Callable[[], None]
    ) -> QPushButton:
        button = QPushButton()
        button.setIcon(_make_glyph_icon(kind))
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(26, 26)
        button.setProperty("iconOnly", True)
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip or kind.replace("_", " ").title())
        button.clicked.connect(callback)
        return button

    def _page_with_copy_button(
        self,
        editor: QWidget,
        callback: Callable[[], None],
        *,
        leading_buttons: list[QWidget] | None = None,
    ) -> tuple[QWidget, ResponseCopyButton]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 6)
        layout.setSpacing(0)

        bubble = AssistResponseBubble()
        bubble.setObjectName("AssistResponseBubble")
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 6)
        bubble_layout.setSpacing(3)
        bubble_layout.addWidget(editor, 1)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        footer.addStretch(1)
        for button in leading_buttons or []:
            footer.addWidget(button)
        copy_button = ResponseCopyButton(callback)
        copy_button.setEnabled(False)
        footer.addWidget(copy_button)
        bubble_layout.addLayout(footer)

        bubble.setVisible(False)
        bubble_row = QHBoxLayout()
        bubble_row.setContentsMargins(0, 0, 0, 0)
        bubble_row.setSpacing(0)
        bubble_row.addWidget(bubble, 1)
        bubble_row.addSpacing(28)
        layout.addLayout(bubble_row)
        layout.addStretch(1)
        return page, copy_button

    def _enable_window_mouse_handling(self, widget: QWidget) -> None:
        widget.mousePressEvent = self.mousePressEvent
        widget.mouseMoveEvent = self.mouseMoveEvent
        widget.mouseReleaseEvent = self.mouseReleaseEvent
        widget.leaveEvent = self.leaveEvent

    def _wire_bus(self) -> None:
        self.bus.status.connect(self._set_status)
        self.bus.listening_changed.connect(self._set_listening)
        self.bus.microphone_changed.connect(self._set_microphone)
        self.bus.speech_activity_changed.connect(self._set_speech_activity)
        self.bus.transcript_delta.connect(self._on_transcript_delta)
        self.bus.transcript_final.connect(self._on_transcript_final)
        self.bus.transcribing_changed.connect(self._set_transcribing_active)
        self.bus.ai_started.connect(self._on_ai_started)
        self.bus.ai_delta.connect(self._on_ai_delta)
        self.bus.ai_finished.connect(self._on_ai_finished)
        self.bus.notes_updated.connect(self._on_notes_updated)

    def _set_status(self, state: str, message: str) -> None:
        self._bus_status = state
        self._refresh_status_dots()

    def _refresh_status_dots(self) -> None:
        s = self._bus_status
        if s == "paused" or not self._listening:
            effective = "paused"
        elif s == "error":
            effective = "error"
        elif s == "recovering":
            effective = "recovering"
        elif (
            s in {"hearing", "transcribing"}
            or self._speech_activity_active
            or self._transcribing_active
            or bool(self._current_transcript_items)
        ):
            effective = "transcribing"
        elif self._ai_streams_active > 0:
            effective = "busy"
        else:
            effective = "idle"
        self._dot_state = effective
        self.compact_dot.set_state(effective)
        self.panel_dot.set_state(effective)
        self._transition_dot.set_state(effective)

    def _set_listening(self, listening: bool) -> None:
        self._listening = listening
        self.pause_button.setIcon(_make_glyph_icon("pause" if listening else "play"))
        self.pause_button.setToolTip(
            "Pause listening" if listening else "Resume listening"
        )
        self._refresh_status_dots()

    def _set_microphone(self, enabled: bool) -> None:
        self._microphone_enabled = enabled
        if not getattr(self, "microphone_button", None):
            return
        self.microphone_button.setChecked(enabled)
        self.microphone_button.setProperty("active", enabled)
        self.microphone_button.setToolTip(
            "Stop listening to your microphone"
            if enabled
            else "Also listen to your microphone"
        )
        self.microphone_button.style().unpolish(self.microphone_button)
        self.microphone_button.style().polish(self.microphone_button)
        self.microphone_button.update()

    def _set_speech_activity(self, active: bool) -> None:
        self._speech_activity_active = active
        self._refresh_status_dots()

    def _set_transcribing_active(self, active: bool) -> None:
        self._transcribing_active = active
        self._refresh_status_dots()

    def _current_panel_id(self) -> str:
        index = self.content.currentIndex()
        return ("assist", "transcript", "notes", "ask")[index]

    def _update_export_button(self) -> None:
        if not getattr(self, "export_button", None):
            return
        panel = self._current_panel_id()
        self.export_button.setEnabled(panel in {"assist", "transcript"})
        if panel == "assist":
            tooltip = (
                "Export the current Assist page or all Assist pages to a text file"
            )
        elif panel == "transcript":
            tooltip = "Export the transcript to a text file"
        else:
            tooltip = "Export is available on the Assist and Transcript tabs"
        self.export_button.setToolTip(tooltip)

    def _reset_delete_confirm(self) -> None:
        if not getattr(self, "delete_button", None):
            return
        self._delete_clear_armed = False
        self.delete_button.setIcon(_make_glyph_icon("trash"))
        self._update_delete_button_tooltip()

    def _update_delete_button_tooltip(self) -> None:
        if not getattr(self, "delete_button", None):
            return
        panel = self._current_panel_id()
        labels = {
            "assist": "Assist output",
            "transcript": "Transcript (also removes context sent to the AI)",
            "notes": "Notes",
            "ask": "Ask history",
        }
        what = labels.get(panel, "This tab")
        if self._delete_clear_armed:
            self.delete_button.setToolTip(f"Click again to permanently clear {what}")
        else:
            self.delete_button.setToolTip(f"Clear {what} (click again to confirm)")

    def _on_delete_clicked(self) -> None:
        if self._delete_clear_armed:
            self._clear_current_panel()
            self._reset_delete_confirm()
            return
        self._delete_clear_armed = True
        self.delete_button.setIcon(_make_glyph_icon("confirm_check"))
        self._update_delete_button_tooltip()

    def _clear_current_panel(self) -> None:
        panel = self._current_panel_id()
        if panel == "assist":
            self._assist_pages.clear()
            self._selected_assist_page_index = None
            self._active_assist_pages.clear()
            self.assist_box.clear()
            self._update_assist_page_navigation()
        elif panel == "transcript":
            self._transcript_lines.clear()
            self._current_transcript_items.clear()
            self.transcript_box.clear()
            if self._on_clear_panel_context is not None:
                self._on_clear_panel_context("transcript")
        elif panel == "notes":
            self._notes_stream_markdown = ""
            self.notes_box.clear()
            self.notes_response_bubble.setVisible(False)
            self.notes_copy_button.setEnabled(False)
            self.notes_copy_button.set_copied(False)
        elif panel == "ask":
            self._ask_entries.clear()
            self._active_ask_indices.clear()
            self.ask_history.clear()
            self.ask_response_bubble.setVisible(False)
            self.ask_copy_button.setEnabled(False)
            self.ask_copy_button.set_copied(False)

    def _on_export_clicked(self) -> None:
        panel = self._current_panel_id()
        if panel == "assist":
            if not self._assist_pages:
                QMessageBox.information(
                    self,
                    "Nothing to export",
                    "There are no Assist pages to export yet.",
                )
                return
            scope = self._choose_assist_export_scope()
            if scope is None:
                return
            text = self._build_assist_export_text(scope)
            filename = self._build_export_filename("assist", scope)
        elif panel == "transcript":
            text = "\n".join(self._transcript_lines).strip()
            if not text:
                QMessageBox.information(
                    self,
                    "Nothing to export",
                    "There is no transcript to export yet.",
                )
                return
            filename = self._build_export_filename("transcript")
        else:
            return
        self._save_text_export(text, filename)

    def _choose_assist_export_scope(self) -> str | None:
        message = QMessageBox(self)
        message.setWindowTitle("Export Assist")
        app = QApplication.instance()
        if app is not None:
            message.setWindowIcon(app.windowIcon())
        message.setText("Which Assist pages do you want to export?")
        current_button = message.addButton(
            "Current Page", QMessageBox.ButtonRole.AcceptRole
        )
        all_button = message.addButton("All Pages", QMessageBox.ButtonRole.ActionRole)
        message.addButton(QMessageBox.StandardButton.Cancel)
        message.exec()
        clicked = message.clickedButton()
        if clicked is current_button:
            return "current"
        if clicked is all_button:
            return "all"
        return None

    @staticmethod
    def _markdown_to_plain_text(markdown: str) -> str:
        document = QTextDocument()
        document.setMarkdown(markdown)
        return document.toPlainText().strip()

    def _build_assist_export_text(self, scope: str) -> str:
        if not self._assist_pages:
            return ""
        total = len(self._assist_pages)
        if scope == "current":
            index = self._selected_assist_page_index
            if index is None:
                index = total - 1
            index = max(0, min(index, total - 1))
            page_entries = [(index, self._assist_pages[index])]
        elif scope == "all":
            page_entries = list(enumerate(self._assist_pages))
        else:
            raise ValueError(f"Unsupported Assist export scope: {scope}")

        sections: list[str] = []
        for index, page in page_entries:
            title = page["title"].strip() or "Assist"
            if scope == "all":
                heading = f"Page {index + 1} of {total} - {title}"
            else:
                heading = title
            body = self._markdown_to_plain_text(page["content"])
            section = f"{heading}\n{'=' * len(heading)}"
            if body:
                section += f"\n\n{body}"
            sections.append(section)
        return "\n\n\n".join(sections)

    @staticmethod
    def _safe_filename_part(value: str, fallback: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        if not cleaned:
            cleaned = fallback
        return cleaned[:48].rstrip(" .")

    def _build_export_filename(
        self,
        panel: str,
        scope: str | None = None,
        exported_at: datetime | None = None,
    ) -> str:
        stamp = (exported_at or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
        if panel == "transcript":
            stem = "Mimir_Transcript"
        elif panel == "assist" and scope == "all":
            stem = "Mimir_Assist_All_Pages"
        elif panel == "assist":
            total = len(self._assist_pages)
            index = self._selected_assist_page_index
            if index is None:
                index = max(0, total - 1)
            index = max(0, min(index, max(0, total - 1)))
            title = (
                self._assist_pages[index]["title"] if self._assist_pages else "Assist"
            )
            safe_title = self._safe_filename_part(title, "Assist").replace(" ", "_")
            stem = f"Mimir_Assist_Page_{index + 1:02d}_{safe_title}"
        else:
            raise ValueError(f"Unsupported export panel: {panel}")
        return f"{stem}_{stamp}.txt"

    def _save_text_export(self, text: str, suggested_filename: str) -> bool:
        documents = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        initial_path = str(Path(documents or Path.home()) / suggested_filename)
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export to Text File",
            initial_path,
            "Text Files (*.txt)",
        )
        if not file_path:
            return False
        if not file_path.lower().endswith(".txt"):
            file_path += ".txt"
        try:
            with open(file_path, "w", encoding="utf-8", newline="\n") as output:
                output.write(text.rstrip())
                output.write("\n")
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Export failed",
                f"Mimir could not save the text file.\n\n{exc}",
            )
            return False
        return True

    def _on_postpone_auto_assist_clicked(self) -> None:
        if self._auto_assist_timer_long_pressed:
            self._auto_assist_timer_long_pressed = False
            return
        if self._on_postpone_auto_assist is not None:
            self._on_postpone_auto_assist()
        self._update_auto_assist_countdown()

    def _on_send_current_transcript_clicked(self) -> None:
        if self._on_send_current_transcript is None:
            return
        self._on_send_current_transcript()

    def _on_auto_assist_timer_button_pressed(self) -> None:
        self._auto_assist_timer_long_pressed = False
        if self._on_hold_auto_assist_countdown is not None:
            self._auto_assist_pause_long_press_timer.start()

    def _on_auto_assist_timer_button_released(self) -> None:
        if self._auto_assist_pause_long_press_timer.isActive():
            self._auto_assist_pause_long_press_timer.stop()
            return
        if self._auto_assist_timer_long_pressed:
            if self._on_release_auto_assist_countdown is not None:
                self._on_release_auto_assist_countdown()
            self._on_smart_assist()
            self._update_auto_assist_countdown()
            QTimer.singleShot(0, self._clear_auto_assist_timer_long_press)

    def _clear_auto_assist_timer_long_press(self) -> None:
        self._auto_assist_timer_long_pressed = False

    def _hold_auto_assist_countdown_from_timer_button(self) -> None:
        if self._on_hold_auto_assist_countdown is None:
            return
        self._auto_assist_timer_long_pressed = True
        self._on_hold_auto_assist_countdown()
        self._update_auto_assist_countdown()

    def _pause_auto_assist_countdown_from_smart_button(self) -> None:
        if self._on_hold_auto_assist_countdown is None:
            return
        self._smart_assist_long_pressed = True
        self._auto_assist_paused = True
        self._on_hold_auto_assist_countdown()
        self._update_auto_assist_countdown()

    def _on_smart_assist_button_pressed(self) -> None:
        if self._auto_assist_paused:
            return
        self._smart_assist_long_pressed = False
        if self._on_hold_auto_assist_countdown is not None:
            self._smart_assist_pause_long_press_timer.start()
        self._update_auto_assist_countdown()

    def _on_smart_assist_button_released(self) -> None:
        was_paused = self._auto_assist_paused
        if self._smart_assist_pause_long_press_timer.isActive():
            self._smart_assist_pause_long_press_timer.stop()
        elif self._smart_assist_long_pressed:
            self._smart_assist_long_pressed = False
            self.smart_assist_button.setDown(False)
            self._update_auto_assist_countdown()
            return
        if was_paused and self._on_release_auto_assist_countdown is not None:
            self._on_release_auto_assist_countdown()
        self._auto_assist_paused = False
        self._on_smart_assist()
        self._update_auto_assist_countdown()
        if was_paused:
            self.smart_assist_button.setDown(False)

    def _update_auto_assist_countdown(self) -> None:
        if not hasattr(self, "smart_assist_button"):
            return
        if self._auto_assist_paused:
            self.smart_assist_button.setIcon(_make_glyph_icon("pause"))
            self.smart_assist_button.setText("")
            self.smart_assist_button.setToolTip(
                "Smart Assist auto send paused - click to send transcript and restart countdown"
            )
            return
        self.smart_assist_button.setIcon(_make_glyph_icon("sparkle"))
        seconds = 0
        if self._get_auto_assist_seconds is not None:
            try:
                seconds = max(0, int(self._get_auto_assist_seconds()))
            except Exception:
                seconds = 0
        if seconds > 0:
            self.smart_assist_button.setText(f"  {seconds}s")
            self.smart_assist_button.setToolTip(
                f"Smart Assist - next auto in {seconds}s (hold to pause auto send; click to run)"
            )
        else:
            self.smart_assist_button.setText("  Now")
            self.smart_assist_button.setToolTip(
                "Smart Assist - ready now (hold to pause auto send; click to run)"
            )

    def _on_transcript_delta(self, item_id: str, delta: str) -> None:
        current = self._current_transcript_items.get(item_id, "")
        self._current_transcript_items[item_id] = current + delta
        self._refresh_status_dots()

    def _on_transcript_final(
        self, item_id: str, text: str, completed_at: float
    ) -> None:
        self._current_transcript_items.pop(item_id, None)
        self.append_transcript_final(item_id, text, completed_at)
        self._refresh_status_dots()

    def _on_ai_started(self, request_id: str, mode: str, title: str) -> None:
        self._ai_streams_active += 1
        self._refresh_status_dots()
        if mode == "ask":
            self._ask_entries.append({"title": title, "content": ""})
            self._active_ask_indices[request_id] = len(self._ask_entries) - 1
            self._render_ask_history()
        elif mode == "notes":
            self._notes_stream_markdown = ""
            self.notes_box.set_markdown("")
            self.notes_response_bubble.setVisible(False)
            self.notes_copy_button.setEnabled(False)
            self.notes_copy_button.set_copied(False)
        else:
            was_on_latest = (
                self._selected_assist_page_index is None
                or self._selected_assist_page_index == len(self._assist_pages) - 1
            )
            page_title = title.strip() or "Auto Assist"
            self._assist_pages.append({"title": page_title, "content": ""})
            page_index = len(self._assist_pages) - 1
            self._active_assist_pages[request_id] = page_index
            if title.strip() or was_on_latest:
                self._selected_assist_page_index = page_index
            if title.strip():
                self._select_tab(0)
            self._render_assist()

    def _on_ai_delta(self, request_id: str, mode: str, token: str) -> None:
        if mode == "ask":
            entry_index = self._active_ask_indices.get(request_id)
            if entry_index is not None and entry_index < len(self._ask_entries):
                self._ask_entries[entry_index]["content"] += token
                self._render_ask_history()
        elif mode == "notes":
            self._notes_stream_markdown += token
            self.notes_box.set_markdown(self._notes_stream_markdown)
            has_notes = bool(self._notes_stream_markdown.strip())
            self.notes_response_bubble.setVisible(has_notes)
            self.notes_copy_button.setEnabled(has_notes)
            if has_notes:
                self._schedule_response_bubble_resize("notes")
        else:
            page_index = self._active_assist_pages.get(request_id)
            if page_index is not None and page_index < len(self._assist_pages):
                self._assist_pages[page_index]["content"] += token
                if page_index == self._selected_assist_page_index:
                    self._schedule_assist_render()

    def _on_ai_finished(self, request_id: str, mode: str, full: str) -> None:
        if mode == "ask":
            entry_index = self._active_ask_indices.pop(request_id, None)
            if (
                entry_index is not None
                and entry_index < len(self._ask_entries)
                and full
            ):
                self._ask_entries[entry_index]["content"] = full
            self._render_ask_history()
        elif mode == "notes":
            if full:
                self._notes_stream_markdown = full
            self.notes_box.set_markdown(self._notes_stream_markdown)
            has_notes = bool(self._notes_stream_markdown.strip())
            self.notes_response_bubble.setVisible(has_notes)
            self.notes_copy_button.setEnabled(has_notes)
            if has_notes:
                self._schedule_response_bubble_resize("notes")
        else:
            page_index = self._active_assist_pages.pop(request_id, None)
            if page_index is not None and page_index < len(self._assist_pages):
                if full:
                    self._assist_pages[page_index]["content"] = full
                if page_index == self._selected_assist_page_index:
                    self._assist_render_timer.stop()
                    self._render_assist()
        self._ai_streams_active = max(0, self._ai_streams_active - 1)
        self._refresh_status_dots()

    def _on_notes_updated(self, text: str) -> None:
        if text:
            self._notes_stream_markdown = text
            self.notes_box.set_markdown(text)
            self.notes_response_bubble.setVisible(True)
            self.notes_copy_button.setEnabled(True)
            self._schedule_response_bubble_resize("notes")

    def _render_assist(self) -> None:
        if not self._assist_pages or self._selected_assist_page_index is None:
            self.assist_box.clear()
            self.assist_response_bubble.setVisible(False)
            self._update_assist_page_navigation()
            return
        self.assist_response_bubble.setVisible(True)
        page_index = max(
            0,
            min(self._selected_assist_page_index, len(self._assist_pages) - 1),
        )
        self._selected_assist_page_index = page_index
        page = self._assist_pages[page_index]
        raw_title = page["title"].strip()
        title = escape_markdown_inline(raw_title) if raw_title else ""
        body = page["content"].strip()
        if title:
            self.assist_box.set_markdown(
                f"### {title}\n\n{body}" if body else f"### {title}"
            )
        else:
            self.assist_box.set_markdown(body)
        self._update_assist_page_navigation()
        self._schedule_assist_bubble_resize()

    def _schedule_assist_bubble_resize(self) -> None:
        if self._assist_bubble_resize_pending:
            return
        self._assist_bubble_resize_pending = True
        QTimer.singleShot(0, self._resize_assist_bubble_to_content)

    def _resize_assist_bubble_to_content(self) -> None:
        self._assist_bubble_resize_pending = False
        self._resize_response_bubble_to_content(
            self.assist_page, self.assist_response_bubble, self.assist_box
        )

    def _schedule_response_bubble_resize(self, panel: str) -> None:
        if panel in self._response_bubble_resize_pending:
            return
        self._response_bubble_resize_pending.add(panel)
        QTimer.singleShot(0, lambda: self._resize_named_response_bubble(panel))

    def _resize_named_response_bubble(self, panel: str) -> None:
        self._response_bubble_resize_pending.discard(panel)
        if panel == "notes":
            self._resize_response_bubble_to_content(
                self.notes_content_page,
                self.notes_response_bubble,
                self.notes_box,
            )
        elif panel == "ask":
            self._resize_response_bubble_to_content(
                self.ask_page, self.ask_response_bubble, self.ask_history
            )

    @staticmethod
    def _resize_response_bubble_to_content(
        page: QWidget, bubble: QWidget, editor: MarkdownTextBrowser
    ) -> None:
        if bubble.isHidden():
            return
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        available_height = max(92, page.contentsRect().height() - 14)
        document_height = math.ceil(editor.document().size().height())
        bubble_chrome_height = 48
        desired_height = max(92, document_height + bubble_chrome_height)
        bubble.setFixedHeight(min(available_height, desired_height))
        editor._schedule_scrollbar_visibility_update()
        QTimer.singleShot(
            0,
            lambda: GlassWindow._refine_response_bubble_height(page, bubble, editor),
        )

    @staticmethod
    def _refine_response_bubble_height(
        page: QWidget, bubble: QWidget, editor: MarkdownTextBrowser
    ) -> None:
        if bubble.isHidden():
            return
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        available_height = max(92, page.contentsRect().height() - 14)
        document_height = math.ceil(editor.document().size().height())
        desired_height = max(92, document_height + 48)
        bubble.setFixedHeight(min(available_height, desired_height))
        editor._schedule_scrollbar_visibility_update()

    def _copy_current_assist_response(self) -> None:
        if not self._assist_pages or self._selected_assist_page_index is None:
            return
        page_index = max(
            0, min(self._selected_assist_page_index, len(self._assist_pages) - 1)
        )
        markdown = self._assist_pages[page_index]["content"].strip()
        self._copy_markdown(markdown, self.assist_copy_button)

    def _copy_notes(self) -> None:
        self._copy_markdown(self._notes_stream_markdown, self.notes_copy_button)

    def _copy_ask_history(self) -> None:
        self._copy_markdown(self._ask_history_markdown(), self.ask_copy_button)

    @staticmethod
    def _copy_markdown(markdown: str, button: ResponseCopyButton) -> None:
        markdown = markdown.strip()
        if not markdown:
            return
        QApplication.clipboard().setText(markdown)
        button.set_copied(True)
        QTimer.singleShot(1600, lambda: button.set_copied(False))

    def _make_current_response_smarter(self) -> None:
        if (
            self._on_smarter is None
            or not self._assist_pages
            or self._selected_assist_page_index is None
        ):
            return
        page_index = max(
            0, min(self._selected_assist_page_index, len(self._assist_pages) - 1)
        )
        response = self._assist_pages[page_index]["content"].strip()
        if not response:
            return
        self._on_smarter(response, self._full_current_transcript_text())

    def _full_current_transcript_text(self) -> str:
        lines = list(self._transcript_lines)
        in_progress = [
            text.strip()
            for text in self._current_transcript_items.values()
            if text.strip()
        ]
        if in_progress:
            lines.extend(f"[IN PROGRESS] {text}" for text in in_progress)
        return "\n".join(lines).strip()

    def _reset_assist_copy_button(self) -> None:
        self.assist_copy_button.set_copied(False)

    def _schedule_assist_render(self) -> None:
        if not self._assist_render_timer.isActive():
            self._assist_render_timer.start()

    def _show_previous_assist_page(self) -> None:
        self._move_assist_page(-1)

    def _show_next_assist_page(self) -> None:
        self._move_assist_page(1)

    def _move_assist_page(self, offset: int) -> None:
        if not self._assist_pages or self._selected_assist_page_index is None:
            return
        target = self._selected_assist_page_index + offset
        target = max(0, min(target, len(self._assist_pages) - 1))
        if target == self._selected_assist_page_index:
            return
        self._selected_assist_page_index = target
        self._reset_assist_copy_button()
        self._render_assist()

    def _update_assist_page_navigation(self) -> None:
        if not hasattr(self, "assist_previous_button"):
            return
        total = len(self._assist_pages)
        if total == 0 or self._selected_assist_page_index is None:
            current = 0
            self.assist_previous_button.setEnabled(False)
            self.assist_next_button.setEnabled(False)
            self.assist_deeper_button.setEnabled(False)
            self.assist_smarter_button.setEnabled(False)
            self.assist_copy_button.setEnabled(False)
        else:
            current = max(0, min(self._selected_assist_page_index, total - 1))
            self.assist_previous_button.setEnabled(current > 0)
            self.assist_next_button.setEnabled(current < total - 1)
            has_response = bool(self._assist_pages[current]["content"].strip())
            self.assist_deeper_button.setEnabled(has_response)
            self.assist_smarter_button.setEnabled(has_response)
            self.assist_copy_button.setEnabled(has_response)
        self.assist_page_indicator.setText(f"{current + 1 if total else 0} / {total}")

    def _render_ask_history(self) -> None:
        markdown = self._ask_history_markdown()
        self.ask_history.set_markdown(markdown)
        has_history = bool(markdown.strip())
        self.ask_response_bubble.setVisible(has_history)
        self.ask_copy_button.setEnabled(
            any(entry["content"].strip() for entry in self._ask_entries)
        )
        if has_history:
            self._schedule_response_bubble_resize("ask")

    def _ask_history_markdown(self) -> str:
        sections = []
        for entry in self._ask_entries:
            title = escape_markdown_inline(entry["title"])
            content = entry["content"].strip()
            sections.append(
                f"> **Question:** {title}\n\n{content}"
                if content
                else f"> **Question:** {title}"
            )
        return "\n\n---\n\n".join(sections)

    def _select_tab(self, index: int) -> None:
        self.content.setCurrentIndex(index)
        if index == 0:
            self._schedule_assist_bubble_resize()
        elif index == 2:
            self._schedule_response_bubble_resize("notes")
        elif index == 3:
            self._schedule_response_bubble_resize("ask")
        self._reset_delete_confirm()
        self._update_export_button()
        for i, button in enumerate(self.tab_buttons):
            button.setProperty("selected", i == index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _submit_question(self) -> None:
        question = self.ask_input.text().strip()
        if not question:
            return
        self.ask_input.clear()
        self._select_tab(3)
        self._on_ask(question, None, None)

    def _ask_deeper(self) -> None:
        self._select_tab(0)
        self._on_ask(
            "Focus only on the current discussion item in the latest transcript entries. Expand it: define key terms, walk through any "
            "process step-by-step, and cover trade-offs and implications the meeting only touched on. Use scannable "
            "Markdown (headings, labeled bullets, tables for definitions or contrasts).",
            "Deeper",
            None,
            True,
            "assist",
        )

    def _ask_followup_questions(self) -> None:
        self._select_tab(0)
        self._on_ask(
            "Based on the conversation so far, propose several sharp follow-up questions the user could ask "
            "to clarify ambiguity, stress-test assumptions, or move the discussion forward. Use a numbered list.",
            "Follow-Up Questions",
            None,
            False,
            "assist",
        )

    def _ask_nudge(self) -> None:
        self._select_tab(0)
        body = "\n".join(self._transcript_lines).strip()
        max_chars = 52_000
        if len(body) > max_chars:
            body = (
                "[Note: transcript was truncated from the beginning to fit; the end is most recent.]\n"
                + body[-max_chars:]
            )
        self._on_ask(
            "What should I say next?",
            "Nudge",
            body,
            False,
            "assist",
            nudge=True,
        )

    def _ask_recap(self) -> None:
        self._select_tab(0)
        body = "\n".join(self._transcript_lines).strip()
        max_chars = 52_000
        if len(body) > max_chars:
            body = (
                "[Note: transcript was truncated from the beginning to fit; the end is most recent.]\n"
                + body[-max_chars:]
            )
        self._on_ask(
            "Summarize the entire transcript below from start to finish (within the excerpt if truncated). "
            "Cover main themes, key facts or decisions, open questions, and anything actionable. "
            "Use Markdown headings and bullets. Stay faithful to what was said—do not invent content.",
            "Recap",
            body,
            False,
            "assist",
        )

    def _save_key(self) -> None:
        if not self.audio_consent_checkbox.isChecked():
            self.onboarding_stack.setCurrentIndex(1)
            self._update_onboarding_nav()
            self.audio_consent_hint.setText(
                "Please acknowledge audio processing before continuing"
            )
            return

        key = self.key_input.text().strip()
        if key and not self._looks_like_openai_key(key):
            self._set_onboarding_key_invalid(
                True,
                "That key does not look complete. OpenAI API keys begin with sk-. Check the value and try again.",
            )
            return
        if self._onboarding_requires_key and not key:
            self._set_onboarding_key_invalid(
                True,
                "Enter an OpenAI API key to start transcription and assistance.",
            )
            return

        desired_microphone = self.onboarding_microphone_checkbox.isChecked()
        if self._on_set_microphone_enabled is not None:
            self._on_set_microphone_enabled(desired_microphone)
        else:
            self.settings.microphone_enabled = desired_microphone

        if key:
            self._on_save_key(key)
        elif (
            not self.settings.onboarding_completed
            and self._on_complete_onboarding is not None
        ):
            self._on_complete_onboarding()
        self.show_compact() if self.settings.start_compact else self.show_panel()

    @staticmethod
    def _looks_like_openai_key(key: str) -> bool:
        return (
            key.startswith("sk-")
            and len(key) >= 20
            and not any(char.isspace() for char in key)
        )

    def _set_onboarding_key_invalid(self, invalid: bool, message: str = "") -> None:
        if not hasattr(self, "key_input"):
            return
        self.key_input.setProperty("invalid", invalid)
        self.key_input.style().unpolish(self.key_input)
        self.key_input.style().polish(self.key_input)
        if hasattr(self, "onboarding_key_error"):
            self.onboarding_key_error.setText(message)
            self.onboarding_key_error.setVisible(bool(invalid and message))

    def _toggle_onboarding_key_visibility(self, visible: bool) -> None:
        self.key_input.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password)
        self.key_visibility_button.setText("Hide" if visible else "Show")

    def _animate_resize(
        self, width: int, height: int, on_finished: Callable[[], None] | None = None
    ) -> None:
        self._animation = QPropertyAnimation(self, b"size")
        self._animation.setDuration(self.RESIZE_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._animation.setStartValue(self.size())
        self._animation.setEndValue(
            self.size()
            .expandedTo(self.minimumSize())
            .boundedTo(self.maximumSize())
            .scaled(width, height, Qt.IgnoreAspectRatio)
        )
        if on_finished is not None:
            self._animation.finished.connect(on_finished)
        self._animation.start()

    def _hit_test_resize_edges(self, pos: QPoint) -> set[str]:
        if self._mode == "compact":
            return set()
        margin = 10
        edges = set()
        if pos.x() <= margin:
            edges.add("left")
        elif pos.x() >= self.width() - margin:
            edges.add("right")
        if pos.y() <= margin:
            edges.add("top")
        elif pos.y() >= self.height() - margin:
            edges.add("bottom")
        return edges

    def _update_resize_cursor(self, pos: QPoint) -> None:
        edges = self._resize_edges or self._hit_test_resize_edges(pos)
        if {"left", "top"} <= edges or {"right", "bottom"} <= edges:
            self.setCursor(Qt.SizeFDiagCursor)
        elif {"right", "top"} <= edges or {"left", "bottom"} <= edges:
            self.setCursor(Qt.SizeBDiagCursor)
        elif "left" in edges or "right" in edges:
            self.setCursor(Qt.SizeHorCursor)
        elif "top" in edges or "bottom" in edges:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.unsetCursor()

    def _resize_from_edges(self, global_pos: QPoint) -> None:
        if not self._resize_start_pos or not self._resize_start_geometry:
            return
        delta = global_pos - self._resize_start_pos
        geom = QRect(self._resize_start_geometry)
        min_width = self.minimumWidth()
        min_height = self.minimumHeight()

        if "left" in self._resize_edges:
            left = min(geom.right() - min_width + 1, geom.left() + delta.x())
            geom.setLeft(left)
        elif "right" in self._resize_edges:
            geom.setRight(max(geom.left() + min_width - 1, geom.right() + delta.x()))

        if "top" in self._resize_edges:
            top = min(geom.bottom() - min_height + 1, geom.top() + delta.y())
            geom.setTop(top)
        elif "bottom" in self._resize_edges:
            geom.setBottom(max(geom.top() + min_height - 1, geom.bottom() + delta.y()))

        self.setGeometry(geom)

    def _snap_to_edges(self) -> None:
        screen = self.screen()
        if not screen:
            return
        area = screen.availableGeometry()
        geom = self.frameGeometry()
        x, y = geom.x(), geom.y()
        threshold = 28
        if abs(geom.left() - area.left()) < threshold:
            x = area.left()
        if abs(area.right() - geom.right()) < threshold:
            x = area.right() - geom.width() + 1
        if abs(geom.top() - area.top()) < threshold:
            y = area.top()
        if abs(area.bottom() - geom.bottom()) < threshold:
            y = area.bottom() - geom.height() + 1
        self.move(x, y)

    def _save_geometry(self) -> None:
        self.settings.x = self.x()
        self.settings.y = self.y()
        if self._mode == "panel":
            self.settings.width = self.width()
            self.settings.height = self.height()

    def _apply_styles(self, widget: QWidget) -> None:
        widget.setStyleSheet(
            """
            QWidget {
                color: rgba(248, 250, 255, 210);
                font-family: Segoe UI;
                font-size: 13px;
                background: transparent;
            }
            QLabel#Muted {
                color: rgba(226, 232, 244, 150);
                font-size: 12px;
            }
            QLabel#OnboardingTitle {
                color: rgba(255, 255, 255, 242);
            }
            QLabel#OnboardingInfo {
                color: rgba(242, 247, 255, 230);
                background-color: rgba(222, 236, 255, 14);
                border: 1px solid rgba(235, 245, 255, 25);
                border-radius: 10px;
                padding: 10px;
            }
            QWidget#OnboardingCard {
                background-color: rgba(214, 231, 255, 10);
                border: 1px solid rgba(231, 242, 255, 20);
                border-radius: 11px;
            }
            QWidget#MaterialToolbar {
                background-color: rgba(226, 239, 255, 9);
                border: 1px solid rgba(235, 246, 255, 20);
                border-radius: 12px;
            }
            QPushButton {
                background-color: rgba(0, 0, 0, 52);
                border: none;
                border-radius: 7px;
                padding: 5px 10px;
                color: rgba(248, 250, 255, 214);
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 78);
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 100);
            }
            QPushButton:disabled {
                color: rgba(218, 229, 244, 115);
                background-color: rgba(225, 238, 255, 10);
            }
            QPushButton#OnboardingPrimary {
                background-color: rgba(82, 130, 255, 190);
                border: 1px solid rgba(154, 190, 255, 88);
                border-radius: 9px;
                color: rgba(255, 255, 255, 242);
                font-weight: 600;
                padding: 6px 14px;
            }
            QPushButton#OnboardingPrimary:hover {
                background-color: rgba(96, 144, 255, 220);
            }
            QPushButton#OnboardingPrimary:disabled {
                background-color: rgba(74, 94, 132, 80);
                border-color: rgba(166, 190, 230, 30);
                color: rgba(225, 235, 248, 105);
            }
            QPushButton[iconOnly="true"] {
                padding: 0;
                background-color: rgba(0, 0, 0, 44);
                border-radius: 7px;
            }
            QPushButton[iconOnly="true"]:hover {
                background-color: rgba(0, 0, 0, 72);
            }
            QPushButton[iconOnly="true"]:pressed {
                background-color: rgba(0, 0, 0, 96);
            }
            QPushButton[iconOnly="true"][active="true"] {
                background-color: rgba(64, 220, 160, 92);
            }
            QPushButton[iconOnly="true"][active="true"]:hover {
                background-color: rgba(64, 220, 160, 118);
            }
            QPushButton[smartAssist="true"] {
                padding: 4px 8px;
                text-align: center;
            }
            QPushButton[quickAsk="true"] {
                padding: 2px 6px;
                font-size: 11px;
            }
            QPushButton#panelAskSubmit {
                padding: 4px 10px;
            }
            QLineEdit#panelAskInput {
                padding: 4px 8px;
            }
            QWidget#Panel QTextBrowser,
            QWidget#Panel QPlainTextEdit {
                padding: 6px;
            }
            QWidget#AssistResponseBubble QTextBrowser,
            QWidget#AssistResponseBubble QTextBrowser:hover,
            QWidget#AssistResponseBubble QTextBrowser:focus {
                background: transparent;
                border: none;
                border-radius: 8px;
                padding: 4px 2px;
            }
            QPushButton[tab="true"] {
                background-color: rgba(0, 0, 0, 22);
                padding: 3px 8px;
            }
            QPushButton[tab="true"]:hover {
                background-color: rgba(0, 0, 0, 40);
            }
            QPushButton[tab="true"][selected="true"] {
                background-color: rgba(0, 0, 0, 72);
            }
            QLineEdit, QTextBrowser, QPlainTextEdit {
                background-color: rgba(0, 0, 0, 48);
                border: none;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: rgba(210, 225, 255, 74);
                color: rgba(248, 250, 255, 218);
            }
            QLineEdit#OnboardingKey {
                background-color: rgba(4, 10, 20, 105);
                border: 1px solid rgba(220, 235, 255, 42);
                border-radius: 9px;
                padding: 9px 11px;
            }
            QLineEdit#OnboardingKey:focus {
                border-color: rgba(120, 178, 255, 125);
                background-color: rgba(8, 18, 34, 125);
            }
            QLineEdit#OnboardingKey[invalid="true"] {
                border-color: rgba(255, 124, 132, 170);
                background-color: rgba(76, 24, 35, 105);
            }
            QLineEdit:hover, QTextBrowser:hover, QPlainTextEdit:hover,
            QLineEdit:focus, QTextBrowser:focus, QPlainTextEdit:focus {
                background-color: rgba(0, 0, 0, 72);
            }
            QTextBrowser, QPlainTextEdit {
                line-height: 1.3;
            }
            QScrollBar:vertical, QScrollBar:horizontal {
                background: transparent;
                border: 0;
                margin: 0;
                width: 8px;
                height: 8px;
            }
            QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                background: rgba(242, 247, 255, 190);
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                background: rgba(255, 255, 255, 232);
            }
            QScrollBar::add-line, QScrollBar::sub-line,
            QScrollBar::add-page, QScrollBar::sub-page {
                background: transparent;
                border: 0;
            }
            """
        )


class SettingsDialog(QDialog):
    def __init__(
        self, settings: Settings, current_key: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mimir Settings")
        self.settings = settings
        self.replay_requested = False
        self._shell_alpha_orig = max(6, min(255, int(settings.shell_alpha)))
        self._dialog_drag_offset: QPoint | None = None
        self.setModal(True)
        self.setMinimumSize(600, 560)
        self.resize(640, 720)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAutoFillBackground(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        header_icon = QLabel()
        header_icon.setPixmap(_make_glyph_icon("settings").pixmap(18, 18))
        header_icon.setFixedSize(22, 22)
        title = QLabel("Mimir Settings")
        title.setObjectName("SettingsTitle")
        title.setFont(QFont("Segoe UI", 16, QFont.DemiBold))
        close_button = QPushButton("X")
        close_button.setObjectName("SettingsClose")
        close_button.setFixedSize(26, 26)
        close_button.setToolTip("Close settings")
        close_button.clicked.connect(self.reject)
        header.addWidget(header_icon)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(close_button)
        layout.addLayout(header)

        form_widget = QWidget()
        grid = QGridLayout(form_widget)
        grid.setContentsMargins(2, 2, 10, 2)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("Leave unchanged" if current_key else "sk-...")
        self.analysis_model = QLineEdit(settings.analysis_model)
        self.analysis_service_tier = QLineEdit(settings.analysis_service_tier)
        self.analysis_reasoning_effort = QLineEdit(settings.analysis_reasoning_effort)
        self.smarter_model = QLineEdit(settings.smarter_model)
        self.smarter_model.setToolTip(
            "Model used only by the brain icon beside Copy on an assistant response"
        )
        self.smarter_service_tier = QLineEdit(settings.smarter_service_tier)
        self.smarter_service_tier.setToolTip(
            "Service tier for Smarter: priority (faster), default, or blank"
        )
        self.smarter_reasoning_effort = QLineEdit(settings.smarter_reasoning_effort)
        self.smarter_reasoning_effort.setToolTip(
            "Reasoning effort for Smarter: none, low, medium, high, or xhigh"
        )
        self.smarter_web_search_enabled = QCheckBox(
            "Use live web search for Smarter responses"
        )
        self.smarter_web_search_enabled.setChecked(settings.smarter_web_search_enabled)
        self.smarter_web_search_enabled.setToolTip(
            "When enabled, Smarter can use live web search to improve its response"
        )
        self.smarter_web_search_context_size = QLineEdit(
            settings.smarter_web_search_context_size
        )
        self.smarter_web_search_context_size.setToolTip(
            "Web search context for Smarter: low (few sources, faster), medium, or high"
        )
        self.visual_model = QLineEdit(settings.visual_model)
        self.visual_model.setToolTip(
            "Model used only for selected screenshot / Visual AI extraction"
        )
        self.visual_reasoning_effort = QLineEdit(settings.visual_reasoning_effort)
        self.visual_reasoning_effort.setToolTip(
            "Reasoning effort used only for Visual AI extraction: none, low, medium, high, or xhigh"
        )
        self.transcription_model = QLineEdit(settings.transcription_model)
        self.language = QLineEdit(settings.language)
        self.auto_assist_interval_sec = QSpinBox()
        self.auto_assist_interval_sec.setRange(3, 300)
        self.auto_assist_interval_sec.setSuffix(" sec")
        self.auto_assist_interval_sec.setValue(
            max(3, min(300, int(settings.auto_assist_interval_sec)))
        )
        self.audio_vad_enabled = QCheckBox("Skip silent audio before sending")
        self.audio_vad_enabled.setChecked(settings.audio_vad_enabled)
        self.audio_vad_threshold = QLineEdit(str(settings.audio_vad_threshold))
        self.audio_vad_silence_ms = QLineEdit(str(settings.audio_vad_silence_ms))
        self.ai_prompt_context = QPlainTextEdit()
        self.ai_prompt_context.setPlainText(settings.ai_prompt_context)
        self.ai_prompt_context.setPlaceholderText(
            "Example: I am going into an IT help desk interview. Favor clear Active Directory, Microsoft 365, networking, and troubleshooting context."
        )
        self.ai_prompt_context.setMinimumHeight(92)
        self.capture_exclusion = QCheckBox(
            "Hide overlay from screen share and recording (Windows)"
        )
        self.capture_exclusion.setChecked(settings.capture_exclusion)

        shell_panel = QWidget()
        shell_layout = QVBoxLayout(shell_panel)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(4)
        shell_hint = QLabel(
            "Transparent (left) to solid black (right). Applies to the main overlay window."
        )
        shell_hint.setWordWrap(True)
        shell_hint.setStyleSheet("color: rgba(255, 255, 255, 140); font-size: 11px;")
        shell_row = QHBoxLayout()
        self.shell_alpha_slider = QSlider(Qt.Horizontal)
        self.shell_alpha_slider.setRange(6, 255)
        self.shell_alpha_slider.setValue(self._shell_alpha_orig)
        self.shell_alpha_value = QLabel(str(self.shell_alpha_slider.value()))
        self.shell_alpha_value.setMinimumWidth(28)
        self.shell_alpha_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.shell_alpha_slider.valueChanged.connect(self._on_shell_alpha_slider)
        shell_row.addWidget(self.shell_alpha_slider, 1)
        shell_row.addWidget(self.shell_alpha_value)
        shell_layout.addWidget(shell_hint)
        shell_layout.addLayout(shell_row)

        rows = [
            ("OpenAI API key", self.key),
            ("Analysis model", self.analysis_model),
            ("Analysis service tier", self.analysis_service_tier),
            ("Analysis reasoning", self.analysis_reasoning_effort),
            ("Smarter model", self.smarter_model),
            ("Smarter service tier", self.smarter_service_tier),
            ("Smarter reasoning", self.smarter_reasoning_effort),
            ("Smarter web search", self.smarter_web_search_enabled),
            ("Smarter web search context", self.smarter_web_search_context_size),
            ("Visual AI model", self.visual_model),
            ("Visual AI reasoning", self.visual_reasoning_effort),
            ("Transcription model", self.transcription_model),
            ("Language", self.language),
            ("Auto Assist countdown", self.auto_assist_interval_sec),
            ("Audio cost saver", self.audio_vad_enabled),
            ("Silence threshold", self.audio_vad_threshold),
            ("Silence hangover ms", self.audio_vad_silence_ms),
            ("AI prompt context", self.ai_prompt_context),
            ("Privacy", self.capture_exclusion),
            ("Black background", shell_panel),
        ]
        for row, (label, field) in enumerate(rows):
            grid.addWidget(QLabel(label), row, 0)
            grid.addWidget(field, row, 1)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        form_scroll.setFrameShape(QScrollArea.NoFrame)
        form_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )
        form_scroll.setWidget(form_widget)
        layout.addWidget(form_scroll, 1)
        replay_button = QPushButton("Replay onboarding")
        replay_button.setToolTip("Close Settings and open the guided product tour")
        replay_button.clicked.connect(self._request_onboarding_replay)
        layout.addWidget(replay_button, 0, Qt.AlignLeft)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setStyleSheet(
            """
            QDialog {
                background: transparent;
                color: rgba(248, 250, 255, 214);
                font-family: Segoe UI;
                font-size: 13px;
            }
            QLabel {
                color: rgba(248, 250, 255, 214);
                background: transparent;
            }
            QLabel#SettingsTitle {
                color: rgba(255, 255, 255, 226);
                font-size: 16px;
            }
            QLineEdit, QPlainTextEdit, QSpinBox {
                background-color: rgba(0, 0, 0, 48);
                color: rgba(248, 250, 255, 218);
                border: none;
                border-radius: 7px;
                padding: 8px;
                selection-background-color: rgba(210, 225, 255, 74);
            }
            QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover,
            QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {
                background-color: rgba(0, 0, 0, 72);
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: rgba(0, 0, 0, 44);
                border: none;
                width: 18px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: rgba(0, 0, 0, 72);
            }
            QCheckBox {
                color: rgba(248, 250, 255, 214);
                spacing: 8px;
                background: transparent;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                background-color: rgba(0, 0, 0, 48);
                border: 1px solid rgba(170, 176, 188, 58);
            }
            QCheckBox::indicator:checked {
                background-color: rgba(64, 220, 160, 118);
                border: 1px solid rgba(64, 220, 160, 150);
            }
            QPushButton {
                background-color: rgba(0, 0, 0, 52);
                color: rgba(248, 250, 255, 214);
                border: none;
                border-radius: 7px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 78);
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 100);
            }
            QPushButton#SettingsClose {
                padding: 0;
                font-size: 11px;
                background-color: rgba(0, 0, 0, 44);
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: rgba(0, 0, 0, 72);
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: rgba(248, 250, 255, 150);
                width: 13px;
                height: 13px;
                margin: -5px 0;
                border-radius: 6px;
            }
            QSlider::sub-page:horizontal {
                background: rgba(210, 225, 255, 74);
                border-radius: 3px;
            }
            """
        )

    def _request_onboarding_replay(self) -> None:
        self.replay_requested = True
        self.reject()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect().adjusted(1, 1, -1, -1))
        shell = QPainterPath()
        shell.addRoundedRect(rect, 18, 18)
        painter.fillPath(shell, QColor(0, 0, 0, 242))
        painter.strokePath(shell, QPen(QColor(170, 176, 188, 58), 1))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and event.position().y() <= 44:
            self._dialog_drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dialog_drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._dialog_drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._dialog_drag_offset = None
        super().mouseReleaseEvent(event)

    def _on_shell_alpha_slider(self, value: int) -> None:
        self.settings.shell_alpha = value
        self.shell_alpha_value.setText(str(value))
        parent = self.parent()
        if isinstance(parent, GlassWindow):
            parent.update()
            parent._apply_styles(parent)

    def reject(self) -> None:
        self.settings.shell_alpha = self._shell_alpha_orig
        parent = self.parent()
        if isinstance(parent, GlassWindow):
            parent.update()
            parent._apply_styles(parent)
        super().reject()

    def apply_to_settings(self) -> str:
        self.settings.analysis_model = (
            self.analysis_model.text().strip() or self.settings.analysis_model
        )
        self.settings.analysis_service_tier = (
            self.analysis_service_tier.text().strip()
            or self.settings.analysis_service_tier
        )
        self.settings.analysis_reasoning_effort = (
            self.analysis_reasoning_effort.text().strip()
            or self.settings.analysis_reasoning_effort
        )
        self.settings.smarter_model = (
            self.smarter_model.text().strip() or self.settings.smarter_model
        )
        self.settings.smarter_service_tier = (
            self.smarter_service_tier.text().strip()
            or self.settings.smarter_service_tier
        )
        self.settings.smarter_reasoning_effort = (
            self.smarter_reasoning_effort.text().strip()
            or self.settings.smarter_reasoning_effort
        )
        self.settings.smarter_web_search_enabled = (
            self.smarter_web_search_enabled.isChecked()
        )
        self.settings.smarter_web_search_context_size = (
            self.smarter_web_search_context_size.text().strip()
            or self.settings.smarter_web_search_context_size
        )
        self.settings.visual_model = (
            self.visual_model.text().strip() or self.settings.visual_model
        )
        self.settings.visual_reasoning_effort = (
            self.visual_reasoning_effort.text().strip()
            or self.settings.visual_reasoning_effort
        )
        self.settings.transcription_model = (
            self.transcription_model.text().strip() or self.settings.transcription_model
        )
        self.settings.language = self.language.text().strip() or "en"
        self.settings.auto_assist_interval_sec = self.auto_assist_interval_sec.value()
        self.settings.audio_vad_enabled = self.audio_vad_enabled.isChecked()
        self.settings.audio_vad_threshold = _parse_float(
            self.audio_vad_threshold.text(),
            self.settings.audio_vad_threshold,
            minimum=0.0001,
        )
        self.settings.audio_vad_silence_ms = _parse_int(
            self.audio_vad_silence_ms.text(),
            self.settings.audio_vad_silence_ms,
            minimum=100,
        )
        self.settings.ai_prompt_context = self.ai_prompt_context.toPlainText().strip()
        self.settings.capture_exclusion = self.capture_exclusion.isChecked()
        self.settings.shell_alpha = max(6, min(255, self.shell_alpha_slider.value()))
        return self.key.text().strip()


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _parse_float(text: str, fallback: float, minimum: float | None = None) -> float:
    try:
        value = float(text.strip())
    except ValueError:
        return fallback
    if minimum is not None:
        value = max(minimum, value)
    return value


def _parse_int(text: str, fallback: int, minimum: int | None = None) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        return fallback
    if minimum is not None:
        value = max(minimum, value)
    return value


def escape_markdown_inline(text: str) -> str:
    escaped = _escape_html(" ".join(text.split()))
    for char in "\\`*_{}[]()#+-.!|":
        escaped = escaped.replace(char, f"\\{char}")
    return escaped
