"""가상 카메라 출력.

처리된 프레임을 가상 카메라로 내보내 Zoom 등에서 선택할 수 있게 한다.
출력 계층은 플랫폼마다 다르므로 여기서 격리해 둔다. macOS는 직접 만든
Camera Extension(camsink)을 쓰고, Windows를 붙일 때도 이 파일만 바꾸면 된다.
"""

from camsink import SETUP_GUIDE, CamsinkError, VirtualCamera as _Camsink

__all__ = ["VirtualCamera", "VirtualCameraError", "SETUP_GUIDE"]


class VirtualCameraError(RuntimeError):
    pass


class VirtualCamera:
    """프레임을 가상 카메라로 송출한다."""

    def __init__(self):
        self._cam = None

    @property
    def running(self):
        return self._cam is not None and self._cam.running

    @property
    def device_name(self):
        return self._cam.camera_name if self._cam else None

    def send(self, frame_bgr):
        """BGR 프레임을 송출한다. 필요하면 장치를 연다."""
        try:
            if self._cam is None:
                self._cam = _Camsink()
                self._cam.start()
            self._cam.send(frame_bgr)
        except CamsinkError as exc:
            self.close()
            raise VirtualCameraError(str(exc)) from exc

    def close(self):
        if self._cam is not None:
            self._cam.close()
            self._cam = None
