"""Exercise real KP20D Python transaction state machine against simulated HID/clock only."""
import binascii
import contextlib
import hashlib
import heapq
import importlib.util
import io
import json
import math
import struct
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
spec=importlib.util.spec_from_file_location('tested_native_updater',ROOT/'tools/native_updater.py')
updater=importlib.util.module_from_spec(spec);spec.loader.exec_module(updater)

class Clock:
    def __init__(self):self.now=1000.0
    def monotonic(self):return self.now
    def advance(self,seconds):self.now+=max(0,seconds)

class Journal:
    def __init__(self):self.events=[]
    def log(self,event,**values):self.events.append({'event':event,**values})

class FakeHID:
    """ACK framing is generated independently; write/erase only mutate Python memory."""
    def __init__(self,clock,images,scenario='normal'):
        self.clock=clock;self.images=images;self.scenario=scenario;self.queue=[];self.seq=0
        self.part=bytearray();self.part_channel=None;self.commands=[];self.frames=[]
        self.erase_started=set();self.attempts=Counter();self.closed=False;self.restart_at=None
        self.invalid_injected=0
    def schedule(self,raw,delay=.001):
        self.seq+=1;heapq.heappush(self.queue,(self.clock.now+delay,self.seq,raw))
    def response(self,ch,body,delay=.001):
        p=bytearray([2,15,ch,0x7e])+struct.pack('<H',len(body))+body
        p+=struct.pack('<H',binascii.crc_hqx(p[3:],0))+b'\xe7'
        self.schedule(bytes(p).ljust(64,b'\0'),delay)
    def connect(self,seconds):return {'vid':'20bc','pid':'5158'}
    def write(self,frame):
        assert len(frame)==64
        if frame[:2]==b'\2\x55':
            self.schedule(b'\2\x55\1\1\1'.ljust(64,b'\0'));return
        assert frame[:2]==b'\2\x0f' and frame[2] in (1,2)
        self.frames.append(frame)
        ch=frame[2]
        if not self.part:self.part_channel=ch
        assert self.part_channel==ch
        self.part+=frame[3:]
        assert self.part[0]==0x7e
        n=struct.unpack_from('<H',self.part,1)[0];total=n+6
        if len(self.part)<total:return
        packet=bytes(self.part[:total]);padding=self.part[total:]
        assert not any(padding) and packet[-1]==0xe7
        assert binascii.crc_hqx(packet[:-3],0)==struct.unpack_from('<H',packet,total-3)[0]
        body=packet[3:-3];assert body[:2]==b'\x22\0'
        op=body[2];self.part=bytearray();self.part_channel=None
        self.commands.append({'time':round(self.clock.now,9),'channel':ch,'operation':op})
        data=self.images[ch];pages=math.ceil(len(data)/256);padded=data.ljust(pages*256,b'\0')
        if op in (0,1):assert body[3:]==struct.pack('<H',pages)
        if op==0:
            # Confirmed ARM target status byte is 1, not the old synthetic 0.
            if self.scenario=='bad_prepare_status' and self.invalid_injected==0:
                self.invalid_injected+=1;self.response(ch,b'\x22\0\0\0'+struct.pack('<H',pages));return
            self.response(ch,b'\x22\0\0\1'+struct.pack('<H',pages))
        elif op==1:
            if self.scenario=='erase_progress':
                if ch not in self.erase_started:
                    self.erase_started.add(ch)
                    for delay,progress in [(0.001,0),(.1,37),(1.05,100)]:
                        self.response(ch,bytes([0x22,0,1,progress]),delay)
            elif self.scenario=='erase_timeout':self.response(ch,bytes([0x22,0,1,37]))
            else:self.response(ch,bytes([0x22,0,1,100]))
        elif op==2:
            assert len(body)==262 and body[5]==1
            index=struct.unpack_from('<H',body,3)[0];block=body[6:]
            assert 0<=index<pages and block==padded[index*256:(index+1)*256]
            self.commands[-1]['block_index']=index;self.attempts[(ch,index)]+=1
            if self.scenario=='block_timeout':return
            checksum=sum(block)&65535;block_count=1
            if self.scenario=='bad_block_sum' and index==0 and self.attempts[(ch,index)]==1:checksum^=1
            if self.scenario=='bad_block_count' and index==0 and self.attempts[(ch,index)]==1:block_count=0
            self.response(ch,b'\x22\0\2'+struct.pack('<H',index)+bytes([block_count])+struct.pack('<H',checksum))
        elif op==3:
            expected=binascii.crc_hqx(padded,0)
            assert body[3:]==struct.pack('<H',expected)
            self.response(ch,b'\x22\0\3'+struct.pack('<H',expected^(1 if self.scenario=='bad_final_crc' else 0)))
            if ch==1:self.restart_at=self.clock.now+.4
        else:raise AssertionError('Unapproved upgrade operation '+str(op))
    def read(self,milliseconds):
        end=self.clock.now+milliseconds/1000.0
        if self.queue and self.queue[0][0]<=end:
            when,_,raw=heapq.heappop(self.queue);self.clock.now=max(self.clock.now,when);return raw
        self.clock.now=end;return None
    def pump(self,milliseconds):self.clock.advance(milliseconds/1000)
    def disconnected(self):return self.restart_at is not None and self.clock.now>=self.restart_at
    def close(self):self.closed=True

def execute(scenario,main=False):
    clock=Clock();journal=Journal()
    images={2:bytes((i*13+7)&255 for i in range(239816)),1:bytes((i*17+3)&255 for i in range(100452))}
    image=images[1]+images[2];segments=[(0,len(images[1])),(len(images[1]),len(images[2]))]
    if not main:images={1:bytes((i*17+3)&255 for i in range(513))}
    hid=FakeHID(clock,images,scenario)
    oldinspect=updater.inspect_image
    updater.inspect_image=lambda data: ('synthetic-offline-fixture',segments)
    oldofficial=updater.OFFICIAL
    import tempfile
    tmp=tempfile.TemporaryDirectory()
    updater.OFFICIAL=Path(tmp.name)/'synthetic.bin'
    updater.OFFICIAL.write_bytes(image)
    oldtime,oldhid,oldjournal,oldargv=updater.time,updater.HID,updater.Journal,sys.argv
    updater.time=clock;updater.HID=lambda:hid;updater.Journal=lambda path:journal
    failure=None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if main:
                sys.argv=['kp20d-native-updater.py','--flash',str(updater.OFFICIAL),'--journal','not-created-by-fake-journal']
                updater.main()
            else:updater.transfer(hid,1,images[1],journal)
    except RuntimeError as exc:failure=str(exc)
    finally:updater.time,updater.HID,updater.Journal,sys.argv=oldtime,oldhid,oldjournal,oldargv
    updater.inspect_image=oldinspect;updater.OFFICIAL=oldofficial;tmp.cleanup()
    commands=hid.commands;ops=[x['operation'] for x in commands]
    successful=[x for x in journal.events if x['event']=='segment_success']
    row={'scenario':scenario,'main_path_tested':main,'simulated_seconds':round(clock.now-1000,6),'failure':failure,'operations':ops,'success_channels':[x['channel'] for x in successful],'hid_output_frames':len(hid.frames),'hardware_access':False}
    if scenario=='normal':
        assert main and failure is None and [x['channel'] for x in successful]==[2,1]
        assert hid.closed and hid.disconnected()
        sequence=[]
        for c in commands:
            if not sequence or sequence[-1]!=c['channel']:sequence.append(c['channel'])
        assert sequence==[2,1]
        assert len(hid.frames)==6656
        assert any(x['event']=='restart_observation' and x['usb_disconnect_seen'] for x in journal.events)
        row['channel_sequence']=sequence;row['restart_seen']=True
        row['operations']={'counts':dict(Counter(ops))}
    elif scenario=='erase_progress':
        assert failure is None and len(successful)==1
        erase_times=[c['time'] for c in commands if c['operation']==1]
        intervals=[round(b-a,9) for a,b in zip(erase_times,erase_times[1:])]
        assert len(erase_times)>=3 and all(x>=.5-1e-8 for x in intervals),intervals
        row['erase_request_times']=erase_times;row['erase_intervals_seconds']=intervals
        row['intermediate_progress_inputs']=[0,37,100]
    elif scenario in ('bad_block_sum','bad_block_count'):
        assert failure is None and len(successful)==1 and hid.attempts[(1,0)]==2
        row['block0_attempts']=hid.attempts[(1,0)]
        if scenario=='bad_block_count':assert any(x['event']=='invalid_ack' for x in journal.events)
    elif scenario=='bad_prepare_status':
        assert failure is None and len(successful)==1 and ops[:2]==[0,0]
        assert any(x['event']=='invalid_ack' for x in journal.events)
        row['invalid_prepare_status_rejected']=True
    elif scenario=='bad_final_crc':
        assert failure and 'CRC does not match' in failure and not successful and ops[-1]==3
        row['success_refused']=True
    elif scenario=='block_timeout':
        assert failure and 'no commit sent' in failure and 3 not in ops and not successful
        assert hid.attempts[(1,0)]==3
        row['commit_sent']=False;row['block0_attempts']=3
    elif scenario=='erase_timeout':
        assert failure and 'Staging erase deadline' in failure and 2 not in ops and 3 not in ops and not successful
        erase_times=[c['time'] for c in commands if c['operation']==1]
        assert all(b-a>=.5-1e-8 for a,b in zip(erase_times,erase_times[1:]))
        row['operations']={'counts':dict(Counter(ops))};row['blocks_sent']=False;row['commit_sent']=False
    return row

def main():
    results=[execute('normal',main=True)]
    results += [execute(s) for s in ['erase_progress','bad_block_sum','bad_final_crc','block_timeout','erase_timeout','bad_prepare_status','bad_block_count']]
    report={'passed':True,'cases':results,'scope':'Real Python main()/transfer()/wait_ack()/send() functions with FakeHID and a simulated monotonic clock. No ctypes library, hardware, VM, USB, firmware erase/write or network used.','ack_model':'ARM-target acceptance status 1 and confirmed block-count 1.','updater_sha256':hashlib.sha256((ROOT/'tools/native_updater.py').read_bytes()).hexdigest(),'test_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    destination=ROOT/'build/offline-transactions.json'
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'passed':True,'cases':len(results),'report':str(destination)},ensure_ascii=False))
if __name__=='__main__':main()
