#!/usr/bin/env python3
"""Host-side STT/TTS bridge for the T-TWR channel 6 firmware.

Loads Castalia credentials from ../castalia.institute/.env.local or .env, then:
  - transcribes received radio audio with Gemini audio understanding
  - synthesizes a short spoken reply with Gemini TTS
  - sends 8 kHz unsigned 8-bit PCM back to the board for RF transmit
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import wave

import serial


SAMPLE_RATE = 8000
GEMINI_TEXT_MODEL = "gemini-3.5-flash"
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TTS_SAMPLE_RATE = 24000


class CredentialError(RuntimeError):
    pass


def default_castalia_env_files() -> list[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    castalia = repo_root.parent / "castalia.institute"
    return [castalia / ".env.local", castalia / ".env"]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def load_castalia_env(extra_env_files: list[str]) -> list[Path]:
    loaded: list[Path] = []
    for raw in extra_env_files:
        path = Path(raw).expanduser().resolve()
        load_env_file(path)
        if path.exists():
            loaded.append(path)
    for path in default_castalia_env_files():
        load_env_file(path)
        if path.exists():
            loaded.append(path)

    if os.getenv("NEXT_PUBLIC_SUPABASE_URL") and not os.getenv("SUPABASE_URL"):
        os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
    if os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY") and not os.getenv("SUPABASE_ANON_KEY"):
        os.environ["SUPABASE_ANON_KEY"] = os.environ["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
    if os.getenv("SUPABASE_SERVICE_ROLE_KEY") and not os.getenv("SUPABASE_SERVICE_KEY"):
        os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return loaded


def http_json(url: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: int) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {err.code} from {url}: {detail}") from err


def resolve_gemini_api_key() -> tuple[str, str]:
    for key_name in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_AI_API_KEY"):
        value = os.getenv(key_name, "").strip()
        if value:
            return value, key_name

    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not supabase_url or not service_key:
        raise CredentialError(
            "No Gemini key found. Set GEMINI_API_KEY in castalia.institute/.env.local "
            "or provide SUPABASE_URL + SUPABASE_SERVICE_KEY for secret lookup."
        )

    table = os.getenv("SUPABASE_SECRET_TABLE", "llm_secrets")
    key_column = os.getenv("SUPABASE_SECRET_KEY_COLUMN", "key")
    value_column = os.getenv("SUPABASE_SECRET_VALUE_COLUMN", "value")
    gemini_key_name = os.getenv("SUPABASE_GEMINI_KEY_NAME", "GEMINI_API_KEY")
    timeout = int(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "30"))
    url = (
        f"{supabase_url}/rest/v1/{quote(table)}"
        f"?{quote(key_column)}=eq.{quote(gemini_key_name)}"
        f"&select={quote(value_column)}&limit=1"
    )
    payload = http_json(
        url,
        None,
        {
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
        },
        timeout,
    )
    if not payload:
        raise CredentialError(f"Gemini key not found in Supabase table {table!r}.")
    value = payload[0].get(value_column, "")
    if not isinstance(value, str) or not value.strip():
        raise CredentialError(f"Supabase table {table!r} returned an empty Gemini key.")
    return value.strip(), "supabase"


def pcm_u8_to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return out.getvalue()


def response_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return "\n".join(texts).strip()


def transcribe_wav(wav_bytes: bytes, api_key: str) -> str:
    prompt = (
        "Transcribe the speech in this narrowband walkie-talkie audio. "
        "Return only the spoken words. If there is no intelligible speech, return an empty string."
    )
    model = os.getenv("GEMINI_STT_MODEL", os.getenv("GEMINI_MODEL", GEMINI_TEXT_MODEL))
    timeout = int(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "30"))
    payload = http_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": base64.b64encode(wav_bytes).decode("ascii"),
                            }
                        },
                    ]
                }
            ]
        },
        {"Content-Type": "application/json", "x-goog-api-key": api_key},
        timeout,
    )
    return response_text(payload).strip().strip('"')


def gemini_reply(text: str, api_key: str) -> str:
    model = os.getenv("GEMINI_REPLY_MODEL", os.getenv("GEMINI_MODEL", GEMINI_TEXT_MODEL))
    timeout = int(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "30"))
    payload = http_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are replying over an analog walkie-talkie. "
                            "Be brief, clear, and conversational. Avoid markdown."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": text}]}],
        },
        {"Content-Type": "application/json", "x-goog-api-key": api_key},
        timeout,
    )
    return response_text(payload)


def extract_inline_audio(payload: dict[str, Any]) -> bytes:
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data") or {}
            data = inline.get("data")
            if isinstance(data, str) and data:
                return base64.b64decode(data)
    return b""


def pcm_s16le_24k_to_u8_8k(pcm: bytes) -> bytes:
    out = bytearray()
    frame_count = len(pcm) // 2
    for index in range(0, frame_count, GEMINI_TTS_SAMPLE_RATE // SAMPLE_RATE):
        sample = struct.unpack_from("<h", pcm, index * 2)[0]
        out.append(max(0, min(255, (sample + 32768) >> 8)))
    return bytes(out)


def synthesize_to_pcm_u8(text: str, api_key: str) -> bytes:
    model = os.getenv("GEMINI_TTS_MODEL", GEMINI_TTS_MODEL)
    voice = os.getenv("GEMINI_TTS_VOICE", "Kore")
    timeout = int(os.getenv("GEMINI_HTTP_TIMEOUT_SECONDS", "30"))
    payload = http_json(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        {
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice,
                        }
                    }
                },
            },
        },
        {"Content-Type": "application/json", "x-goog-api-key": api_key},
        timeout,
    )
    pcm_24k_s16 = extract_inline_audio(payload)
    return pcm_s16le_24k_to_u8_8k(pcm_24k_s16)


def read_audio_frame(port: serial.Serial) -> bytes | None:
    header = port.readline().decode("ascii", errors="ignore").strip()
    if header.startswith("READY") or header.startswith("ERR"):
        print(header, file=sys.stderr)
        return None
    if not header.startswith("AUD "):
        return None

    try:
        size = int(header.split()[1])
    except (IndexError, ValueError):
        return None

    return port.read(size)


def send_tx_audio(port: serial.Serial, pcm: bytes) -> None:
    if not pcm:
        return
    port.write(f"TX {len(pcm)}\n".encode("ascii"))
    port.write(pcm)
    port.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, help="Serial port, e.g. /dev/cu.usbmodem0007602232171")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--window-ms", type=int, default=1500)
    parser.add_argument("--env-file", action="append", default=[], help="Additional env file to load first")
    args = parser.parse_args()

    loaded_env = load_castalia_env(args.env_file)
    api_key, key_source = resolve_gemini_api_key()
    if loaded_env:
        loaded = ", ".join(str(path) for path in loaded_env)
        print(f"Loaded env files: {loaded}", file=sys.stderr)
    print(f"Gemini key source: {key_source}", file=sys.stderr)

    chunk_target = SAMPLE_RATE * args.window_ms // 1000
    buffered = bytearray()

    with serial.Serial(args.port, args.baud, timeout=2) as port:
        time.sleep(2)
        port.reset_input_buffer()
        print("Listening. Ctrl-C to stop.", file=sys.stderr)

        while True:
            frame = read_audio_frame(port)
            if not frame:
                continue
            buffered.extend(frame)
            if len(buffered) < chunk_target:
                continue

            wav_bytes = pcm_u8_to_wav(bytes(buffered))
            buffered.clear()

            text = transcribe_wav(wav_bytes, api_key)
            if not text:
                continue

            print(f"RX: {text}")
            reply = gemini_reply(text, api_key)
            if not reply:
                continue
            print(f"TX: {reply}")
            response_pcm = synthesize_to_pcm_u8(reply, api_key)
            send_tx_audio(port, response_pcm)


if __name__ == "__main__":
    raise SystemExit(main())
