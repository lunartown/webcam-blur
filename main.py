"""웹캠 화질 저하 프로토타입.

원본 웹캠 영상을 받아 해상도를 떨어뜨려 사생활 노출을 줄인다.
미리보기 창으로 결과만 확인하는 단계이며, 가상카메라 출력은 아직 없다.

키 조작:
    1~5     프리셋 단계 (1=거의 원본, 5=매우 흐림)
    [ / ]   다운스케일 배율 미세 조정
    n       리샘플 방식 전환 (부드럽게 <-> 모자이크)
    b       추가 블러 on/off
    j       JPEG 압축 아티팩트 on/off
    c       원본/처리본 나란히 보기
    space   효과 전체 on/off
    q, ESC  종료
"""

import argparse
import time

import cv2
import numpy as np

# 카메라가 첫 프레임을 내보내기까지 몇 번의 시도가 필요할 수 있다.
WARMUP_ATTEMPTS = 20
WARMUP_DELAY = 0.1
# 실행 중 일시적인 읽기 실패는 이 횟수까지 넘긴다.
MAX_CONSECUTIVE_FAILURES = 30

# 프리셋: (다운스케일 배율, 블러 커널, JPEG 품질)
# 배율이 작을수록 해상도를 더 떨어뜨린다.
PRESETS = {
    1: (0.50, 0, 90),
    2: (0.30, 0, 70),
    3: (0.18, 3, 50),
    4: (0.10, 5, 35),
    5: (0.05, 7, 20),
}

PRESET_KEYS = {ord(str(n)) for n in PRESETS}

MIN_SCALE = 0.02
MAX_SCALE = 1.0


class QualityReducer:
    """프레임 단위로 화질을 떨어뜨린다."""

    def __init__(self, preset=3):
        self.scale = 0.18
        self.blur_kernel = 3
        self.jpeg_quality = 50
        self.smooth = True      # True면 부드럽게, False면 모자이크
        self.use_blur = True
        self.use_jpeg = False
        self.enabled = True
        self.apply_preset(preset)

    def apply_preset(self, level):
        if level not in PRESETS:
            return
        self.scale, self.blur_kernel, self.jpeg_quality = PRESETS[level]
        self.use_blur = self.blur_kernel > 0

    def adjust_scale(self, delta):
        self.scale = float(np.clip(self.scale + delta, MIN_SCALE, MAX_SCALE))

    def process(self, frame):
        if not self.enabled:
            return frame

        h, w = frame.shape[:2]
        small_w = max(2, int(w * self.scale))
        small_h = max(2, int(h * self.scale))

        # 핵심: 축소했다가 원래 크기로 되돌리면 잃어버린 디테일은 복구되지 않는다.
        down = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
        up_interp = cv2.INTER_LINEAR if self.smooth else cv2.INTER_NEAREST
        out = cv2.resize(down, (w, h), interpolation=up_interp)

        if self.use_blur and self.blur_kernel > 0:
            k = self.blur_kernel * 2 + 1  # 가우시안 커널은 홀수여야 한다
            out = cv2.GaussianBlur(out, (k, k), 0)

        if self.use_jpeg:
            # 저품질 JPEG로 재인코딩해 블록 아티팩트를 입힌다.
            ok, buf = cv2.imencode(
                ".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            if ok:
                out = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        return out

    def status_line(self):
        if not self.enabled:
            return "OFF (space to enable)"
        mode = "smooth" if self.smooth else "mosaic"
        parts = [
            f"scale {self.scale:.2f}",
            f"~{mode}",
            f"blur {'on' if self.use_blur else 'off'}",
            f"jpeg {'q' + str(self.jpeg_quality) if self.use_jpeg else 'off'}",
        ]
        return " | ".join(parts)


def draw_status(frame, text):
    """좌하단에 상태 문구를 읽기 쉽게 얹는다."""
    h = frame.shape[0]
    org = (12, h - 14)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return frame


def open_camera(index, width=1280, height=720):
    """카메라를 열고 첫 프레임이 나올 때까지 기다린다.

    맥에서는 장치가 준비되기 전에 read()가 몇 번 실패하는 일이 흔하므로
    곧바로 포기하지 않는다.
    """
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        raise SystemExit(
            f"카메라 {index}번을 열 수 없습니다. --camera 로 다른 번호를 시도해보세요.\n"
            "권한 문제라면: 시스템 설정 > 개인정보 보호 및 보안 > 카메라"
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    for _ in range(WARMUP_ATTEMPTS):
        ok, frame = cap.read()
        if ok and frame is not None:
            h, w = frame.shape[:2]
            print(f"카메라 {index}번 연결됨 ({w}x{h})")
            return cap
        time.sleep(WARMUP_DELAY)

    cap.release()
    raise SystemExit(
        f"카메라 {index}번은 열렸지만 프레임이 오지 않습니다.\n"
        "다른 앱이 카메라를 쓰고 있거나, 연속성 카메라(iPhone)가 준비되지 않았을 수 있습니다.\n"
        "--camera 로 다른 번호를 시도해보세요."
    )


def main():
    parser = argparse.ArgumentParser(description="웹캠 화질 저하 미리보기")
    parser.add_argument("--camera", type=int, default=0, help="카메라 인덱스 (기본 0)")
    parser.add_argument("--preset", type=int, default=3, choices=sorted(PRESETS), help="시작 강도")
    args = parser.parse_args()

    cap = open_camera(args.camera)

    reducer = QualityReducer(preset=args.preset)
    compare = False
    window = "webcam-blur (q to quit)"

    failures = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            # 간헐적인 실패는 흔하므로 한 번 놓쳤다고 종료하지 않는다.
            failures += 1
            if failures > MAX_CONSECUTIVE_FAILURES:
                print("카메라에서 프레임이 계속 오지 않아 종료합니다.")
                break
            time.sleep(0.02)
            continue
        failures = 0

        frame = cv2.flip(frame, 1)  # 거울 모드가 자기 모습 확인엔 자연스럽다
        out = reducer.process(frame)

        if compare:
            view = np.hstack([frame, out])
            view = cv2.resize(view, None, fx=0.6, fy=0.6, interpolation=cv2.INTER_AREA)
        else:
            view = out

        draw_status(view, reducer.status_line())
        cv2.imshow(window, view)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        elif key in PRESET_KEYS:
            reducer.apply_preset(int(chr(key)))
        elif key == ord("["):
            reducer.adjust_scale(-0.02)
        elif key == ord("]"):
            reducer.adjust_scale(+0.02)
        elif key == ord("n"):
            reducer.smooth = not reducer.smooth
        elif key == ord("b"):
            reducer.use_blur = not reducer.use_blur
        elif key == ord("j"):
            reducer.use_jpeg = not reducer.use_jpeg
        elif key == ord("c"):
            compare = not compare
        elif key == ord(" "):
            reducer.enabled = not reducer.enabled

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
