"""Optional visual evidence generation; requires matplotlib."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root=Path(__file__).resolve().parents[1]
trace=json.loads((root/'evidence/uds_trace.json').read_text())
requests=[e for e in trace if e['direction']=='request']
transfer=[e for e in requests if e['pdu'].startswith('36')]
x,y=[],[]
total=0; previous=None
for e in transfer:
    data=bytes.fromhex(e['pdu'])
    if data!=previous: total+=len(data)-2
    previous=data
    x.append(e['ms']); y.append(total)
plt.rcParams.update({'font.size':11,'axes.spines.top':False,'axes.spines.right':False})
fig,axes=plt.subplots(2,1,figsize=(11,7),layout='constrained')
fig.suptitle('UDS programming — executed virtual-bench trace',fontsize=18,fontweight='bold')
axes[0].step(x,y,where='post',color='#008d88',linewidth=2)
axes[0].set_ylabel('Unique transferred bytes'); axes[0].set_xlabel('Simulated time (ms)')
drop=next(e for e in trace if e.get('dropped'))
axes[0].axvline(drop['ms'],color='#bf6f31',linestyle='--',label='Injected lost TransferData acknowledgement')
axes[0].legend(loc='upper left'); axes[0].grid(alpha=.2)
names={0x10:'Session',0x27:'Lab security',0x31:'Routine',0x34:'Download',0x36:'Transfer',0x37:'Exit',0x11:'Reset',0x22:'Read DID',0x19:'Read DTC'}
for i,(sid,name) in enumerate(names.items()):
    times=[e['ms'] for e in requests if int(e['pdu'][:2],16)==sid]
    axes[1].scatter(times,[i]*len(times),s=25,color='#215ea0')
axes[1].set_yticks(range(len(names)),list(names.values()))
axes[1].set_xlabel('Simulated time (ms)'); axes[1].grid(axis='x',alpha=.2)
fig.savefig(root/'evidence/programming_trace.png',dpi=160)
fig.savefig(root/'evidence/programming_trace.svg')
print('Generated actual virtual-bench trace plots.')
