"""가상 카메라 출력.

처리된 프레임을 가상 카메라로 내보내 Zoom 등에서 선택할 수 있게 한다.
macOS에서는 pyvirtualcam이 OBS의 가상 카메라를 빌려 쓰므로, OBS 설치와
최초 1회 활성화가 필요하다.
"""

import cv2

SETUP_GUIDE = (
    "가상 카메라를 쓰려면 최초 1회 설정이 필요합니다.\n\n"
    "1. OBS를 실행합니다\n"
    "2. 오른쪽 아래 '가상 카메라 시작'을 누릅니다\n"
    "3. 시스템 설정에서 확장 프로그램을 허용합니다\n"
    "4. '가상 카메라 중지'를 누르고 OBS를 종료합니다\n\n"
    "이후로는 OBS를 띄우지 않아도 됩니다."
)


class VirtualCameraError(RuntimeError):
    pass


class VirtualCamera:
    """프레임을 가상 카메라로 송출한다. 해상도가 바뀌면 알아서 다시 연다."""

    def __init__(self, fps=30):
        self.fps = fps
        self._cam = None
        self._size = None

    @property
    def running(self):
        return self._cam is not None

    @property
    def device_name(self):
        return self._cam.device if self._cam else None

    def send(self, frame_bgr):
        """BGR 프레임을 송출한다. 필요하면 장치를 연다."""
        h, w = frame_bgr.shape[:2]
        if self._cam is None or self._size != (w, h):
            self._open(w, h)
        # pyvirtualcam은 기본적으로 RGB를 받는다.
        self._cam.send(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

    def _open(self, width, height):
        self.close()
        try:
            import pyvirtualcam
        except ImportError as exc:
            raise VirtualCameraError(f"pyvirtualcam을 불러올 수 없습니다: {exc}") from exc

        try:
            self._cam = pyvirtualcam.Camera(width=width, height=height, fps=self.fps)
        except Exception as exc:
            # 대부분 OBS 가상 카메라가 아직 활성화되지 않은 경우다.
            raise VirtualCameraError(f"{exc}\n\n{SETUP_GUIDE}") from exc
        self._size = (width, height)

    def close(self):
        if self._cam is not None:
            self._cam.close()
            self._cam = None
            self._size = None
