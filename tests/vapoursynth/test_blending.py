"""Run with python -m unittest discover -s tests/vapoursynth -v.

Runtime comparisons require VapourSynth R82+, Akarin and numpy. Set
BLUR_REQUIRE_GPU=1 to fail instead of skipping when that runtime is unavailable;
BLUR_TEST_4K=1 includes the 3840x2160, 240-to-60 fps regression. A non-autoloaded
Akarin library can be supplied using BLUR_AKARIN_PATH.
"""

import contextlib
import importlib
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "vapoursynth"))

try:
    import vapoursynth as vs

    HAVE_VS = True
except ImportError:
    HAVE_VS = False
    # Only the capability tests run against this stub. Pixel comparisons below
    # always require the real engine, real shader execution and real Akarin.
    vs = ModuleType("vapoursynth")
    vs.VideoNode = object
    vs.Error = type("VapourSynthError", (Exception,), {})
    vs.core = SimpleNamespace()
    sys.modules["vapoursynth"] = vs

blending = importlib.import_module("blur.blending")


class BackendSelectionTests(unittest.TestCase):
    def setUp(self):
        self.clip = SimpleNamespace(
            gpu_resident=False, format=object(), width=64, height=40
        )
        self.uploaded = SimpleNamespace(gpu_resident=True)
        self.core = SimpleNamespace(
            core_version=SimpleNamespace(release_major=82),
            vulkan_devices=[{"usable": True, "type": "discrete"}],
            vulkan_device_info={"name": "Test GPU"},
            std=SimpleNamespace(
                GPUUpload=Mock(return_value=self.uploaded),
                GPUDownload=Mock(return_value=self.clip),
            ),
        )
        self.log = io.StringIO()
        self.core_patch = patch.object(blending, "core", self.core)
        self.core_patch.start()
        self.addCleanup(self.core_patch.stop)

    def prepare(self, weights=None, gpu=True, gamma=1.0):
        with contextlib.redirect_stderr(self.log):
            return blending._prepare_blending_clip(
                self.clip, weights or [0.2] * 5, gpu, gamma
            )

    def assert_cpu(self, reason):
        self.assertIs(self.prepare(), self.clip)
        self.core.std.GPUUpload.assert_not_called()
        self.assertIn(reason, self.log.getvalue())

    def test_old_runtime_falls_back(self):
        self.core.core_version.release_major = 70
        self.assert_cpu("R82")

    def test_runtime_without_core_version_falls_back(self):
        del self.core.core_version
        self.assert_cpu("R82")

    def test_no_device_falls_back(self):
        self.core.vulkan_devices = []
        self.assert_cpu("no usable Vulkan 1.4 GPU")

    def test_unsupported_gpu_falls_back(self):
        self.core.vulkan_devices = [{"usable": False, "type": "discrete"}]
        self.assert_cpu("no usable Vulkan 1.4 GPU")

    def test_cpu_vulkan_driver_is_not_gpu_acceleration(self):
        self.core.vulkan_devices = [{"usable": True, "type": "cpu"}]
        self.assert_cpu("no usable Vulkan 1.4 GPU")

    def test_missing_vulkan_loader_falls_back(self):
        class MissingVulkan:
            core_version = SimpleNamespace(release_major=82)
            std = self.core.std

            @property
            def vulkan_devices(self):
                raise vs.Error("no Vulkan loader")

        with patch.object(blending, "core", MissingVulkan()):
            self.assert_cpu("no Vulkan loader")

    def test_disabled_gpu_does_not_upload(self):
        self.assertIs(self.prepare(gpu=False), self.clip)
        self.core.std.GPUUpload.assert_not_called()
        self.assertIn("GPU blending disabled", self.log.getvalue())

    def test_large_window_falls_back(self):
        self.assertIs(self.prepare(weights=[1.0] * 27), self.clip)
        self.core.std.GPUUpload.assert_not_called()
        self.assertIn("more than 26", self.log.getvalue())

    def test_gamma_preserves_cpu_path(self):
        self.assertIs(self.prepare(gamma=2.2), self.clip)
        self.core.std.GPUUpload.assert_not_called()
        self.assertIn("gamma-correct", self.log.getvalue())

    def test_shared_source_is_uploaded_once_and_device_logged(self):
        self.assertIs(self.prepare(), self.uploaded)
        self.core.std.GPUUpload.assert_called_once_with(self.clip)
        self.assertIn("Vulkan std.Expr", self.log.getvalue())
        self.assertIn("Test GPU", self.log.getvalue())

    def test_diagnostics_do_not_corrupt_vspipe_stdout(self):
        binary_output = io.StringIO()
        with contextlib.redirect_stdout(binary_output):
            self.prepare()
            self.prepare(gpu=False)
        self.assertEqual(binary_output.getvalue(), "")
        self.assertIn("frame blending", self.log.getvalue())

    def test_upload_errors_propagate(self):
        self.core.std.GPUUpload.side_effect = vs.Error("out of device memory")
        with self.assertRaisesRegex(vs.Error, "out of device memory"):
            self.prepare()


class RuntimeBlendingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        def unavailable(reason):
            if os.environ.get("BLUR_REQUIRE_GPU") == "1":
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)

        if not HAVE_VS:
            unavailable("VapourSynth runtime is not installed")
        try:
            import numpy as np
        except ImportError:
            unavailable("numpy is not installed")
        cls.np = np
        cls.core = vs.core
        if not hasattr(cls.core, "akarin") and os.environ.get("BLUR_AKARIN_PATH"):
            cls.core.std.LoadPlugin(path=os.environ["BLUR_AKARIN_PATH"])
        if not hasattr(cls.core, "akarin"):
            unavailable("Akarin is not installed")
        probe = cls.core.std.BlankClip(width=16, height=16, length=5)
        cls.log = io.StringIO()
        with contextlib.redirect_stderr(cls.log):
            result = blending.blend(probe, [1] * 5)
        if not getattr(result, "gpu_resident", False):
            unavailable(cls.log.getvalue().strip())
        # Force actual upload, shader compilation, execution and download.
        blending.download(result).get_frame(0).close()
        cls.change_fps = staticmethod(importlib.import_module("blur.interpolate").change_fps)

    def make_clip(self, fmt, width=64, height=40, length=17):
        np = self.np
        base = self.core.std.BlankClip(
            width=width, height=height, format=fmt, length=length, fpsnum=240
        )

        def fill(n, f):
            result = f.copy()
            for plane in range(result.format.num_planes):
                pixels = np.asarray(result[plane])
                x = np.arange(pixels.shape[1], dtype=np.int32)[None, :]
                y = np.arange(pixels.shape[0], dtype=np.int32)[:, None]
                values = x * 7 + y * 11 + n * 37 + plane * 53
                if result.format.sample_type == vs.INTEGER:
                    pixels[:] = values % (1 << result.format.bits_per_sample)
                else:
                    pixels[:] = (values % 1000) / 1000.0
                    if result.format.color_family == vs.YUV and plane:
                        pixels[:] -= 0.5
            result.props["_Matrix"] = 1
            if getattr(getattr(self.core, "core_version", None), "release_major", 0) >= 80:
                result.props["_Range"] = 0
            else:
                result.props["_ColorRange"] = 1
            return result

        return base.std.ModifyFrame(clips=base, selector=fill)

    def cpu_and_gpu(self, clip, weights, divisor=None):
        cpu = blending.average(clip, weights, divisor)
        with contextlib.redirect_stderr(io.StringIO()):
            gpu = blending.blend(clip, weights, divisor)
        self.assertTrue(gpu.gpu_resident)
        self.assertEqual(cpu.format.id, gpu.format.id)
        self.assertEqual(cpu.num_frames, gpu.num_frames)
        self.assertEqual(cpu.fps, gpu.fps)
        return cpu, gpu

    def assert_pixels(self, reference, actual, indices=None, atol=0):
        np = self.np
        actual = blending.download(actual)
        if indices is None:
            indices = range(len(reference))
        for n in indices:
            with reference.get_frame(n) as expected, actual.get_frame(n) as observed:
                self.assertEqual(expected.format.id, observed.format.id)
                for p in range(expected.format.num_planes):
                    np.testing.assert_allclose(
                        np.asarray(observed[p]), np.asarray(expected[p]),
                        rtol=0, atol=atol, err_msg=f"frame {n}, plane {p}",
                    )

    def test_equal_integer_weights_are_bitexact(self):
        for fmt in (vs.YUV420P8, vs.YUV420P10, vs.YUV444P16):
            with self.subTest(format=fmt):
                clip = self.make_clip(fmt)
                for weights in ([1] * 5, [0.2] * 5):
                    cpu, gpu = self.cpu_and_gpu(clip, weights)
                    self.assert_pixels(cpu, gpu)

    def test_weighting_direction_divisor_and_clamped_edges(self):
        np = self.np
        clip = self.make_clip(vs.GRAYS, length=7)
        weights = [1.0, 2.0, 4.0, 8.0, 16.0]
        divisor = 40.0  # Deliberately different from sum(weights).
        cpu, gpu = self.cpu_and_gpu(clip, weights, divisor)
        self.assert_pixels(cpu, gpu, atol=2e-7)
        # Independent double-precision temporal reference, including both ends.
        actual = blending.download(gpu)
        for n in range(len(clip)):
            expected = np.zeros((clip.height, clip.width), dtype=np.float64)
            for tap, weight in enumerate(weights):
                index = min(max(n + tap - 2, 0), len(clip) - 1)
                with clip.get_frame(index) as frame:
                    expected += np.asarray(frame[0]) * float(weight)
            expected /= divisor
            with actual.get_frame(n) as frame:
                np.testing.assert_allclose(np.asarray(frame[0]), expected, rtol=0, atol=2e-7)

    def test_float_gaussian_and_asymmetric_weights(self):
        weighting = importlib.import_module("blur.weighting")
        for fmt in (vs.RGBS, vs.YUV444PS):
            clip = self.make_clip(fmt)
            for weights in (
                weighting.gaussian_sym(5),
                weighting.ascending(5),
                weighting.descending(5),
                weighting.pyramid(25),
            ):
                with self.subTest(format=fmt, weights=len(weights)):
                    cpu, gpu = self.cpu_and_gpu(clip, weights)
                    self.assert_pixels(cpu, gpu, atol=3e-7)

    def test_short_clip_clamps_window_beyond_both_ends(self):
        for length in (1, 2):
            clip = self.make_clip(vs.YUV420P8, length=length)
            cpu, gpu = self.cpu_and_gpu(clip, [1] * 5)
            self.assert_pixels(cpu, gpu)

    def test_240_to_60_keeps_gpu_frames_until_download(self):
        clip = self.make_clip(vs.YUV420P10, length=17)
        cpu, gpu = self.cpu_and_gpu(clip, [0.2] * 5)
        expected = self.change_fps(cpu, 60)
        observed = self.change_fps(gpu, 60)
        self.assertTrue(observed.gpu_resident)
        self.assertEqual(observed.fps_num, 60)
        self.assertEqual(observed.fps_den, 1)
        self.assertEqual(len(observed), 4)
        self.assert_pixels(expected, observed)
        downloaded = blending.download(observed)
        self.assertFalse(downloaded.gpu_resident)
        # Exercise a CPU-only Akarin downstream filter after the boundary.
        self.assert_pixels(expected, self.core.akarin.Expr(downloaded, "x"))

    def test_disabled_and_large_window_preserve_cpu_output(self):
        clip = self.make_clip(vs.YUV420P8, length=31)
        for weights, enabled in (([1] * 5, False), ([1] * 27, True)):
            with contextlib.redirect_stderr(io.StringIO()):
                result = blending.blend(clip, weights, gpu=enabled)
            self.assertFalse(result.gpu_resident)
            self.assert_pixels(blending.average(clip, weights), result)

    def test_gamma_fallback_matches_existing_gamma_algorithm(self):
        weights = [0.2] * 5
        for fmt in (vs.RGBS, vs.YUV420P8, vs.YUV420P10):
            for full in (False, True):
                with self.subTest(format=fmt, full=full):
                    clip = self.make_clip(fmt)
                    expected = blending.average_bright(clip, full, 2.2, weights)
                    log = io.StringIO()
                    with contextlib.redirect_stderr(log):
                        actual = blending.blend(
                            clip, weights, gamma=2.2, is_full_color_range=full
                        )
                    self.assertFalse(actual.gpu_resident)
                    self.assertIn("gamma-correct", log.getvalue())
                    self.assert_pixels(expected, actual)

    @unittest.skipUnless(os.environ.get("BLUR_TEST_4K") == "1", "set BLUR_TEST_4K=1")
    def test_4k240_to_60(self):
        for fmt in (vs.YUV420P8, vs.YUV420P10):
            with self.subTest(format=fmt):
                clip = self.make_clip(fmt, width=3840, height=2160, length=17)
                cpu, gpu = self.cpu_and_gpu(clip, [0.2] * 5)
                self.assert_pixels(self.change_fps(cpu, 60), self.change_fps(gpu, 60))


if __name__ == "__main__":
    unittest.main()
