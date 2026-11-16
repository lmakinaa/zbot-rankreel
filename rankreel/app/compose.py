"""ffmpeg: trim, scale/crop, overlay, normalize, concat.

Every segment is encoded with identical parameters so the final concat is a
stream copy (no re-encode).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
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
class SegmentSpec:
    clip: Clip
    overlay: Path
    start: float
    length: float  # clip footage length
    hold: float  # extra frozen-frame seconds at the end
    has_audio: bool
    sfx: Path | None
    tx_in: bool = False  # play the transition at the start of this segment
    tx_out: bool = False  # "fade" only: dip to black at the end of this segment

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
    sfx_in = None
    if seg.sfx:
        cmd += ["-i", str(seg.sfx)]
        sfx_in = f"[{next_input}:a]"
        next_input += 1

    if style.fit == "contain":
        fit = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
               f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={_hex_ff(style.background)}")
    else:
        fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    hold = f",tpad=stop_mode=clone:stop_duration={seg.hold:.3f}" if seg.hold > 0 else ""
    video = (f"[0:v]{fit},fps={fps},setsar=1[vfit];"
             f"{transition_graph(seg, style, '[vfit]', '[v]')};"
             f"[v][1:v]overlay=0:0:shortest=1,format=yuv420p{hold}[vout]")

    afmt = f"aformat=sample_fmts=fltp:sample_rates={AUDIO_RATE}:channel_layouts=stereo"
    chain = []
    if style.loudnorm and seg.has_audio:
        chain.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    chain += [f"aresample={AUDIO_RATE}", afmt]
    audio = f"{audio_in}{','.join(chain)}"
    if sfx_in:
        audio += (f"[amain];{sfx_in}{afmt},volume={style.sfx_volume:g}[sfx];"
                  f"[amain][sfx]amix=inputs=2:duration=first:dropout_transition=0:normalize=0")
    audio += ",apad[aout]"

    cmd += [
        "-filter_complex", f"{video};{audio}",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-r", str(fps),
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-video_track_timescale", str(fps * 512),
        "-c:a", "aac", "-b:a", "160k", "-ar", str(AUDIO_RATE), "-ac", "2",
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
        specs.append(SegmentSpec(
            clip=clip, overlay=ov, start=start, length=end - start,
            hold=style.end_hold_seconds if i == len(order) - 1 else 0.0,
            has_audio=info.has_audio,
            sfx=sfx if i > 0 else None,  # the sound marks each cut, in sync with the transition
            tx_in=style.transition != "none" and i > 0,
            tx_out=style.transition == "fade" and i < len(order) - 1,
        ))
    return specs


def concat_segments(segments: list[Path], out: Path, work_dir: Path) -> None:
    list_file = work_dir / "list.txt"
    lines = []
    for s in segments:
        escaped = str(s.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n")
    cmd = [ffmpeg_bin(), "-y", "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0",
           "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out)]
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
        out = work_dir / f"seg_{k:02d}.mp4"
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
