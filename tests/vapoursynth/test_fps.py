"""CPU-only frame-rate selection comparisons against the original blur graph."""

import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "vapoursynth"))

try:
    import vapoursynth as vs
except ImportError:
    vs = None

HAVE_VS = vs is not None and hasattr(vs, "VideoFrame")
if HAVE_VS:
    import blur.interpolate as interpolate


def original_change_fps(clip, fpsnum, fpsden=1):
    """Unmodified mapping from upstream blur commit 6fd0ecc."""
    if not isinstance(clip, vs.VideoNode):
        raise vs.Error("ChangeFPS: This is not a clip")
    factor = (fpsnum / fpsden) * (clip.fps_den / clip.fps_num)

    def frame_adjuster(n):
        real_n = math.floor(n / factor)
        return clip[real_n] * (len(clip) + 100)

    attribute_clip = clip.std.BlankClip(
        length=math.floor(len(clip) * factor), fpsnum=fpsnum, fpsden=fpsden
    )
    return attribute_clip.std.FrameEval(eval=frame_adjuster)


@unittest.skipUnless(HAVE_VS, "VapourSynth is required for pixel comparisons")
class FrameRateTests(unittest.TestCase):
    def source(self, length, fpsnum, fpsden=1, bits=8):
        clip = vs.core.std.BlankClip(
            width=16, height=12,
            format=vs.YUV420P8 if bits == 8 else vs.YUV420P10,
            length=length, fpsnum=fpsnum, fpsden=fpsden,
        )

        def identify(n, f):
            frame = f.copy()
            for plane in range(frame.format.num_planes):
                frame[plane][0, 0] = (n * 31 + plane * 17) % (1 << bits)
            frame.props["audit_index"] = n
            frame.props["audit_array"] = [n, n + 1]
            frame.props["audit_data"] = b"source\x00metadata"
            frame.props["_AbsoluteTime"] = n * fpsden / fpsnum
            if n % 4 == 0:
                del frame.props["_DurationNum"]
                del frame.props["_DurationDen"]
            else:
                frame.props["_DurationNum"] = n + 7
                frame.props["_DurationDen"] = 999
            return frame

        return vs.core.std.ModifyFrame(clip, clips=clip, selector=identify)

    def assert_same_graph_output(self, original, actual):
        self.assertEqual(len(actual), len(original))
        self.assertEqual((actual.fps_num, actual.fps_den), (original.fps_num, original.fps_den))
        self.assertEqual((actual.width, actual.height, actual.format.id),
                         (original.width, original.height, original.format.id))
        for n in reversed(range(len(original))):
            with self.subTest(output_frame=n):
                with original.get_frame(n) as expected, actual.get_frame(n) as result:
                    self.assertEqual(dict(result.props), dict(expected.props))
                    for plane in range(expected.format.num_planes):
                        self.assertEqual(memoryview(result[plane]).tobytes(),
                                         memoryview(expected[plane]).tobytes())

    def forbid_native_selection(self):
        # The legacy implementation uses clip.std, so this catches accidental
        # entry into the new path without replacing the real video runtime.
        return patch.object(interpolate, "core", SimpleNamespace(std=SimpleNamespace(
            SelectEvery=Mock(side_effect=AssertionError("Expected the legacy FPS path"))
        )))

    def test_integer_power_of_two_ratios_match_every_frame_and_property(self):
        for bits in (8, 10):
            for input_fps in (120, 240, 480):
                cycle = input_fps // 60
                for length in (cycle, cycle + 1, cycle * 2 - 1, cycle * 3, cycle * 3 + 1):
                    with self.subTest(bits=bits, input_fps=input_fps, length=length):
                        source = self.source(length, input_fps, bits=bits)
                        native_selection = Mock(wraps=vs.core.std.SelectEvery)
                        native_core = SimpleNamespace(std=SimpleNamespace(
                            SelectEvery=native_selection,
                            AssumeFPS=vs.core.std.AssumeFPS,
                            CopyFrameProps=vs.core.std.CopyFrameProps,
                        ))
                        with patch.object(interpolate, "core", native_core):
                            actual = interpolate.change_fps(source, 60)
                        native_selection.assert_called_once()
                        self.assert_same_graph_output(
                            original_change_fps(source, 60), actual
                        )

    def test_one_frame_and_short_tails_keep_original_empty_output_error(self):
        for input_fps, length in ((120, 1), (240, 1), (240, 3)):
            with self.subTest(input_fps=input_fps, length=length):
                source = self.source(length, input_fps)
                with self.assertRaises(vs.Error) as expected:
                    original_change_fps(source, 60)
                with self.forbid_native_selection():
                    with self.assertRaises(vs.Error) as actual:
                        interpolate.change_fps(source, 60)
                self.assertEqual(str(actual.exception), str(expected.exception))

    def test_fractional_and_other_ratios_keep_original_mapping(self):
        for input_num, input_den, output_num, output_den in (
            (120000, 1001, 60, 1),
            (240000, 1001, 60, 1),
            (120000, 1001, 60000, 1001),
            (180, 1, 60, 1),
            (360, 1, 60, 1),
            (98, 1, 49, 1),  # Integer 2:1, but legacy factor is slightly below 0.5.
            (120, 1, 120, 1),
            (60, 1, 120, 1),
        ):
            with self.subTest(rate=(input_num, input_den, output_num, output_den)):
                source = self.source(25, input_num, input_den)
                with self.forbid_native_selection():
                    actual = interpolate.change_fps(source, output_num, output_den)
                self.assert_same_graph_output(
                    original_change_fps(source, output_num, output_den), actual
                )

    def test_180_to_60_retains_legacy_float_rounding(self):
        source = self.source(25, 180)
        result = interpolate.change_fps(source, 60)
        with result.get_frame(1) as frame:
            self.assertEqual(frame.props["audit_index"], 2)

    def test_float_fps_arguments_keep_original_validation(self):
        source = self.source(9, 120)
        for fpsnum, fpsden in ((60.0, 1), (60, 1.0)):
            with self.subTest(fpsnum=fpsnum, fpsden=fpsden):
                try:
                    expected = original_change_fps(source, fpsnum, fpsden)
                except (vs.Error, TypeError) as expected_error:
                    with self.forbid_native_selection():
                        with self.assertRaises(type(expected_error)) as actual:
                            interpolate.change_fps(source, fpsnum, fpsden)
                    self.assertEqual(str(actual.exception), str(expected_error))
                else:
                    with self.forbid_native_selection():
                        actual = interpolate.change_fps(source, fpsnum, fpsden)
                    self.assert_same_graph_output(expected, actual)

    def test_gpu_residency_uses_legacy_path_without_allocating_gpu_frames(self):
        source = self.source(9, 120)
        with self.forbid_native_selection(), patch.object(
            interpolate, "getattr", return_value=True, create=True
        ):
            actual = interpolate.change_fps(source, 60)
        self.assert_same_graph_output(original_change_fps(source, 60), actual)


if __name__ == "__main__":
    if not HAVE_VS:
        raise SystemExit("Run test_fps.py with the bundled VapourSynth Python runtime.")
    unittest.main()
