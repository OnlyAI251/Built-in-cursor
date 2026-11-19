"""FFmpeg-backed synthetic smoke test for the shorts media helper."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).parents[1] / "tools" / "shorts_media.py"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
class RenderSmokeTest(unittest.TestCase):
    def test_internal_cut_audio_and_caption_remapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "synthetic.mp4"
            # Generated color pattern plus continuous tone exercises video/audio routing without external media.
            subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                "testsrc2=size=640x360:rate=25:duration=8", "-f", "lavfi", "-i",
                "sine=frequency=880:sample_rate=48000:duration=8", "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)
            ], check=True)
            edl = root / "edl.json"
            edl.write_text(json.dumps({"segments": [{"start": 1, "end": 3}, {"start": 5, "end": 7}]}))
            captions = root / "source.srt"
            captions.write_text("1\n00:00:01,500 --> 00:00:02,000\nFirst cue\n\n2\n00:00:05,500 --> 00:00:06,000\nCue after cut\n", encoding="utf-8")
            output = root / "result.mp4"
            subprocess.run(["python3", str(TOOL), "render", str(source), "--edl", str(edl), "--out", str(output), "--audio-index", "0", "--srt", str(captions)], check=True)
            result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], check=True, capture_output=True, text=True)
            metadata = json.loads(result.stdout)
            video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1080, 1920))
            self.assertAlmostEqual(float(metadata["format"]["duration"]), 4, delta=0.75)
            sidecar = output.with_suffix(".srt").read_text(encoding="utf-8")
            self.assertIn("00:00:02,500 --> 00:00:03,000", sidecar)
            self.assertIn("Cue after cut", sidecar)
            # Decode final output and measure its audio: the synthetic tone must survive the render.
            measured = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-af", "volumedetect", "-f", "null", "-"], check=True, capture_output=True, text=True)
            self.assertIn("mean_volume", measured.stderr)
            self.assertNotIn("-inf dB", measured.stderr)


if __name__ == "__main__":
    unittest.main()
