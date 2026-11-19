#!/usr/bin/env python3
"""Inspect and render authorized local gaming clips using FFmpeg/ffprobe."""

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(args, *, capture=False):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def probe(path):
    result = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture=True)
    data = json.loads(result.stdout)
    streams = []
    for stream in data.get("streams", []):
        item = {key: stream.get(key) for key in ("index", "codec_type", "codec_name", "width", "height", "sample_rate", "channels", "channel_layout", "duration", "bit_rate") if key in stream}
        if stream.get("codec_type") == "video":
            item["avg_frame_rate"] = stream.get("avg_frame_rate")
            item["rotation"] = next((int(side.get("rotation", 0)) for side in stream.get("side_data_list", []) if "rotation" in side), 0)
        streams.append(item)
    fmt = data.get("format", {})
    return {"file": str(path), "duration": float(fmt.get("duration", 0)), "format": fmt.get("format_name"), "streams": streams}


def decode(path):
    run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])


def parse_rect(value, label):
    try:
        rect = tuple(int(part) for part in value.split(","))
        if len(rect) != 4 or rect[2] <= 0 or rect[3] <= 0 or rect[0] < 0 or rect[1] < 0:
            raise ValueError
        return rect
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be x,y,width,height with nonnegative origin and positive size") from exc


def read_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        left, right = (part.strip().split()[0] for part in lines[1].split("-->"))
        cues.append((time_seconds(left), time_seconds(right), "\n".join(lines[2:])))
    return cues


def time_seconds(value):
    hms, millis = value.replace(",", ".").split(".")
    hours, minutes, seconds = (int(x) for x in hms.split(":"))
    return hours * 3600 + minutes * 60 + seconds + int((millis + "000")[:3]) / 1000


def srt_time(value):
    ms = max(0, round(value * 1000))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def remap_srt(cues, segments):
    output, offset = [], 0.0
    for start, end in segments:
        for cue_start, cue_end, text in cues:
            left, right = max(start, cue_start), min(end, cue_end)
            if right > left:
                output.append((offset + left - start, offset + right - start, text))
        offset += end - start
    output.sort(key=lambda cue: cue[0])
    return "\n\n".join(f"{i}\n{srt_time(start)} --> {srt_time(end)}\n{text}" for i, (start, end, text) in enumerate(output, 1)) + ("\n" if output else "")


def load_segments(edl, duration):
    data = json.loads(Path(edl).read_text(encoding="utf-8"))
    segments = [(float(row["start"]), float(row["end"])) for row in data.get("segments", [])]
    if not segments:
        raise ValueError("EDL must contain a non-empty segments array")
    for start, end in segments:
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= duration):
            raise ValueError(f"Invalid source interval {start}–{end}; source duration is {duration:.3f}s")
    return segments


def render(args):
    info = probe(args.input)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audios = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if video is None:
        raise ValueError("Input has no video stream")
    if not args.no_audio and (args.audio_index is None or args.audio_index < 0 or args.audio_index >= len(audios)):
        raise ValueError(f"Select an existing audio ordinal with --audio-index (found {len(audios)} audio stream(s)); use --no-audio only for silent source")
    segments = load_segments(args.edl, info["duration"])
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    for label, rect in (("webcam", args.webcam), ("game", args.game)):
        if rect and (rect[0] + rect[2] > width or rect[1] + rect[3] > height):
            raise ValueError(f"{label} rectangle exceeds source dimensions {width}x{height}")
    if args.webcam and not args.game:
        raise ValueError("--webcam requires an explicit clean --game rectangle")
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".tmp.mp4", dir=str(output.parent))
    os.close(fd)
    os.unlink(temp_name)
    temp = Path(temp_name)
    temp_srt = None
    try:
        filters, concat_inputs = [], []
        for index, (start, end) in enumerate(segments):
            base = f"[0:v:0]trim=start={start}:end={end},setpts=PTS-STARTPTS"
            if args.webcam:
                x, y, w, h = args.webcam
                gx, gy, gw, gh = args.game
                filters.append(f"{base},split=2[vraw{index}][vrawg{index}];[vraw{index}]crop={w}:{h}:{x}:{y},scale=1080:640:force_original_aspect_ratio=decrease,pad=1080:640:(ow-iw)/2:(oh-ih)/2,setsar=1[cam{index}];[vrawg{index}]crop={gw}:{gh}:{gx}:{gy},scale=1080:1280:force_original_aspect_ratio=decrease,pad=1080:1280:(ow-iw)/2:(oh-ih)/2,setsar=1[game{index}];[cam{index}][game{index}]vstack=inputs=2[v{index}]")
            else:
                if args.game:
                    gx, gy, gw, gh = args.game
                    base += f",crop={gw}:{gh}:{gx}:{gy}"
                filters.append(f"{base},scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1[v{index}]")
            concat_inputs.append(f"[v{index}]")
            if not args.no_audio:
                filters.append(f"[0:a:{args.audio_index}]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample=48000[a{index}]")
                concat_inputs.append(f"[a{index}]")
        count = len(segments)
        concat = "".join(concat_inputs) + f"concat=n={count}:v=1:a={0 if args.no_audio else 1}[vcat]" + ("" if args.no_audio else "[acat]")
        filters.append(concat)
        if args.srt:
            mapped = remap_srt(read_srt(args.srt), segments)
            sidecar = output.with_suffix(".srt")
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            fd, side_name = tempfile.mkstemp(prefix=f".{sidecar.stem}.", suffix=".tmp.srt", dir=str(sidecar.parent))
            os.close(fd)
            temp_srt = Path(side_name)
            temp_srt.write_text(mapped, encoding="utf-8")
            escaped = str(temp_srt.resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
            filters.append(f"[vcat]subtitles=filename='{escaped}'[vout]")
            vmap = "[vout]"
        else:
            vmap = "[vcat]"
        command = ["ffmpeg", "-v", "error", "-y", "-i", str(args.input), "-filter_complex", ";".join(filters), "-map", vmap]
        if not args.no_audio:
            command += ["-map", "[acat]"]
        command += ["-c:v", "libx264", "-preset", args.preset, "-crf", "20"]
        if not args.no_audio:
            command += ["-c:a", "aac", "-b:a", "192k"]
        else:
            command += ["-an"]
        command += ["-movflags", "+faststart", str(temp)]
        run(command)
        decode(temp)
        rendered = probe(temp)
        out_video = next(s for s in rendered["streams"] if s.get("codec_type") == "video")
        expected = sum(end - start for start, end in segments)
        actual = rendered["duration"]
        if (out_video.get("width"), out_video.get("height")) != (1080, 1920):
            raise RuntimeError(f"Unexpected output dimensions: {out_video.get('width')}x{out_video.get('height')}")
        if abs(actual - expected) > max(0.75, expected * 0.02):
            raise RuntimeError(f"Unexpected output duration: {actual:.3f}s, expected about {expected:.3f}s")
        if not args.no_audio and not any(s.get("codec_type") == "audio" for s in rendered["streams"]):
            raise RuntimeError("Expected audio stream missing from rendered MP4")
        os.replace(temp, output)
        if temp_srt:
            os.replace(temp_srt, output.with_suffix(".srt"))
        print(json.dumps({"output": str(output), "duration": actual, "dimensions": "1080x1920", "segments": len(segments), "audio": "none" if args.no_audio else f"audio ordinal {args.audio_index}", "srt": str(output.with_suffix('.srt')) if args.srt else None}, indent=2))
    finally:
        for path in (temp, temp_srt):
            if path and path.exists():
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="Report media streams and metadata")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--decode", action="store_true", help="Decode the entire source to detect decode errors")
    render_parser = sub.add_parser("render", help="Render an EDL into a verified vertical MP4")
    render_parser.add_argument("input", type=Path)
    render_parser.add_argument("--edl", required=True, help="JSON file with a segments array of start/end source seconds")
    render_parser.add_argument("--out", required=True, type=Path)
    render_parser.add_argument("--webcam", type=lambda value: parse_rect(value, "webcam"))
    render_parser.add_argument("--game", type=lambda value: parse_rect(value, "game"))
    render_parser.add_argument("--audio-index", type=int, help="Ordinal among audio streams; choose an audible stream explicitly")
    render_parser.add_argument("--no-audio", action="store_true")
    render_parser.add_argument("--srt", help="Verified source-time SRT; remap and burn captions on final timeline")
    render_parser.add_argument("--preset", default="veryfast", choices=("ultrafast", "superfast", "veryfast", "faster", "fast", "medium"))
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            info = probe(args.input)
            if args.decode:
                decode(args.input)
                info["full_decode"] = "passed"
            print(json.dumps(info, indent=2))
        else:
            render(args)
    except (subprocess.CalledProcessError, OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"shorts_media: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
