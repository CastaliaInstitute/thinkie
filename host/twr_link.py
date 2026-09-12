"""Serial link to the twr base-station firmware. Mirror of firmware/src/protocol.h.

frame := 0xAA 0x55 | type:u8 | len:u16le | payload | crc8(type,len,payload)
"""
from __future__ import annotations
import struct, threading, queue, time
from dataclasses import dataclass
import serial

SYNC = b"\xAA\x55"
T_AUDIO_RX, T_STATUS, T_LOG = 0x01, 0x02, 0x03
T_SET_FREQ, T_AUDIO_TX, T_PTT, T_TX_ARM, T_SPK, T_PING = 0x10, 0x11, 0x12, 0x13, 0x14, 0x15
AUDIO_RATE_HZ = 16000
AUDIO_FRAME_SAMPLES = 320

_STATUS_FMT = "<BhHBBIIBBBIHH"

def crc8(data: bytes, crc: int = 0) -> int:
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def frame(t: int, payload: bytes = b"") -> bytes:
    hdr = struct.pack("<BH", t, len(payload))
    return SYNC + hdr + payload + bytes([crc8(hdr + payload)])

@dataclass
class Status:
    sql: int; rssi: int; batt_mv: int; tx: int; tx_enabled: int
    rx_hz: int; tx_hz: int; sq: int; band: int; hw_rev: int
    uptime_ms: int; adc_dc: int; dropped: int
    @classmethod
    def unpack(cls, b: bytes) -> "Status":
        return cls(*struct.unpack(_STATUS_FMT, b[:struct.calcsize(_STATUS_FMT)]))
    @property
    def band_name(self): return {1: "VHF", 2: "UHF"}.get(self.band, "?")

class TwrLink:
    """Background reader; frames land in .q as (type, payload) tuples."""
    def __init__(self, port: str):
        self.ser = serial.Serial(port, 115200, timeout=0.05)
        self.ser.dtr = True                         # firmware streams only when DTR set
        self.q: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=2000)
        self.bad_crc = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._reader, daemon=True); self._t.start()

    def _reader(self):
        buf = bytearray()
        while not self._stop.is_set():
            chunk = self.ser.read(4096)
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
                    try: self.q.put_nowait((t, payload))
                    except queue.Full: pass
                    del buf[:6 + n]
                else:
                    self.bad_crc += 1; del buf[:2]

    def send(self, t: int, payload: bytes = b""):
        self.ser.write(frame(t, payload))

    def ping(self): self.send(T_PING)
    def set_freq(self, rx_hz: int, tx_hz: int | None = None, sq: int = 1, ctcss_rx: int = 0, ctcss_tx: int = 0):
        self.send(T_SET_FREQ, struct.pack("<IIBBB", rx_hz, tx_hz or rx_hz, sq, ctcss_rx, ctcss_tx))
    def speaker(self, route_esp: bool, volume: int):
        self.send(T_SPK, struct.pack("<BB", 1 if route_esp else 0, volume))

    def close(self):
        self._stop.set(); self._t.join(1); self.ser.close()
