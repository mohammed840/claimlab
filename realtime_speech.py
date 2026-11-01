"""Small synchronous client for prerecorded Realtime speech output."""

import base64
import json
import re
import time


MODEL = "gpt-realtime-2.1-mini"
URL = "wss://api.openai.com/v1/realtime?model=" + MODEL
SAMPLE_RATE = 24000
DEFAULT_TIMEOUT = 90


def _connect(api_key, timeout):
    import websocket

    return websocket.create_connection(
        URL,
        header=["Authorization: Bearer " + api_key],
        timeout=min(timeout, 10),
    )


def _next_event(socket, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Realtime speech exceeded its time limit")
    socket.settimeout(remaining)
    try:
        value = json.loads(socket.recv())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Realtime speech returned an invalid event") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Realtime speech returned an invalid event")
    if value.get("type") == "error":
        error = value.get("error") or {}
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError("Realtime speech error: " + str(message or "unknown error"))
    return value


def _wait_for(socket, event_type, deadline):
    while True:
        event = _next_event(socket, deadline)
        if event.get("type") == event_type:
            return event


def _normalized_words(text):
    return " ".join(re.findall(r"\w+", text.casefold(), flags=re.UNICODE))


def _diagnostic_repr(text):
    compact = " ".join(text.split())
    return repr(compact[:220] + ("…" if len(compact) > 220 else ""))


def synthesize(text, voice, instructions, api_key, timeout=DEFAULT_TIMEOUT):
    """Return mono PCM16 audio and its spoken transcript, without retrying."""
    deadline = time.monotonic() + timeout
    socket = _connect(api_key, timeout)
    try:
        _wait_for(socket, "session.created", deadline)
        socket.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": instructions,
                "audio": {"output": {
                    "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                    "voice": voice,
                }},
            },
        }, ensure_ascii=False))
        _wait_for(socket, "session.updated", deadline)
        socket.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        }, ensure_ascii=False))
        socket.send(json.dumps({"type": "response.create", "response": {"output_modalities": ["audio"]}}))

        audio, transcript = bytearray(), None
        while True:
            event = _next_event(socket, deadline)
            kind = event.get("type")
            if kind == "response.output_audio.delta":
                audio.extend(base64.b64decode(event.get("delta", ""), validate=True))
            elif kind == "response.output_audio_transcript.done":
                transcript = event.get("transcript")
            elif kind == "response.done":
                if event.get("response", {}).get("status") != "completed":
                    raise RuntimeError("Realtime speech response did not complete")
                break

        if len(audio) == 0 or len(audio) % 2:
            raise RuntimeError("Realtime speech returned invalid PCM audio")
        if not isinstance(transcript, str) or not transcript.strip():
            raise RuntimeError("Realtime speech returned no transcript")
        if _normalized_words(transcript) != _normalized_words(text):
            raise RuntimeError(
                "Realtime speech transcript differs from the supplied script "
                f"(expected={_diagnostic_repr(text)}, actual={_diagnostic_repr(transcript)})"
            )
        return bytes(audio), transcript
    finally:
        socket.close()
