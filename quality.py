"""화질 저하 처리.

원본 프레임의 해상도를 떨어뜨려 사생활 노출을 줄인다. GUI와 분리해
두어 나중에 배경 블러 등 다른 처리를 얹기 쉽게 했다.
"""

import cv2
import numpy as np

# 프리셋: (다운스케일 배율, 블러 세기, JPEG 품질)
# 배율이 작을수록 해상도를 더 떨어뜨린다.
PRESETS = {
    1: (0.50, 0, 90),
    2: (0.30, 0, 70),
    3: (0.18, 3, 50),
    4: (0.10, 5, 35),
    5: (0.05, 7, 20),
}

PRESET_LABELS = {
    1: "1 - 거의 원본",
    2: "2 - 약하게",
    3: "3 - 보통",
    4: "4 - 강하게",
    5: "5 - 매우 흐림",
}

MIN_SCALE = 0.02
MAX_SCALE = 1.0


class QualityReducer:
    """프레임 단위로 화질을 떨어뜨린다."""

    def __init__(self, preset=3):
        self.scale = 0.18
        self.blur_strength = 3
        self.jpeg_quality = 50
        self.smooth = True      # True면 부드럽게, False면 모자이크
        self.use_blur = True
        self.use_jpeg = False
        self.enabled = True
        self.apply_preset(preset)

    def apply_preset(self, level):
        if level not in PRESETS:
            return
        self.scale, self.blur_strength, self.jpeg_quality = PRESETS[level]
        self.use_blur = self.blur_strength > 0

    def process(self, frame):
        if frame is None or frame.size == 0:
            return None

        if not self.enabled:
            return frame

        h, w = frame.shape[:2]
        if h <= 0 or w <= 0:
            return None

        small_w = max(2, int(w * self.scale))
        small_h = max(2, int(h * self.scale))

        # 핵심: 축소했다가 원래 크기로 되돌리면 잃어버린 디테일은 복구되지 않는다.
        down = cv2.resize(frame, (small_w, small_h), interpolation=cv2.INTER_AREA)
        up_interp = cv2.INTER_LINEAR if self.smooth else cv2.INTER_NEAREST
        out = cv2.resize(down, (w, h), interpolation=up_interp)

        if self.use_blur and self.blur_strength > 0:
            k = self.blur_strength * 2 + 1  # 가우시안 커널은 홀수여야 한다
            out = cv2.GaussianBlur(out, (k, k), 0)

        if self.use_jpeg:
            # 저품질 JPEG로 재인코딩해 블록 아티팩트를 입힌다.
            ok, buf = cv2.imencode(
                ".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
            )
            if ok:
                out = cv2.imdecode(buf, cv2.IMREAD_COLOR)

        return out

    def effective_resolution(self, width, height):
        """상대방에게 실제로 전달되는 정보량 (축소된 시점의 해상도)."""
        return max(2, int(width * self.scale)), max(2, int(height * self.scale))
