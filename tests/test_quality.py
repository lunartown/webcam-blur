import unittest

import numpy as np

from quality import PRESETS, QualityReducer


class QualityReducerTest(unittest.TestCase):
    def test_disabled_returns_original_frame(self):
        reducer = QualityReducer(preset=3)
        reducer.enabled = False
        frame = np.zeros((12, 16, 3), dtype=np.uint8)

        self.assertIs(reducer.process(frame), frame)

    def test_process_keeps_shape_and_dtype(self):
        reducer = QualityReducer(preset=3)
        frame = np.random.default_rng(0).integers(
            0, 256, size=(72, 128, 3), dtype=np.uint8
        )

        out = reducer.process(frame)

        self.assertEqual(out.shape, frame.shape)
        self.assertEqual(out.dtype, frame.dtype)
        self.assertFalse(np.array_equal(out, frame))

    def test_apply_preset_updates_effective_resolution(self):
        reducer = QualityReducer(preset=1)
        reducer.apply_preset(5)
        scale, _, _ = PRESETS[5]

        self.assertEqual(
            reducer.effective_resolution(1280, 720),
            (max(2, int(1280 * scale)), max(2, int(720 * scale))),
        )

    def test_unknown_preset_is_ignored(self):
        reducer = QualityReducer(preset=2)
        before = (reducer.scale, reducer.blur_strength, reducer.jpeg_quality)

        reducer.apply_preset(99)

        self.assertEqual(
            before,
            (reducer.scale, reducer.blur_strength, reducer.jpeg_quality),
        )


if __name__ == "__main__":
    unittest.main()
