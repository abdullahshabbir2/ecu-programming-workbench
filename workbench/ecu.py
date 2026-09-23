"""Stateful UDS subset for our own virtual ECU, not an OEM emulator."""
import hashlib
import hmac
import secrets
from .image import MAX_IMAGE

LAB_KEY = b"public-laboratory-key-not-for-deployment"


def lab_key(seed):
    return hmac.new(LAB_KEY,seed,hashlib.sha256).digest()[:16]


class VirtualECU:
    BASE = 0x00100000
    BLOCK_DATA = 64
    SESSION_TIMEOUT = 2000

    def __init__(self, store, *, nonce=secrets.token_bytes):
        self.store, self.nonce = store, nonce
        self.session, self.unlocked = 1, False
        self.last_activity = 0
        self.voltage_mv, self.engine_rpm = 12500, 0
        self.seed, self.seed_at = None, 0
        self.failures, self.locked_until = 0, 0
        self.transfer, self.completed, self.erased = None, None, False
        self.dtcs = {0xC00101:0x08}  # Synthetic pre-existing confirmed DTC retained throughout programming.

    def _abort(self):
        self.transfer = self.completed = None
        self.erased = False
        self.store.abort()

    def tick(self, now):
        if self.session != 1 and now-self.last_activity >= self.SESSION_TIMEOUT:
            self.session, self.unlocked, self.seed = 1, False, None
            self._abort()

    def handle(self, request, now):
        self.tick(now)
        if not request:
            return [(0,b"\x7f\x00\x13")]
        sid = request[0]
        self.last_activity = now
        def negative(code): return [(0,bytes([0x7f,sid,code]))]
        def positive(data): return [(0,bytes([sid+0x40])+data)]
        if sid == 0x10:
            if len(request) != 2: return negative(0x13)
            if request[1] not in (1,2,3): return negative(0x12)
            self.session, self.unlocked, self.seed = request[1], False, None
            self._abort()
            return positive(bytes([self.session,0,50,0,100]))
        if sid == 0x3e:
            if len(request) != 2: return negative(0x13)
            if request[1] not in (0,0x80): return negative(0x12)
            return [] if request[1] == 0x80 else positive(b"\x00")
        if sid == 0x22:
            if len(request) != 3: return negative(0x13)
            did = request[1:]
            if did == b"\xf1\x90": return positive(did+b"LABECU00000000001")
            if did == b"\xf1\x81": return positive(did+self.store.version.to_bytes(4,"big"))
            if did == b"\xf1\xa0": return positive(did+hashlib.sha256(self.store.read_active() or b"").digest())
            return negative(0x31)
        if sid == 0x19:
            if len(request) != 3: return negative(0x13)
            if request[1] != 2: return negative(0x12)
            records = b"".join(dtc.to_bytes(3,"big")+bytes([status]) for dtc,status in sorted(self.dtcs.items()) if status & request[2])
            return positive(b"\x02\xff"+records)
        if sid == 0x27:
            if self.session != 2: return negative(0x7e)
            if len(request) < 2: return negative(0x13)
            if now < self.locked_until: return negative(0x37)
            if request[1] == 1:
                if len(request) != 2: return negative(0x13)
                self.seed, self.seed_at = self.nonce(16), now
                return positive(b"\x01"+self.seed)
            if request[1] == 2:
                if len(request) != 18: return negative(0x13)
                if self.seed is None or now-self.seed_at > 1000: return negative(0x24)
                seed, self.seed = self.seed, None
                if not hmac.compare_digest(request[2:],lab_key(seed)):
                    self.failures += 1
                    self.unlocked = False
                    if self.failures >= 3:
                        self.locked_until = now+5000
                        return negative(0x36)
                    return negative(0x35)
                self.failures, self.unlocked = 0, True
                return positive(b"\x02")
            return negative(0x12)
        if sid not in (0x31,0x34,0x36,0x37,0x11): return negative(0x11)
        if self.session != 2: return negative(0x7f)
        if not self.unlocked: return negative(0x33)
        if not 11000 <= self.voltage_mv <= 15000 or self.engine_rpm != 0:
            self.dtcs[0xC00201] = 0x09
            self._abort()
            return negative(0x22)
        if sid == 0x31:
            if len(request) != 4: return negative(0x13)
            if request[1] != 1: return negative(0x12)
            routine = int.from_bytes(request[2:],"big")
            if routine == 0xff00:
                self._abort()
                self.erased = True
                return positive(request[1:]+b"\x00")
            if routine == 0xff01:
                if self.completed is None: return negative(0x24)
                try:
                    self.store.stage(self.completed)
                except ValueError:
                    self._abort()
                    return negative(0x72)
                return [(0,b"\x7f\x31\x78"),(30,b"\x71"+request[1:]+b"\x00")]
            return negative(0x31)
        if sid == 0x34:
            if len(request) != 11: return negative(0x13)
            if request[1:3] != b"\x00\x44": return negative(0x31)
            address, size = int.from_bytes(request[3:7],"big"),int.from_bytes(request[7:11],"big")
            if address != self.BASE or not 1 <= size <= MAX_IMAGE: return negative(0x31)
            if not self.erased or self.transfer is not None: return negative(0x24)
            self.transfer = dict(size=size,buffer=bytearray(),expected=1,last=None,last_data=None)
            self.completed, self.erased = None, False
            return positive(b"\x20"+(self.BLOCK_DATA+2).to_bytes(2,"big"))
        if sid == 0x36:
            if not 3 <= len(request) <= self.BLOCK_DATA+2: return negative(0x13)
            t = self.transfer
            if t is None: return negative(0x24)
            seq, data = request[1],request[2:]
            if seq == t["last"]:
                return positive(bytes([seq])) if data == t["last_data"] else negative(0x73)
            if seq != t["expected"]: return negative(0x73)
            if len(t["buffer"])+len(data) > t["size"]: return negative(0x71)
            t["buffer"].extend(data)
            t.update(last=seq,last_data=data,expected=(seq+1)&255)
            return positive(bytes([seq]))
        if sid == 0x37:
            if len(request) != 1: return negative(0x13)
            if self.transfer is None or len(self.transfer["buffer"]) != self.transfer["size"]:
                return negative(0x24)
            self.completed = bytes(self.transfer["buffer"])
            self.transfer = None
            return positive(b"")
        if sid == 0x11:
            if len(request) != 2: return negative(0x13)
            if request[1] != 1: return negative(0x12)
            try: self.store.activate()
            except ValueError: return negative(0x24)
            self.session, self.unlocked, self.seed = 1, False, None
            self._abort()
            return positive(b"\x01")
