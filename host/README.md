# Host side

```sh
python3 -m venv .venv && .venv/bin/pip install pyserial
.venv/bin/python host/monitor.py A                    # status + level meter
.venv/bin/python host/monitor.py A --freq 162.550 --sq 1
.venv/bin/python host/monitor.py A --wav rx.wav --secs 30 --sql   # record while squelch open
```
- `thinkie_link.py` — framed protocol (mirror of `firmware/src/protocol.h`), background reader
- `monitor.py` — Stage 1 bench tool
- (next) `agent.py` — STT → LLM → TTS loop
