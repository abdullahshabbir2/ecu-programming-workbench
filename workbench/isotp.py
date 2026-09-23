"""Classical CAN, normal addressing ISO-TP subset with an injected clock.

No hardware adapter is included. Maximum PDU 4095 bytes; milliseconds only.
Frames are fixed to eight bytes with zero padding. No CAN FD or functional addressing.
"""
from collections import deque
from dataclasses import dataclass


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanFrame:
    identifier: int
    data: bytes


class Endpoint:
    def __init__(self, tx_id, rx_id, *, block_size=8, stmin=1, timeout_ms=100, max_wait=3):
        if not (0 <= tx_id <= 0x7ff and 0 <= rx_id <= 0x7ff and tx_id != rx_id):
            raise ValueError("distinct standard identifiers required")
        if not 0 <= block_size <= 255 or not 0 <= stmin <= 127 or timeout_ms <= 0:
            raise ValueError("invalid transport timing")
        self.tx_id, self.rx_id = tx_id, rx_id
        self.block_size, self.stmin, self.timeout_ms, self.max_wait = block_size, stmin, timeout_ms, max_wait
        self.outbox, self.messages = deque(), deque()
        self.tx = None
        self.rx = None
        self.last_now = 0

    @property
    def busy(self):
        return self.tx is not None

    def reset(self):
        self.tx = self.rx = None
        self.outbox.clear()
        self.messages.clear()

    def _emit(self, payload):
        self.outbox.append(CanFrame(self.tx_id, payload.ljust(8, b"\x00")))

    def _clock(self, now):
        if now < self.last_now:
            raise ValueError("transport clock moved backwards")
        self.last_now = now

    def send(self, payload, now):
        self._clock(now)
        if self.busy:
            raise TransportError("transmit already active")
        if not isinstance(payload, bytes) or not 1 <= len(payload) <= 4095:
            raise ValueError("PDU must contain 1..4095 bytes")
        if len(payload) <= 7:
            self._emit(bytes([len(payload)])+payload)
            return
        self._emit(bytes([0x10 | (len(payload) >> 8), len(payload) & 255])+payload[:6])
        self.tx = dict(payload=payload, offset=6, sequence=1, waiting=True,
                       deadline=now+self.timeout_ms, credit=0, separation=0, next_at=now, waits=0)

    def _fail(self, message):
        self.tx = self.rx = None
        self.outbox.clear()
        raise TransportError(message)

    def receive(self, frame, now):
        self._clock(now)
        if frame.identifier != self.rx_id:
            return
        if len(frame.data) != 8:
            self._fail("classical padded CAN frame must have eight bytes")
        b = frame.data
        kind = b[0] >> 4
        if kind == 0:
            length = b[0] & 15
            if not 1 <= length <= 7 or self.rx is not None:
                self._fail("invalid or interleaved single frame")
            self.messages.append(b[1:1+length])
        elif kind == 1:
            length = ((b[0] & 15) << 8) | b[1]
            if length <= 7 or self.rx is not None:
                self._fail("invalid or interleaved first frame")
            self.rx = dict(length=length, data=bytearray(b[2:]), sequence=1,
                           count=0, deadline=now+self.timeout_ms)
            self._emit(bytes([0x30, self.block_size, self.stmin]))
        elif kind == 2:
            r = self.rx
            if r is None or (b[0] & 15) != r["sequence"]:
                self._fail("unexpected consecutive-frame sequence")
            if now >= r["deadline"]:
                self._fail("consecutive frame arrived after deadline")
            remaining = r["length"]-len(r["data"])
            r["data"].extend(b[1:1+min(remaining,7)])
            r["sequence"] = (r["sequence"]+1) & 15
            r["count"] += 1
            r["deadline"] = now+self.timeout_ms
            if len(r["data"]) == r["length"]:
                self.messages.append(bytes(r["data"]))
                self.rx = None
            elif self.block_size and r["count"] == self.block_size:
                r["count"] = 0
                self._emit(bytes([0x30, self.block_size, self.stmin]))
        elif kind == 3:
            t = self.tx
            if t is None or not t["waiting"]:
                self._fail("unsolicited flow control")
            if now >= t["deadline"]:
                self._fail("flow control arrived after deadline")
            status = b[0] & 15
            if status == 1:
                t["waits"] += 1
                if t["waits"] > self.max_wait:
                    self._fail("flow-control WAIT limit exceeded")
                t["deadline"] = now+self.timeout_ms
            elif status == 2:
                self._fail("receiver overflow")
            elif status == 0:
                if b[2] > 127:
                    self._fail("sub-millisecond/reserved STmin unsupported by this clock")
                t.update(waiting=False, credit=b[1] or 65536, separation=b[2], next_at=now+b[2])
            else:
                self._fail("invalid flow-control status")
        else:
            self._fail("unknown PCI type")

    def tick(self, now):
        self._clock(now)
        if self.rx is not None and now >= self.rx["deadline"]:
            self._fail("consecutive-frame timeout")
        t = self.tx
        if t is None:
            return
        if t["waiting"]:
            if now >= t["deadline"]:
                self._fail("flow-control timeout")
            return
        if now < t["next_at"]:
            return
        piece = t["payload"][t["offset"]:t["offset"]+7]
        self._emit(bytes([0x20 | t["sequence"]])+piece)
        t["offset"] += len(piece)
        t["sequence"] = (t["sequence"]+1) & 15
        t["credit"] -= 1
        if t["offset"] == len(t["payload"]):
            self.tx = None
        elif not t["credit"]:
            t.update(waiting=True, deadline=now+self.timeout_ms)
        else:
            t["next_at"] = now+t["separation"]
