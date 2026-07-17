#!/usr/bin/env python3
"""Generate classification test fixtures.

Synthesizes short tone-per-channel PCM files (channel N carries a distinct
frequency so bed routing is audibly and measurably verifiable), then uses
`afconvert` to produce CAF variants carrying explicit Core Audio channel
layout tags. Untagged WAV variants exercise the channel-count fallback paths
of SAKSpatialAudioClassifier.

Run from this directory:  python3 Scripts/generate_fixtures.py
Requires macOS (afconvert, afinfo).
"""

import math
import os
import struct
import subprocess
import sys
import wave

SAMPLE_RATE = 48000
DURATION_SECONDS = 2.0
AMPLITUDE = 0.25
BASE_FREQUENCY_HZ = 220.0  # channel n plays 220 * 2^(n/12)

OUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (basename, channel count, afconvert layout tag or None for untagged WAV,
#  fragment expected in afinfo's human-readable "Channel layout:" line)
FIXTURES = [
    ("mono", 1, None, None),
    ("stereo", 2, None, None),
    ("untagged_4ch", 4, None, None),  # must classify as FOA (square-count heuristic)
    ("untagged_6ch", 6, None, None),
    ("untagged_8ch", 8, None, None),
    ("quad_tagged", 4, "Quadraphonic", "Quadraphonic"),
    ("51_tagged", 6, "MPEG_5_1_A", "5.1"),
    ("71_tagged", 8, "MPEG_7_1_C", "7.1"),
    ("octo_tagged", 8, "Octagonal", "Octagonal"),
    ("714_atmos_tagged", 12, "Atmos_7_1_4", "7.1.4"),
]


def tone_for_channel(channel_index: int, frame_index: int) -> float:
    frequency = BASE_FREQUENCY_HZ * (2.0 ** (channel_index / 12.0))
    return AMPLITUDE * math.sin(2.0 * math.pi * frequency * frame_index / SAMPLE_RATE)


def write_wav(path: str, channel_count: int) -> None:
    frame_count = int(SAMPLE_RATE * DURATION_SECONDS)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(channel_count)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for frame in range(frame_count):
            for channel in range(channel_count):
                sample = int(tone_for_channel(channel, frame) * 32767)
                frames += struct.pack("<h", sample)
        handle.writeframes(bytes(frames))


def verify_layout(path: str, expected_fragment: str) -> None:
    info = subprocess.run(
        ["afinfo", path], capture_output=True, text=True, check=True
    ).stdout
    layout_lines = [line for line in info.splitlines() if "Channel layout:" in line]
    if not any(expected_fragment in line for line in layout_lines):
        print(info)
        raise SystemExit(f"{path}: expected '{expected_fragment}' in afinfo channel layout")


def main() -> None:
    generated = []
    for basename, channel_count, layout_tag, expected_fragment in FIXTURES:
        if layout_tag is None:
            path = os.path.join(OUT_DIR, f"{basename}.wav")
            write_wav(path, channel_count)
        else:
            scratch = os.path.join(OUT_DIR, f".{basename}_scratch.wav")
            write_wav(scratch, channel_count)
            path = os.path.join(OUT_DIR, f"{basename}.caf")
            subprocess.run(
                ["afconvert", "-f", "caff", "-d", "LEI16", "-l", layout_tag, scratch, path],
                check=True,
            )
            os.remove(scratch)
            verify_layout(path, expected_fragment)
        generated.append(os.path.basename(path))
        print(f"wrote {path}")

    print(f"\n{len(generated)} fixtures generated: {', '.join(generated)}")


if __name__ == "__main__":
    sys.exit(main())
