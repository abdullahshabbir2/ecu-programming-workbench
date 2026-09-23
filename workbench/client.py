"""Synchronous UDS client with bounded response-pending handling."""
import hashlib
from .ecu import VirtualECU, lab_key
from .image import validate_image


class ResponseError(RuntimeError):
    pass


class NegativeResponse(ResponseError):
    def __init__(self,sid,nrc):
        self.sid,self.nrc = sid,nrc
        super().__init__(f"service 0x{sid:02X}: NRC 0x{nrc:02X}")


class DiagnosticTimeout(ResponseError):
    pass


class Client:
    def __init__(self,bus):
        self.bus = bus
        self.p2, self.p2_star = 100,1000
        self.events = []

    def request(self,pdu,*,echo=b"",overall_timeout=5000):
        if self.bus.client.messages or self.bus.client.busy:
            raise ResponseError("connection is not idle")
        start = self.bus.now
        self.bus.client.send(pdu,self.bus.now)
        # P2 begins after the complete request has left the transport.
        while self.bus.client.busy:
            self.bus.step()
            if self.bus.now-start >= overall_timeout: raise DiagnosticTimeout("request transport deadline")
        deadline = self.bus.now+self.p2
        pending = 0
        while self.bus.now-start < overall_timeout:
            self.bus.step()
            while self.bus.client.messages:
                response = self.bus.client.messages.popleft()
                if response[:1] == b"\x7f":
                    if len(response) != 3 or response[1] != pdu[0]:
                        raise ResponseError("malformed or unrelated negative response")
                    if response[2] == 0x78:
                        pending += 1
                        if pending > 8: raise ResponseError("response-pending limit exceeded")
                        deadline = self.bus.now+self.p2_star
                        self.events.append({"event":"response_pending","ms":self.bus.now})
                        continue
                    raise NegativeResponse(pdu[0],response[2])
                if not response or response[0] != pdu[0]+0x40 or not response[1:].startswith(echo):
                    raise ResponseError("positive service/subfunction echo mismatch")
                return response
            if self.bus.now >= deadline: raise DiagnosticTimeout("P2/P2* expired")
        raise DiagnosticTimeout("overall diagnostic deadline")

    def session(self,session):
        response = self.request(bytes([0x10,session]),echo=bytes([session]))
        if len(response) != 6: raise ResponseError("invalid session timing record")
        self.p2 = max(1,int.from_bytes(response[2:4],"big"))
        self.p2_star = max(1,int.from_bytes(response[4:6],"big")*10)

    def unlock(self,key_provider=lab_key):
        response = self.request(b"\x27\x01",echo=b"\x01")
        if len(response) != 18: raise ResponseError("invalid lab seed record")
        self.request(b"\x27\x02"+key_provider(response[2:]),echo=b"\x02")

    def program(self,image):
        version = validate_image(image)
        before = self.request(b"\x19\x02\xff",echo=b"\x02")
        self.session(2)
        self.unlock()
        self.request(b"\x31\x01\xff\x00",echo=b"\x01\xff\x00")
        download = b"\x34\x00\x44"+VirtualECU.BASE.to_bytes(4,"big")+len(image).to_bytes(4,"big")
        response = self.request(download)
        if len(response) != 4 or response[1] != 0x20:
            raise ResponseError("unsupported maxNumberOfBlockLength format")
        block_size = int.from_bytes(response[2:],"big")-2
        if not 1 <= block_size <= 4093: raise ResponseError("invalid block length")
        sequence = 1
        for offset in range(0,len(image),block_size):
            request = bytes([0x36,sequence])+image[offset:offset+block_size]
            try:
                self.request(request,echo=bytes([sequence]))
            except DiagnosticTimeout:
                # Retry only the same TransferData block. Never blindly retry erase/reset.
                self.events.append({"event":"transfer_retry","offset":offset})
                self.request(request,echo=bytes([sequence]))
            sequence = (sequence+1)&255
        self.request(b"\x37")
        self.request(b"\x31\x01\xff\x01",echo=b"\x01\xff\x01")
        self.request(b"\x11\x01",echo=b"\x01")
        observed = self.request(b"\x22\xf1\x81",echo=b"\xf1\x81")
        digest = self.request(b"\x22\xf1\xa0",echo=b"\xf1\xa0")
        after = self.request(b"\x19\x02\xff",echo=b"\x02")
        if observed[3:] != version.to_bytes(4,"big") or digest[3:] != hashlib.sha256(image).digest():
            raise ResponseError("post-reset image verification failed")
        if before != after: raise ResponseError("diagnostic state changed during update")
        return {"version":version,"sha256":hashlib.sha256(image).hexdigest(),"bytes":len(image),
                "elapsed_simulated_ms":self.bus.now,"dtcs_preserved":True,"events":self.events}
