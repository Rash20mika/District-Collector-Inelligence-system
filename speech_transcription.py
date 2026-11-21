"""Speech-to-text support for Tamil and English voice questions."""

import requests

TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
TRANSCRIPTION_MODEL = "whisper-large-v3"
MAX_AUDIO_BYTES = 25_000_000


def transcribe_audio(audio_bytes: bytes, filename: str, content_type: str, api_key: str) -> str:
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set.")
    if not audio_bytes:
        raise ValueError("The recording is empty. Please record your question again.")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ValueError("The recording exceeds the 25 MB transcription limit.")

    response = requests.post(
        TRANSCRIPTION_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        files={"file": (filename, audio_bytes, content_type or "audio/wav")},
        data={"model": TRANSCRIPTION_MODEL, "response_format": "json"},
        timeout=(10, 120),
    )
    if response.status_code >= 400:
        try:
            error_result = response.json()
        except requests.exceptions.JSONDecodeError:
            error_result = {}
        detail = error_result.get("error", {}) if isinstance(error_result, dict) else {}
        if isinstance(detail, dict):
            detail = detail.get("message", "")
        if not isinstance(detail, str) or not detail.strip():
            detail = response.text.strip()[:300] or response.reason
        raise RuntimeError(
            f"Groq transcription failed (HTTP {response.status_code}): {detail}"
        )
    try:
        result = response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError("The transcription service returned an invalid response.") from exc

    if not isinstance(result, dict):
        raise RuntimeError("The transcription service returned an invalid response.")
    transcript = result.get("text")
    if not isinstance(transcript, str) or not transcript.strip():
        raise RuntimeError("No speech was recognized. Please try recording your question again.")
    return transcript.strip()
