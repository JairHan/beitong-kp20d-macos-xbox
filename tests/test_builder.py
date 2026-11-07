import binascii
import importlib.util
from pathlib import Path
import struct
import unittest

spec=importlib.util.spec_from_file_location('builder',Path(__file__).resolve().parents[1]/'tools/build_firmware.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class BuilderTests(unittest.TestCase):
    def fixture(self):
        payload=b'first-segment'+b'second-segment'
        data=bytearray(34)+payload
        struct.pack_into('<IIIH',data,0,1,2,len(data),binascii.crc_hqx(payload,0))
        for i,(off,length) in enumerate(((34,13),(47,14))):
            struct.pack_into('<IIH',data,14+i*10,off,length,binascii.crc_hqx(data[off:off+length],0))
        return bytes(data)
    def test_valid_container(self):
        self.assertEqual(b.inspect(self.fixture()),[(34,13),(47,14)])
    def test_rejects_corrupted_payload(self):
        image=bytearray(self.fixture());image[-1]^=1
        with self.assertRaises(ValueError):b.inspect(image)
    def test_rejects_wrong_layout_even_with_valid_checksums(self):
        image=bytearray(self.fixture());struct.pack_into('<I',image,24,48)
        with self.assertRaises(ValueError):b.inspect(image)
    def test_rejects_unpinned_image(self):
        with self.assertRaises(ValueError):b.build(self.fixture())
    def test_rejects_unrelated_restore(self):
        with self.assertRaises(ValueError):b.restore(self.fixture())

if __name__=='__main__':unittest.main()
