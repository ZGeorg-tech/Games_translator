"""Lightweight shared subtitle appearance; no capture or model dependencies."""
from statistics import median
import math

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QTextLayout, QTextOption, QTextCharFormat, QImage
from PyQt6.QtWidgets import QLabel


def capture_scope(session):
    zone = session.get("monitor_zone", {}) or {}
    return (
        str(session.get("profile_path") or session.get("profile_id") or session.get("game_name") or ""),
        int(session.get("game_hwnd") or 0),
        *(int(zone.get(key) or 0) for key in
          ("left", "top", "left_offset", "top_offset", "width", "height")),
    )


def sample_points(width, height):
    return [(min(width - 1, int((width - 1) * x)), min(height - 1, int((height - 1) * y)))
            for x, y in ((.08, .12), (.50, .12), (.92, .12), (.08, .88), (.92, .88))]


def sample_background(image):
    """Five original ROI pixels, channel-wise median; no full-frame conversion."""
    if image is None or image.width < 1 or image.height < 1:
        return None
    points = sample_points(image.width, image.height)
    pixels = [image.getpixel(point) for point in points]
    if image.mode not in ("RGB", "RGBA"):
        # Conversion is restricted to five pixels, never the complete ROI.
        pixels = [image.crop((x, y, x + 1, y + 1)).convert("RGB").getpixel((0, 0))
                  for x, y in points]
    return tuple(int(median(pixel[channel] for pixel in pixels)) for channel in range(3))


def plate_color(visuals, sampled_rgb=None, default_opacity=135):
    if visuals.get("adaptive_plate_enabled", False) and sampled_rgb is not None:
        return QColor(*sampled_rgb, 255)
    if not visuals.get("draw_background_plate", True):
        return None
    color = QColor(visuals.get("background_color", "#000000"))
    if not color.isValid():
        color = QColor("#000000")
    try:
        opacity = int(visuals.get("background_opacity", default_opacity))
    except (TypeError, ValueError):
        opacity = default_opacity
    color.setAlpha(max(0, min(255, opacity)))
    return color


def paint_plate(painter, rect, visuals, sampled_rgb=None, default_opacity=135):
    color = plate_color(visuals, sampled_rgb, default_opacity)
    if color is None:
        return
    painter.setPen(Qt.PenStyle.NoPen)
    if visuals.get("adaptive_plate_enabled", False) and sampled_rgb is not None:
        # Full coverage, including corners, is essential when hiding source text.
        painter.fillRect(rect, color)
    else:
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 6.0, 6.0)


class SubtitleLabel(QLabel):
    """Keep QLabel sizing/normal painting; extend only outlined glyph painting."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._visuals = {}
        self.preview_background = False
        self._outline_cache = None

    def outline_width(self):
        if not self._visuals.get("outline_enabled", False):
            return 0.0
        points = self.font().pointSizeF()
        if points <= 0:
            points = self.font().pixelSize() * 72.0 / self.logicalDpiY()
        return float(self._visuals.get("outline_thickness", 3)) * points / 19.0 * self.logicalDpiY() / 96.0

    def set_visuals(self, visuals):
        self._visuals = dict(visuals)
        self._sync_outline_margin()
        self.update()

    def setFont(self, font):
        super().setFont(font)
        if hasattr(self, "_visuals"):
            self._sync_outline_margin()

    def _sync_outline_margin(self):
        margin = math.ceil(self.outline_width())
        self.setContentsMargins(margin, margin, margin, margin)

    def paintEvent(self, event):
        if self.preview_background:
            painter = QPainter(self)
            paint_plate(painter, self.rect(), self._visuals, (50, 65, 80))
            painter.end()
        width = self.outline_width()
        if width <= 0 or not self.text():
            super().paintEvent(event)
            return
        self._sync_outline_margin()
        rect = self.contentsRect()
        dpr = self.devicePixelRatioF()
        key = (self.text(), self.font().toString(), rect.width(), rect.height(), dpr, int(self.alignment()),
               self.wordWrap(), width, self._visuals.get("outline_color"), self._visuals.get("font_color"))
        if not self._outline_cache or self._outline_cache[0] != key:
            option = QTextOption()
            option.setAlignment(self.alignment() & Qt.AlignmentFlag.AlignHorizontal_Mask)
            option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere if self.wordWrap() else QTextOption.WrapMode.NoWrap)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(self._visuals.get("font_color", "#FFFFFF")))
            pen = QPen(QColor(self._visuals.get("outline_color", "#000000")))
            pen.setWidthF(width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            fmt.setTextOutline(pen)
            layouts, height = [], 0.0
            for paragraph in self.text().split("\n"):
                layout = QTextLayout(paragraph or " ", self.font(), self)
                layout.setTextOption(option)
                span = QTextLayout.FormatRange()
                span.start, span.length, span.format = 0, len((paragraph or " ").encode("utf-16-le")) // 2, fmt
                layout.setFormats([span])
                layout.beginLayout()
                while True:
                    line = layout.createLine()
                    if not line.isValid():
                        break
                    line.setLineWidth(max(1, rect.width()))
                    line.setPosition(QPointF(0, height))
                    height += line.height()
                layout.endLayout()
                layouts.append(layout)
            y = rect.top()
            if self.alignment() & Qt.AlignmentFlag.AlignVCenter:
                y += (rect.height() - height) / 2.0
            elif self.alignment() & Qt.AlignmentFlag.AlignBottom:
                y += rect.height() - height
            image = QImage(max(1, math.ceil(self.width() * dpr)), max(1, math.ceil(self.height() * dpr)),
                           QImage.Format.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(dpr)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            for layout in layouts:
                layout.draw(painter, QPointF(rect.left(), y))
                # Stroke behind the fill, preserving the chosen foreground.
                fill = QTextLayout.FormatRange()
                fill.start, fill.length = 0, len(layout.text().encode("utf-16-le")) // 2
                fill.format.setForeground(QColor(self._visuals.get("font_color", "#FFFFFF")))
                fill.format.setTextOutline(QPen(Qt.PenStyle.NoPen))
                layout.draw(painter, QPointF(rect.left(), y), [fill])
            painter.end()
            # One glyph layer only; the live background never enters this cache.
            self._outline_cache = (key, image)
        painter = QPainter(self)
        painter.drawImage(QPointF(0, 0), self._outline_cache[1])
