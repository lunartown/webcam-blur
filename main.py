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

import cv2
import numpy as np

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


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        raise SystemExit(
            "웹캠을 열 수 없습니다. macOS라면 터미널 앱에 카메라 권한이 있는지 확인하세요.\n"
            "시스템 설정 > 개인정보 보호 및 보안 > 카메라"
        )

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    reducer = QualityReducer(preset=3)
    compare = False
    window = "webcam-blur (q to quit)"

    while True:
        ok, frame = cap.read()
        if not ok:
            print("프레임을 읽지 못했습니다.")
            break

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
