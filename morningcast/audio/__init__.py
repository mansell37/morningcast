"""Audio layer.

`AudioGenerator` is the swappable interface. We ship four backends:

- StubAudio: produces a silent/placeholder mp3 so the whole pipeline is testable
  with zero accounts. Default-safe.
- KokoroAudio: free, Apache-2.0, runs on CPU. Good default for the A9 Max.
- Dia2Audio: free, Nari Labs, purpose-built for two-speaker dialogue (best free
  podcast feel). Prefers a GPU.
- ElevenLabsAudio: paid, highest quality. Drop-in for later.

All produce a single mixed-down mp3 for a Script. NotebookLM-style backends can be
added behind this same interface if an API ever appears.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..config import settings
from ..models import Script


def _find_ffmpeg() -> str:
    """Locate the ffmpeg binary in a way that survives stale PATHs.

    On Windows the winget-installed ffmpeg lives outside the default PATH a
    process inherits unless the shell was reopened after install. On Railway
    the nixpacks-installed ffmpeg is on PATH. We check shutil.which first,
    then fall back to known install locations, then to the literal 'ffmpeg'
    so the error message still points at the real problem.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    fallbacks = [
        # Windows winget install
        os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
            r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            r"\ffmpeg-8.1.1-full_build\bin\ffmpeg.exe"
        ),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]
    for p in fallbacks:
        if p and Path(p).exists():
            return p
    return "ffmpeg"


_FFMPEG = _find_ffmpeg()


@runtime_checkable
class AudioGenerator(Protocol):
    name: str

    def render(self, script: Script, out_path: Path) -> Path:
        """Render the full script to a single mp3 at out_path."""
        ...


# --- helpers ---------------------------------------------------------------

def _concat_wavs_to_mp3(wav_paths: list[Path], out_path: Path) -> Path:
    """Concatenate wavs and encode to mp3 via ffmpeg (loudness-normalised).

    Falls back to a plain encode if the loudnorm filter aborts (can happen on
    pathological/near-silent input on some ffmpeg+LAME builds).
    """
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in wav_paths))
    base = [_FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    normalised = base + [
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-codec:a", "libmp3lame", "-q:a", "4", str(out_path),
    ]
    plain = base + ["-codec:a", "libmp3lame", "-q:a", "4", str(out_path)]
    result = subprocess.run(normalised, capture_output=True)
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        subprocess.run(plain, check=True, capture_output=True)
    list_file.unlink(missing_ok=True)
    return out_path


# --- backends --------------------------------------------------------------

class StubAudio:
    """Silent placeholder so the pipeline runs end-to-end with no TTS account."""

    name = "stub"

    def render(self, script: Script, out_path: Path) -> Path:
        # ~1s of near-silence per line, so duration roughly tracks script length.
        # NB: pure-zero PCM makes libmp3lame abort (calc_energy assertion), so we
        # write a tiny low-amplitude tone instead of literal silence.
        import math
        import struct

        seconds = max(2, len(script.lines))
        tmp = out_path.with_suffix(".wav")
        framerate = 22050
        frames = bytearray()
        for n in range(framerate * seconds):
            sample = int(8 * math.sin(2 * math.pi * 110 * n / framerate))  # ~inaudible
            frames += struct.pack("<h", sample)
        with wave.open(str(tmp), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(framerate)
            w.writeframes(bytes(frames))
        _concat_wavs_to_mp3([tmp], out_path)
        tmp.unlink(missing_ok=True)
        return out_path


# Curated subset of Kokoro's English voices shown in the settings dropdown.
# `b*` voices use British English (lang_code "b"), `a*` use American ("a").
# Keep entries grouped by accent then gender for a tidy dropdown.
KOKORO_VOICES: list[tuple[str, str]] = [
    ("bm_george",   "George — British male"),
    ("bm_lewis",    "Lewis — British male"),
    ("bm_fable",    "Fable — British male (storyteller)"),
    ("bm_daniel",   "Daniel — British male"),
    ("bf_emma",     "Emma — British female"),
    ("bf_isabella", "Isabella — British female"),
    ("bf_alice",    "Alice — British female"),
    ("bf_lily",     "Lily — British female"),
    ("am_michael",  "Michael — American male"),
    ("am_adam",     "Adam — American male"),
    ("am_eric",     "Eric — American male"),
    ("am_onyx",     "Onyx — American male (deep)"),
    ("af_heart",    "Heart — American female (warm)"),
    ("af_bella",    "Bella — American female"),
    ("af_nicole",   "Nicole — American female"),
    ("af_sky",      "Sky — American female"),
]


def _voice_lang(voice_id: str) -> str:
    """Kokoro requires a per-language pipeline. 'b*' = British, default American."""
    return "b" if voice_id.startswith("b") else "a"


class KokoroAudio:
    """Free CPU-friendly TTS. Voices configurable via the web UI (app_settings)."""

    name = "kokoro"

    DEFAULT_VOICE_A = "bm_george"
    DEFAULT_VOICE_B = "bf_emma"

    def __init__(self):
        from ..db import get_setting
        self.voice_a = get_setting("voice_a", self.DEFAULT_VOICE_A)
        self.voice_b = get_setting("voice_b", self.DEFAULT_VOICE_B)

    def render(self, script: Script, out_path: Path) -> Path:
        import soundfile as sf  # noqa
        from kokoro import KPipeline

        # Kokoro pipelines are per-language; cache one per accent we touch.
        pipelines: dict[str, "KPipeline"] = {}
        def _pipe(lang: str) -> "KPipeline":
            if lang not in pipelines:
                pipelines[lang] = KPipeline(lang_code=lang)
            return pipelines[lang]

        wavs: list[Path] = []
        for i, line in enumerate(script.lines):
            voice = self.voice_a if line.speaker == "A" else self.voice_b
            seg = out_path.parent / f"_seg_{i:03d}.wav"
            audio = None
            for _, _, audio in _pipe(_voice_lang(voice))(line.text, voice=voice):
                pass
            sf.write(str(seg), audio, 24000)
            wavs.append(seg)
        _concat_wavs_to_mp3(wavs, out_path)
        for w in wavs:
            w.unlink(missing_ok=True)
        return out_path


class Dia2Audio:
    """Free dialogue-native TTS (Nari Labs). Best free podcast feel; prefers GPU.

    Requires the dia package + weights. Uses [S1]/[S2] speaker tags natively, so
    we render the whole script in one pass rather than line-by-line.
    """

    name = "dia2"

    def render(self, script: Script, out_path: Path) -> Path:
        import soundfile as sf  # noqa
        from dia.model import Dia

        model = Dia.from_pretrained("nari-labs/Dia-1.6B")
        tagged = " ".join(
            f"[S1] {l.text}" if l.speaker == "A" else f"[S2] {l.text}"
            for l in script.lines
        )
        audio = model.generate(tagged)
        tmp = out_path.with_suffix(".wav")
        sf.write(str(tmp), audio, 44100)
        _concat_wavs_to_mp3([tmp], out_path)
        tmp.unlink(missing_ok=True)
        return out_path


class ElevenLabsAudio:
    """Paid, highest quality. Configured but optional."""

    name = "elevenlabs"

    VOICE_A = "Rachel"
    VOICE_B = "Adam"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.elevenlabs_api_key

    def render(self, script: Script, out_path: Path) -> Path:
        from elevenlabs import save
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=self.api_key)
        wavs: list[Path] = []
        for i, line in enumerate(script.lines):
            voice = self.VOICE_A if line.speaker == "A" else self.VOICE_B
            seg = out_path.parent / f"_seg_{i:03d}.mp3"
            audio = client.text_to_speech.convert(
                voice_id=voice, text=line.text, model_id="eleven_turbo_v2_5"
            )
            save(audio, str(seg))
            wavs.append(seg)
        _concat_wavs_to_mp3(wavs, out_path)
        for w in wavs:
            w.unlink(missing_ok=True)
        return out_path


_BACKENDS: dict[str, type] = {
    "stub": StubAudio,
    "kokoro": KokoroAudio,
    "dia2": Dia2Audio,
    "elevenlabs": ElevenLabsAudio,
}


def get_audio_generator(name: str | None = None) -> AudioGenerator:
    name = name or settings.audio_backend
    if name not in _BACKENDS:
        raise ValueError(f"Unknown audio backend '{name}'. Options: {list(_BACKENDS)}")
    return _BACKENDS[name]()
