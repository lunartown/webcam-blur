"""camsink - Python에서 macOS 가상 카메라로 프레임 보내기.

OBS 같은 다른 프로그램 없이, 직접 만든 Camera Extension으로 내보낸다.

    import cv2
    from camsink import VirtualCamera

    with VirtualCamera() as cam:
        cam.send(frame)   # OpenCV BGR 프레임

프레임은 작은 헬퍼 실행 파일(camsink-feed)을 거쳐 전달된다. CoreMediaIO의
sink stream이 C API로만 열리기 때문이다.

이 패키지는 나중에 별도 저장소로 떼어낼 예정이라 webcam-blur 쪽 코드에
의존하지 않는다.
"""

import subprocess
import threading
from pathlib import Path

import cv2

__all__ = ["VirtualCamera", "CamsinkError", "SETUP_GUIDE"]

# 익스텐션이 고정 해상도로 만들어져 있다. 다른 크기는 여기에 맞춰 리사이즈한다.
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
DEFAULT_CAMERA_NAME = "camsink"

SETUP_GUIDE = """camsink 가상 카메라를 찾을 수 없습니다.

최초 1회 설정이 필요합니다:
  1. CamsinkApp을 실행하고 activate를 누릅니다
  2. 시스템 설정 > 일반 > 로그인 항목 및 확장 > 카메라 확장 프로그램에서
     camsink을 켭니다

CamsinkApp이 없다면 native/macos/build.sh로 빌드해 설치하세요."""


class CamsinkError(RuntimeError):
    pass


def _find_helper():
    """camsink-feed 실행 파일을 찾는다."""
    here = Path(__file__).resolve().parent
    candidates = [
        here / "bin" / "camsink-feed",                       # 패키지에 동봉된 경우
        here.parent / "native/macos/bin/camsink-feed",       # 저장소에서 바로 실행
    ]
    for path in candidates:
        if path.exists():
            return path
    raise CamsinkError(
        "camsink-feed 헬퍼를 찾을 수 없습니다. "
        "native/macos/build-feed.sh로 빌드하세요."
    )


class VirtualCamera:
    """가상 카메라로 프레임을 내보낸다."""

    def __init__(self, camera_name=DEFAULT_CAMERA_NAME,
                 width=CAMERA_WIDTH, height=CAMERA_HEIGHT):
        self.camera_name = camera_name
        self.width = width
        self.height = height
        self._proc = None
        self._errors = []

    # ---------- 수명 관리 ----------

    def start(self):
        if self._proc is not None:
            return
        helper = _find_helper()
        self._proc = subprocess.Popen(
            [str(helper), "--camera-name", self.camera_name,
             "--width", str(self.width), "--height", str(self.height)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        # 헬퍼의 stderr를 계속 읽어둔다. 안 읽으면 파이프가 차서 멈출 수 있다.
        threading.Thread(target=self._drain_stderr, daemon=True).start()

        # 연결 실패는 곧바로 종료로 나타난다.
        try:
            self._proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            return  # 살아 있으면 정상
        raise CamsinkError(self._failure_message())

    def _drain_stderr(self):
        for line in self._proc.stderr:
            text = line.decode(errors="replace").strip()
            if text:
                self._errors.append(text)

    def _failure_message(self):
        detail = "\n".join(self._errors[-5:])
        if "찾지 못했습니다" in detail or not detail:
            return f"{detail}\n\n{SETUP_GUIDE}".strip()
        return detail

    def close(self):
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
            self._proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()
        finally:
            self._proc = None

    @property
    def running(self):
        return self._proc is not None and self._proc.poll() is None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------- 송출 ----------

    def send(self, frame_bgr):
        """OpenCV BGR 프레임 한 장을 내보낸다.

        크기가 다르면 카메라 해상도에 맞춰 리사이즈한다.
        """
        if self._proc is None:
            self.start()
        if self._proc.poll() is not None:
            raise CamsinkError(self._failure_message())

        h, w = frame_bgr.shape[:2]
        if (w, h) != (self.width, self.height):
            frame_bgr = cv2.resize(frame_bgr, (self.width, self.height),
                                   interpolation=cv2.INTER_AREA)

        bgra = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA)
        try:
            self._proc.stdin.write(bgra.tobytes())
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise CamsinkError(self._failure_message()) from exc
