# Gaming Shorts media workflow

This repository's helper uses Python 3 and the `ffmpeg`/`ffprobe` executables; no Python packages are needed. It provides local-file inspection and rendering. It does **not** download from YouTube/Twitch/Kick, detect highlights, identify a webcam, or transcribe speech: use permitted access and Claude's available video/transcription tools for those steps. If those tools are unavailable, ask for a local upload or verified transcript and do not claim visual/audio analysis. Confirm media rights before editing.

Run commands from the repository root.

## Inspect

```bash
python3 .claude/skills/viral-gaming-shorts/tools/shorts_media.py inspect /path/to/authorized-recording.mp4
```

The JSON report lists duration, dimensions, rotation, frame rate, stream types and indices, and audio stream metadata. Add `--decode` to test a complete decode. `ffprobe` metadata alone does not establish that an audio stream is audible; listen/check candidate streams or measure them before selecting. Rendering requires an explicit audio ordinal (`--audio-index`), preventing silent first-track defaults. Use `--no-audio` only when the source truly has no required sound. If recording has separate mic/game stems, determine whether there is already a full mix and choose either that mix or the stems; this helper selects one audio stream only, so mix stems in a deliberate audio workflow before rendering.

## Render one or more moments

Create a JSON edit decision list (EDL) with source seconds. Coordinates are **source pixels**, origin at the upper-left; `x,y,width,height`. Select stable webcam and gameplay regions by inspecting actual frames. For example, if the 1920×1080 source facecam occupies x=1400,y=20,w=480,h=300 and the relevant gameplay area is x=0,y=0,w=1400,h=1080:

```json
{
  "segments": [
    {"start": 63.2, "end": 77.8},
    {"start": 102.0, "end": 115.1}
  ]
}
```

Render with a verified audio stream (the audio ordinal is the order among audio streams, not its absolute ffprobe stream index):

```bash
python3 .claude/skills/viral-gaming-shorts/tools/shorts_media.py render /path/to/authorized-recording.mp4 --edl /tmp/short-edl.json --out ./shorts/clutch-play.mp4 --webcam 1400,20,480,300 --game 0,0,1400,1080 --audio-index 0 --srt /tmp/verified-source-captions.srt
```

For no reliable facecam, omit `--webcam`; gameplay fills the vertical frame fit-first with padding as required. Set `--game` to the clean gameplay region; if the facecam is baked into the game image and cannot be removed without losing useful action, choose a wider crop or avoid duplicating it. Don't use detector guesses as crop truth. The helper uses a fixed region for all EDL segments; use separate renders when the webcam moves or disappears and report the layout change.

The helper renders 1080×1920 H.264/AAC. Webcam split defaults to 640 px high on top and gameplay 1280 px below; source aspect is preserved and padded, never stretched. Gameplay-only uses the full output. EDL segments are joined in order. With `--srt`, cues are intersected with each source segment and shifted onto the concatenated output timeline; gaps are removed. It writes the remapped UTF-8 SRT alongside the MP4 and burns the same cues into the video. Supply verified source-time SRT cues; verify captions after internal cuts. The helper cannot create a transcript. It selects only one audio stream and does not add music; use cleared music only in an explicitly reviewed mix. If using music added on YouTube, deliver the clean export and tell the user to add the cleared track there.

The renderer writes a temporary MP4, verifies expected dimensions and approximate EDL duration, decodes the file, and then renames it to the requested output. A failed render does not leave a finished-named MP4. It does not assert that audio is audible or captions are semantically correct; perform the human/listening checks in SKILL.md. On a batch, render each independently so a failure does not invalidate completed clips.

## Regression/smoke checks

```bash
python3 -m unittest discover -s .claude/skills/viral-gaming-shorts/tests -v
```

The smoke test generates owned synthetic test patterns and tones with FFmpeg, renders an internal-cut EDL and known captions, and checks MP4 decode/dimensions/duration, tone presence, and SRT remapping. It does not test speech recognition accuracy, real facecam detection, game-specific crop quality, music licensing, or platform upload. The generated synthetic fixture has no actual streamer/gameplay; inspect real source frames and listen to real footage before publishing.

If tests are unavailable, report the exact missing executable or failing command. Do not report a check as passed unless it was actually run.
