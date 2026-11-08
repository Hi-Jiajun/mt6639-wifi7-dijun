"""Exercise the real observer lifecycle and independently inspect its compiled ELF."""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLANG = (os.environ.get("MT6639_CLANG") or shutil.which("clang")
         or "clang")
VERMAGIC = b"vermagic=6.18.21-android17-5-g1d099fcb35e0-abogki538445360-4k SMP preempt mod_unload modversions aarch64\0"

def elf_info(path):
    data = path.read_bytes()
    assert data[:6] == b"\x7fELF\x02\x01" and struct.unpack_from("<HH", data, 16) == (1, 183)
    shoff = struct.unpack_from("<Q", data, 40)[0]
    entsize, count, stridx = struct.unpack_from("<HHH", data, 58)
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff+i*entsize) for i in range(count)]
    def blob(s): return data[s[4]:s[4]+s[5]]
    def string(b, off): return b[off:b.index(b"\0", off)].decode()
    names = blob(sections[stridx])
    named = {string(names,s[0]):s for s in sections}
    symbols=[]
    for s in sections:
        if s[1] != 2: continue
        strings=blob(sections[s[6]])
        for off in range(s[4],s[4]+s[5],s[9]):
            name,info,other,index,value,size=struct.unpack_from("<IBBHQQ",data,off)
            symbols.append((string(strings,name),index,value,size,info))
    return data, sections, named, symbols

class ObserverTests(unittest.TestCase):
    def require_sources(self):
        for name in ("native/mlo-gate/observer.c", "native/mlo-gate/abi.h", "tools/build_mlo_observer.py"):
            self.assertTrue((ROOT/name).is_file(), "missing observer implementation: "+name)

    def test_real_lifecycle_and_readonly_callback(self):
        self.require_sources()
        with tempfile.TemporaryDirectory(prefix="mtmlo-host-") as folder:
            exe=pathlib.Path(folder)/"harness"
            subprocess.run([CLANG,"-std=c11","-Wall","-Wextra","-Werror","-O2",str(ROOT/"tests/mlo_observer_harness.c"),"-o",str(exe)],check=True)
            output=subprocess.check_output([str(exe)],text=True)
            self.assertIn("PASS lifecycle:",output)

    def test_kernel_artifact_and_rejection_variant(self):
        self.require_sources()
        with tempfile.TemporaryDirectory(prefix="mtmlo-elf-") as folder:
            for reject in (False, True):
                out=pathlib.Path(folder)/("reject.ko" if reject else "observer.ko")
                cmd=[sys.executable,str(ROOT/"tools/build_mlo_observer.py"),"--out",str(out)]
                if reject: cmd.append("--reject-opcode")
                subprocess.run(cmd,check=True,capture_output=True,text=True)
                data,secs,named,symbols=elf_info(out)
                byname={s[0]:s for s in symbols}
                for name,expected in (("mtmlo_init",0x6fbb3035),("mtmlo_exit",0xe5c47d60),("mtmlo_pre",0xc073bc77)):
                    sym=byname[name]; section=secs[sym[1]]; start=section[4]+sym[2]
                    self.assertEqual(struct.unpack_from("<I",data,start-4)[0],expected)
                    self.assertEqual(struct.unpack_from("<I",data,start)[0],0xd503245f)
                self.assertEqual({s[0] for s in symbols if s[1]==0 and s[0]}, {"register_kprobe","unregister_kprobe","_printk"})
                m=named[".modinfo"]; meta=data[m[4]:m[4]+m[5]]
                self.assertIn(VERMAGIC,meta); self.assertIn(b"name=mtmlo_observer\0",meta)
                m=named[".gnu.linkonce.this_module"]
                self.assertEqual((m[5],m[8]),(0x680,64))
                r=named[".rela.gnu.linkonce.this_module"]
                rels=[struct.unpack_from("<QQq",data,o) for o in range(r[4],r[4]+r[5],24)]
                self.assertEqual({x[0] for x in rels},{0x178,0x600})
                for off,info,addend in rels:
                    target=symbols[info>>32]
                    expected=byname["mtmlo_init" if off==0x178 else "mtmlo_exit"]
                    self.assertEqual(info & 0xffffffff,257)
                    self.assertEqual((target[1],target[2]+addend),(expected[1],expected[2]))
                v=named["__versions"]; vb=data[v[4]:v[4]+v[5]]
                entries={vb[o+8:o+64].split(b"\0",1)[0]:struct.unpack_from("<Q",vb,o)[0] for o in range(0,len(vb),64)}
                self.assertEqual(entries[b"module_layout"],0x60801db3)
                for s in (".plt",".init.plt",".text.ftrace_trampoline",".init.text.ftrace_trampoline"):
                    self.assertIn(s,named)
                # Build must not silently overwrite a previous artifact.
                second=subprocess.run(cmd,capture_output=True,text=True)
                self.assertNotEqual(second.returncode,0)
                self.assertEqual(out.read_bytes(),data)

if __name__=="__main__": unittest.main()
