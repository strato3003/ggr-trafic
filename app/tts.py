"""Synthèse vocale du bulletin (MP3) pour la carte son du PC shack → DATA TRX.

La lecture temps réel passe par le navigateur (SpeechSynthesis, voix OS).
Le MP3 utilise les voix neurales Microsoft (edge-tts). Pas d’espeak.
"""

from __future__ import annotations

VOICES: list[dict[str, str]] = [
    {"id": "fr-FR-DeniseNeural", "label": "Français — Denise", "lang": "fr"},
    {"id": "fr-FR-HenriNeural", "label": "Français — Henri", "lang": "fr"},
    {"id": "en-GB-SoniaNeural", "label": "English — Sonia", "lang": "en"},
    {"id": "en-GB-RyanNeural", "label": "English — Ryan", "lang": "en"},
]


def voices_for(lang: str) -> list[dict[str, str]]:
    want = "en" if str(lang).startswith("en") else "fr"
    rows = [row for row in VOICES if row["lang"] == want]
    return rows or list(VOICES)


def _voice_id(voice: str) -> str:
    ids = {row["id"] for row in VOICES}
    return voice if voice in ids else VOICES[0]["id"]


async def to_mp3(text: str, voice: str) -> bytes:
    """MP3 neural (edge-tts). Lève RuntimeError si le service est injoignable."""
    body = (text or "").strip()
    if not body:
        raise ValueError("Texte vide")
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts n’est pas installé") from exc
    communicate = edge_tts.Communicate(body[:12000], _voice_id(voice))
    chunks: list[bytes] = []
    try:
        async for item in communicate.stream():
            if item.get("type") == "audio":
                chunks.append(item["data"])
    except Exception as exc:
        raise RuntimeError("Voix neurale Microsoft indisponible") from exc
    if not chunks:
        raise RuntimeError("Aucun audio TTS")
    return b"".join(chunks)
