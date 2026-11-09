from vapoursynth import core
import vapoursynth as vs
import sys

import blur.utils as u


GPU_EXPR_MAX_INPUTS = 26
GPU_MIN_RELEASE = 82
EXPR_NAMES = "xyzabcdefghijklmnopqrstuvw"


def download(clip: vs.VideoNode) -> vs.VideoNode:
    """Finish the GPU section before CPU-only filters or the output pipe."""
    if getattr(clip, "gpu_resident", False):
        return core.std.GPUDownload(clip)
    return clip


def _prepare_blending_clip(clip, weights, gpu, gamma):
    """Fall back only during capability checks, never after rendering starts."""
    reason = None
    device = None
    if not gpu:
        reason = "GPU blending disabled"
    elif gamma != 1.0:
        reason = "gamma-correct blending uses the existing CPU path"
    elif len(weights) > GPU_EXPR_MAX_INPUTS:
        reason = f"more than {GPU_EXPR_MAX_INPUTS} frames in the blending window"
    elif (
        getattr(getattr(core, "core_version", None), "release_major", 0)
        < GPU_MIN_RELEASE
        or not hasattr(core.std, "GPUUpload")
    ):
        reason = f"VapourSynth R{GPU_MIN_RELEASE} or newer is required"
    elif clip.format is None or clip.width == 0 or clip.height == 0:
        reason = "GPU blending requires constant dimensions and format"
    else:
        try:
            devices = core.vulkan_devices
            if not any(d["usable"] and d["type"] != "cpu" for d in devices):
                reason = "no usable Vulkan 1.4 GPU"
            else:
                # This initializes the device without evaluating any video frames.
                device = core.vulkan_device_info
        except vs.Error as error:
            reason = f"Vulkan initialization unavailable: {error}"

    if reason is not None:
        print(f"frame blending: CPU (akarin.Expr); {reason}", file=sys.stderr, flush=True)
        return download(clip)

    # Upload the shared source once, before making the shifted clips. Overlapping
    # windows can then reuse the GPU frame cache instead of uploading each tap.
    uploaded = core.std.GPUUpload(clip)
    print(
        f"frame blending: GPU (Vulkan std.Expr) on {device['name']}; "
        f"{len(weights)} frames",
        file=sys.stderr,
        flush=True,
    )
    return uploaded


def blend(
    clip: vs.VideoNode,
    weights: list[float],
    divisor: float | None = None,
    *,
    gpu: bool = False,
    gamma: float = 1.0,
    is_full_color_range: bool = False,
) -> vs.VideoNode:
    """Select the blending backend, preserving the existing temporal algorithm."""
    clip = _prepare_blending_clip(clip, weights, gpu, gamma)
    if gamma == 1.0:
        return average(clip, weights, divisor)
    return average_bright(clip, is_full_color_range, gamma, weights, divisor)


# https://github.com/AkarinVS/vapoursynth-plugin/issues/17#issuecomment-1312639376
# can't use Expr2 which supports src0,1,2 etc. when using asmjit so youre limited to 26 clips
# this is a workaround
def expr1_arbitrary_weights_blend(clips, weights):
    names = "".join(map(lambda x: chr(x + ord("a")), range(26)))
    names = names[-3:] + names[:-3]  # 'xyzabc...w'
    limit = 26
    if len(clips) <= limit:
        expr = " ".join(
            map(lambda cw: f"{cw[0]} {cw[1]} *", zip(names[: len(clips)], weights))
        ) + " +" * (len(clips) - 1)

        return core.akarin.Expr(
            clips,
            expr,
        )
    else:
        return expr1_arbitrary_weights_blend(
            [expr1_arbitrary_weights_blend(clips[:limit], weights[:limit])]
            + clips[limit:],
            [1.0] + weights[limit:],
        )


def average_expr1(
    clip: vs.VideoNode, weights: list[float], divisor: float | None = None
):
    def get_offset_clip(offset: int) -> vs.VideoNode:
        if offset > 0:
            return clip[offset:] + clip[-1] * offset
        elif offset < 0:
            return clip[0] * -offset + clip[:offset]
        else:
            return clip

    diameter = len(weights)
    radius = diameter // 2

    if divisor is None:
        divisor = sum(weights)

    assert diameter % 2 == 1, "An odd number of weights is required."

    clips = [get_offset_clip(offset) for offset in range(-radius, radius + 1)]

    # todo: divisor? do u need it?
    return expr1_arbitrary_weights_blend(clips, weights)


# https://github.com/couleur-tweak-tips/smoothie-rs/blob/main/target/scripts/blending.py
def average(clip: vs.VideoNode, weights: list[float], divisor: float | None = None):
    def get_offset_clip(offset: int) -> vs.VideoNode:
        if offset > 0:
            if offset >= len(clip):
                return clip[-1] * len(clip)
            return clip[offset:] + clip[-1] * offset
        elif offset < 0:
            if -offset >= len(clip):
                return clip[0] * len(clip)
            return clip[0] * -offset + clip[:offset]
        else:
            return clip

    diameter = len(weights)
    radius = diameter // 2

    if divisor is None:
        divisor = sum(weights)

    assert diameter % 2 == 1, "An odd number of weights is required."

    on_gpu = getattr(clip, "gpu_resident", False)
    if on_gpu and diameter > GPU_EXPR_MAX_INPUTS:
        raise u.BlurException("GPU blending supports at most 26 input frames")

    clips = [get_offset_clip(offset) for offset in range(-radius, radius + 1)]

    expr = ""
    for i in range(0, diameter):
        name = EXPR_NAMES[i] if on_gpu else f"src{i}"
        expr += f"{name} {weights[i]} * "

    expr += "+ " * (diameter - 1)
    expr += f"{divisor} /" if divisor != 1 else ""

    # Keep the same ordering, weights, divisor and output format on both paths.
    # A shader compilation or execution failure must propagate to the renderer;
    # silently retrying on CPU could conceal invalid expressions or bad frames.
    return core.std.Expr(clips, expr) if on_gpu else core.akarin.Expr(clips, expr)


def average_bright(
    _video: vs.VideoNode,
    is_full_color_range: bool,
    gamma: float,
    weights: list[float],
    divisor: float | None = None,
):
    def process(video):
        def gamma_correct(video, gamma):
            expr = f"x {gamma} pow"
            return core.std.Expr(video, expr=expr)
            # return core.std.Levels(
            #     video, gamma=gamma, min_in=0.0, max_in=1.0, min_out=0.0, max_out=1.0
            # )

        video = gamma_correct(video, gamma)
        video = average(video, weights, divisor)
        video = gamma_correct(video, 1.0 / gamma)

        return video

    return u.with_format(_video, is_full_color_range, vs.RGBS, process)
