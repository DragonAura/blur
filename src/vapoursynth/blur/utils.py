import vapoursynth as vs
from vapoursynth import core
import sys

from pathlib import Path
from fractions import Fraction


class BlurException(Exception):
    pass


def load_plugins(extension: str):
    plugin_dir = Path("../vapoursynth-plugins")
    ignored = {
        f"libbestsource{extension}",
    }

    for plugin in plugin_dir.glob(f"*{extension}"):
        if plugin.name not in ignored:
            print("Loading", plugin.name, file=sys.stderr)
            try:
                core.std.LoadPlugin(path=str(plugin))
            except Exception as e:
                print(f"Failed to load plugin {plugin.name}: {e}", file=sys.stderr)


def safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coalesce(value, fallback):
    return fallback if value is None else value


def check_model_path(rife_model_path: str):
    path = Path(rife_model_path)

    if not path.exists():
        raise BlurException(f"RIFE model not found at {path}")


def assume_scaled_fps(clip, timescale):
    new_fps = clip.fps * timescale
    fps_frac = Fraction(new_fps).limit_denominator()

    return core.std.AssumeFPS(
        clip, fpsnum=fps_frac.numerator, fpsden=fps_frac.denominator
    )


def with_format(
    video: vs.VideoNode, is_full_color_range: bool, target_format, process_func
):
    orig_format = video.format
    needs_conversion = orig_format.id != target_format

    # Use the resizer's named ranges rather than confusing numeric arguments
    # with the differently numbered legacy _ColorRange frame property.
    range_name = "full" if is_full_color_range else "limited"

    if needs_conversion:
        convert_kwargs = {
            "format": target_format,
            "range_in_s": range_name,
            "range_s": range_name,
        }

        if target_format == vs.RGBS and orig_format.color_family == vs.YUV:
            convert_kwargs["matrix_in_s"] = "709"

        video = core.resize.Point(video, **convert_kwargs)

    video = process_func(video)

    if needs_conversion:
        convert_back_kwargs = {
            "format": orig_format.id,
            "range_in_s": range_name,
            "range_s": range_name,
        }

        if target_format == vs.RGBS and orig_format.color_family == vs.YUV:
            convert_back_kwargs["matrix_s"] = "709"

        video = core.resize.Point(video, **convert_back_kwargs)

    return video
