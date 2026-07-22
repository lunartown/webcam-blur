"""웹캠 화질 저하 GUI.

회의 직전에 여는 도구라 조작은 카메라, 흐림 정도, 효과 On/Off, 가상 카메라
송출만 둔다. UI는 macOS 유틸리티 앱처럼 조용하게 유지한다.
"""

import os
import sys
import threading
import time
import webbrowser

import cv2
from PySide6.QtCore import QCameraPermission, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
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

APP_STYLE = """
QWidget#root {
    background: #f5f5f7;
}

QLabel#title {
    color: #1d1d1f;
    font-size: 19px;
    font-weight: 600;
}

QLabel#subtitle,
QLabel#caption,
QLabel#fieldLabel {
    color: #6e6e73;
}

QLabel#fieldLabel {
    font-size: 12px;
}

QFrame#previewCard {
    background: #101010;
    border: 1px solid #d1d1d6;
    border-radius: 12px;
}

QLabel#preview {
    background: #101010;
    color: #a1a1a6;
    border-radius: 11px;
}

QFrame#controlsCard {
    background: #ffffff;
    border: 1px solid #d7d7dc;
    border-radius: 12px;
}

QLabel#statusLine {
    color: #3a3a3c;
    font-size: 12px;
}

QLabel#statusMuted {
    color: #8a8a8e;
    font-size: 12px;
}

QPushButton#primaryButton {
    min-width: 120px;
    background: #1d1d1f;
    border: 1px solid #1d1d1f;
    border-radius: 6px;
    color: #ffffff;
    padding: 4px 12px;
}

QPushButton#primaryButton:checked {
    background: #ffffff;
    border-color: #b9b9bf;
    color: #1d1d1f;
}

QStatusBar {
    color: #6e6e73;
}
"""


def to_pixmap(frame):
    """OpenCV BGR 프레임을 QPixmap으로."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w, _ = rgb.shape
    # QImage는 버퍼를 복사하지 않으므로, numpy 배열이 사라지기 전에 복사해 둔다.
    image = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


class PreviewLabel(QLabel):
    """원본 프레임을 보관해 리사이즈 때도 프리뷰가 빈 화면이 되지 않게 한다."""

    def __init__(self, text):
        super().__init__(text)
        self._source_pixmap = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(*PREVIEW_MIN_SIZE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setObjectName("preview")

    def set_frame_pixmap(self, pixmap):
        self._source_pixmap = pixmap
        self.setText("")
        self._update_scaled_pixmap()

    def clear_frame(self, text):
        self._source_pixmap = None
        self.clear()
        self.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scaled_pixmap()

    def _update_scaled_pixmap(self):
        if self._source_pixmap is None or self._source_pixmap.isNull():
            return
        self.setPixmap(
            self._source_pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )


class MainWindow(QMainWindow):
    update_checked = Signal(object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("webcam-blur")
        self.update_checked.connect(self._on_update_checked)

        self.reducer = QualityReducer(preset=3)
        self.current_preset = 3
        self.vcam = VirtualCamera()
        self.cameras = []
        self.source = None
        self.source_size = (0, 0)
        self._frame_times = []
        self._last_frame_at = None
        self._camera_permission = QCameraPermission()
        self._permission_request_pending = False

        self._build_ui()
        QTimer.singleShot(0, self._ensure_camera_permission)

    # ---------- UI 구성 ----------

    def _build_ui(self):
        self.setMinimumSize(860, 580)
        self.setStyleSheet(APP_STYLE)

        layout = QVBoxLayout()
        layout.setContentsMargins(18, 16, 18, 8)
        layout.setSpacing(12)
        layout.addLayout(self._build_header())
        layout.addWidget(self._build_preview_card(), stretch=1)
        layout.addWidget(self._build_controls_card())

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
        row.setSpacing(10)

        title_col = QVBoxLayout()
        title_col.setSpacing(1)
        title = QLabel("webcam-blur")
        title.setObjectName("title")
        subtitle = QLabel(f"v{APP_VERSION} · 프라이빗 가상 카메라")
        subtitle.setObjectName("subtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        row.addLayout(title_col, stretch=1)

        self.update_button = QPushButton("업데이트 확인")
        self.update_button.clicked.connect(self._check_for_updates)
        row.addWidget(self.update_button)

        self.refresh_button = QPushButton("카메라 새로고침")
        self.refresh_button.clicked.connect(self._refresh_cameras)
        row.addWidget(self.refresh_button)
        return row

    def _build_preview_card(self):
        self.preview = PreviewLabel("카메라를 준비하는 중...")

        layout = QVBoxLayout()
        layout.setContentsMargins(1, 1, 1, 1)
        layout.addWidget(self.preview)

        frame = QFrame()
        frame.setObjectName("previewCard")
        frame.setLayout(layout)
        return frame

    def _build_controls_card(self):
        grid = QGridLayout()
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.input_status = QLabel("입력 대기")
        self.input_status.setObjectName("statusLine")
        self.output_status = QLabel("송출 대기")
        self.output_status.setObjectName("statusMuted")
        grid.addWidget(self.input_status, 0, 0, 1, 3)
        grid.addWidget(self.output_status, 0, 2, 1, 2, Qt.AlignRight)

        grid.addWidget(self._field_label("카메라"), 1, 0)
        self.camera_combo = QComboBox()
        self.camera_combo.currentIndexChanged.connect(self._on_camera_changed)
        self._populate_camera_combo()
        grid.addWidget(self.camera_combo, 1, 1, 1, 3)

        grid.addWidget(self._field_label("흐림 정도"), 2, 0)
        self.preset_slider = QSlider(Qt.Horizontal)
        self.preset_slider.setRange(min(PRESETS), max(PRESETS))
        self.preset_slider.setValue(self.current_preset)
        self.preset_slider.setTickPosition(QSlider.TicksBelow)
        self.preset_slider.setTickInterval(1)
        self.preset_slider.setPageStep(1)
        self.preset_slider.valueChanged.connect(self._on_preset_changed)
        grid.addWidget(self.preset_slider, 2, 1)

        self.preset_label = QLabel(PRESET_LABELS[self.current_preset])
        self.preset_label.setObjectName("caption")
        self.preset_label.setMinimumWidth(94)
        grid.addWidget(self.preset_label, 2, 2)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.toggle_button = QPushButton("효과 끄기")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.toggled.connect(self._on_enabled_toggled)
        actions.addWidget(self.toggle_button)

        self.vcam_button = QPushButton("가상 카메라 시작")
        self.vcam_button.setObjectName("primaryButton")
        self.vcam_button.setCheckable(True)
        self.vcam_button.setEnabled(bool(self.cameras))
        self.vcam_button.toggled.connect(self._on_vcam_toggled)
        actions.addWidget(self.vcam_button)
        grid.addLayout(actions, 2, 3)

        grid.setColumnStretch(1, 1)

        frame = QFrame()
        frame.setObjectName("controlsCard")
        frame.setLayout(grid)
        return frame

    def _field_label(self, text):
        label = QLabel(text)
        label.setObjectName("fieldLabel")
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
            self.camera_combo.addItem("카메라 준비 중", None)
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

    def _camera_permission_status(self):
        app = QApplication.instance()
        if app is None:
            return Qt.PermissionStatus.Denied
        return app.checkPermission(self._camera_permission)

    def _ensure_camera_permission(self):
        status = self._camera_permission_status()
        if status == Qt.PermissionStatus.Granted:
            self._permission_request_pending = False
            self._load_cameras_and_start()
            return

        self._stop_camera_source()
        self._show_camera_permission_pending()
        if self._permission_request_pending:
            return

        self._permission_request_pending = True
        QApplication.instance().requestPermission(
            self._camera_permission,
            self,
            self._on_camera_permission_result,
        )

    def _on_camera_permission_result(self, _permission):
        self._permission_request_pending = False
        if self._camera_permission_status() == Qt.PermissionStatus.Granted:
            self._load_cameras_and_start()
        else:
            self._show_camera_permission_denied()

    def _load_cameras_and_start(self, preferred_name=None):
        self.cameras = available_cameras()
        self._populate_camera_combo(preferred_name)
        self.vcam_button.setEnabled(bool(self.cameras))
        if self.cameras:
            self._start_camera()
        else:
            self._show_no_camera()

    def _start_camera(self):
        if not self.cameras:
            self._show_no_camera()
            return

        self._stop_camera_source()
        self.source = CameraSource(self)
        self.source.frame_ready.connect(self._on_frame)
        self.source.error.connect(self._on_error)
        self.source.opened.connect(self._on_opened)
        self.source_size = (0, 0)
        self._last_frame_at = None
        self.input_status.setText("카메라 여는 중")
        self.preview.clear_frame("카메라를 여는 중...")
        self.source.start(self.camera_combo.currentData())
        self._schedule_camera_watchdog()

    def _refresh_cameras(self):
        if self._camera_permission_status() != Qt.PermissionStatus.Granted:
            self._ensure_camera_permission()
            return

        current = self.camera_combo.currentData()
        preferred_name = current.description() if current is not None else None
        self.status.showMessage("카메라 목록 새로고침 완료", 3000)
        self._load_cameras_and_start(preferred_name)

    def _schedule_camera_watchdog(self):
        QTimer.singleShot(3000, self._check_camera_watchdog)

    def _check_camera_watchdog(self):
        if (
            self.source is None
            or self.source_size != (0, 0)
            or self._last_frame_at is not None
        ):
            return
        device = self.camera_combo.currentData()
        name = device.description() if device is not None else "카메라"
        self.input_status.setText(f"{name} 프레임 대기 중")
        self.status.showMessage(
            "카메라 화면이 없으면 macOS 카메라 권한이나 다른 앱의 사용 여부를 확인하세요.",
            6000,
        )

    def _stop_camera_source(self):
        if self.source is not None:
            self.source.stop()
            self.source = None
        self.source_size = (0, 0)
        self._last_frame_at = None

    def _show_camera_permission_pending(self):
        self._stop_camera_source()
        self.vcam.close()
        self.vcam_button.setEnabled(False)
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItem("카메라 권한 확인 중", None)
        self.camera_combo.setEnabled(False)
        self.camera_combo.blockSignals(False)
        self.preview.clear_frame("macOS 카메라 권한을 요청하는 중...")
        self.input_status.setText("카메라 권한 확인 중")
        self.output_status.setText("송출 대기")
        self.status.showMessage("카메라 권한 확인 중")

    def _show_camera_permission_denied(self):
        self._stop_camera_source()
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.setEnabled(False)
        self.vcam_button.blockSignals(False)
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItem("카메라 권한 필요", None)
        self.camera_combo.setEnabled(False)
        self.camera_combo.blockSignals(False)
        self.preview.clear_frame(
            "카메라 권한이 꺼져 있습니다.\n"
            "시스템 설정 > 개인정보 보호 및 보안 > 카메라에서 webcam-blur를 켜세요."
        )
        self.input_status.setText("카메라 권한 필요")
        self.output_status.setText("송출 불가")
        self.status.showMessage("카메라 권한 필요")

    def _show_no_camera(self):
        self.source_size = (0, 0)
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.setEnabled(False)
        self.vcam_button.blockSignals(False)
        self.preview.clear_frame(
            "사용 가능한 카메라를 찾지 못했습니다.\n"
            "시스템 설정 > 개인정보 보호 및 보안 > 카메라 권한을 확인하세요."
        )
        self.input_status.setText("카메라 없음")
        self.output_status.setText("송출 불가")
        self.status.showMessage("카메라 없음")

    def _on_camera_changed(self, *_args):
        device = self.camera_combo.currentData()
        if self.source is not None and device is not None:
            self.source_size = (0, 0)
            self._last_frame_at = None
            self.input_status.setText("카메라 여는 중")
            self.preview.clear_frame("카메라를 여는 중...")
            self.status.showMessage(f"{device.description()} 여는 중")
            self.source.start(device)
            self._schedule_camera_watchdog()

    def _on_opened(self, width, height):
        self.source_size = (width, height)
        device = self.camera_combo.currentData()
        name = device.description() if device is not None else "카메라"
        self.input_status.setText(f"{name} · {width}x{height}")

    def _on_error(self, message):
        self.input_status.setText("카메라 오류")
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
            self.output_status.setText("송출 준비 중")
            # 실제 연결은 첫 프레임을 보낼 때 이루어진다.
        else:
            self.vcam.close()
            self.vcam_button.setText("가상 카메라 시작")
            self.output_status.setText("송출 대기")
        self._update_status()

    def _stop_vcam_with_error(self, message):
        self.vcam.close()
        self.vcam_button.blockSignals(True)
        self.vcam_button.setChecked(False)
        self.vcam_button.setText("가상 카메라 시작")
        self.vcam_button.blockSignals(False)
        self.output_status.setText("송출 오류")
        QMessageBox.warning(self, "가상 카메라를 열 수 없습니다", message)

    # ---------- 프레임 ----------

    def _on_frame(self, frame):
        if frame is None or frame.size == 0:
            return

        self._last_frame_at = time.monotonic()
        out = self.reducer.process(frame)
        if out is None or out.size == 0:
            return

        if self.vcam_button.isChecked():
            # 좌우 반전 전의 화면을 보낸다. 반전된 걸 보내면 상대방에게
            # 글자가 뒤집혀 보인다.
            try:
                self.vcam.send(out)
            except VirtualCameraError as exc:
                self._stop_vcam_with_error(str(exc))

        # 미리보기만 거울 모드로 둔다. 자기 모습 확인엔 그게 자연스럽다.
        self.preview.set_frame_pixmap(to_pixmap(cv2.flip(out, 1)))
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
            self.output_status.setText(f"{self.vcam.device_name} 송출 중")
        elif self.vcam_button.isChecked():
            parts.append("송출 준비 중")
            self.output_status.setText("송출 준비 중")
        else:
            self.output_status.setText("송출 대기")

        self.status.showMessage("  |  ".join(parts))

    def closeEvent(self, event):
        if self.source is not None:
            self.source.stop()
        self.vcam.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(920, 600)
    window.show()

    if os.environ.get("WEBCAM_BLUR_CAMERA_PROBE"):
        def report_probe():
            print(f"source_size={window.source_size}", flush=True)
            print(f"last_frame={window._last_frame_at is not None}", flush=True)
            print(f"input_status={window.input_status.text()}", flush=True)
            print(f"status={window.status.currentMessage()}", flush=True)
            window.close()

        QTimer.singleShot(4500, report_probe)
        QTimer.singleShot(4600, app.quit)

    if os.environ.get("WEBCAM_BLUR_SMOKE_TEST"):
        QTimer.singleShot(1500, window.close)
        QTimer.singleShot(1600, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
