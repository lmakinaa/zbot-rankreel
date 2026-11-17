"""ffmpeg: trim, scale/crop, overlay, normalize, concat.

Every segment is encoded with identical parameters so the final concat is a
stream copy (no re-encode).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import ffmpeg_bin, sfx_path
from .models import Clip, Project, Style
from .overlay import overlay_png
from .probe import probe
from .validate import clip_bounds

AUDIO_RATE = 44100
# progress(fraction 0..1, message)
Progress = Callable[[float, str], None]


class ComposeError(RuntimeError):
    pass


@dataclass
class SfxPiece:
    """Part of the transition sound placed inside one segment.

    The sound is first cut to ``length`` (with a short fade-out if that shortens it),
    then the slice [src_start, src_end) of it plays ``at`` seconds into the segment.
    """
    src_start: float
    src_end: float
    at: float
    length: float
    trimmed: bool


@dataclass
class SegmentSpec:
    clip: Clip
    overlay: Path
    start: float
    length: float  # clip footage length
    hold: float  # extra frozen-frame seconds at the end
    has_audio: bool
    sfx: Path | None
    src_w: int | None = None  # displayed size of the source (rotation applied)
    src_h: int | None = None
    tx_in: bool = False  # play the transition at the start of this segment
    tx_out: bool = False  # "fade" only: dip to black at the end of this segment
    sfx_pieces: list[SfxPiece] = field(default_factory=list)
    crop: tuple[int, int, int, int] | None = None  # (w, h, x, y) of the source kept

    @property
    def duration(self) -> float:
        return self.length + self.hold


def resolve_sfx(style: Style) -> Path | None:
    if not style.transition_sfx:
        return None
    p = sfx_path(style.transition_sfx)
    if not p.exists():
        raise ComposeError(f"Transition sound not found: {style.transition_sfx} (pick another sound)")
    return p


def _hex_ff(color: str) -> str:
    return "0x" + color.lstrip("#")


SFX_FADE_OUT = 0.06  # when a sound is shortened to fit its transition
ZOOM_AMOUNT = 0.18  # zoom punch starts at 118% and eases back to 100%
SHAKE_ZOOM = 0.10  # shake headroom: up to 5% movement each way
SHAKE_HZ = 13


def transition_graph(seg: SegmentSpec, style: Style, src: str, dst: str) -> str:
    """Filtergraph taking the fitted clip ``src`` to ``dst`` with the transition applied.

    Applied to the footage only (before the title/rank overlay), so text stays steady.
    """
    W, H, fps = style.width, style.height, style.fps
    kind = style.transition
    d = min(style.transition_seconds, seg.length / 2)
    chain: list[str] = []
    if seg.tx_in and kind in ("zoom", "punch"):
        # Per-frame scale, then a centered crop back to the frame size.
        chain.append(
            f"scale=w='trunc({W}*(1+{ZOOM_AMOUNT}*pow(max(0\\,1-t/{d:.3f})\\,2))/2)*2'"
            f":h=-2:eval=frame,crop={W}:{H}"
        )
    if seg.tx_in and kind == "shake":
        # Phone-vibration jitter: zoom in slightly so the frame can move without
        # showing edges, then wobble the crop window; both decay to rest.
        env = f"max(0\\,1-t/{d:.3f})"
        chain.append(
            f"scale=w='trunc({W}*(1+{SHAKE_ZOOM}*{env})/2)*2':h=-2:eval=frame,"
            f"crop={W}:{H}:x='(in_w-out_w)/2*(1+sin(2*PI*{SHAKE_HZ}*t))'"
            f":y='(in_h-out_h)/2*(1+cos(2*PI*{SHAKE_HZ * 1.37:.2f}*t))'"
        )
    if seg.tx_in and kind in ("flash", "punch"):
        chain.append(f"fade=t=in:st=0:d={d:.3f}:color=white")
    if kind == "fade":
        half = d / 2
        if seg.tx_in:
            chain.append(f"fade=t=in:st=0:d={half:.3f}:color=black")
        if seg.tx_out:
            chain.append(f"fade=t=out:st={seg.length - half:.3f}:d={half:.3f}:color=black")
    graph = f"{src}{','.join(chain) or 'null'}"
    if seg.tx_in and kind == "slide":
        # New clip slides in from the right over black (ease-out cubic).
        return (f"{graph}[txs];color=c=black:s={W}x{H}:r={fps}:d={seg.length:.3f}[txbg];"
                f"[txbg][txs]overlay=x='if(lt(t\\,{d:.3f})\\,{W}*pow(1-t/{d:.3f}\\,3)\\,0)'"
                f":y=0:eval=frame:shortest=1{dst}")
    return f"{graph}{dst}"


# Every fit mode hands the same pixel format to the transition and overlay steps.
PIX = "format=yuv420p"
MAX_FILL_CROP = 0.06  # "cover" crops at most this share of the picture, else shows it whole


def crop_share(src_w: int | None, src_h: int | None, W: int, H: int) -> float:
    """Share of the picture that cropping to fill W x H would cut off (0 = none)."""
    if not src_w or not src_h:
        return 0.0
    src, dst = src_w / src_h, W / H
    return 1 - min(src, dst) / max(src, dst)


def effective_fit(style: Style, seg: SegmentSpec) -> str:
    if style.fit != "cover":
        return style.fit
    return "crop" if crop_share(seg.src_w, seg.src_h, style.width, style.height) <= MAX_FILL_CROP else "blur"


def fit_graph(style: Style, seg: SegmentSpec, src: str, dst: str) -> str:
    """Filtergraph placing the source in the W x H frame according to the fit mode."""
    W, H = style.width, style.height
    mode = effective_fit(style, seg)
    if mode == "crop":
        return f"{src}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},{PIX}{dst}"
    whole = f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    if mode == "contain":
        return (f"{src}{whole},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_hex_ff(style.background)},{PIX}{dst}")
    # blur: the whole video, centred over a blurred, darkened, zoomed copy of itself.
    # Blurring at 1/8 size and scaling back up is fast and gives a smooth backdrop.
    w8, h8 = W // 8, H // 8
    return (f"{src}split=2[fitbg][fitfg];"
            f"[fitbg]scale={w8}:{h8}:force_original_aspect_ratio=increase,crop={w8}:{h8},"
            # Darken with a translucent black layer, not eq: eq's frames were later brightened
            # by the white "flash" transition for the rest of the clip (ffmpeg quirk).
            f"boxblur=6:2,drawbox=c=black@0.25:t=fill,scale={W}:{H}[fitbgb];"
            f"[fitfg]{whole}[fitfgs];"
            f"[fitbgb][fitfgs]overlay=(W-w)/2:(H-h)/2:format=yuv420,{PIX}{dst}")


def segment_command(seg: SegmentSpec, style: Style, out: Path) -> list[str]:
    W, H, fps = style.width, style.height, style.fps
    cmd = [
        ffmpeg_bin(), "-y", "-hide_banner", "-nostdin",
        "-ss", f"{seg.start:.3f}", "-t", f"{seg.length:.3f}", "-i", seg.clip.local_path,
        "-loop", "1", "-framerate", str(fps), "-i", str(seg.overlay),
    ]
    next_input = 2
    if seg.has_audio:
        audio_in = "[0:a]"
    else:
        cmd += ["-f", "lavfi", "-t", f"{seg.duration:.3f}",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_RATE}"]
        audio_in = f"[{next_input}:a]"
        next_input += 1
    sfx_ins = []
    for _ in seg.sfx_pieces:
        cmd += ["-i", str(seg.sfx)]
        sfx_ins.append(f"[{next_input}:a]")
        next_input += 1

    hold = f",tpad=stop_mode=clone:stop_duration={seg.hold:.3f}" if seg.hold > 0 else ""
    crop = f",crop={seg.crop[0]}:{seg.crop[1]}:{seg.crop[2]}:{seg.crop[3]}" if seg.crop else ""
    video = (f"[0:v]fps={fps}{crop}[vsrc];{fit_graph(style, seg, '[vsrc]', '[vfit0]')};"
             f"[vfit0]setsar=1[vfit];"
             f"{transition_graph(seg, style, '[vfit]', '[v]')};"
             f"[v][1:v]overlay=0:0:shortest=1,format=yuv420p{hold}[vout]")

    afmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    chain = []
    if style.loudnorm and seg.has_audio:
        chain.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    chain += [f"aresample={AUDIO_RATE}", afmt]
    audio = f"{audio_in}{','.join(chain)}"
    if sfx_ins:
        audio += "[amain]"
        labels = []
        for k, (inp, pc) in enumerate(zip(sfx_ins, seg.sfx_pieces)):
            fade = min(SFX_FADE_OUT, pc.length / 3)
            shape = (f"atrim=end={pc.length:.4f},afade=t=out:st={pc.length - fade:.4f}:d={fade:.4f},"
                     if pc.trimmed else "")
            delay = round(pc.at * 1000)
            audio += (f";{inp}{shape}atrim=start={pc.src_start:.4f}:end={pc.src_end:.4f},"
                      f"asetpts=PTS-STARTPTS,{afmt},volume={style.sfx_volume:g},"
                      f"adelay={delay}:all=1[sfx{k}]")
            labels.append(f"[sfx{k}]")
        audio += (f";[amain]{''.join(labels)}amix=inputs={1 + len(labels)}:duration=first:"
                  f"dropout_transition=0:normalize=0")
    audio += ",apad[aout]"

    cmd += [
        "-filter_complex", f"{video};{audio}",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-video_track_timescale", str(fps * 512),
        # Lossless PCM inside the segment (.mov): no AAC priming, so joined segments stay
        # sample-accurate. The final file gets a single AAC encode in concat_segments().
        "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2",
        "-t", f"{seg.duration:.3f}",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]
    return cmd


def _run_ffmpeg(cmd: list[str], log_path: Path, expected: float,
                on_time: Callable[[float], None] | None = None) -> None:
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
        assert proc.stdout is not None
        for line in proc.stdout:
            if on_time and line.startswith("out_time_us="):
                try:
                    t = int(line.split("=", 1)[1]) / 1e6
                    on_time(max(0.0, min(1.0, t / expected)) if expected > 0 else 0.0)
                except ValueError:
                    pass
        rc = proc.wait()
    if rc != 0:
        tail = log_path.read_text(errors="replace").strip().splitlines()[-15:]
        raise ComposeError("ffmpeg failed:\n" + "\n".join(tail))


def plan_segments(project: Project, cache_dir: Path | None = None) -> list[SegmentSpec]:
    style = project.style
    sfx = resolve_sfx(style)
    specs = []
    order = project.play_order
    for i, rank in enumerate(order):
        clip = project.clip_by_rank(rank)
        if not clip.local_path or not Path(clip.local_path).exists():
            raise ComposeError(f"#{rank}: source video missing (download it first).")
        info = probe(clip.local_path)
        if not info.has_video:
            raise ComposeError(f"#{rank}: source has no video stream (photo post?).")
        if clip.source_duration is None:
            clip.source_duration = info.duration
        start, end = clip_bounds(clip)
        if end is None or end <= start:
            raise ComposeError(f"#{rank}: invalid trim {start}..{end}.")
        ov = overlay_png(project, i, cache_dir) if cache_dir else overlay_png(project, i)
        src_w, src_h, crop = info.width, info.height, None
        if clip.crop and src_w and src_h:
            crop = clip.crop.rect(src_w, src_h)
            src_w, src_h = crop[0], crop[1]  # fit decisions use what's left after the crop
        specs.append(SegmentSpec(
            clip=clip, overlay=ov, start=start, length=end - start,
            hold=style.end_hold_seconds if i == len(order) - 1 else 0.0,
            has_audio=info.has_audio,
            sfx=sfx,
            src_w=src_w, src_h=src_h, crop=crop,
            tx_in=style.transition != "none" and i > 0,
            tx_out=style.transition == "fade" and i < len(order) - 1,
        ))
    if sfx:
        place_sfx(specs, style, probe(sfx).duration)
    return specs


def transition_window(kind: str, seconds: float, prev_len: float, next_len: float) -> tuple[float, float]:
    """(start, end) of the visual transition around a cut, relative to the cut.

    Mirrors transition_graph(): effects play over the first d seconds of the incoming
    clip; "fade" dips to black over the last d/2 of the outgoing clip and the first d/2
    of the incoming one.
    """
    if kind == "none":
        return 0.0, 0.0
    if kind == "fade":
        return -min(seconds, prev_len / 2) / 2, min(seconds, next_len / 2) / 2
    return 0.0, min(seconds, next_len / 2)


def place_sfx(specs: list[SegmentSpec], style: Style, sound_len: float) -> None:
    """Fit the transition sound to each cut's transition: same start, same end.

    With no visual transition the whole sound plays from the cut.
    """
    for k in range(1, len(specs)):
        prev, nxt = specs[k - 1], specs[k]
        a, b = transition_window(style.transition, style.transition_seconds, prev.length, nxt.length)
        length = sound_len if b <= a else min(sound_len, b - a)
        trimmed = length < sound_len - 1e-3
        if a < 0:  # starts in the outgoing clip (dip to black)
            first = min(length, -a)
            prev.sfx_pieces.append(SfxPiece(0.0, first, prev.length + a, length, trimmed))
            if length > first:
                nxt.sfx_pieces.append(SfxPiece(first, length, 0.0, length, trimmed))
        else:
            nxt.sfx_pieces.append(SfxPiece(0.0, length, 0.0, length, trimmed))


def concat_segments(segments: list[Path], out: Path, work_dir: Path) -> None:
    list_file = work_dir / "list.txt"
    lines = []
    for s in segments:
        escaped = str(s.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n")
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0",
           "-i", str(list_file),
           # Video is copied (identical encodes, no quality loss). Audio is PCM in the segments
           # and encoded to AAC once here. (AAC segments carry encoder "priming" that shifted
           # the audio ~20-45 ms later at every cut: A/V drift and late transition sounds.)
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_RATE), "-ac", "2",
           "-movflags", "+faststart", str(out)]
    _run_ffmpeg(cmd, work_dir / "concat.log", 0)


def compose(project: Project, out_path: Path, work_dir: Path,
            progress: Progress | None = None, keep_work: bool = False) -> Path:
    """Render the whole project to ``out_path``. Clips must have ``local_path`` set."""
    report = progress or (lambda f, m: None)
    work_dir.mkdir(parents=True, exist_ok=True)
    report(0.0, "Rendering overlays")
    specs = plan_segments(project)
    n = len(specs)
    seg_paths = []
    for k, seg in enumerate(specs):
        msg = f"Segment {k + 1} of {n} (#{seg.clip.rank})"
        report(k / (n + 0.2), msg)
        out = work_dir / f"seg_{k:02d}.mov"
        _run_ffmpeg(
            segment_command(seg, project.style, out), work_dir / f"seg_{k:02d}.log", seg.duration,
            on_time=lambda f, k=k, msg=msg: report((k + f) / (n + 0.2), msg),
        )
        seg_paths.append(out)
    report(n / (n + 0.2), "Joining segments")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_name(out_path.stem + ".part.mp4")
    concat_segments(seg_paths, tmp_out, work_dir)
    tmp_out.replace(out_path)
    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    report(1.0, "Done")
    return out_path
