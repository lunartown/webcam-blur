"""웹캠 화질 저하 GUI.

화면에 두는 조작은 네 가지뿐이다: 카메라 고르기, 흐림 정도, 효과 On/Off,
가상 카메라 송출. 회의 직전에 열어서 바로 쓰는 도구라 선택지를 늘리지 않았다.
"""

import sys
import time

import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from camera import CameraSource, available_cameras
from quality import PRESET_LABELS, PRESETS, QualityReducer
from vcam import VirtualCamera, VirtualCameraError

PREVIEW_MIN_SIZE = (640, 360)

APP_STYLE = """
QMainWindow, QWidget#root {
    background: #121416;
    color: #e8ecef;
}

QLabel#title {
    color: #f7f9fa;
    font-size: 22px;
    font-weight: 700;
}

QLabel#subtle {
    color: #8c969f;
}

QLabel#controlLabel {
    color: #b5bdc5;
    font-size: 13px;
}

QFrame#previewFrame {
    background: #070809;
    border: 1px solid #2a3036;
    border-radius: 8px;
}

QLabel#preview {
    background: #070809;
    color: #7c858d;
    border-radius: 8px;
    font-size: 15px;
}

QFrame#controlsPanel {
    background: #1a1d20;
    border: 1px solid #2a3036;
    border-radius: 8px;
}

QComboBox {
    min-height: 32px;
    padding: 4px 10px;
    border: 1px solid #343b42;
    border-radius: 6px;
    background: #101214;
    color: #eef2f4;
}

QComboBox:disabled {
    color: #626b73;
}

QSlider::groove:horizontal {
    height: 6px;
    border-radius: 3px;
    background: #343b42;
}

QSlider::sub-page:horizontal {
    border-radius: 3px;
    background: #7cb7ff;
}

QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
    background: #f7f9fa;
}

QPushButton {
    min-height: 32px;
    padding: 4px 14px;
    border: 1px solid #3a424a;
    border-radius: 6px;
    background: #23282d;
    color: #eef2f4;
}

QPushButton:hover {
    background: #2b3137;
}

QPushButton:disabled {
    color: #68727a;
    background: #1a1d20;
}

QPushButton#primaryButton {
    border-color: #4b7f67;
    background: #214437;
}

QPushButton#primaryButton:checked {
    border-color: #8c5a5a;
    background: #4a2727;
}

QLabel[tone] {
    padding: 5px 10px;
    border-radius: 12px;
    font-size: 12px;
}

QLabel[tone="muted"] {
    color: #9aa3ab;
    background: #20252a;
    border: 1px solid #30363d;
}

QLabel[tone="info"] {
    color: #d8ecff;
    background: #182d42;
    border: 1px solid #2f5d87;
}

QLabel[tone="good"] {
    color: #dff7ea;
    background: #173526;
    border: 1px solid #356d50;
}

QLabel[tone="warn"] {
    color: #ffe8b7;
    background: #3d3019;
    border: 1px solid #735a27;
}

QLabel[tone="bad"] {
    color: #ffd5d5;
    background: #402020;
    border: 1px solid #7b3c3c;
}

QStatusBar {
    background: #121416;
    color: #8c969f;
}
"""


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
        self.current_preset = 3
        self.vcam = VirtualCamera()
        self.cameras = available_cameras()
        self.source = None
        self.source_size = (0, 0)
        self._frame_times = []

        self._build_ui()
        self._start_camera()

    # ---------- UI 구성 ----------

    def _build_ui(self):
        self.setMinimumSize(840, 560)
        self.setStyleSheet(APP_STYLE)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 10)
        layout.setSpacing(12)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_preview(), stretch=1)
        layout.addLayout(self._build_badges())
        layout.addWidget(self._build_controls())

        central = QWidget()
        central.setObjectName("root")
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.status = self.statusBar()
        self.status.showMessage("준비 중")

        # 상태 표시줄을 매 프레임 갱신하면 낭비이므로 주기적으로만 갱신한다.
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(500)

    def _build_header(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("webcam-blur")
        title.setObjectName("title")
        self.header_status = QLabel("카메라 준비 중")
        self.header_status.setObjectName("subtle")
        text.addWidget(title)
        text.addWidget(self.header_status)
        row.addLayout(text, stretch=1)

        self.refresh_button = QPushButton("새로고침")
        self.refresh_button.clicked.connect(self._refresh_cameras)
        row.addWidget(self.refresh_button)
        return row

    def _build_preview(self):
        self.preview = QLabel("카메라를 준비하는 중...")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(*PREVIEW_MIN_SIZE)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview)

        frame = QFrame()
        frame.setObjectName("previewFrame")
        frame.setLayout(layout)
        return frame

    def _build_badges(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 0, 0, 0)

        self.source_badge = self._badge("카메라 대기", "muted")
        self.effect_badge = self._badge("효과 보통", "info")
        self.output_badge = self._badge("송출 대기", "muted")
        row.addWidget(self.source_badge)
        row.addWidget(self.effect_badge)
        row.addWidget(self.output_badge)
        row.addStretch(1)
        return row

    def _badge(self, text, tone):
        label = QLabel(text)
        label.setContentsMargins(10, 5, 10, 5)
        label.setProperty("tone", tone)
        return label

    def _build_controls(self):
        panel = QFrame()
        panel.setObjectName("controlsPanel")

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        camera_row = QHBoxLayout()
        camera_row.setSpacing(10)

        self.camera_combo = QComboBox()
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        self._populate_camera_combo()
        camera_row.addWidget(self._control_label("카메라"))
        camera_row.addWidget(self.camera_combo, stretch=1)
        layout.addLayout(camera_row)

        effect_row = QHBoxLayout()
        effect_row.setSpacing(10)

        self.preset_slider = QSlider(Qt.Horizontal)
        self.preset_slider.setRange(min(PRESETS), max(PRESETS))
        self.preset_slider.setValue(self.current_preset)
        self.preset_slider.setTickPosition(QSlider.TicksBelow)
        self.preset_slider.setTickInterval(1)
        self.preset_slider.setPageStep(1)
        self.preset_slider.valueChanged.connect(self._on_preset_changed)
        self.preset_label = QLabel(PRESET_LABELS[self.current_preset])
        self.preset_label.setObjectName("subtle")
        self.preset_label.setFixedWidth(110)
        effect_row.addWidget(self._control_label("흐림 정도"))
        effect_row.addWidget(self.preset_slider, stretch=1)
        effect_row.addWidget(self.preset_label)

        self.toggle_button = QPushButton("효과 끄기")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.toggled.connect(self._on_enabled_toggled)
        effect_row.addWidget(self.toggle_button)

        self.vcam_button = QPushButton("가상 카메라 시작")
        self.vcam_button.setObjectName("primaryButton")
        self.vcam_button.setCheckable(True)
        self.vcam_button.setEnabled(bool(self.cameras))
        self.vcam_button.toggled.connect(self._on_vcam_toggled)
        effect_row.addWidget(self.vcam_button)

        layout.addLayout(effect_row)
        panel.setLayout(layout)
        return panel

    def _control_label(self, text):
        label = QLabel(text)
        label.setObjectName("controlLabel")
        label.setFixedWidth(64)
        return label

    def _populate_camera_combo(self, preferred_name=None):
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        selected_index = 0
        for index, device in enumerate(self.cameras):
            # 번호가 아니라 장치 자체를 들고 있는다.
            name = device.description()
            self.camera_combo.addItem(name, device)
            if name == preferred_name:
                selected_index = index
        if self.cameras:
            self.camera_combo.setCurrentIndex(selected_index)
            self.camera_combo.setEnabled(True)
        else:
            self.camera_combo.addItem("카메라 없음", None)
            self.camera_combo.setEnabled(False)
        self.camera_combo.blockSignals(False)

    def _set_badge(self, label, text, tone):
        label.setText(text)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    # ---------- 카메라 ----------

    def _start_camera(self):
        if not self.cameras:
            self._show_no_camera()
            return

        self.source = CameraSource(self)
        self.source.frame_ready.connect(self._on_frame)
        self.source.error.connect(self._on_error)
        self.source.opened.connect(self._on_opened)
        self.source.start(self.camera_combo.currentData())

    def _refresh_cameras(self):
        current = self.camera_combo.currentData()
        preferred_name = current.description() if current is not None else None
        self.cameras = available_cameras()
        self._populate_camera_combo(preferred_name)
        self.vcam_button.setEnabled(bool(self.cameras))

        if not self.cameras:
            if self.source is not None:
                self.source.stop()
            self._show_no_camera()
            return

        self.header_status.setText("카메라 목록 새로고침 완료")
        if self.source is None:
            self._start_camera()
        else:
            self.source.start(self.camera_combo.currentData())

    def _show_no_camera(self):
        self.source_size = (0, 0)
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.setEnabled(False)
        self.vcam_button.blockSignals(False)
        self.preview.setPixmap(QPixmap())
        self.preview.setText(
            "사용 가능한 카메라를 찾지 못했습니다.\n"
            "시스템 설정 > 개인정보 보호 및 보안 > 카메라 권한을 확인하세요."
        )
        self.header_status.setText("카메라 없음")
        self._set_badge(self.source_badge, "카메라 없음", "bad")
        self._set_badge(self.output_badge, "송출 불가", "bad")

    def _on_camera_changed(self, *_args):
        device = self.camera_combo.currentData()
        if self.source is not None and device is not None:
            self.header_status.setText(f"{device.description()} 여는 중")
            self.source.start(device)

    def _on_opened(self, width, height):
        self.source_size = (width, height)
        device = self.camera_combo.currentData()
        name = device.description() if device is not None else "카메라"
        self.header_status.setText(f"{name} 연결됨")

    def _on_error(self, message):
        self.header_status.setText("카메라 오류")
        self._set_badge(self.source_badge, "카메라 오류", "bad")
        self.status.showMessage(message, 5000)

    # ---------- 컨트롤 ----------

    def _on_preset_changed(self, level):
        self.current_preset = level
        self.reducer.apply_preset(level)
        self.preset_label.setText(PRESET_LABELS[level])
        self._update_status()

    def _on_enabled_toggled(self, checked):
        self.reducer.enabled = checked
        self.toggle_button.setText("효과 끄기" if checked else "효과 켜기")
        self._update_status()

    def _on_vcam_toggled(self, checked):
        if checked:
            self.vcam_button.setText("가상 카메라 중지")
            self._set_badge(self.output_badge, "송출 준비 중", "warn")
            # 실제 연결은 첫 프레임을 보낼 때 이루어진다.
        else:
            self.vcam.close()
            self.vcam_button.setText("가상 카메라 시작")
            self._set_badge(self.output_badge, "송출 대기", "muted")

    def _stop_vcam_with_error(self, message):
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.blockSignals(False)
        self._set_badge(self.output_badge, "송출 오류", "bad")
        QMessageBox.warning(self, "가상 카메라를 열 수 없습니다", message)

    # ---------- 프레임 ----------

    def _on_frame(self, frame):
        out = self.reducer.process(frame)

        if self.vcam_button.isChecked():
            # 좌우 반전 전의 화면을 보낸다. 반전된 걸 보내면 상대방에게
            # 글자가 뒤집혀 보인다.
            try:
                self.vcam.send(out)
            except VirtualCameraError as exc:
                self._stop_vcam_with_error(str(exc))

        # 미리보기만 거울 모드로 둔다. 자기 모습 확인엔 그게 자연스럽다.
        pixmap = to_pixmap(cv2.flip(out, 1))
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

        self._frame_times.append(time.monotonic())

    def _update_status(self):
        now = time.monotonic()
        self._frame_times = [t for t in self._frame_times if now - t < 2.0]
        fps = len(self._frame_times) / 2.0

        w, h = self.source_size
        if not w:
            return
        ew, eh = self.reducer.effective_resolution(w, h)
        parts = [f"원본 {w}x{h}"]
        if self.reducer.enabled:
            parts.append(f"실제 전달 {ew}x{eh}")
            preset_name = PRESET_LABELS[self.current_preset].split(" - ", 1)[1]
            self._set_badge(self.effect_badge, f"효과 {preset_name}", "info")
        else:
            parts.append("효과 꺼짐")
            self._set_badge(self.effect_badge, "효과 꺼짐", "muted")
        parts.append(f"{fps:.0f} fps")
        if self.vcam.running:
            parts.append(f"송출 중 → {self.vcam.device_name}")
            self._set_badge(self.output_badge, f"{self.vcam.device_name} 송출 중", "good")
        elif self.vcam_button.isChecked():
            self._set_badge(self.output_badge, "송출 준비 중", "warn")
        else:
            self._set_badge(self.output_badge, "송출 대기", "muted")
        self._set_badge(self.source_badge, f"{w}x{h} · {fps:.0f} fps", "good")
        self.status.showMessage("  |  ".join(parts))

    def closeEvent(self, event):
        if self.source is not None:
            self.source.stop()
        self.vcam.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(900, 560)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
