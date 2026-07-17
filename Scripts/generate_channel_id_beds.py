#!/usr/bin/env python3
"""Generate spoken channel-identification files for channel-bed layouts.

Each bed gets a file where the channels announce themselves in sequence
("front left", "centre", ...) using macOS text-to-speech; the LFE slot plays a
low tone burst instead (speech would not survive bass management). Channel
orders follow the Core Audio layout tags written into the files, so a
mis-routed channel is immediately audible.

Outputs (per bed, per requested format) land in --out (default: ./ChannelID,
which is gitignored — these files are meant to be generated on the fly):
  caf   PCM with the bed's Core Audio layout tag (afconvert)
  m4a   AAC with the same layout tag (afconvert; skipped if the encoder
        rejects the layout)
  opus  via ffmpeg/libopus (skipped if ffmpeg is missing). Beds with more
        than 8 channels use mapping family 255 (discrete, file order).

Usage:
  python3 Scripts/generate_channel_id_beds.py [--out DIR]
      [--beds quad,5.1,7.1,...|all] [--formats caf,m4a,opus]

Requires macOS (say, afconvert, afinfo); opus additionally needs ffmpeg.
"""

import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave

SAMPLE_RATE = 48000
SLOT_SECONDS = 2.0       # each channel's announcement slot
GAP_SECONDS = 0.6        # silence between slots
LFE_TONE_HZ = 80.0
LFE_TONE_SECONDS = 1.0
SPEECH_GAIN = 0.9

# Channel labels in the order of the Core Audio layout tag written to the file.
BEDS = {
    "quad": ("Quadraphonic",
             ["front left", "front right", "rear left", "rear right"]),
    "octo": ("Octagonal",
             ["front left", "front right", "rear left", "rear right",
              "centre", "rear centre", "side left", "side right"]),
    "5.1": ("MPEG_5_1_A",
            ["left", "right", "centre", None, "left surround", "right surround"]),
    "7.1": ("MPEG_7_1_C",
            ["left", "right", "centre", None, "side left", "side right",
             "rear left", "rear right"]),
    "5.1.2": ("Atmos_5_1_2",
              ["left", "right", "centre", None, "left surround", "right surround",
               "top middle left", "top middle right"]),
    "5.1.4": ("Atmos_5_1_4",
              ["left", "right", "centre", None, "left surround", "right surround",
               "top front left", "top front right", "top rear left", "top rear right"]),
    "7.1.2": ("Atmos_7_1_2",
              ["left", "right", "centre", None, "side left", "side right",
               "rear left", "rear right", "top middle left", "top middle right"]),
    "7.1.4": ("Atmos_7_1_4",
              ["left", "right", "centre", None, "side left", "side right",
               "rear left", "rear right", "top front left", "top front right",
               "top rear left", "top rear right"]),
    "9.1.6": ("Atmos_9_1_6",
              ["left", "right", "centre", None, "side left", "side right",
               "rear left", "rear right", "wide left", "wide right",
               "top front left", "top front right", "top middle left",
               "top middle right", "top rear left", "top rear right"]),
}
# None marks the LFE slot (tone burst instead of speech).

# Apple's AAC encoder only accepts its transport-order layouts; beds without
# one (the Atmos family) have no plain AAC-LC representation and are skipped
# for m4a. The intermediate re-tag also remaps channel order.
AAC_TAGS = {
    "quad": "AAC_Quadraphonic",
    "octo": "AAC_Octagonal",
    "5.1": "MPEG_5_1_D",
    "7.1": "AAC_7_1",
}


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def speak_to_samples(text, scratch_dir):
    """Render text with `say` and return mono 16-bit samples at SAMPLE_RATE."""
    aiff = os.path.join(scratch_dir, "slot.aiff")
    wav = os.path.join(scratch_dir, "slot.wav")
    run(["say", "-o", aiff, text])
    run(["afconvert", "-f", "WAVE", "-d", "LEI16@%d" % SAMPLE_RATE,
         "-c", "1", aiff, wav])
    with wave.open(wav, "rb") as handle:
        raw = handle.readframes(handle.getnframes())
    return list(struct.unpack("<%dh" % (len(raw) // 2), raw))


def lfe_tone_samples():
    frames = int(SAMPLE_RATE * LFE_TONE_SECONDS)
    fade = int(SAMPLE_RATE * 0.05)
    samples = []
    for n in range(frames):
        envelope = min(1.0, n / fade, (frames - n) / fade)
        value = 0.5 * envelope * math.sin(2.0 * math.pi * LFE_TONE_HZ * n / SAMPLE_RATE)
        samples.append(int(value * 32767))
    return samples


def build_multichannel_wav(labels, path, scratch_dir):
    slot_frames = int(SAMPLE_RATE * SLOT_SECONDS)
    gap_frames = int(SAMPLE_RATE * GAP_SECONDS)
    channel_count = len(labels)
    total_frames = channel_count * (slot_frames + gap_frames)

    slots = []
    for index, label in enumerate(labels):
        if label is None:
            samples = lfe_tone_samples()
        else:
            # The channel number makes each announcement unique by ear —
            # several labels share words ("left" appears in five 7.1.4
            # channels) and a mis-routed channel is obvious from the number.
            samples = speak_to_samples(f"channel {index + 1}: {label}", scratch_dir)
            peak = max(1, max(abs(s) for s in samples))
            scale = SPEECH_GAIN * 32767 / peak
            samples = [int(s * scale) for s in samples]
        slots.append(samples[:slot_frames])

    frames = bytearray(total_frames * channel_count * 2)
    for channel, samples in enumerate(slots):
        slot_start = channel * (slot_frames + gap_frames)
        for i, sample in enumerate(samples):
            frame_index = slot_start + i
            offset = (frame_index * channel_count + channel) * 2
            struct.pack_into("<h", frames, offset, sample)

    with wave.open(path, "wb") as handle:
        handle.setnchannels(channel_count)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(bytes(frames))


def verify_layout(path, tag):
    info = run(["afinfo", path]).stdout
    if "Channel layout:" not in info:
        raise SystemExit(f"{path}: no channel layout written (wanted {tag})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="ChannelID")
    parser.add_argument("--beds", default="all")
    parser.add_argument("--formats", default="caf,m4a,opus")
    args = parser.parse_args()

    beds = list(BEDS) if args.beds == "all" else args.beds.split(",")
    formats = args.formats.split(",")
    ffmpeg = shutil.which("ffmpeg")
    os.makedirs(args.out, exist_ok=True)

    generated, skipped = [], []
    with tempfile.TemporaryDirectory() as scratch:
        for bed in beds:
            if bed not in BEDS:
                raise SystemExit(f"unknown bed '{bed}' (choose from {', '.join(BEDS)})")
            tag, labels = BEDS[bed]
            base = bed.replace(".", "") + "_channelID"
            plain_wav = os.path.join(scratch, base + ".wav")
            build_multichannel_wav(labels, plain_wav, scratch)

            caf = os.path.join(args.out, base + ".caf")
            run(["afconvert", "-f", "caff", "-d", "LEI16", "-l", tag, plain_wav, caf])
            verify_layout(caf, tag)
            if "caf" in formats:
                generated.append(caf)

            if "m4a" in formats:
                m4a = os.path.join(args.out, base + ".m4a")
                aac_tag = AAC_TAGS.get(bed)
                if aac_tag is None:
                    skipped.append(f"{base}.m4a (no AAC-LC layout for {bed})")
                else:
                    try:
                        aac_caf = os.path.join(scratch, base + "_aacorder.caf")
                        run(["afconvert", "-f", "caff", "-d", "LEI16", "-l", aac_tag, caf, aac_caf])
                        run(["afconvert", "-f", "m4af", "-d", "aac", aac_caf, m4a])
                        verify_layout(m4a, aac_tag)
                        generated.append(m4a)
                    except subprocess.CalledProcessError:
                        skipped.append(f"{base}.m4a (AAC encoder rejected {aac_tag})")

            if "opus" in formats:
                opus = os.path.join(args.out, base + ".opus")
                if ffmpeg is None:
                    skipped.append(f"{base}.opus (ffmpeg not installed)")
                elif bed == "octo":
                    # Opus has no octagonal layout, and a discrete (family
                    # 255) encode would decode as a 7.1 guess — skip rather
                    # than produce a mislabelled file.
                    skipped.append(f"{base}.opus (opus cannot carry octagonal semantics)")
                else:
                    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", caf,
                           "-c:a", "libopus", "-b:a", "%dk" % (64 * len(labels))]
                    if len(labels) > 8:
                        # No Vorbis layout above 8 channels: discrete family
                        # 255 keeps the file's channel order.
                        cmd += ["-mapping_family", "255"]
                    elif len(labels) == 6:
                        # Core Audio 5.1 reads as 5.1(side); libopus wants
                        # back-channel 5.1.
                        cmd += ["-af", "aformat=channel_layouts=5.1"]
                    elif len(labels) == 8:
                        cmd += ["-af", "aformat=channel_layouts=7.1"]
                    cmd.append(opus)
                    try:
                        run(cmd)
                        generated.append(opus)
                    except subprocess.CalledProcessError as error:
                        skipped.append(f"{base}.opus (ffmpeg: {error.stderr.strip().splitlines()[-1] if error.stderr else 'failed'})")

            if "caf" not in formats:
                os.remove(caf)

    print(f"{len(generated)} files in {args.out}:")
    for path in generated:
        print("  " + os.path.basename(path))
    if skipped:
        print("skipped:")
        for reason in skipped:
            print("  " + reason)


if __name__ == "__main__":
    sys.exit(main())
