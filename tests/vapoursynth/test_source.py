"""CPU-only fast-source regressions; run with the bundled VapourSynth Python."""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "vapoursynth"))

try:
    import vapoursynth as vs
except ImportError:
    vs = None

# The blending capability tests can install a stub during unittest discovery.
# These pixel tests require the real runtime, never that stub.
HAVE_VS = vs is not None and hasattr(vs, "VideoFrame")
if HAVE_VS:
    from blur.source import strict_constant


@unittest.skipUnless(HAVE_VS, "VapourSynth is required for pixel comparisons")
class ConstantSourceTests(unittest.TestCase):
    def test_pixels_properties_length_and_fractional_fps_are_preserved(self):
        for pixel_format, multiplier in ((vs.YUV420P8, 1), (vs.YUV420P10, 4)):
            with self.subTest(pixel_format=pixel_format):
                parts = []
                for i, length in enumerate((1, 2, 2)):
                    part = vs.core.std.BlankClip(
                        width=16,
                        height=12,
                        format=pixel_format,
                        length=length,
                        fpsnum=120000,
                        fpsden=1001,
                        color=[(16 + i * 30) * multiplier, 64 * multiplier, 200 * multiplier],
                        varsize=True,
                        varformat=True,
                    )
                    parts.append(
                        vs.core.std.SetFrameProps(
                            part,
                            audit_integer=471 + i,
                            audit_float=0.25 + i,
                            audit_data=b"original metadata",
                            _Matrix=1,
                            _AbsoluteTime=0.125 + i,
                        )
                    )
                source = vs.core.std.Splice(parts, mismatch=True)
                output = strict_constant(source)

                self.assertEqual(len(output), 5)
                self.assertEqual((output.width, output.height), (16, 12))
                self.assertEqual(output.format.id, pixel_format)
                self.assertEqual((output.fps_num, output.fps_den), (120000, 1001))

                # Distinct frames and nonsequential requests catch accidental
                # first-frame reuse as well as dropped or reordered frames.
                for n in (4, 0, 2, 1, 3):
                    with self.subTest(frame=n):
                        with source.get_frame(n) as original, output.get_frame(n) as actual:
                            self.assertEqual(dict(actual.props), dict(original.props))
                            for plane in range(original.format.num_planes):
                                self.assertEqual(
                                    memoryview(actual[plane]).tobytes(),
                                    memoryview(original[plane]).tobytes(),
                                )

    def test_dimension_and_format_changes_fail_instead_of_converting(self):
        first = vs.core.std.BlankClip(
            width=16, height=16, format=vs.YUV420P8, length=2, fpsnum=120, fpsden=1
        )
        changes = {
            "width": {"width": 32},
            "height": {"height": 24},
            "bit depth": {"format": vs.YUV420P10},
            "chroma subsampling": {"format": vs.YUV444P8},
        }
        for name, arguments in changes.items():
            with self.subTest(change=name):
                changed = vs.core.std.BlankClip(first, **arguments)
                source = vs.core.std.Splice([first, changed], mismatch=True)
                output = strict_constant(source)
                with output.get_frame(0):
                    pass
                with self.assertRaises(vs.Error) as raised:
                    output.get_frame(2)
                message = str(raised.exception)
                self.assertIn("Frame 2", message)
                self.assertIn("constant resolution and pixel format", message)
                self.assertIn("Disable fast decoding and render again", message)


if __name__ == "__main__":
    if not HAVE_VS:
        raise SystemExit("Run test_source.py with the bundled VapourSynth Python runtime.")
    unittest.main()
