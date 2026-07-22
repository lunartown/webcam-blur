"""웹캠 화질 저하 GUI."""

import sys
import time

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from camera import RESOLUTIONS, CameraThread, available_cameras
from quality import MAX_SCALE, MIN_SCALE, PRESET_LABELS, PRESETS, QualityReducer

PREVIEW_MIN_WIDTH = 640


def to_pixmap(frame):
    """OpenCV BGR 프레임을 QPixmap으로."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    # QImage는 버퍼를 복사하지 않으므로, numpy 배열이 사라지기 전에 복사해 둔다.
    image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("webcam-blur")

        self.reducer = QualityReducer(preset=3)
        self.cameras = available_cameras()
        self.compare = False
        self.source_size = (0, 0)
        self._frame_times = []

        self._build_ui()
        self._start_camera()

    # ---------- UI 구성 ----------

    def _build_ui(self):
        self.preview = QLabel("카메라를 준비하는 중...")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumWidth(PREVIEW_MIN_WIDTH)
        self.preview.setStyleSheet("background: #1e1e1e; color: #999;")

        layout = QHBoxLayout()
        layout.addWidget(self.preview, stretch=1)
        layout.addWidget(self._build_controls())

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.showMessage("준비 중")

        # 상태 표시줄을 매 프레임 갱신하면 낭비이므로 주기적으로만 갱신한다.
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

    def _build_controls(self):
        panel = QWidget()
        panel.setFixedWidth(300)
        outer = QVBoxLayout(panel)

        # --- 입력 ---
        source_box = QGroupBox("입력")
        source_form = QFormLayout(source_box)

        self.camera_combo = QComboBox()
        for index, name in self.cameras:
            self.camera_combo.addItem(name, index)
        if not self.cameras:
            self.camera_combo.addItem("사용 가능한 카메라 없음", -1)
            self.camera_combo.setEnabled(False)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        source_form.addRow("카메라", self.camera_combo)

        self.resolution_combo = QComboBox()
        for w, h in RESOLUTIONS:
            self.resolution_combo.addItem(f"{w} x {h}", (w, h))
        self.resolution_combo.setCurrentIndex(RESOLUTIONS.index((1280, 720)))
        self.resolution_combo.currentIndexChanged.connect(self._on_resolution_changed)
        source_form.addRow("해상도", self.resolution_combo)

        outer.addWidget(source_box)

        # --- 화질 저하 ---
        quality_box = QGroupBox("화질 저하")
        quality_form = QFormLayout(quality_box)

        self.preset_combo = QComboBox()
        for level in sorted(PRESETS):
            self.preset_combo.addItem(PRESET_LABELS[level], level)
        self.preset_combo.setCurrentIndex(2)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        quality_form.addRow("강도", self.preset_combo)

        # 슬라이더는 정수만 다루므로 배율을 100배해서 쓴다.
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(int(MIN_SCALE * 100), int(MAX_SCALE * 100))
        self.scale_slider.setValue(int(self.reducer.scale * 100))
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.scale_label = QLabel()
        quality_form.addRow("미세 조정", self.scale_slider)
        quality_form.addRow("", self.scale_label)

        self.resample_combo = QComboBox()
        self.resample_combo.addItem("부드럽게", True)
        self.resample_combo.addItem("모자이크", False)
        self.resample_combo.currentIndexChanged.connect(self._on_resample_changed)
        quality_form.addRow("방식", self.resample_combo)

        outer.addWidget(quality_box)

        # --- 추가 효과 ---
        extra_box = QGroupBox("추가 효과")
        extra_form = QFormLayout(extra_box)

        self.blur_check = QCheckBox("블러")
        self.blur_check.setChecked(self.reducer.use_blur)
        self.blur_check.toggled.connect(self._on_blur_toggled)
        self.blur_slider = QSlider(Qt.Horizontal)
        self.blur_slider.setRange(0, 20)
        self.blur_slider.setValue(self.reducer.blur_strength)
        self.blur_slider.valueChanged.connect(self._on_blur_strength_changed)
        extra_form.addRow(self.blur_check, self.blur_slider)

        self.jpeg_check = QCheckBox("JPEG 아티팩트")
        self.jpeg_check.setChecked(self.reducer.use_jpeg)
        self.jpeg_check.toggled.connect(self._on_jpeg_toggled)
        self.jpeg_slider = QSlider(Qt.Horizontal)
        self.jpeg_slider.setRange(1, 100)
        self.jpeg_slider.setValue(self.reducer.jpeg_quality)
        self.jpeg_slider.valueChanged.connect(self._on_jpeg_quality_changed)
        extra_form.addRow(self.jpeg_check, self.jpeg_slider)

        outer.addWidget(extra_box)

        # --- 보기 ---
        self.compare_check = QCheckBox("원본과 나란히 비교")
        self.compare_check.toggled.connect(self._on_compare_toggled)
        outer.addWidget(self.compare_check)

        self.toggle_button = QPushButton("효과 끄기")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.toggled.connect(self._on_enabled_toggled)
        outer.addWidget(self.toggle_button)

        outer.addStretch(1)
        self._sync_scale_label()
        return panel

    # ---------- 카메라 ----------

    def _start_camera(self):
        if not self.cameras:
            self.preview.setText(
                "사용 가능한 카메라를 찾지 못했습니다.\n"
                "시스템 설정 > 개인정보 보호 및 보안 > 카메라 권한을 확인하세요."
            )
            self.thread = None
            return

        index = self.camera_combo.currentData()
        resolution = self.resolution_combo.currentData()
        self.thread = CameraThread(index, resolution)
        self.thread.frame_ready.connect(self._on_frame)
        self.thread.error.connect(self._on_error)
        self.thread.opened.connect(self._on_opened)
        self.thread.start()

    def _on_camera_changed(self):
        if self.thread is not None:
            self.thread.set_camera(self.camera_combo.currentData())

    def _on_resolution_changed(self):
        if self.thread is not None:
            self.thread.set_resolution(self.resolution_combo.currentData())

    def _on_opened(self, width, height):
        self.source_size = (width, height)

    def _on_error(self, message):
        self.status.showMessage(message, 5000)

    # ---------- 컨트롤 ----------

    def _on_preset_changed(self):
        self.reducer.apply_preset(self.preset_combo.currentData())
        # 프리셋이 바꾼 값을 위젯에도 반영한다. 이때 신호가 되돌아오지 않도록 막는다.
        for widget, value in (
            (self.scale_slider, int(self.reducer.scale * 100)),
            (self.blur_slider, self.reducer.blur_strength),
            (self.jpeg_slider, self.reducer.jpeg_quality),
        ):
            widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(False)
        self.blur_check.blockSignals(True)
        self.blur_check.setChecked(self.reducer.use_blur)
        self.blur_check.blockSignals(False)
        self._sync_scale_label()

    def _on_scale_changed(self, value):
        self.reducer.scale = value / 100
        self._sync_scale_label()

    def _on_resample_changed(self):
        self.reducer.smooth = self.resample_combo.currentData()

    def _on_blur_toggled(self, checked):
        self.reducer.use_blur = checked

    def _on_blur_strength_changed(self, value):
        self.reducer.blur_strength = value

    def _on_jpeg_toggled(self, checked):
        self.reducer.use_jpeg = checked

    def _on_jpeg_quality_changed(self, value):
        self.reducer.jpeg_quality = value

    def _on_compare_toggled(self, checked):
        self.compare = checked

    def _on_enabled_toggled(self, checked):
        self.reducer.enabled = checked
        self.toggle_button.setText("효과 끄기" if checked else "효과 켜기")

    def _sync_scale_label(self):
        w, h = self.source_size
        if w:
            ew, eh = self.reducer.effective_resolution(w, h)
            self.scale_label.setText(f"배율 {self.reducer.scale:.2f}  →  {ew} x {eh}")
        else:
            self.scale_label.setText(f"배율 {self.reducer.scale:.2f}")

    # ---------- 프레임 ----------

    def _on_frame(self, frame):
        frame = cv2.flip(frame, 1)  # 거울 모드가 자기 모습 확인엔 자연스럽다
        out = self.reducer.process(frame)

        view = np.hstack([frame, out]) if self.compare else out
        pixmap = to_pixmap(view)
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        self._frame_times.append(time.monotonic())
        if self.thread is not None:
            self.thread.frame_consumed()

    def _update_status(self):
        now = time.monotonic()
        self._frame_times = [t for t in self._frame_times if now - t < 2.0]
        fps = len(self._frame_times) / 2.0

        w, h = self.source_size
        if not w:
            return
        ew, eh = self.reducer.effective_resolution(w, h)
        state = "적용 중" if self.reducer.enabled else "꺼짐"
        self.status.showMessage(
            f"원본 {w}x{h}  |  실제 전달 {ew}x{eh}  |  {fps:.0f} fps  |  {state}"
        )
        self._sync_scale_label()

    def closeEvent(self, event):
        if self.thread is not None:
            self.thread.stop()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1100, 640)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
