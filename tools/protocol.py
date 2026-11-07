"""Pure framing/validation derived from official KP20D native updater; no device access."""
import struct,binascii,math
crc16=lambda b:binascii.crc_hqx(b,0)
def logical_packet(operation,payload):
 body=bytes([0x22,0,operation])+payload
 p=b'\x7e'+struct.pack('<H',len(body))+body
 return p+struct.pack('<H',crc16(p))+b'\xe7'
def reports(channel,operation,payload):
 if channel not in (1,2):raise ValueError('Unverified channel')
 p=logical_packet(operation,payload)
 return [(bytes([2,15,channel])+p[i:i+61]).ljust(64,b'\0') for i in range(0,len(p),61)]
def transfer_frames(channel,data):
 if not data:raise ValueError('Empty image')
 pages=math.ceil(len(data)/256);padded=data.ljust(pages*256,b'\0')
 out=reports(channel,0,struct.pack('<H',pages))+reports(channel,1,struct.pack('<H',pages))
 for i in range(pages):out+=reports(channel,2,struct.pack('<H',i)+b'\1'+padded[i*256:(i+1)*256])
 out+=reports(channel,3,struct.pack('<H',crc16(padded)))
 return out

def parse_ack(report,channel):
 if len(report)!=64 or report[:2]!=b'\2\x0f' or report[2]&15!=channel:raise ValueError('Wrong HID header/channel')
 n=struct.unpack_from('<H',report,4)[0]
 if n<3 or n>55 or report[6:8]!=b'\x22\0':raise ValueError('Bad length/command')
 if crc16(report[3:6+n])!=struct.unpack_from('<H',report,6+n)[0]:raise ValueError('ACK CRC mismatch')
 # The Windows function does not check these two delimiters; this parser is stricter.
 if report[3]!=0x7e or report[8+n]!=0xe7:raise ValueError('ACK delimiter mismatch')
 op=report[8]
 if op==0:
  if n<6:raise ValueError('Short allocation ACK')
  return {'operation':op,'page_count':struct.unpack_from('<H',report,10)[0]}
 if op==1:
  if n<4:raise ValueError('Short erase ACK')
  return {'operation':op,'erase_progress':report[9]}
 if op==2:
  if n<8:raise ValueError('Short block ACK')
  return {'operation':op,'block_index':struct.unpack_from('<H',report,9)[0],'block_sum':struct.unpack_from('<H',report,12)[0]}
 if op==3:
  if n<5:raise ValueError('Short final CRC ACK')
  return {'operation':op,'payload_crc':struct.unpack_from('<H',report,9)[0]}
 raise ValueError('Unknown ACK operation')
