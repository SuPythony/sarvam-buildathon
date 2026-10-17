"""Narrate feedback with Bulbul v4 Flash."""
import os
import re

import requests

TTS_URL = "https://api.sarvam.ai/text-to-speech"
MODEL = "bulbul:v4-flash"
MAX_CHARS = 2400  # API limit is 2500

# Curated narration / edtech personas. v4-flash has no Malayalam or Odia voices.
VOICES = {
    "en-IN": ["shubh_en_narration_gentle", "simran_en_edtech", "aparna_en_edtech",
              "kalpit_en_edtech", "sarang_en_narration"],
    "hi-IN": ["ishita_hi_edtech", "shruti_hi_edtech", "ritu_hi_edtech",
              "simran_hi_narration", "roopa_hi_narration"],
    "mr-IN": ["ritu_mr_narration", "soham_mr_narration", "neha_mr_narration"],
    "kn-IN": ["chaitra_kn_narration", "suchitra_kn_narration"],
    "pa-IN": ["harpreet_pa_narration"],
    "ta-IN": ["gokul_ta_narration", "vijay_ta_narration"],
    "te-IN": ["kavitha_te_narration", "tarun_te_narration"],
    "gu-IN": ["pooja_gu_conversational", "bhavik_gu_conversation"],
    "bn-IN": ["arnab_bn_conversation", "roopa_bn_conversational"],
}


class TTSError(RuntimeError):
    pass


def _chunks(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?।])\s+", text.strip())
    out, cur = [], ""
    for s in sentences:
        while len(s) > MAX_CHARS:  # pathological long sentence
            out.append(s[:MAX_CHARS])
            s = s[MAX_CHARS:]
        if len(cur) + len(s) + 1 > MAX_CHARS:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def narrate(text: str, language: str, speaker: str | None = None, pace: float = 1.0) -> list[str]:
    """Return base64 WAV clips, in order."""
    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        raise TTSError("SARVAM_API_KEY not set")
    if language not in VOICES:
        raise TTSError(f"Bulbul v4 Flash has no voices for {language}")
    speaker = speaker if speaker in VOICES[language] else VOICES[language][0]
    clips = []
    for chunk in _chunks(text):
        r = requests.post(
            TTS_URL,
            headers={"api-subscription-key": key, "Content-Type": "application/json"},
            json={"text": chunk, "language_code": language, "model": MODEL, "speaker": speaker,
                  "pace": pace, "speech_sample_rate": 24000, "output_audio_codec": "wav"},
            timeout=120,
        )
        if not r.ok:
            raise TTSError(f"bulbul {r.status_code}: {r.text}")
        clips.extend(r.json()["audios"])
    return clips
