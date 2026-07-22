#!/usr/bin/env python3
"""webcam-blur 앱 아이콘을 생성한다.

외부 이미지 도구 없이 빌드할 수 있도록 PNG를 직접 만든 뒤 iconutil로 icns를
묶는다.
"""

import math
from pathlib import Path
import struct
import subprocess
import zlib

ROOT = Path(__file__).resolve().parents[1]
ICONSET = ROOT / "assets" / "AppIcon.iconset"
ICNS = ROOT / "assets" / "AppIcon.icns"


def _png_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _write_png(path, width, height, pixels):
    rows = []
    for y in range(height):
        start = y * width * 4
        rows.append(b"\x00" + bytes(pixels[start : start + width * 4]))
    raw = b"".join(rows)
    data = b"\x89PNG\r\n\x1a\n"
    data += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    data += _png_chunk(b"IDAT", zlib.compress(raw, 9))
    data += _png_chunk(b"IEND", b"")
    path.write_bytes(data)


def _blend(dst, src):
    sr, sg, sb, sa = src
    if sa == 255:
        return [sr, sg, sb, sa]
    dr, dg, db, da = dst
    alpha = sa / 255
    out_a = alpha + da / 255 * (1 - alpha)
    if out_a == 0:
        return [0, 0, 0, 0]
    return [
        round((sr * alpha + dr * da / 255 * (1 - alpha)) / out_a),
        round((sg * alpha + dg * da / 255 * (1 - alpha)) / out_a),
        round((sb * alpha + db * da / 255 * (1 - alpha)) / out_a),
        round(out_a * 255),
    ]


def _rounded_rect(pixels, size, x, y, w, h, radius, color):
    for py in range(max(0, y), min(size, y + h)):
        for px in range(max(0, x), min(size, x + w)):
            dx = max(x - px, 0, px - (x + w - 1))
            dy = max(y - py, 0, py - (y + h - 1))
            cx = min(px - x, x + w - 1 - px)
            cy = min(py - y, y + h - 1 - py)
            if cx < radius and cy < radius:
                rx = radius - cx
                ry = radius - cy
                if rx * rx + ry * ry > radius * radius:
                    continue
            i = (py * size + px) * 4
            pixels[i : i + 4] = _blend(pixels[i : i + 4], color)


def _circle(pixels, size, cx, cy, radius, color):
    r2 = radius * radius
    for py in range(max(0, cy - radius), min(size, cy + radius + 1)):
        for px in range(max(0, cx - radius), min(size, cx + radius + 1)):
            if (px - cx) ** 2 + (py - cy) ** 2 <= r2:
                i = (py * size + px) * 4
                pixels[i : i + 4] = _blend(pixels[i : i + 4], color)


def _make_icon(size):
    pixels = [0, 0, 0, 0] * size * size

    margin = round(size * 0.07)
    _rounded_rect(
        pixels,
        size,
        margin,
        margin,
        size - margin * 2,
        size - margin * 2,
        round(size * 0.18),
        [18, 20, 23, 255],
    )

    # 은은한 사선 하이라이트
    for y in range(size):
        for x in range(size):
            if x + y < size * 0.9:
                i = (y * size + x) * 4
                pixels[i : i + 4] = _blend(pixels[i : i + 4], [55, 105, 145, 70])

    body_x = round(size * 0.18)
    body_y = round(size * 0.34)
    body_w = round(size * 0.64)
    body_h = round(size * 0.34)
    _rounded_rect(
        pixels,
        size,
        body_x,
        body_y,
        body_w,
        body_h,
        round(size * 0.08),
        [216, 236, 255, 245],
    )
    _rounded_rect(
        pixels,
        size,
        round(size * 0.61),
        round(size * 0.40),
        round(size * 0.18),
        round(size * 0.22),
        round(size * 0.04),
        [124, 183, 255, 255],
    )
    _circle(
        pixels,
        size,
        round(size * 0.43),
        round(size * 0.51),
        round(size * 0.13),
        [25, 36, 48, 255],
    )
    _circle(
        pixels,
        size,
        round(size * 0.43),
        round(size * 0.51),
        round(size * 0.07),
        [124, 183, 255, 255],
    )

    # blur 픽셀
    for idx, alpha in enumerate((180, 130, 90)):
        offset = round(size * (0.10 + idx * 0.055))
        _circle(
            pixels,
            size,
            round(size * 0.43) + offset,
            round(size * 0.51),
            max(1, round(size * 0.025)),
            [247, 249, 250, alpha],
        )

    return pixels


def main():
    ICONSET.mkdir(parents=True, exist_ok=True)
    sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, size in sizes.items():
        _write_png(ICONSET / name, size, size, _make_icon(size))

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(ICNS)


if __name__ == "__main__":
    main()
