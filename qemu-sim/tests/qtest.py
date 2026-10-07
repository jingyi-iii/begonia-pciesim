#!/usr/bin/env python3
#
# begonia-pciesim / qemu-sim
# A tiny client for QEMU's qtest protocol, stdlib only.
#
# qtest is the interface QEMU's own test suite uses to drive a machine from the
# outside: it hands you the guest's I/O ports and physical address space, so a
# test can enumerate PCI, poke BARs and inspect guest RAM without booting an OS
# and without a driver. That is exactly the level this project is about -- the
# fabric (root complex, config space, address space, MSI-X) -- with the
# difference that everything is assertable instead of eyeballed in a console.
#
# Protocol: one ASCII line per command, one line of response. Reads answer
#   OK 0x<value>
# and everything else answers OK, or FAIL <message> on error.
#
import os
import socket
import subprocess
import sys
import tempfile
import time


class QtestError(RuntimeError):
    pass


class Qtest:
    def __init__(self, qemu_bin, args, timeout=30.0):
        self.cwd = tempfile.mkdtemp(prefix="qtest-")
        self.sock_path = os.path.join(self.cwd, "qtest.sock")
        self.stderr_path = os.path.join(self.cwd, "qemu.stderr")
        self._stderr_file = open(self.stderr_path, "wb")

        cmd = [qemu_bin, "-qtest", f"unix:{self.sock_path},server=on,wait=off", *args]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=self._stderr_file,      # QEMU talks to us over the socket, not stdout
            stderr=subprocess.STDOUT,
        )

        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.sock_path):
                try:
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.connect(self.sock_path)
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
            if self.proc.poll() is not None:
                raise QtestError(f"QEMU exited early (rc={self.proc.returncode}) while "
                                 f"starting qtest:\n{self.stderr()}")
            time.sleep(0.02)
        else:
            self.close()
            raise QtestError(f"timed out waiting for the qtest socket at {self.sock_path}\n"
                             f"{self.stderr()}")

        self._reader = self.sock.makefile("rb")

    # ---------------------------------------------------------------- plumbing
    def _cmd(self, line):
        self.sock.sendall((line + "\n").encode())
        resp = self._reader.readline()
        if not resp:
            raise QtestError(f"no response to {line!r} (QEMU died?)\n{self.stderr()}")
        resp = resp.decode(errors="replace").strip()
        if not resp.startswith("OK"):
            raise QtestError(f"qtest rejected {line!r}: {resp}")
        parts = resp.split(None, 1)
        return parts[1] if len(parts) > 1 else ""

    def _cmd_value(self, line):
        return int(self._cmd(line), 16)

    def stderr(self):
        try:
            self._stderr_file.flush()
            with open(self.stderr_path) as f:
                return f.read()
        except OSError:
            return ""

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._stderr_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------ qtest verbs
    # I/O ports
    def outb(self, addr, val):  self._cmd(f"outb 0x{addr:x} 0x{val:x}")
    def outw(self, addr, val):  self._cmd(f"outw 0x{addr:x} 0x{val:x}")
    def outl(self, addr, val):  self._cmd(f"outl 0x{addr:x} 0x{val:x}")
    def inb(self, addr):        return self._cmd_value(f"inb 0x{addr:x}")
    def inw(self, addr):        return self._cmd_value(f"inw 0x{addr:x}")
    def inl(self, addr):        return self._cmd_value(f"inl 0x{addr:x}")

    # guest physical memory
    def readb(self, addr):      return self._cmd_value(f"readb 0x{addr:x}")
    def readw(self, addr):      return self._cmd_value(f"readw 0x{addr:x}")
    def readl(self, addr):      return self._cmd_value(f"readl 0x{addr:x}")
    def readq(self, addr):      return self._cmd_value(f"readq 0x{addr:x}")
    def writeb(self, addr, val): self._cmd(f"writeb 0x{addr:x} 0x{val:x}")
    def writew(self, addr, val): self._cmd(f"writew 0x{addr:x} 0x{val:x}")
    def writel(self, addr, val): self._cmd(f"writel 0x{addr:x} 0x{val:x}")
    def writeq(self, addr, val): self._cmd(f"writeq 0x{addr:x} 0x{val:x}")
    def memset(self, addr, val, n): self._cmd(f"memset 0x{addr:x} 0x{val:x} 0x{n:x}")


# --------------------------------------------------------------------- PCI
#
# Legacy 0xCF8/0xCFC configuration mechanism. QEMU's q35 root complex implements
# it (as well as ECAM, which a test could use just as well); 0xCF8/0xCFC needs
# no setup, so it keeps the test readable.
CFG_ADDR = 0xCF8
CFG_DATA = 0xCFC


def cfg_addr(bus, dev, fn, off):
    return 0x80000000 | (bus << 16) | (dev << 11) | (fn << 8) | (off & 0xFC)


class Pci:
    def __init__(self, q):
        self.q = q

    def read32(self, bus, dev, fn, off):
        assert off % 4 == 0
        self.q.outl(CFG_ADDR, cfg_addr(bus, dev, fn, off))
        return self.q.inl(CFG_DATA)

    def write32(self, bus, dev, fn, off, val):
        assert off % 4 == 0
        self.q.outl(CFG_ADDR, cfg_addr(bus, dev, fn, off))
        self.q.outl(CFG_DATA, val)

    def read16(self, bus, dev, fn, off):
        return (self.read32(bus, dev, fn, off & ~0x3) >> ((off & 0x2) * 8)) & 0xFFFF

    def read8(self, bus, dev, fn, off):
        return (self.read32(bus, dev, fn, off & ~0x3) >> ((off & 0x3) * 8)) & 0xFF

    def write16(self, bus, dev, fn, off, val):
        base = off & ~0x3
        cur = self.read32(bus, dev, fn, base)
        shift = (off & 0x2) * 8
        self.write32(bus, dev, fn, base,
                     (cur & ~(0xFFFF << shift)) | ((val & 0xFFFF) << shift))

    def vendor_device(self, bus, dev, fn):
        v = self.read32(bus, dev, fn, 0x00)
        if v == 0xFFFFFFFF or v == 0x00000000:
            return None
        return (v & 0xFFFF, (v >> 16) & 0xFFFF)

    def find(self, bus, vendor, device):
        """Every function on `bus` matching vendor:device, as (dev, fn)."""
        found = []
        for dev in range(32):
            for fn in range(8):
                ids = self.vendor_device(bus, dev, fn)
                if ids == (vendor, device):
                    found.append((dev, fn))
        return found

    def capabilities(self, bus, dev, fn):
        """Walk the capability list; returns {cap_id: offset}."""
        status = self.read16(bus, dev, fn, 0x06)
        if not (status & (1 << 4)):
            return {}
        caps = {}
        ptr = self.read8(bus, dev, fn, 0x34) & 0xFC
        seen = set()
        while ptr and ptr not in seen and len(seen) < 48:
            seen.add(ptr)
            cap_id = self.read8(bus, dev, fn, ptr)
            nxt = self.read8(bus, dev, fn, ptr + 1) & 0xFC
            caps.setdefault(cap_id, ptr)
            ptr = nxt
        return caps

    def bar_address(self, bus, dev, fn, index):
        """Size and current address of BAR `index`, without disturbing it."""
        off = 0x10 + 4 * index
        orig = self.read32(bus, dev, fn, off)
        self.write32(bus, dev, fn, off, 0xFFFFFFFF)
        mask = self.read32(bus, dev, fn, off)
        self.write32(bus, dev, fn, off, orig)
        if mask == 0 or mask == 0xFFFFFFFF:
            return {"present": False, "size": 0, "address": 0, "is_64": False}

        is_io = bool(mask & 0x1)
        is_64 = (not is_io) and ((mask & 0x6) == 0x4)
        size = (~(mask & 0xFFFFFFF0) + 1) & 0xFFFFFFFF if not is_64 else \
               (~(mask & 0xFFFFFFF0) + 1) & 0xFFFFFFFF
        address = orig & 0xFFFFFFF0
        if is_64:
            hi = self.read32(bus, dev, fn, off + 4)
            address |= hi << 32
        return {"present": True, "size": size, "address": address,
                "is_64": is_64, "is_io": is_io, "raw": orig}

    def set_bar(self, bus, dev, fn, index, address):
        off = 0x10 + 4 * index
        self.write32(bus, dev, fn, off, (address & 0xFFFFFFF0) | 0x4)  # keep 64-bit + mem
        self.write32(bus, dev, fn, off + 4, address >> 32)

    def command_bits(self, bus, dev, fn, bits):
        cur = self.read16(bus, dev, fn, 0x04)
        self.write16(bus, dev, fn, 0x04, cur | bits)
        return self.read16(bus, dev, fn, 0x04)


def main():
    """Self-check: start QEMU if asked to, otherwise print usage."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    with Qtest(sys.argv[1], ["-M", "q35", "-display", "none", "-m", "256M"]) as q:
        pci = Pci(q)
        for dev in range(32):
            ids = pci.vendor_device(0, dev, 0)
            if ids:
                print(f"  bus0 dev{dev} fn0: {ids[0]:04x}:{ids[1]:04x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
