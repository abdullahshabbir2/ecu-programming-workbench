import random
import unittest
from workbench.isotp import CanFrame,Endpoint,TransportError


class TransportTests(unittest.TestCase):
    def transfer(self,payload,block_size=8,stmin=1):
        a,b = Endpoint(0x700,0x708),Endpoint(0x708,0x700,block_size=block_size,stmin=stmin)
        a.send(payload,0)
        times=[]
        for now in range(10000):
            a.tick(now); b.tick(now)
            for source,target in ((a,b),(b,a)):
                while source.outbox:
                    frame=source.outbox.popleft()
                    if source is a and frame.data[0]>>4==2: times.append(now)
                    target.receive(frame,now)
            if b.messages:
                self.assertEqual(b.messages.popleft(),payload)
                self.assertTrue(all(y-x>=stmin for x,y in zip(times,times[1:])))
                return
        self.fail('roundtrip did not complete')

    def test_boundary_lengths_and_counter_wrap(self):
        for n in (1,7,8,13,14,111,112,113,255,4095):
            with self.subTest(length=n): self.transfer(bytes(i%256 for i in range(n)))

    def test_random_roundtrips(self):
        rng=random.Random(20260923)
        for _ in range(30):
            n=rng.randrange(1,1500)
            self.transfer(rng.randbytes(n),block_size=rng.choice((0,1,4,8)),stmin=rng.randrange(3))

    def test_flow_control_timeout(self):
        a=Endpoint(0x700,0x708); a.send(bytes(8),0)
        with self.assertRaisesRegex(TransportError,'flow-control timeout'): a.tick(100)
        self.assertFalse(a.busy)

    def test_consecutive_timeout(self):
        b=Endpoint(0x708,0x700)
        b.receive(CanFrame(0x700,b'\x10\x14'+bytes(6)),0)
        with self.assertRaisesRegex(TransportError,'consecutive-frame timeout'): b.tick(100)

    def test_sequence_mismatch(self):
        b=Endpoint(0x708,0x700)
        b.receive(CanFrame(0x700,b'\x10\x14'+bytes(6)),0)
        with self.assertRaisesRegex(TransportError,'sequence'):
            b.receive(CanFrame(0x700,b'\x22'+bytes(7)),1)

    def test_wait_limit_and_overflow(self):
        a=Endpoint(0x700,0x708); a.send(bytes(8),0)
        for now in (1,2,3): a.receive(CanFrame(0x708,b'\x31'+bytes(7)),now)
        with self.assertRaisesRegex(TransportError,'WAIT limit'):
            a.receive(CanFrame(0x708,b'\x31'+bytes(7)),4)
        a.send(bytes(8),5)
        with self.assertRaisesRegex(TransportError,'overflow'):
            a.receive(CanFrame(0x708,b'\x32'+bytes(7)),6)

    def test_invalid_and_unrelated_frames(self):
        for payload in (b'',b'\x00'+bytes(7),b'\x10\x07'+bytes(6),b'\x40'+bytes(7)):
            b=Endpoint(0x708,0x700)
            with self.assertRaises(TransportError): b.receive(CanFrame(0x700,payload),0)
        b=Endpoint(0x708,0x700); b.receive(CanFrame(0x123,b''),0)
        self.assertFalse(b.messages)

    def test_invalid_stmin_and_late_fc(self):
        for now,stmin in ((1,0xf1),(100,0)):
            a=Endpoint(0x700,0x708); a.send(bytes(8),0)
            with self.assertRaises(TransportError):
                a.receive(CanFrame(0x708,bytes([0x30,8,stmin])+bytes(5)),now)

    def test_busy_and_clock(self):
        a=Endpoint(0x700,0x708); a.send(bytes(8),1)
        with self.assertRaises(TransportError): a.send(b'a',2)
        with self.assertRaises(ValueError): a.tick(0)
        for data in (b'',bytes(4096)):
            with self.assertRaises(ValueError): Endpoint(1,2).send(data,0)
