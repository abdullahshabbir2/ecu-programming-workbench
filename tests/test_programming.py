import hashlib
from pathlib import Path
import tempfile
import unittest
from workbench.bus import VirtualBus
from workbench.client import Client,DiagnosticTimeout,NegativeResponse,ResponseError
from workbench.ecu import VirtualECU,lab_key
from workbench.image import build_image,validate_image
from workbench.storage import BankStore,PowerLoss


class ProgrammingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=BankStore(self.temp.name)
        self.old=build_image(b'factory software'*20,1)
        self.store.stage(self.old); self.store.activate()
        self.ecu=VirtualECU(self.store,nonce=lambda n:bytes(range(n)))
        self.bus=VirtualBus(self.ecu); self.client=Client(self.bus)

    def ready(self):
        self.client.session(2); self.client.unlock()

    def download(self,size=64):
        self.client.request(b'\x31\x01\xff\x00')
        return self.client.request(b'\x34\x00\x44'+VirtualECU.BASE.to_bytes(4,'big')+size.to_bytes(4,'big'))

    def test_complete_update_and_diagnostics(self):
        image=build_image(bytes(range(256))*8,2)
        r=self.client.program(image)
        self.assertEqual(self.store.read_active(),image)
        self.assertTrue(r['dtcs_preserved'])
        self.assertIn('response_pending',[e['event'] for e in r['events']])
        self.assertEqual(self.ecu.session,1)
        self.assertTrue(any(int(f['data'][:2],16)>>4==1 for f in self.bus.trace))

    def test_lost_transfer_ack_is_idempotent(self):
        dropped=[]
        def filter_(response,now):
            if response[:1]==b'\x76' and not dropped:
                dropped.append(now); return False
            return True
        self.bus.response_filter=filter_
        image=build_image(b'x'*700,2)
        result=self.client.program(image)
        self.assertEqual(self.store.read_active(),image)
        self.assertEqual(sum(e['event']=='transfer_retry' for e in result['events']),1)

    def test_block_counter_wrap(self):
        image=build_image(bytes(range(256))*70,2)
        self.client.program(image)
        self.assertEqual(self.store.read_active(),image)
        self.assertTrue(any(e['pdu'].startswith('3600') for e in self.bus.uds_trace))

    def test_session_and_security_gates(self):
        with self.assertRaises(NegativeResponse) as caught: self.download()
        self.assertEqual(caught.exception.nrc,0x7f)
        self.client.session(2)
        with self.assertRaises(NegativeResponse) as caught: self.download()
        self.assertEqual(caught.exception.nrc,0x33)

    def test_security_lockout(self):
        self.client.session(2)
        for expected in (0x35,0x35,0x36):
            self.client.request(b'\x27\x01')
            with self.assertRaises(NegativeResponse) as caught: self.client.request(b'\x27\x02'+bytes(16))
            self.assertEqual(caught.exception.nrc,expected)
        with self.assertRaises(NegativeResponse) as caught: self.client.request(b'\x27\x01')
        self.assertEqual(caught.exception.nrc,0x37)

    def test_key_replay_rejected(self):
        self.ready()
        with self.assertRaises(NegativeResponse) as caught:
            self.client.request(b'\x27\x02'+lab_key(bytes(range(16))))
        self.assertEqual(caught.exception.nrc,0x24)

    def test_session_expiry_aborts_download(self):
        self.ready(); self.download(); self.bus.advance(2001)
        self.assertEqual(self.ecu.session,1)
        self.assertIsNone(self.ecu.transfer)
        self.assertEqual(self.store.read_active(),self.old)

    def test_tester_present_keeps_session(self):
        self.ready()
        for _ in range(4): self.bus.advance(1000); self.client.request(b'\x3e\x00')
        self.assertEqual(self.ecu.session,2)

    def test_voltage_and_engine_interlocks(self):
        for voltage,rpm in ((10000,0),(16000,0),(12500,100)):
            self.ready(); self.ecu.voltage_mv,self.ecu.engine_rpm=voltage,rpm
            with self.assertRaises(NegativeResponse) as caught: self.download()
            self.assertEqual(caught.exception.nrc,0x22)
            self.assertEqual(self.store.read_active(),self.old)
            self.ecu.voltage_mv,self.ecu.engine_rpm=12500,0

    def test_voltage_loss_mid_transfer(self):
        self.ready(); self.download()
        self.client.request(b'\x36\x01'+bytes(32))
        self.ecu.voltage_mv=9000
        with self.assertRaises(NegativeResponse): self.client.request(b'\x36\x02'+bytes(32))
        self.assertIsNone(self.ecu.transfer)
        self.assertEqual(self.store.read_active(),self.old)

    def test_transfer_duplicate_changed_payload_rejected(self):
        self.ready(); self.download()
        self.client.request(b'\x36\x01'+bytes(32))
        self.client.request(b'\x36\x01'+bytes(32))
        self.assertEqual(len(self.ecu.transfer['buffer']),32)
        with self.assertRaises(NegativeResponse) as caught: self.client.request(b'\x36\x01'+b'x'*32)
        self.assertEqual(caught.exception.nrc,0x73)

    def test_out_of_order_and_incomplete(self):
        self.ready(); self.download()
        for pdu,nrc in ((b'\x36\x02x',0x73),(b'\x37',0x24)):
            with self.assertRaises(NegativeResponse) as caught: self.client.request(pdu)
            self.assertEqual(caught.exception.nrc,nrc)

    def test_bounds_and_wrong_address(self):
        self.ready(); self.client.request(b'\x31\x01\xff\x00')
        for address,size in ((0,64),(VirtualECU.BASE,0),(VirtualECU.BASE,65537)):
            with self.assertRaises(NegativeResponse) as caught:
                self.client.request(b'\x34\x00\x44'+address.to_bytes(4,'big')+size.to_bytes(4,'big'))
            self.assertEqual(caught.exception.nrc,0x31)

    def test_crc_failure_does_not_activate(self):
        self.ready()
        image=bytearray(build_image(b'candidate',2)); image[-1]^=1
        self.download(len(image)); self.client.request(b'\x36\x01'+bytes(image)); self.client.request(b'\x37')
        with self.assertRaises(NegativeResponse) as caught: self.client.request(b'\x31\x01\xff\x01')
        self.assertEqual(caught.exception.nrc,0x72)
        self.assertEqual(self.store.read_active(),self.old)

    def test_rollback_version_rejected(self):
        with self.assertRaises(NegativeResponse) as caught: self.client.program(self.old)
        self.assertEqual(caught.exception.nrc,0x72)
        self.assertEqual(self.store.read_active(),self.old)

    def test_timeout_no_automatic_erase_retry(self):
        self.ready(); self.bus.response_filter=lambda p,n:p[:1]!=b'\x71'
        with self.assertRaises(DiagnosticTimeout): self.client.request(b'\x31\x01\xff\x00')
        count=sum(e['direction']=='request' and e['pdu']=='3101ff00' for e in self.bus.uds_trace)
        self.assertEqual(count,1)

    def test_unknown_services_and_invalid_lengths(self):
        for pdu,nrc in ((b'\x99',0x11),(b'\x22',0x13),(b'\x22\x00\x01',0x31)):
            with self.assertRaises(NegativeResponse) as caught: self.client.request(pdu)
            self.assertEqual(caught.exception.nrc,nrc)

    def test_response_pending_flood_is_bounded(self):
        self.ecu.handle=lambda request,now:[(i*10,b'\x7f\x22\x78') for i in range(9)]
        with self.assertRaisesRegex(ResponseError,'pending limit'):
            self.client.request(b'\x22\xf1\x90')

    def test_unrelated_response_rejected(self):
        for response in (b'\x63\xf1\x90',b'\x7f\x31\x22',b'\x7f\x22'):
            self.bus=VirtualBus(self.ecu); self.client=Client(self.bus)
            self.ecu.handle=lambda request,now,response=response:[(0,response)]
            with self.assertRaises(ResponseError): self.client.request(b'\x22\xf1\x90')

    def test_expired_challenge_rejected(self):
        self.client.session(2)
        seed=self.client.request(b'\x27\x01')[2:]
        self.bus.advance(1001)
        with self.assertRaises(NegativeResponse) as caught: self.client.request(b'\x27\x02'+lab_key(seed))
        self.assertEqual(caught.exception.nrc,0x24)

    def test_dropped_consecutive_frame_aborts_transport(self):
        from workbench.isotp import TransportError
        dropped=[]
        def filter_(frame,now):
            if frame.identifier==0x700 and frame.data[0]>>4==2 and not dropped:
                dropped.append(now); return False
            return True
        self.ready(); self.download()
        self.bus.frame_filter=filter_
        with self.assertRaises(TransportError): self.client.request(b'\x36\x01'+bytes(64))
        self.assertEqual(self.store.read_active(),self.old)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.old,self.new=build_image(b'old',1),build_image(b'new',2)
        self.store=BankStore(self.temp.name); self.store.stage(self.old); self.store.activate()

    def test_power_loss_after_staging(self):
        with self.assertRaises(PowerLoss): self.store.stage(self.new,failure='after_bank_write')
        rebooted=BankStore(self.temp.name)
        self.assertEqual(rebooted.read_active(),self.old)
        with self.assertRaises(ValueError): rebooted.activate()

    def test_power_loss_before_commit(self):
        self.store.stage(self.new)
        with self.assertRaises(PowerLoss): self.store.activate(failure='before_pointer_commit')
        self.assertEqual(BankStore(self.temp.name).read_active(),self.old)

    def test_power_loss_after_commit(self):
        self.store.stage(self.new)
        with self.assertRaises(PowerLoss): self.store.activate(failure='after_pointer_commit')
        self.assertEqual(BankStore(self.temp.name).read_active(),self.new)

    def test_staged_tampering(self):
        self.store.stage(self.new)
        path=Path(self.temp.name)/(self.store.pending['bank']+'.bin')
        path.write_bytes(b'corrupted')
        with self.assertRaises(ValueError): self.store.activate()
        self.assertEqual(self.store.read_active(),self.old)

    def test_metadata_corruption_fails_closed(self):
        self.store.pointer.write_text('{}')
        with self.assertRaises(ValueError): BankStore(self.temp.name)

    def test_image_integrity_and_lengths(self):
        for data in (b'',b'LAB1',self.old[:-1],self.old+b'x'):
            with self.assertRaises(ValueError): validate_image(data)
        for offset in (0,4,8,20,len(self.old)-1):
            data=bytearray(self.old); data[offset]^=1
            with self.assertRaises(ValueError): validate_image(data)


if __name__=='__main__': unittest.main()
