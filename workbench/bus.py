"""Deterministic millisecond scheduler; all UDS traffic traverses CAN frames."""
from .isotp import Endpoint


class VirtualBus:
    def __init__(self, ecu, *, frame_filter=None, response_filter=None):
        self.ecu, self.now = ecu, 0
        self.client, self.server = Endpoint(0x700,0x708),Endpoint(0x708,0x700)
        self.frame_filter, self.response_filter = frame_filter, response_filter
        self.scheduled, self.trace, self.uds_trace = [], [], []

    def _deliver(self, source, destination):
        while source.outbox:
            frame = source.outbox.popleft()
            passed = self.frame_filter is None or self.frame_filter(frame,self.now)
            self.trace.append(dict(ms=self.now,id=f"{frame.identifier:03X}",data=frame.data.hex(),dropped=not passed))
            if passed: destination.receive(frame,self.now)

    def step(self):
        self.now += 1
        self.ecu.tick(self.now)
        self.client.tick(self.now)
        self.server.tick(self.now)
        self._deliver(self.client,self.server)
        self._deliver(self.server,self.client)
        while self.server.messages:
            request = self.server.messages.popleft()
            self.uds_trace.append(dict(ms=self.now,direction="request",pdu=request.hex()))
            for delay,response in self.ecu.handle(request,self.now):
                self.scheduled.append((self.now+delay,response))
        self.scheduled.sort(key=lambda item:item[0])
        if self.scheduled and self.scheduled[0][0] <= self.now and not self.server.busy:
            _,response = self.scheduled.pop(0)
            passed = self.response_filter is None or self.response_filter(response,self.now)
            self.uds_trace.append(dict(ms=self.now,direction="response",pdu=response.hex(),dropped=not passed))
            if passed: self.server.send(response,self.now)
        self._deliver(self.server,self.client)

    def advance(self, duration_ms):
        for _ in range(duration_ms): self.step()
