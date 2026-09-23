import argparse
import json
from pathlib import Path
import tempfile
from .bus import VirtualBus
from .client import Client
from .ecu import VirtualECU
from .image import build_image
from .storage import BankStore


def demonstration(output):
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        store=BankStore(directory)
        store.stage(build_image(b'owned factory image\n'*100,1)); store.activate()
        dropped=[]
        def drop_ack(response,now):
            if response[:1]==b'\x76' and not dropped:
                dropped.append(now)
                return False
            return True
        bus=VirtualBus(VirtualECU(store),response_filter=drop_ack)
        image=build_image(bytes(range(256))*20,2)
        report=Client(bus).program(image)
        report['environment']='Virtual ECU and virtual CAN; no physical flashing'
        report['fault_injection']='First TransferData acknowledgement dropped'
        for name,data in [('programming_report.json',report),('uds_trace.json',bus.uds_trace),('can_trace.json',bus.trace)]:
            (output/name).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
        (output/'candidate.lab').write_bytes(image)
        (output/'active.lab').write_bytes(store.read_active())
        print(json.dumps(report,indent=2))


def main():
    parser=argparse.ArgumentParser(description='Owned virtual ECU programming workbench')
    parser.add_argument('command',choices=['demo'])
    parser.add_argument('--output',default='evidence')
    args=parser.parse_args()
    demonstration(args.output)


if __name__=='__main__': main()
