"""Main PySide6 window for the first read-only observation step."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from screenvision_sentinel.app.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_SCREENSHOT_DIR,
    AppConfig,
    load_config,
    save_monitor_region,
)
from screenvision_sentinel.capture.base import BaseCaptureService, ScreenRegion
from screenvision_sentinel.capture.mss_capture import MssCaptureService
from screenvision_sentinel.ocr.engine_factory import AVAILABLE_ENGINES, create_ocr_engine
from screenvision_sentinel.safety.controller import SafetyController

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class CaptureScreenGeometry:
    """Mapping between Qt logical coordinates and mss physical coordinates."""

    qt_geometry: QRect
    capture_left: int
    capture_top: int
    capture_width: int
    capture_height: int

    def contains_rect(self, rect: QRect) -> bool:
        """Return whether a Qt rectangle stays within one display."""
        return self.qt_geometry.contains(rect.topLeft()) and self.qt_geometry.contains(
            rect.bottomRight()
        )

    def map_rect_to_capture_region(self, rect: QRect) -> ScreenRegion:
        """Convert a Qt logical rectangle to an mss physical pixel region."""
        x_scale = self.capture_width / self.qt_geometry.width()
        y_scale = self.capture_height / self.qt_geometry.height()

        left = self.capture_left + round((rect.left() - self.qt_geometry.left()) * x_scale)
        top = self.capture_top + round((rect.top() - self.qt_geometry.top()) * y_scale)
        right = self.capture_left + round((rect.right() + 1 - self.qt_geometry.left()) * x_scale)
        bottom = self.capture_top + round((rect.bottom() + 1 - self.qt_geometry.top()) * y_scale)

        return ScreenRegion(
            left=left,
            top=top,
            width=max(1, right - left),
            height=max(1, bottom - top),
        )


class RegionSelectionOverlay(QDialog):
    """Fullscreen mouse-drag selector for a screen region."""

    _MIN_REGION_SIZE = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._origin: QPoint | None = None
        self._current: QPoint | None = None
        self._selected_region: ScreenRegion | None = None
        self._error_message: str | None = None
        self._capture_screens = self._build_capture_screen_geometries()
        self._screen_geometry = self._build_virtual_screen_geometry()

        self.setWindowTitle("选择监控区域")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(self._screen_geometry)

    @property
    def selected_region(self) -> ScreenRegion | None:
        return self._selected_region

    @property
    def error_message(self) -> str | None:
        return self._error_message

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._origin is None:
            event.ignore()
            return
        self._current = event.position().toPoint()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            event.ignore()
            return

        self._current = event.position().toPoint()
        selected_rect = self._selection_rect()
        if (
            selected_rect.width() < self._MIN_REGION_SIZE
            or selected_rect.height() < self._MIN_REGION_SIZE
        ):
            self._origin = None
            self._current = None
            self.update()
            self.reject()
            return

        selected_region = self._map_selection_to_capture_region(selected_rect)
        if selected_region is None:
            self.reject()
            return

        self._selected_region = selected_region
        self.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))

        selected_rect = self._selection_rect()
        if selected_rect.isNull():
            return

        painter.fillRect(selected_rect, QColor(0, 140, 255, 45))
        pen = QPen(QColor(0, 170, 255), 2)
        painter.setPen(pen)
        painter.drawRect(selected_rect.adjusted(0, 0, -1, -1))

    def _selection_rect(self) -> QRect:
        if self._origin is None or self._current is None:
            return QRect()
        return QRect(self._origin, self._current).normalized()

    def _map_selection_to_capture_region(self, selected_rect: QRect) -> ScreenRegion | None:
        global_rect = selected_rect.translated(self.geometry().topLeft())
        for capture_screen in self._capture_screens:
            if capture_screen.contains_rect(global_rect):
                return capture_screen.map_rect_to_capture_region(global_rect)

        self._error_message = "请在同一显示器内拖拽选择区域；跨显示器选区暂不支持。"
        return None

    @staticmethod
    def _build_capture_screen_geometries() -> list[CaptureScreenGeometry]:
        qt_screens = QGuiApplication.screens()
        if not qt_screens:
            return []

        mss_monitors = []
        try:
            import mss

            with mss.MSS() as sct:
                mss_monitors = list(sct.monitors[1:])
        except Exception as exc:
            LOGGER.warning("Unable to read mss monitor geometry: %s", exc)

        capture_screens: list[CaptureScreenGeometry] = []
        for index, screen in enumerate(qt_screens):
            qt_geometry = screen.geometry()
            if index < len(mss_monitors):
                monitor = mss_monitors[index]
                capture_screens.append(
                    CaptureScreenGeometry(
                        qt_geometry=qt_geometry,
                        capture_left=int(monitor["left"]),
                        capture_top=int(monitor["top"]),
                        capture_width=int(monitor["width"]),
                        capture_height=int(monitor["height"]),
                    )
                )
                continue

            device_pixel_ratio = screen.devicePixelRatio()
            capture_screens.append(
                CaptureScreenGeometry(
                    qt_geometry=qt_geometry,
                    capture_left=round(qt_geometry.left() * device_pixel_ratio),
                    capture_top=round(qt_geometry.top() * device_pixel_ratio),
                    capture_width=round(qt_geometry.width() * device_pixel_ratio),
                    capture_height=round(qt_geometry.height() * device_pixel_ratio),
                )
            )
        return capture_screens

    @staticmethod
    def _build_virtual_screen_geometry() -> QRect:
        screens = QGuiApplication.screens()
        if not screens:
            return QRect(0, 0, 800, 600)

        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        return geometry


class RegionDialog(QDialog):
    """Simple coordinate input dialog for the first observation step."""

    def __init__(self, region: ScreenRegion | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择监控区域")
        self.setModal(True)

        self._left_input = self._build_spin_box(-100000, 100000, region.left if region else 0)
        self._top_input = self._build_spin_box(-100000, 100000, region.top if region else 0)
        self._width_input = self._build_spin_box(1, 100000, region.width if region else 400)
        self._height_input = self._build_spin_box(1, 100000, region.height if region else 160)

        layout = QFormLayout(self)
        layout.addRow("左上角 X", self._left_input)
        layout.addRow("左上角 Y", self._top_input)
        layout.addRow("宽度", self._width_input)
        layout.addRow("高度", self._height_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def selected_region(self) -> ScreenRegion:
        return ScreenRegion(
            left=self._left_input.value(),
            top=self._top_input.value(),
            width=self._width_input.value(),
            height=self._height_input.value(),
        )

    @staticmethod
    def _build_spin_box(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin_box = QSpinBox()
        spin_box.setRange(minimum, maximum)
        spin_box.setValue(value)
        spin_box.setSingleStep(10)
        return spin_box


class MainWindow(QMainWindow):
    """Desktop window for region selection and one-shot screenshots."""

    def __init__(
        self,
        safety_controller: SafetyController | None = None,
        config_path: Path = DEFAULT_CONFIG_PATH,
        capture_service: BaseCaptureService | None = None,
    ) -> None:
        super().__init__()
        self._safety_controller = safety_controller or SafetyController()
        self._config_path = config_path
        self._config = self._load_initial_config()
        self._capture_service = capture_service or MssCaptureService(DEFAULT_SCREENSHOT_DIR)
        self._region_label: QLabel | None = None
        self._screenshot_label: QLabel | None = None
        self._status_label: QLabel | None = None
        self._current_ocr_label: QLabel | None = None
        self._previous_ocr_label: QLabel | None = None
        self._last_screenshot_path: Path | None = None
        self._ocr_engine = create_ocr_engine(self._config.ocr_engine)
        self._engine_combo: QComboBox | None = None
        self._current_ocr_text: str = ""
        self._previous_ocr_text: str = ""
        self.setWindowTitle("屏幕视觉哨兵")
        self.setMinimumSize(620, 420)
        self.setCentralWidget(self._build_content())
        self._refresh_region_label()

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setSpacing(14)

        title = QLabel("屏幕视觉哨兵")
        title.setObjectName("titleLabel")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        mode = QLabel("当前模式：只读观察模式")
        mode.setAlignment(Qt.AlignmentFlag.AlignCenter)

        stage = QLabel("当前阶段：阶段 1：只观察（区域选择与单次截图）")
        stage.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status_label = QLabel("启动状态：未开始截图、未开始 OCR、未创建自动操作任务")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._region_label = QLabel()
        self._region_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._screenshot_label = QLabel("最近截图：未执行")
        self._screenshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._current_ocr_label = QLabel("当前识别结果：未执行")
        self._current_ocr_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._current_ocr_label.setWordWrap(True)

        self._previous_ocr_label = QLabel("上一轮识别结果：无")
        self._previous_ocr_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._previous_ocr_label.setWordWrap(True)

        engine_row = QHBoxLayout()
        engine_label = QLabel("OCR 引擎：")
        self._engine_combo = QComboBox()
        for engine_name in AVAILABLE_ENGINES:
            self._engine_combo.addItem(engine_name)
        initial_engine = self._config.ocr_engine
        if initial_engine in AVAILABLE_ENGINES:
            self._engine_combo.setCurrentText(initial_engine)
        self._engine_combo.currentTextChanged.connect(self._handle_engine_changed)
        engine_row.addWidget(engine_label)
        engine_row.addWidget(self._engine_combo)
        engine_row.addStretch(1)

        button_grid = QGridLayout()
        buttons = [
            ("选择监控区域", self._handle_select_region),
            ("手动输入坐标", self._handle_manual_region_input),
            ("测试截图", self._handle_test_screenshot),
            ("测试 OCR", self._handle_test_ocr),
            ("开始监控", self._handle_start_monitoring),
            ("暂停监控", self._handle_pause_monitoring),
            ("紧急停止", self._handle_emergency_stop),
        ]

        for index, (label, handler) in enumerate(buttons):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, slot=handler: slot())
            row, column = divmod(index, 2)
            button_grid.addWidget(button, row, column)

        layout.addWidget(title)
        layout.addWidget(mode)
        layout.addWidget(stage)
        layout.addWidget(self._status_label)
        layout.addWidget(self._region_label)
        layout.addWidget(self._screenshot_label)
        layout.addWidget(self._current_ocr_label)
        layout.addWidget(self._previous_ocr_label)
        layout.addLayout(engine_row)
        layout.addLayout(button_grid)
        layout.addStretch(1)
        return root

    def _load_initial_config(self) -> AppConfig:
        try:
            return load_config(self._config_path)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Local config ignored because it is invalid: %s", exc)
            return AppConfig()

    def _handle_select_region(self) -> None:
        self.hide()
        overlay = RegionSelectionOverlay(self)
        accepted = overlay.exec() == QDialog.DialogCode.Accepted
        self.show()
        self.raise_()
        self.activateWindow()

        region = overlay.selected_region
        if not accepted or region is None:
            if overlay.error_message is not None:
                self._show_warning("区域选择失败", overlay.error_message)
            self._set_status("区域选择已取消。")
            return

        self._save_selected_region(region, "监控区域已保存")

    def _handle_manual_region_input(self) -> None:
        dialog = RegionDialog(self._config.monitor_region, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_status("手动输入已取消。")
            return

        self._save_selected_region(dialog.selected_region(), "监控区域已保存")

    def _save_selected_region(self, region: ScreenRegion, title: str) -> None:
        try:
            save_monitor_region(self._config_path, region, self._config)
        except (OSError, ValueError) as exc:
            self._show_warning("保存失败", f"监控区域保存失败：{exc}")
            self._set_status("监控区域保存失败。")
            return

        self._config = AppConfig(
            screenshot_interval_seconds=self._config.screenshot_interval_seconds,
            confirmation_count=self._config.confirmation_count,
            ocr_confidence_threshold=self._config.ocr_confidence_threshold,
            alert_cooldown_seconds=self._config.alert_cooldown_seconds,
            automatic_actions_enabled=False,
            save_trigger_screenshots=self._config.save_trigger_screenshots,
            ocr_engine=self._config.ocr_engine,
            max_capture_width=self._config.max_capture_width,
            max_capture_height=self._config.max_capture_height,
            max_capture_pixels=self._config.max_capture_pixels,
            min_capture_coordinate=self._config.min_capture_coordinate,
            max_capture_coordinate=self._config.max_capture_coordinate,
            debug_image_dir=self._config.debug_image_dir,
            server_host=self._config.server_host,
            server_port=self._config.server_port,
            monitor_region=region,
        )
        self._refresh_region_label()
        self._set_status("监控区域已保存。")
        QMessageBox.information(self, title, self._format_region(region))

    def _handle_test_screenshot(self) -> None:
        region = self._config.monitor_region
        if region is None:
            self._show_warning("缺少监控区域", "请先选择监控区域，再执行测试截图。")
            self._set_status("测试截图未执行：缺少监控区域。")
            return

        result = self._capture_service.capture_region(region)
        if not result.success:
            error = result.error or "未知错误"
            self._show_warning("测试截图失败", error)
            self._set_status("测试截图失败。")
            if self._screenshot_label is not None:
                self._screenshot_label.setText(f"最近截图：失败，{error}")
            return

        self._last_screenshot_path = result.image_path
        image_path = result.image_path
        label_text = f"最近截图：成功，保存至 {image_path}" if image_path else "最近截图：成功"
        if self._screenshot_label is not None:
            self._screenshot_label.setText(label_text)
        self._set_status("测试截图完成；未启动连续监控。")
        QMessageBox.information(self, "测试截图成功", label_text)

    def _handle_test_ocr(self) -> None:
        if self._last_screenshot_path is None or not self._last_screenshot_path.exists():
            self._show_warning("缺少截图", "请先执行测试截图，再执行测试 OCR。")
            self._set_status("测试 OCR 未执行：缺少截图。")
            return

        result = self._ocr_engine.recognize(self._last_screenshot_path)
        if not result.success:
            error = result.error or "未知错误"
            self._show_warning("OCR 失败", error)
            self._set_status("测试 OCR 失败。")
            return

        self._previous_ocr_text = self._current_ocr_text
        self._current_ocr_text = result.text

        current_display = (
            f"当前识别结果（{result.engine_name}，"
            f"置信度 {result.confidence:.2f}，"
            f"耗时 {result.elapsed_ms:.1f}ms）：\n{result.text}"
        )
        if self._current_ocr_label is not None:
            self._current_ocr_label.setText(current_display)

        previous_display = (
            f"上一轮识别结果：{self._previous_ocr_text}"
            if self._previous_ocr_text
            else "上一轮识别结果：无"
        )
        if self._previous_ocr_label is not None:
            self._previous_ocr_label.setText(previous_display)

        self._set_status(f"测试 OCR 完成；当前使用 {self._ocr_engine.name}。")

    def _handle_engine_changed(self, engine_name: str) -> None:
        self._ocr_engine = create_ocr_engine(engine_name)
        self._set_status(f"OCR 引擎已切换为 {self._ocr_engine.name}。")

    def _handle_start_monitoring(self) -> None:
        self._show_stage_message("连续监控循环尚未实现；当前只支持手动单次截图。")
        self._set_status("未启动连续监控。")

    def _handle_pause_monitoring(self) -> None:
        self._show_stage_message("当前没有运行中的监控任务。")
        self._set_status("当前没有运行中的监控任务。")

    def _handle_emergency_stop(self) -> None:
        self._safety_controller.emergency_stop()
        self._show_stage_message("已进入紧急停止状态，自动动作保持关闭。")
        self._set_status("紧急停止已触发；自动动作保持关闭。")

    def _refresh_region_label(self) -> None:
        if self._region_label is None:
            return
        region = self._config.monitor_region
        text = "监控区域：未设置" if region is None else f"监控区域：{self._format_region(region)}"
        self._region_label.setText(text)

    def _set_status(self, message: str) -> None:
        if self._status_label is not None:
            self._status_label.setText(f"状态：{message}")

    def _show_stage_message(self, message: str) -> None:
        QMessageBox.information(self, "当前阶段提示", message)

    def _show_warning(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)

    @staticmethod
    def _format_region(region: ScreenRegion) -> str:
        return f"left={region.left}, top={region.top}, width={region.width}, height={region.height}"
