"""Source wrappers that preserve decoded pixels and frame properties."""

import vapoursynth as vs
from vapoursynth import core


def strict_constant(clip: vs.VideoNode) -> vs.VideoNode:
    """Declare a variable source constant, rejecting changes instead of resizing."""
    with clip.get_frame(0) as first:
        width, height = first.width, first.height
        frame_format = first.format

    if clip.fps_num <= 0 or clip.fps_den <= 0:
        raise vs.Error(
            "Fast decoding requires a valid source frame rate. "
            "Disable fast decoding and render again."
        )

    # ModifyFrame uses this node only for output metadata. Its selector returns
    # the original decoded frame, including its properties and plane buffers.
    attributes = core.std.BlankClip(
        width=width,
        height=height,
        format=frame_format.id,
        length=len(clip),
        fpsnum=clip.fps_num,
        fpsden=clip.fps_den,
        keep=True,
    )

    def select_frame(n: int, f: vs.VideoFrame) -> vs.VideoFrame:
        if (
            f.width != width
            or f.height != height
            or f.format.id != frame_format.id
        ):
            raise vs.Error(
                "Fast decoding requires a constant resolution and pixel format. "
                f"Frame {n} is {f.width}x{f.height} {f.format.name}; "
                f"expected {width}x{height} {frame_format.name}. "
                "Disable fast decoding and render again."
            )
        return f

    return core.std.ModifyFrame(attributes, clips=clip, selector=select_frame)
