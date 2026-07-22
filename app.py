"""웹캠 화질 저하 GUI.

회의 직전에 여는 도구라 조작은 카메라, 흐림 정도, 효과 On/Off, 가상 카메라
송출만 둔다. UI는 macOS 기본 위젯 톤을 유지한다.
"""

import os
import sys
import threading
import time
import webbrowser

import cv2
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from camera import CameraSource, available_cameras
from quality import PRESET_LABELS, PRESETS, QualityReducer
from updater import check_for_update
from vcam import VirtualCamera, VirtualCameraError
from version import APP_VERSION

PREVIEW_MIN_SIZE = (640, 360)


def to_pixmap(frame):
    """OpenCV BGR 프레임을 QPixmap으로."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    # QImage는 버퍼를 복사하지 않으므로, numpy 배열이 사라지기 전에 복사해 둔다.
    image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class MainWindow(QMainWindow):
    update_checked = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("webcam-blur")
        self.update_checked.connect(self._on_update_checked)

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
        self.setMinimumSize(820, 540)

        layout = QVBoxLayout()
        layout.setContentsMargins(14, 14, 14, 6)
        layout.setSpacing(10)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_preview(), stretch=1)
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

    def _build_header(self):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self.header_status = QLabel(f"webcam-blur {APP_VERSION}")
        row.addWidget(self.header_status, stretch=1)

        self.update_button = QPushButton("업데이트 확인")
        self.update_button.clicked.connect(self._check_for_updates)
        row.addWidget(self.update_button)

        self.refresh_button = QPushButton("카메라 새로고침")
        self.refresh_button.clicked.connect(self._refresh_cameras)
        row.addWidget(self.refresh_button)
        return row

    def _build_preview(self):
        self.preview = QLabel("카메라를 준비하는 중...")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(*PREVIEW_MIN_SIZE)
        self.preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.preview.setStyleSheet("background: black; color: #888;")
        return self.preview

    def _build_controls(self):
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.camera_combo = QComboBox()
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        self._populate_camera_combo()
        form.addRow("카메라", self.camera_combo)

        effect_row = QHBoxLayout()
        effect_row.setSpacing(8)

        self.preset_slider = QSlider(Qt.Horizontal)
        self.preset_slider.setRange(min(PRESETS), max(PRESETS))
        self.preset_slider.setValue(self.current_preset)
        self.preset_slider.setTickPosition(QSlider.TicksBelow)
        self.preset_slider.setTickInterval(1)
        self.preset_slider.setPageStep(1)
        self.preset_slider.valueChanged.connect(self._on_preset_changed)

        self.preset_label = QLabel(PRESET_LABELS[self.current_preset])
        self.preset_label.setFixedWidth(100)

        self.toggle_button = QPushButton("효과 끄기")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.toggled.connect(self._on_enabled_toggled)

        self.vcam_button = QPushButton("가상 카메라 시작")
        self.vcam_button.setCheckable(True)
        self.vcam_button.setEnabled(bool(self.cameras))
        self.vcam_button.toggled.connect(self._on_vcam_toggled)

        effect_row.addWidget(self.preset_slider, stretch=1)
        effect_row.addWidget(self.preset_label)
        effect_row.addWidget(self.toggle_button)
        effect_row.addWidget(self.vcam_button)
        form.addRow("흐림 정도", effect_row)
        return form

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

    # ---------- 업데이트 ----------

    def _check_for_updates(self):
        self.update_button.setEnabled(False)
        self.update_button.setText("확인 중")
        threading.Thread(target=self._check_for_updates_worker, daemon=True).start()

    def _check_for_updates_worker(self):
        try:
            result = check_for_update()
        except Exception as exc:
            result = exc
        self.update_checked.emit(result)

    def _on_update_checked(self, result):
        self.update_button.setEnabled(True)
        self.update_button.setText("업데이트 확인")

        if isinstance(result, Exception):
            QMessageBox.warning(self, "업데이트 확인 실패", str(result))
            return

        if result is None:
            QMessageBox.information(
                self,
                "최신 버전입니다",
                f"현재 {APP_VERSION} 버전이 설치되어 있습니다.",
            )
            return

        message = (
            f"새 버전 {result.version}을 사용할 수 있습니다.\n\n"
            "DMG를 내려받아 앱을 교체하세요."
        )
        box = QMessageBox(self)
        box.setWindowTitle("업데이트 사용 가능")
        box.setText(message)
        if result.body:
            box.setInformativeText(result.body[:1000])
        download = box.addButton("DMG 다운로드", QMessageBox.AcceptRole)
        box.addButton("나중에", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == download:
            webbrowser.open(result.url)

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

        self.status.showMessage("카메라 목록 새로고침 완료", 3000)
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
        self.status.showMessage("카메라 없음")

    def _on_camera_changed(self, *_args):
        device = self.camera_combo.currentData()
        if self.source is not None and device is not None:
            self.status.showMessage(f"{device.description()} 여는 중")
            self.source.start(device)

    def _on_opened(self, width, height):
        self.source_size = (width, height)

    def _on_error(self, message):
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
            # 실제 연결은 첫 프레임을 보낼 때 이루어진다.
        else:
            self.vcam.close()
            self.vcam_button.setText("가상 카메라 시작")
        self._update_status()

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
            parts.append(f"송출 중: {self.vcam.device_name}")
        elif self.vcam_button.isChecked():
            parts.append("송출 준비 중")
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
    if os.environ.get("WEBCAM_BLUR_SMOKE_TEST"):
        QTimer.singleShot(1500, window.close)
        QTimer.singleShot(1600, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
