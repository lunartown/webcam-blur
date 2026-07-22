"""카메라 탐색과 캡처 스레드."""

import time

import cv2
from PySide6.QtCore import QThread, Signal

# 카메라가 첫 프레임을 내보내기까지 몇 번의 시도가 필요할 수 있다.
WARMUP_ATTEMPTS = 20
WARMUP_DELAY = 0.1
# 실행 중 일시적인 읽기 실패는 이 횟수까지 넘긴다.
MAX_CONSECUTIVE_FAILURES = 30

MAX_PROBE_INDEX = 5

RESOLUTIONS = [
    (640, 480),
    (1280, 720),
    (1920, 1080),
]


def device_names():
    """Qt가 인식한 카메라 이름 목록.

    OpenCV는 장치 이름을 알려주지 않으므로 Qt 쪽에서 가져온다. 두 라이브러리가
    같은 순서로 장치를 열거한다는 보장은 없어서, 개수가 맞을 때만 이름을 쓴다.
    """
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return [d.description() for d in QMediaDevices.videoInputs()]
    except Exception:
        return []


def available_cameras():
    """카메라 목록을 [(인덱스, 표시이름)]로 돌려준다.

    장치를 하나하나 열어보며 확인하면 정확하지만, 맥에서는 카메라 하나를
    여는 데만 몇 초가 걸려 창이 뜨기 전에 오래 멈춘다. 그래서 Qt에 목록만
    물어보고, 실제 열기는 사용자가 고른 것 하나만 시도한다.
    """
    names = device_names()
    if names:
        # Qt와 OpenCV가 같은 순서로 장치를 열거한다고 보고 위치로 대응시킨다.
        # 어긋나면 선택한 카메라가 열리지 않고, 그때 이전 카메라로 되돌아간다.
        return list(enumerate(names))

    # Qt가 아무것도 못 찾은 경우에만 직접 열어보며 찾는다.
    found = []
    for index in range(MAX_PROBE_INDEX):
        cap = cv2.VideoCapture(index)
        opened = cap.isOpened()
        cap.release()
        if opened:
            found.append((index, f"카메라 {index}"))
    return found


def _warmup(cap):
    """첫 프레임이 나올 때까지 기다린다.

    맥에서는 장치가 준비되기 전에 read()가 몇 번 실패하는 일이 흔하므로
    곧바로 포기하지 않는다.
    """
    for _ in range(WARMUP_ATTEMPTS):
        ok, frame = cap.read()
        if ok and frame is not None:
            return True, frame
        time.sleep(WARMUP_DELAY)
    return False, None


class CameraThread(QThread):
    """카메라를 계속 읽어 프레임을 내보낸다. GUI를 막지 않도록 별도 스레드."""

    frame_ready = Signal(object)     # numpy BGR 프레임
    error = Signal(str)
    opened = Signal(int, int)        # 실제 적용된 width, height
    reverted = Signal(int)           # 전환 실패로 되돌아간 카메라 인덱스

    def __init__(self, index=0, resolution=(1280, 720), parent=None):
        super().__init__(parent)
        self._index = index
        self._resolution = resolution
        self._running = False
        self._reopen = True          # 첫 진입 시 한 번 연다
        self._pending = False        # GUI가 아직 소화하지 못한 프레임이 있는지

    def frame_consumed(self):
        """GUI가 프레임 처리를 끝냈음을 알린다."""
        self._pending = False

    def set_camera(self, index):
        self._index = index
        self._reopen = True

    def set_resolution(self, resolution):
        self._resolution = resolution
        self._reopen = True

    def stop(self):
        self._running = False
        self.wait(2000)

    def run(self):
        self._running = True
        cap = None
        failures = 0
        last_good = None

        while self._running:
            # cap이 None인 경우도 함께 처리한다. 열기에 실패한 뒤 그대로
            # read()를 호출하면 스레드가 죽어버린다.
            if self._reopen or cap is None:
                self._reopen = False
                if cap is not None:
                    cap.release()
                    cap = None
                cap = self._open()
                if cap is None:
                    # _open이 이미 사유를 보고했다.
                    if last_good is not None and self._index != last_good:
                        # 마지막으로 잘 되던 카메라로 되돌린다.
                        self._index = last_good
                        self.reverted.emit(last_good)
                    else:
                        self.msleep(500)
                    continue
                last_good = self._index
                failures = 0
                self._pending = False   # 재오픈 시 대기 상태가 남지 않게

            ok, frame = cap.read()
            if not ok or frame is None:
                # 간헐적인 실패는 흔하므로 한 번 놓쳤다고 종료하지 않는다.
                failures += 1
                if failures > MAX_CONSECUTIVE_FAILURES:
                    self.error.emit("카메라에서 프레임이 계속 오지 않습니다.")
                    self.msleep(500)
                    failures = 0
                self.msleep(20)
                continue

            failures = 0
            if self._pending:
                # GUI가 앞 프레임을 아직 처리 중이면 이번 것은 버린다.
                # 그러지 않으면 큐가 쌓여 지연이 계속 늘어난다.
                continue
            self._pending = True
            self.frame_ready.emit(frame)

        if cap is not None:
            cap.release()

    def _open(self):
        cap = cv2.VideoCapture(self._index)
        if not cap.isOpened():
            cap.release()
            self.error.emit(f"카메라 {self._index}번을 열 수 없습니다.")
            return None

        width, height = self._resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        ok, frame = _warmup(cap)
        if not ok:
            cap.release()
            self.error.emit(
                f"카메라 {self._index}번은 열렸지만 프레임이 오지 않습니다. "
                "다른 앱이 쓰고 있는지 확인하세요."
            )
            return None

        # 요청한 해상도가 그대로 적용되지 않는 카메라가 많다.
        h, w = frame.shape[:2]
        self.opened.emit(w, h)
        return cap
