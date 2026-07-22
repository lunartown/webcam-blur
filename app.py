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
        self.vcam = VirtualCamera()
        self.cameras = available_cameras()
        self.source = None
        self.source_size = (0, 0)
        self._frame_times = []

        self._build_ui()
        self._start_camera()

    # ---------- UI 구성 ----------

    def _build_ui(self):
        self.preview = QLabel("카메라를 준비하는 중...")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(*PREVIEW_MIN_SIZE)
        self.preview.setStyleSheet("background: #1e1e1e; color: #999;")

        layout = QVBoxLayout()
        layout.addWidget(self.preview, stretch=1)
        layout.addLayout(self._build_controls())

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
        row = QHBoxLayout()

        self.camera_combo = QComboBox()
        for device in self.cameras:
            # 번호가 아니라 장치 자체를 들고 있는다.
            self.camera_combo.addItem(device.description(), device)
        if not self.cameras:
            self.camera_combo.addItem("카메라 없음", None)
            self.camera_combo.setEnabled(False)
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        row.addWidget(QLabel("카메라"))
        row.addWidget(self.camera_combo, stretch=1)

        row.addSpacing(16)

        self.preset_slider = QSlider(Qt.Horizontal)
        self.preset_slider.setRange(min(PRESETS), max(PRESETS))
        self.preset_slider.setValue(3)
        self.preset_slider.setTickPosition(QSlider.TicksBelow)
        self.preset_slider.setTickInterval(1)
        self.preset_slider.setPageStep(1)
        self.preset_slider.setFixedWidth(150)
        self.preset_slider.valueChanged.connect(self._on_preset_changed)
        self.preset_label = QLabel(PRESET_LABELS[3])
        self.preset_label.setFixedWidth(110)
        row.addWidget(QLabel("흐림 정도"))
        row.addWidget(self.preset_slider)
        row.addWidget(self.preset_label)

        row.addSpacing(16)

        self.toggle_button = QPushButton("효과 끄기")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.toggled.connect(self._on_enabled_toggled)
        row.addWidget(self.toggle_button)

        self.vcam_button = QPushButton("가상 카메라 시작")
        self.vcam_button.setCheckable(True)
        self.vcam_button.toggled.connect(self._on_vcam_toggled)
        row.addWidget(self.vcam_button)

        return row

    # ---------- 카메라 ----------

    def _start_camera(self):
        if not self.cameras:
            self.preview.setText(
                "사용 가능한 카메라를 찾지 못했습니다.\n"
                "시스템 설정 > 개인정보 보호 및 보안 > 카메라 권한을 확인하세요."
            )
            return

        self.source = CameraSource(self)
        self.source.frame_ready.connect(self._on_frame)
        self.source.error.connect(self._on_error)
        self.source.opened.connect(self._on_opened)
        self.source.start(self.camera_combo.currentData())

    def _on_camera_changed(self):
        device = self.camera_combo.currentData()
        if self.source is not None and device is not None:
            self.source.start(device)

    def _on_opened(self, width, height):
        self.source_size = (width, height)

    def _on_error(self, message):
        self.status.showMessage(message, 5000)

    # ---------- 컨트롤 ----------

    def _on_preset_changed(self, level):
        self.reducer.apply_preset(level)
        self.preset_label.setText(PRESET_LABELS[level])

    def _on_enabled_toggled(self, checked):
        self.reducer.enabled = checked
        self.toggle_button.setText("효과 끄기" if checked else "효과 켜기")

    def _on_vcam_toggled(self, checked):
        if checked:
            self.vcam_button.setText("가상 카메라 중지")
            # 실제 연결은 첫 프레임을 보낼 때 이루어진다.
        else:
            self.vcam.close()
            self.vcam_button.setText("가상 카메라 시작")

    def _stop_vcam_with_error(self, message):
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.blockSignals(False)
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
        else:
            parts.append("효과 꺼짐")
        parts.append(f"{fps:.0f} fps")
        if self.vcam.running:
            parts.append(f"송출 중 → {self.vcam.device_name}")
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
