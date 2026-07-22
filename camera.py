"""카메라 입력.

캡처를 Qt로 한다. OpenCV의 카메라 번호는 Qt가 보여주는 장치 목록의 순서와
일치하지 않아서, 이름만 Qt에서 가져다 쓰면 목록에 보이는 것과 다른 카메라가
열릴 수 있었다. 실제로 같은 장치가 Qt 0번, AVFoundation 2번, OpenCV 1번으로
제각각 나왔다.

Qt로 캡처하면 번호가 아니라 장치 객체를 직접 지정하므로 이 문제가 없어진다.
덤으로 워밍업 재시도나 별도 캡처 스레드도 필요 없어졌다. 프레임은 Qt가
알아서 비동기로 넘겨준다.
"""

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import (
    QCamera,
    QMediaCaptureSession,
    QMediaDevices,
    QVideoSink,
)

# 가상 카메라를 입력으로 고르면 자기 출력을 다시 읽어 피드백 루프가 생긴다.
VIRTUAL_CAMERA_NAMES = ("camsink", "obs virtual camera", "sample camera")

PREFERRED_RESOLUTION = (1280, 720)


def _is_virtual(name):
    return name.strip().lower() in VIRTUAL_CAMERA_NAMES


def available_cameras():
    """쓸 수 있는 카메라 장치 목록.

    QCameraDevice 를 그대로 돌려준다. 번호로 바꾸지 않는 것이 요점이다.
    """
    return [d for d in QMediaDevices.videoInputs() if not _is_virtual(d.description())]


def _best_format(device, target=PREFERRED_RESOLUTION):
    """원하는 해상도에 가장 가까운 형식을 고른다."""
    formats = device.videoFormats()
    if not formats:
        return None
    target_pixels = target[0] * target[1]

    def distance(fmt):
        size = fmt.resolution()
        return (
            abs(size.width() * size.height() - target_pixels),
            -fmt.maxFrameRate(),
        )

    return min(formats, key=distance)


def qimage_to_bgr(image):
    """QImage를 OpenCV가 쓰는 BGR numpy 배열로."""
    image = image.convertToFormat(QImage.Format.Format_BGR888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    buffer = np.frombuffer(image.constBits(), dtype=np.uint8, count=stride * height)
    # 행마다 여백이 붙을 수 있어 실제 폭만 잘라낸다.
    return buffer.reshape(height, stride // 3, 3)[:, :width, :]


class CameraSource(QObject):
    """선택한 카메라에서 프레임을 받아 BGR 배열로 내보낸다."""

    frame_ready = Signal(object)     # numpy BGR 프레임
    error = Signal(str)
    opened = Signal(int, int)        # 실제 적용된 width, height

    def __init__(self, parent=None):
        super().__init__(parent)
        self._camera = None
        self._size = None
        self._session = QMediaCaptureSession(self)
        self._sink = QVideoSink(self)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)

    @property
    def running(self):
        return self._camera is not None and self._camera.isActive()

    def start(self, device):
        """주어진 장치로 전환한다. 이미 다른 장치를 보고 있으면 갈아탄다."""
        self.stop()

        camera = QCamera(device, self)
        best = _best_format(device)
        if best is not None:
            camera.setCameraFormat(best)
        camera.errorOccurred.connect(
            lambda _err, message: self.error.emit(
                message or f"{device.description()} 을(를) 열 수 없습니다."))

        self._camera = camera
        self._size = None
        self._session.setCamera(camera)
        camera.start()

        if not camera.isActive():
            # 권한이 없거나 다른 앱이 쓰고 있으면 여기서 걸린다.
            self.error.emit(
                f"{device.description()} 을(를) 시작하지 못했습니다. "
                "카메라 권한이나 다른 앱의 사용 여부를 확인하세요.")

    def stop(self):
        if self._camera is not None:
            self._camera.stop()
            self._session.setCamera(None)
            self._camera.deleteLater()
            self._camera = None
        self._size = None

    def _on_frame(self, frame):
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return

        size = (image.width(), image.height())
        if size != self._size:
            self._size = size
            self.opened.emit(*size)

        self.frame_ready.emit(qimage_to_bgr(image))
