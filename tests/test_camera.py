import unittest

import numpy as np
from PySide6.QtGui import QImage

from camera import qimage_to_bgr


class CameraConversionTest(unittest.TestCase):
    def test_qimage_to_bgr_converts_channels_and_owns_memory(self):
        rgb = np.array(
            [
                [[10, 20, 30], [40, 50, 60]],
                [[70, 80, 90], [100, 110, 120]],
            ],
            dtype=np.uint8,
        )
        image = QImage(
            rgb.data,
            2,
            2,
            2 * 3,
            QImage.Format.Format_RGB888,
        ).copy()

        bgr = qimage_to_bgr(image)

        np.testing.assert_array_equal(bgr, rgb[:, :, ::-1])
        self.assertTrue(bgr.flags.owndata)

    def test_qimage_to_bgr_ignores_empty_image(self):
        self.assertIsNone(qimage_to_bgr(QImage()))


if __name__ == "__main__":
    unittest.main()
