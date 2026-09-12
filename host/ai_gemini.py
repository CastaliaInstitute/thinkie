"""Gemini STT / reply / TTS for the radio loop. Ported from the June channel-6 host script.

Credentials: GEMINI_API_KEY / GOOGLE_GEMINI_API_KEY / GOOGLE_AI_API_KEY / GCP_API_KEY from the
environment, host/.env, or ../castalia.institute/.env(.local); else the Supabase llm_secrets
table (SUPABASE_URL + SUPABASE_SERVICE_KEY).
"""
from __future__ import annotations
import base64, io, json, os, struct, wave
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

GEMINI_TEXT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
GEMINI_TTS_RATE = 24000
RADIO_RATE = 16000

RADIO_SYSTEM_PROMPT = (
    "You are an AI answering questions over an analog VHF walkie-talkie. The person speaking "
    "cannot see anything; they only hear you. Reply in one to three short spoken sentences, "
    "plain words, no markdown, no lists. Spell out numbers and abbreviations the way a radio "
    "operator would. If the transcript is garbled or empty, say briefly that you did not copy "
    "and ask them to repeat. End every reply with the word 'over'."
)

class CredentialError(RuntimeError):
    pass

# ---- env / credentials ------------------------------------------------------
def _load_env_file(path: Path) -> bool:
    if not path.exists():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip(); v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v
    return True

def load_env() -> list[Path]:
    here = Path(__file__).resolve().parent
    candidates = [here / ".env", here.parent.parent / "castalia.institute" / ".env.local",
                  here.parent.parent / "castalia.institute" / ".env"]
    loaded = [p for p in candidates if _load_env_file(p)]
    if os.getenv("NEXT_PUBLIC_SUPABASE_URL") and not os.getenv("SUPABASE_URL"):
        os.environ["SUPABASE_URL"] = os.environ["NEXT_PUBLIC_SUPABASE_URL"]
    if os.getenv("SUPABASE_SERVICE_ROLE_KEY") and not os.getenv("SUPABASE_SERVICE_KEY"):
        os.environ["SUPABASE_SERVICE_KEY"] = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return loaded

def http_json(url: str, body: dict | None, headers: dict[str, str], timeout: int = 60) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = Request(url, data=data, headers=headers, method="GET" if body is None else "POST")
    try:
        with urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode())
    except HTTPError as err:
        raise RuntimeError(f"HTTP {err.code} from {url.split('?')[0]}: {err.read().decode(errors='replace')[:400]}") from err

def resolve_api_key() -> tuple[str, str]:
    for name in ("GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY", "GOOGLE_AI_API_KEY", "GCP_API_KEY"):
        v = os.getenv(name, "").strip()
        if v:
            return v, name
    url = os.getenv("SUPABASE_URL", "").rstrip("/"); svc = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not svc:
        raise CredentialError("No Gemini key: set GEMINI_API_KEY (host/.env) or SUPABASE_URL + SUPABASE_SERVICE_KEY")
    table = os.getenv("SUPABASE_SECRET_TABLE", "llm_secrets")
    q = f"{url}/rest/v1/{quote(table)}?key=eq.{quote(os.getenv('SUPABASE_GEMINI_KEY_NAME', 'GEMINI_API_KEY'))}&select=value&limit=1"
    rows = http_json(q, None, {"apikey": svc, "Authorization": f"Bearer {svc}"})
    if not rows or not str(rows[0].get("value", "")).strip():
        raise CredentialError(f"Gemini key not found in Supabase table {table!r}")
    return rows[0]["value"].strip(), "supabase"

def _hdr(key: str) -> dict[str, str]:
    return {"Content-Type": "application/json", "x-goog-api-key": key}

def _gen(model: str, body: dict, key: str, timeout: int = 60) -> dict:
    return http_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body, _hdr(key), timeout)

def _text(payload: dict) -> str:
    for c in payload.get("candidates", []):
        return "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
    return ""

# ---- audio helpers ----------------------------------------------------------
def pcm16_to_wav(pcm: bytes, rate: int = RADIO_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return out.getvalue()

def resample_s16(pcm: bytes, src: int, dst: int) -> bytes:
    """Linear-interpolation resample of mono s16le."""
    n = len(pcm) // 2
    if n == 0 or src == dst:
        return pcm
    src_s = struct.unpack(f"<{n}h", pcm[: n * 2])
    m = int(n * dst / src)
    out = bytearray()
    for i in range(m):
        x = i * src / dst
        j = int(x); f = x - j
        a = src_s[j]; b = src_s[j + 1] if j + 1 < n else a
        out += struct.pack("<h", int(a + (b - a) * f))
    return bytes(out)

# ---- the three calls ----------------------------------------------------------
def transcribe(pcm16: bytes, key: str, rate: int = RADIO_RATE) -> str:
    body = {"contents": [{"parts": [
        {"text": "Transcribe the speech in this narrowband FM walkie-talkie audio. Output ONLY the spoken "
                 "words, nothing else. If there is no intelligible speech, output exactly: [no speech]"},
        {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(pcm16_to_wav(pcm16, rate)).decode()}},
    ]}]}
    out = _text(_gen(os.getenv("GEMINI_STT_MODEL", GEMINI_TEXT_MODEL), body, key)).strip().strip('"')
    return "" if "[no speech]" in out.lower() or out.lower().startswith("an empty string") else out

def reply(transcript: str, key: str, history: list[dict] | None = None, system: str = RADIO_SYSTEM_PROMPT) -> str:
    contents = list(history or []) + [{"role": "user", "parts": [{"text": transcript or "(nothing intelligible)"}]}]
    body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": contents}
    out = _text(_gen(os.getenv("GEMINI_REPLY_MODEL", GEMINI_TEXT_MODEL), body, key)).strip()
    if history is not None:
        history.append(contents[-1]); history.append({"role": "model", "parts": [{"text": out}]})
    return out

def synthesize(text: str, key: str, rate: int = RADIO_RATE) -> bytes:
    """Return mono s16le PCM at `rate`."""
    body = {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": os.getenv("GEMINI_TTS_VOICE", "Kore")}}}}}
    payload = _gen(GEMINI_TTS_MODEL, body, key, timeout=90)
    for c in payload.get("candidates", []):
        for p in c.get("content", {}).get("parts", []):
            inline = p.get("inlineData") or {}
            if inline.get("data"):
                mime = inline.get("mimeType", "")
                src_rate = GEMINI_TTS_RATE
                if "rate=" in mime:
                    src_rate = int(mime.split("rate=")[1].split(";")[0])
                return resample_s16(base64.b64decode(inline["data"]), src_rate, rate)
    raise RuntimeError(f"TTS returned no audio: {json.dumps(payload)[:300]}")

if __name__ == "__main__":
    import sys
    load_env()
    key, src = resolve_api_key()
    print(f"key from {src}")
    print("reply:", reply(sys.argv[1] if len(sys.argv) > 1 else "What is two plus two?", key))
