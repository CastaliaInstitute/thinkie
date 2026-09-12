"""Serial link to the twr base-station firmware. Mirror of firmware/src/protocol.h.

frame := 0xAA 0x55 | type:u8 | len:u16le | payload | crc8(type,len,payload)
"""
from __future__ import annotations
import os, struct, subprocess, sys, threading, queue, time
from dataclasses import dataclass
import serial

SYNC = b"\xAA\x55"
T_AUDIO_RX, T_STATUS, T_LOG = 0x01, 0x02, 0x03
T_SET_FREQ, T_AUDIO_TX, T_PTT, T_TX_ARM, T_SPK, T_PING, T_GAIN, T_FLUSH = 0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17
T_CLIP_LOAD, T_CLIP_TX, T_CLIP_PLAY, T_CLIP_CLEAR = 0x18, 0x19, 0x1A, 0x1B
CLIP_RUNNING = 0xFFFF
TX_ARM_MAGIC = 0x54582D4F
AUDIO_RATE_HZ = 16000
AUDIO_FRAME_SAMPLES = 320
FRAME_BYTES = AUDIO_FRAME_SAMPLES * 2

_STATUS_FMT = "<BhHBBIIBBBIHHBBHBI"

def crc8(data: bytes, crc: int = 0) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def rx_frame(payload: bytes):
    """T_AUDIO_RX payload -> (frame_idx, sql, pcm16 bytes)."""
    return struct.unpack_from("<I", payload)[0], payload[4], payload[5:]

def frame(t: int, payload: bytes = b"") -> bytes:
    hdr = struct.pack("<BH", t, len(payload))
    return SYNC + hdr + payload + bytes([crc8(hdr + payload)])

def resolve_port(p: str) -> str:
    """'A' / 'B' -> /dev/cu.* via tools/ports.py (matched by MAC); paths pass through."""
    if p.startswith("/dev/"):
        return p
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "ports.py")
    return subprocess.check_output([sys.executable, tool, p], text=True).strip()

@dataclass
class Status:
    sql: int; rssi: int; batt_mv: int; tx: int; tx_enabled: int
    rx_hz: int; tx_hz: int; sq: int; band: int; hw_rev: int
    uptime_ms: int; adc_dc: int; dropped: int
    board_id: int; sink: int; play_queued: int; tx_build: int; rx_frames: int
    @classmethod
    def unpack(cls, b: bytes) -> "Status":
        return cls(*struct.unpack(_STATUS_FMT, b[:struct.calcsize(_STATUS_FMT)]))
    @property
    def band_name(self): return {1: "VHF", 2: "UHF"}.get(self.band, "?")
    @property
    def board(self): return chr(self.board_id) if self.board_id else "?"
    def __str__(self):
        return (f"[{self.board}] {self.band_name} rx={self.rx_hz/1e6:.4f} sq={self.sq} "
                f"SQL={'OPEN' if self.sql else '----'} rssi={self.rssi} batt={self.batt_mv}mV "
                f"{'TX! ' if self.tx else ''}{'armed ' if self.tx_enabled else ''}"
                f"sink={'mic' if self.sink else 'spk'} q={self.play_queued} drop={self.dropped}")

class ThinkieLink:
    """Background reader; frames land in .q as (type, payload). .status holds the latest Status."""
    def __init__(self, port: str, log=None):
        self.alias = None if port.startswith("/dev/") else port.upper()
        self.port = resolve_port(port)
        self.ser = serial.Serial(self.port, 115200, timeout=0.05)
        self.ser.dtr = True                         # firmware streams only when DTR set
        self.q: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=4000)
        self.status: Status | None = None
        self.bad_crc = 0
        self.log = log                              # optional callable for T_LOG lines
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._reader, daemon=True); self._t.start()

    def _reader(self):
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(4096)
            except (serial.SerialException, OSError, TypeError):
                time.sleep(0.2); continue          # port being reopened by send()/reconnect()
            if not chunk: continue
            buf += chunk
            while True:
                i = buf.find(SYNC)
                if i < 0: buf = buf[-1:] if buf and buf[-1] == 0xAA else bytearray(); break
                if i: del buf[:i]
                if len(buf) < 5: break
                t, n = struct.unpack_from("<BH", buf, 2)
                if n > 1024: del buf[:2]; continue
                if len(buf) < 6 + n: break
                payload = bytes(buf[5:5 + n])
                if crc8(buf[2:5 + n]) == buf[5 + n]:
                    self._last_rx = time.time()
                    if t == T_STATUS: self.status = Status.unpack(payload)
                    elif t == T_AUDIO_RX: self.last_rx_idx = struct.unpack_from("<I", payload)[0]
                    elif t == T_LOG and self.log: self.log(payload.decode("utf-8", "replace"))
                    try: self.q.put_nowait((t, payload))
                    except queue.Full: pass
                    del buf[:6 + n]
                else:
                    self.bad_crc += 1; del buf[:2]

    # ---- commands ----
    def send(self, t: int, payload: bytes = b""):
        with self._wlock:
            try:
                self.ser.write(frame(t, payload))
            except (serial.SerialException, OSError) as e:
                # USB CDC dropped (seen when a board keys up next to the cable). The firmware
                # unkeys itself when DTR goes away; we reopen and let the caller decide what to redo.
                self.reconnect(str(e))
                self.ser.write(frame(t, payload))

    def reconnect(self, why: str = ""):
        self.dropped = getattr(self, "dropped", 0) + 1
        for i in range(75):                      # up to ~15 s for re-enumeration (name may change)
            try:
                try: self.ser.close()
                except Exception: pass
                if self.alias:
                    try: self.port = resolve_port(self.alias)
                    except Exception: pass
                self.ser = serial.Serial(self.port, 115200, timeout=0.05); self.ser.dtr = True
                if self.log: self.log(f"(link) reopened {self.port} after: {why[:60]}")
                return
            except (serial.SerialException, OSError):
                time.sleep(0.2)
        raise RuntimeError(f"could not reopen {self.port}")

    def ping(self): self.send(T_PING)
    def set_freq(self, rx_hz: int, tx_hz: int | None = None, sq: int = 1, ctcss_rx: int = 0, ctcss_tx: int = 0):
        self.send(T_SET_FREQ, struct.pack("<IIBBB", int(rx_hz), int(tx_hz or rx_hz), sq, ctcss_rx, ctcss_tx))
    def speaker(self, route_esp: bool, volume: int = 4):
        self.send(T_SPK, struct.pack("<BB", 1 if route_esp else 0, volume))
    def gain(self, spk_pct: int = 100, mic_pct: int = 15):
        self.send(T_GAIN, struct.pack("<BB", spk_pct, mic_pct))
    def flush(self): self.send(T_FLUSH)
    def tx_arm(self, on: bool = True): self.send(T_TX_ARM, struct.pack("<I", TX_ARM_MAGIC if on else 0))
    def ptt(self, on: bool): self.send(T_PTT, bytes([1 if on else 0]))

    def play_pcm(self, pcm16: bytes, pace: bool = True):
        """Stream mono s16le @16k to the board's current sink. Paces to real time so the
        on-board 3 s queue never overflows; returns when the last frame has been *sent*."""
        pad = (-len(pcm16)) % FRAME_BYTES
        pcm16 += b"\x00" * pad
        n = len(pcm16) // FRAME_BYTES
        t0 = time.time()
        for i in range(n):
            self.send(T_AUDIO_TX, pcm16[i * FRAME_BYTES:(i + 1) * FRAME_BYTES])
            if pace and i >= 25:                           # keep ~0.5 s ahead of playback
                target = t0 + (i - 25) * 0.02
                d = target - time.time()
                if d > 0: time.sleep(d)
        return n * 0.02

    # ---- autonomous clip playback / transmit (survives a USB drop mid-clip) ----
    def clip_load(self, pcm16: bytes):
        self.send(T_CLIP_CLEAR)
        for i in range(0, len(pcm16), FRAME_BYTES):
            self.send(T_CLIP_LOAD, pcm16[i:i + FRAME_BYTES])
            if i % (FRAME_BYTES * 25) == 0: time.sleep(0.05)      # stay inside the board's RX buffer
        time.sleep(0.2)

    def clip_run(self, transmit: bool, expect_s: float) -> bool:
        """Start the on-board job and wait for it to finish. Tolerates the link dropping."""
        self.send(T_CLIP_TX if transmit else T_CLIP_PLAY)
        deadline = time.time() + expect_s + 4.0
        seen_running = False
        while time.time() < deadline:
            time.sleep(0.25)
            st = self.status
            if st and st.play_queued == CLIP_RUNNING: seen_running = True
            elif seen_running and st and st.play_queued != CLIP_RUNNING: return True
            if time.time() - getattr(self, "_last_rx", time.time()) > 2:      # link quiet: try pinging
                try: self.ping()
                except Exception: pass
        return seen_running

    def wait_caught_up(self, timeout: float = 20.0) -> bool:
        """After a transmission (and maybe a USB drop), wait until the audio stream has delivered
        every frame the board captured up to now. Frames are consumed by whoever reads .q, so we
        track the last idx seen by the reader thread."""
        self.ping(); time.sleep(0.3)
        target = self.status.rx_frames if self.status else 0
        t0 = time.time()
        while time.time() - t0 < timeout:
            if getattr(self, "last_rx_idx", -1) >= target - 2: return True
            time.sleep(0.1)
            if int((time.time() - t0) * 10) % 20 == 0:
                try: self.ping()
                except Exception: pass
        return False

    def wait_status(self, timeout: float = 2.0) -> Status | None:
        self.ping(); t0 = time.time(); before = self.status
        while time.time() - t0 < timeout:
            if self.status is not None and self.status is not before: return self.status
            time.sleep(0.02)
        return self.status

    def close(self):
        self._stop.set(); self._t.join(1); self.ser.close()
