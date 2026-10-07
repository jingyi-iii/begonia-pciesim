#!/usr/bin/env python3
#
# begonia-pciesim / qemu-sim
# Functional test for the my-pcie-dev device model, driven over qtest.
#
# What this proves, and at which level:
#
#   * the machine enumerates our device from configuration space (vendor/device,
#     header type, class, revision) -- the "root complex + config space" claim
#   * BAR0 is a 16 KB 64-bit memory BAR, reports its size correctly, and can be
#     assigned and enabled like any other device's
#   * the register file behaves: read-only registers stay read-only, partial
#     writes touch only the bytes they cover, a write-to-clear status clears
#   * a DOORBELL drives a real DMA through the PCI address space: data appears
#     in guest memory at the destination and the source is untouched
#   * the length guard rejects an impossible transfer with the error bit
#   * MSI-X is present, has 4 vectors, and its table and PBA are where the
#     capability says they are (inside BAR0)
#   * an unmasked vector delivers a real MSI message: the vector's data lands at
#     the vector's address in guest RAM
#   * a masked vector (both per-vector and by the function mask) sets the PBA
#     bit instead of delivering
#   * IRQ_MASK suppresses a raise, and IRQ_COUNT counts what got through
#   * a Function Level Reset puts the register file back to its reset values,
#     leaves the device enumerable, and leaves it still able to deliver
#     interrupts -- the check that the MSI-X vectors are declared once, in
#     realize, and not re-declared on every reset
#
# Everything runs without a guest OS: qtest hands us the guest's I/O ports,
# configuration space and physical memory, so each claim above is an assertion
# rather than something read off a scrolling serial console. `make guest` boots
# a real Linux for the demonstration a human would do by hand; this file is the
# part that can run in CI.
#
# QEMU is started with `-accel qtest`, the setting QEMU's own qtest suite uses:
# no guest code is ever executed, so nothing here depends on TCG, KVM or root.
#
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qtest import Qtest, Pci, QtestError          # noqa: E402

# ---------------------------------------------------------------------------
# The contract with device/my_pcie_dev.c. If you change the register map there,
# change it here too -- that is the point of the test.
# ---------------------------------------------------------------------------
VENDOR_ID       = 0x1AF4
DEVICE_ID       = 0x1000
REVISION        = 0x01
BAR_SIZE        = 0x4000
MSIX_VECTORS    = 4
MSIX_TABLE_OFF  = 0x1000
MSIX_PBA_OFF    = 0x2000

REG_MAGIC       = 0x00
REG_VERSION     = 0x04
REG_SCRATCH     = 0x08
REG_DMA_STATUS  = 0x0C
REG_DOORBELL    = 0x10
REG_DMA_SRC     = 0x14
REG_DMA_DST     = 0x18
REG_DMA_LEN     = 0x1C
REG_IRQ_RAISE   = 0x20
REG_IRQ_MASK    = 0x24
REG_IRQ_COUNT   = 0x28

MAGIC_VALUE     = 0x50434945
VERSION_VALUE   = 0x00010001

DMA_BUSY        = 1 << 0
DMA_DONE        = 1 << 1
DMA_ERROR       = 1 << 2

# Where we tell QEMU to put BAR0, and where the test keeps its scratch memory.
# Both are in the 32-bit PCI hole / low RAM of a 256 MB q35 machine, clear of
# each other and of anything QEMU maps by default.
BAR_BASE        = 0xFE000000
DMA_SRC_ADDR    = 0x00020000
DMA_DST_ADDR    = 0x00030000
MSI_MSG_ADDR    = 0x00010000

PCI_COMMAND     = 0x04
PCI_STATUS      = 0x06
PCI_REVISION_ID = 0x08
PCI_CLASS       = 0x0B       # class code lives in the top 3 bytes of 0x09..0x0B
PCI_HEADER_TYPE = 0x0E       # byte-wide, and NOT 4-byte aligned
PCI_BAR0        = 0x10
PCI_CAP_PTR     = 0x34

# PCI_STATUS bit 4 says a capability list is present. Polled by wait_for_device():
# it is one of the things a half-realized device does not have yet.
PCI_STATUS_CAP_LIST = 1 << 4

CMD_MEMORY      = 1 << 1
CMD_BUS_MASTER  = 1 << 2

CAP_ID_PCI_EXPRESS = 0x10
CAP_ID_MSIX        = 0x11

# PCIe capability register layout, relative to the capability's offset.
PCI_EXP_DEVCAP = 0x04
PCI_EXP_DEVCTL = 0x08

# PCI_EXP_FLAGS has the capability version in bits 0-3 and the device/port type
# in bits 4-7 (linux/pci_regs.h: PCI_EXP_FLAGS_VERS / PCI_EXP_FLAGS_TYPE).
PCI_EXP_TYPE_ENDPOINT = 0x0
PCI_EXP_TYPE_RC_END   = 0x9

# Function Level Reset: advertised in Device Capabilities, triggered by writing
# Device Control bit 15. BCR_FLR always reads back 0.
PCI_EXP_DEVCAP_FLR     = 0x10000000
PCI_EXP_DEVCTL_BCR_FLR = 0x8000

MSIX_CTRL_ENABLE  = 1 << 15
MSIX_CTRL_FMASK   = 1 << 14
MSIX_VEC_MASK_BIT = 1 << 0


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------
TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Env:
    """Everything a test needs: qtest connection, PCI accessors, device id."""

    def __init__(self, q):
        self.q = q
        self.pci = Pci(q)
        self.bus = 0
        self.dev = None
        self.fn = 0

    # --- configuration space shorthands -----------------------------------
    def cfg_read32(self, off):
        return self.pci.read32(self.bus, self.dev, self.fn, off)

    def cfg_write32(self, off, val):
        self.pci.write32(self.bus, self.dev, self.fn, off, val)

    def cfg_read16(self, off):
        return self.pci.read16(self.bus, self.dev, self.fn, off)

    def cfg_write16(self, off, val):
        self.pci.write16(self.bus, self.dev, self.fn, off, val)

    # --- BAR0 -------------------------------------------------------------
    def bar_read32(self, off):
        return self.q.readl(BAR_BASE + off)

    def bar_write32(self, off, val):
        self.q.writel(BAR_BASE + off, val)

    def bar_read64(self, off):
        return self.q.readq(BAR_BASE + off)

    def bar_write64(self, off, val):
        self.q.writeq(BAR_BASE + off, val)

    def bar_read8(self, off):
        return self.q.readb(BAR_BASE + off)

    def bar_write8(self, off, val):
        self.q.writeb(BAR_BASE + off, val)

    def bar_write16(self, off, val):
        self.q.writew(BAR_BASE + off, val)

    # --- convenience ------------------------------------------------------
    def reassign_bar(self):
        """Map BAR0 at BAR_BASE and enable memory decode + bus mastering.

        A real driver (or firmware) does this once at probe time. A test has to
        do it itself -- with -qtest there is no firmware -- and it has to be
        done again after any reset, because pci_do_device_reset() clears the
        BAR address and the command register bits for every PCI function.
        """
        self.pci.set_bar(self.bus, self.dev, self.fn, 0, BAR_BASE)
        self.pci.command_bits(self.bus, self.dev, self.fn,
                              CMD_MEMORY | CMD_BUS_MASTER)

    def dma_status(self):
        return self.bar_read32(REG_DMA_STATUS)

    def clear_dma_status(self):
        self.bar_write32(REG_DMA_STATUS, 0xFFFFFFFF)

    def do_dma(self, src, dst, length, timeout=200):
        """Program a transfer and ring the doorbell; returns the status."""
        self.bar_write32(REG_DMA_SRC, src)
        self.bar_write32(REG_DMA_DST, dst)
        self.bar_write32(REG_DMA_LEN, length)
        self.bar_write32(REG_DOORBELL, 1)
        # The device performs the copy synchronously inside the MMIO write, so
        # the status is already final; poll anyway so the test does not silently
        # depend on that implementation detail.
        for _ in range(timeout):
            st = self.dma_status()
            if st & (DMA_DONE | DMA_ERROR):
                return st
        return self.dma_status()

    def msix_control(self):
        cap = self.msix_cap
        return self.cfg_read16(cap + 2)

    def msix_set_control(self, bits):
        cap = self.msix_cap
        self.cfg_write16(cap + 2, self.msix_control() | bits)

    def msix_clear_control(self, bits):
        cap = self.msix_cap
        self.cfg_write16(cap + 2, self.msix_control() & ~bits)

    def msix_program_vector(self, vector, addr, data, masked=False):
        """Point a vector at a guest RAM address, the way a driver would."""
        base = MSIX_TABLE_OFF + vector * 16
        self.bar_write32(base + 0, addr & 0xFFFFFFFF)          # message address low
        self.bar_write32(base + 4, (addr >> 32) & 0xFFFFFFFF)  # message address high
        self.bar_write32(base + 8, data)                       # message data
        self.bar_write32(base + 12, MSIX_VEC_MASK_BIT if masked else 0)

    def msix_mask_vector(self, vector, masked=True):
        base = MSIX_TABLE_OFF + vector * 16
        ctrl = self.bar_read32(base + 12)
        self.bar_write32(base + 12, (ctrl | MSIX_VEC_MASK_BIT) if masked
                         else (ctrl & ~MSIX_VEC_MASK_BIT))

    def pba(self):
        return self.bar_read32(MSIX_PBA_OFF)

    def raise_vector(self, vector):
        # not `raise`: that is a Python keyword, so a method with that name is
        # a SyntaxError for the whole file -- which is how this one was found.
        self.bar_write32(REG_IRQ_RAISE, vector)


DEVICE_READY_TIMEOUT = 5.0


def _probe_device(env):
    """One attempt at "is the model there and fully realized?".

    Returns (dev, fn, msix_cap) or None. The MSI-X capability is checked for
    because it is the *last* thing my_pcie_dev_realize() adds: if the device
    answers at 0x00 but has no capability list yet, realize() is still running.
    """
    hits = env.pci.find(0, VENDOR_ID, DEVICE_ID)
    if not hits:
        return None
    dev, fn = hits[0]
    if not env.pci.read16(0, dev, fn, PCI_STATUS) & PCI_STATUS_CAP_LIST:
        return None
    caps = env.pci.capabilities(0, dev, fn)
    if CAP_ID_MSIX not in caps or CAP_ID_PCI_EXPRESS not in caps:
        return None
    return dev, fn, caps[CAP_ID_MSIX]


def wait_for_device(env, timeout=DEVICE_READY_TIMEOUT):
    """
    Poll until the device is enumerable *and* carries its capabilities.

    This is not belt-and-braces, it is a real race that bites about a third of
    runs. QEMU creates the qtest chardev in qemu_create_late_backends(), which
    is ahead of the point where the board's devices are realized, so a client
    that connects the instant the socket appears can read configuration space
    around a half-built device tree: offset 0x00 answers with the right ids
    (PCIDevice sets those during construction) while 0x34 -- the capability
    pointer -- still reads as an unclaimed 0xffffffff. The symptom is a
    misleading "the device has no MSI-X capability".

    Measured here before the fix: 7 passes in 10. With a 50 ms pause before the
    first command: 8 in 8 -- so it is time, not logic. Polling is used instead
    of a sleep because a fixed delay is either too short on a loaded machine or
    wasted time on an idle one.

    Bounded on purpose: a device that genuinely is not in this binary still
    fails, just after the timeout, and with the diagnosis rather than a
    shrug.
    """
    deadline = time.time() + timeout
    while True:
        got = _probe_device(env)
        if got:
            return got
        if time.time() >= deadline:
            hits = env.pci.find(0, VENDOR_ID, DEVICE_ID)
            if not hits:
                raise QtestError(
                    f"no {VENDOR_ID:04x}:{DEVICE_ID:04x} device on bus 0 after "
                    f"{timeout:g}s; is the device compiled into this QEMU?")
            dev, fn = hits[0]
            caps = env.pci.capabilities(0, dev, fn)
            raise QtestError(
                f"{VENDOR_ID:04x}:{DEVICE_ID:04x} is on the bus but has no "
                f"MSI-X capability after {timeout:g}s (capabilities: "
                f"{[hex(c) for c in caps]}); did realize() fail?")
        time.sleep(0.02)


def make_env(q):
    """Wait for the device to be fully realized, then set it up like firmware."""
    env = Env(q)
    env.dev, env.fn, env.msix_cap = wait_for_device(env)

    # A real driver (or firmware) maps the BAR and enables memory + bus master.
    # With -qtest there is no firmware, so the test does it, which is also the
    # only way the BAR address is known: QEMU maps a BAR when it is written.
    bar0 = env.pci.read32(0, env.dev, env.fn, PCI_BAR0)
    assert bar0 & 0x4, f"BAR0 is not a 64-bit memory BAR (0x{bar0:08x})"
    env.reassign_bar()
    return env


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@test
def test_device_is_enumerable(env):
    vd = env.cfg_read32(0x00)
    assert vd & 0xFFFF == VENDOR_ID, f"vendor id 0x{vd & 0xFFFF:04x}"
    assert vd >> 16 == DEVICE_ID, f"device id 0x{vd >> 16:04x}"
    assert env.cfg_read16(PCI_HEADER_TYPE) & 0xFF == 0x00, \
        "header type is not a single-function endpoint"
    rev = env.cfg_read32(PCI_REVISION_ID) & 0xFF
    assert rev == REVISION, f"revision 0x{rev:02x} != 0x{REVISION:02x}"
    klass = (env.cfg_read32(0x08) >> 8) & 0xFFFFFF
    assert klass != 0 and klass != 0xFFFFFF, f"class code 0x{klass:06x} looks unset"
    # exactly one function of this device, as the model describes
    assert len(env.pci.find(0, VENDOR_ID, DEVICE_ID)) == 1, "device present more than once"


@test
def test_pcie_capability_present(env):
    """Walk to the PCIe capability and read its two-bit-field flags register."""
    caps = env.pci.capabilities(env.bus, env.dev, env.fn)
    assert caps, "the device advertises no capability list at all"
    assert CAP_ID_PCI_EXPRESS in caps, f"no PCIe capability; got {[hex(c) for c in caps]}"
    off = caps[CAP_ID_PCI_EXPRESS]

    flags = env.cfg_read16(off + 2)         # PCI_EXP_FLAGS
    version = flags & 0x000F                # bits 0-3
    cap_type = (flags & 0x00F0) >> 4        # bits 4-7

    assert version in (1, 2), f"PCIe capability version {version} is not 1 or 2"

    # QEMU rewrites a plain endpoint (0x0) into an "RC integrated endpoint"
    # (0x9) when the function sits directly on an express root bus -- which is
    # exactly where `-device my-pcie-dev` puts it on q35. The reason is in
    # pcie_endpoint_cap_common_init() (hw/pci/pcie.c): Windows guests report
    # Code 10 for a regular endpoint on a root complex. Both values describe a
    # function rather than a bridge, which is the property worth asserting.
    assert cap_type in (PCI_EXP_TYPE_ENDPOINT, PCI_EXP_TYPE_RC_END), \
        (f"PCIe capability type {cap_type} is neither an endpoint (0x0) nor an "
         f"RC integrated endpoint (0x9) -- is the device claiming to be a bridge?")

    assert off >= 0x40, f"PCIe capability at 0x{off:02x} overlaps the standard header"
    # The capability is PCI_EXP_VER2_SIZEOF (0x3C) bytes long, which is why the
    # MSI-X capability has to start at 0xC0 and not, say, 0xB0.
    assert off + 0x3C <= 0x100, \
        f"PCIe capability at 0x{off:02x} runs past the 256-byte config space"


@test
def test_bar0_size_and_width(env):
    bar = env.pci.bar_address(env.bus, env.dev, env.fn, 0)
    assert bar["present"], "BAR0 is not implemented"
    assert not bar["is_io"], "BAR0 is an I/O BAR, expected memory"
    assert bar["is_64"], "BAR0 is not 64-bit"
    assert bar["size"] == BAR_SIZE, f"BAR0 size 0x{bar['size']:x} != 0x{BAR_SIZE:x}"


@test
def test_identity_registers(env):
    magic = env.bar_read32(REG_MAGIC)
    version = env.bar_read32(REG_VERSION)
    assert magic == MAGIC_VALUE, f"MAGIC 0x{magic:08x} != 0x{MAGIC_VALUE:08x}"
    assert version == VERSION_VALUE, f"VERSION 0x{version:08x} != 0x{VERSION_VALUE:08x}"
    # a byte read inside a computed register must return the right byte
    assert env.bar_read8(REG_MAGIC + 0) == (MAGIC_VALUE & 0xFF)
    assert env.bar_read8(REG_MAGIC + 3) == ((MAGIC_VALUE >> 24) & 0xFF)


@test
def test_scratch_roundtrip(env):
    for value in (0x00000000, 0xFFFFFFFF, 0xDEADBEEF, 0x00000001):
        env.bar_write32(REG_SCRATCH, value)
        got = env.bar_read32(REG_SCRATCH)
        assert got == value, f"SCRATCH wrote 0x{value:08x}, read 0x{got:08x}"


@test
def test_partial_writes_touch_only_their_bytes(env):
    env.bar_write32(REG_SCRATCH, 0x11223344)
    env.bar_write8(REG_SCRATCH + 1, 0xAA)
    assert env.bar_read32(REG_SCRATCH) == 0x1122AA44, "byte write clobbered other bytes"
    env.bar_write16(REG_SCRATCH + 2, 0xBBCC)
    assert env.bar_read32(REG_SCRATCH) == 0xBBCCAA44, "halfword write clobbered other bytes"


@test
def test_read_only_registers_stay_read_only(env):
    env.bar_write32(REG_MAGIC, 0x0)
    env.bar_write32(REG_VERSION, 0x0)
    assert env.bar_read32(REG_MAGIC) == MAGIC_VALUE, "MAGIC was writable"
    assert env.bar_read32(REG_VERSION) == VERSION_VALUE, "VERSION was writable"


@test
def test_dma_copies_through_the_address_space(env):
    env.clear_dma_status()
    pattern = [0x0123456789ABCDEF, 0xFEDCBA9876543210,
               0x5555555555555555, 0xAAAAAAAAAAAAAAAA]
    for i, word in enumerate(pattern):
        env.q.writeq(DMA_SRC_ADDR + 8 * i, word)
    env.q.memset(DMA_DST_ADDR, 0x00, 8 * len(pattern))

    # the device asks for an interrupt when the transfer completes
    before = env.bar_read32(REG_IRQ_COUNT)
    status = env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 8 * len(pattern))
    assert status & DMA_DONE, f"transfer did not complete (status 0x{status:x})"
    assert not status & DMA_ERROR, f"transfer reported an error (status 0x{status:x})"
    assert env.bar_read32(REG_IRQ_COUNT) == before + 1, \
        "the transfer did not raise an interrupt"

    # the data really went through the address space
    for i, word in enumerate(pattern):
        got = env.q.readq(DMA_DST_ADDR + 8 * i)
        assert got == word, \
            f"dword {i}: guest memory has 0x{got:016x}, expected 0x{word:016x}"

    # a copy, not a move
    for i, word in enumerate(pattern):
        got = env.q.readq(DMA_SRC_ADDR + 8 * i)
        assert got == word, f"source dword {i} was altered by the transfer"


@test
def test_dma_rejects_impossible_length(env):
    env.clear_dma_status()
    status = env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 0x10000)   # > 4096
    assert status & DMA_ERROR, f"over-long transfer not rejected (status 0x{status:x})"
    assert not status & DMA_DONE, "a rejected transfer reported done"

    env.clear_dma_status()
    status = env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 0)          # empty
    assert status & DMA_ERROR, f"zero-length transfer not rejected (status 0x{status:x})"

    # a legal transfer still works afterwards: the error is not sticky
    env.clear_dma_status()
    status = env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 8)
    assert status & DMA_DONE and not status & DMA_ERROR, \
        f"the device did not recover from an error (status 0x{status:x})"


@test
def test_status_register_is_write_to_clear(env):
    env.clear_dma_status()
    assert env.dma_status() == 0, "status did not clear"
    env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 8)
    assert env.dma_status() & DMA_DONE, "done bit missing"
    env.bar_write32(REG_DMA_STATUS, 1)
    assert env.dma_status() == 0, "status did not clear on write"


@test
def test_msix_capability_layout(env):
    caps = env.pci.capabilities(env.bus, env.dev, env.fn)
    assert CAP_ID_MSIX in caps, f"no MSI-X capability; got {[hex(c) for c in caps]}"
    env.msix_cap = caps[CAP_ID_MSIX]
    off = env.msix_cap

    ctrl = env.cfg_read16(off + 2)
    nvectors = (ctrl & 0x07FF) + 1
    assert nvectors == MSIX_VECTORS, f"MSI-X reports {nvectors} vectors, expected {MSIX_VECTORS}"
    assert not ctrl & MSIX_CTRL_ENABLE, "MSI-X is enabled before anything enabled it"

    table = env.cfg_read32(off + 4)
    pba = env.cfg_read32(off + 8)
    table_bir = table & 0x7
    pba_bir = pba & 0x7
    table_off = table & ~0x7
    pba_off = pba & ~0x7
    assert table_bir == 0, f"MSI-X table is in BAR{table_bir}, expected BAR0"
    assert pba_bir == 0, f"MSI-X PBA is in BAR{pba_bir}, expected BAR0"
    assert table_off == MSIX_TABLE_OFF, f"table offset 0x{table_off:x} != 0x{MSIX_TABLE_OFF:x}"
    assert pba_off == MSIX_PBA_OFF, f"PBA offset 0x{pba_off:x} != 0x{MSIX_PBA_OFF:x}"
    assert table_off + nvectors * 16 <= BAR_SIZE, "the table does not fit in BAR0"
    assert pba_off + 4 <= BAR_SIZE, "the PBA does not fit in BAR0"


@test
def test_msix_table_is_mapped_and_writable(env):
    """The table lives inside BAR0, so a driver can program it with normal stores."""
    env.msix_program_vector(0, MSI_MSG_ADDR, 0x5A5A)
    base = MSIX_TABLE_OFF + 0
    assert env.bar_read32(base + 0) == MSI_MSG_ADDR, "table address low did not stick"
    assert env.bar_read32(base + 4) == 0, "table address high did not stay 0"
    assert env.bar_read32(base + 8) == 0x5A5A, "table data did not stick"
    assert env.bar_read32(base + 12) == 0, "vector is masked by default"


@test
def test_msix_delivers_the_message(env):
    env.q.writel(MSI_MSG_ADDR, 0x00000000)
    env.msix_program_vector(0, MSI_MSG_ADDR, 0x1234ABCD)
    before = env.bar_read32(REG_IRQ_COUNT)
    env.msix_set_control(MSIX_CTRL_ENABLE)
    assert env.msix_control() & MSIX_CTRL_ENABLE, "MSI-X enable bit did not stick"

    env.raise_vector(0)
    got = env.q.readl(MSI_MSG_ADDR)
    assert got == 0x1234ABCD, \
        f"no MSI message at 0x{MSI_MSG_ADDR:x}: read 0x{got:08x} -- the vector was " \
        f"not delivered"
    assert env.bar_read32(REG_IRQ_COUNT) == before + 1, "IRQ_COUNT did not count the vector"
    assert env.pba() == 0, "a delivered vector must not set a PBA bit"


@test
def test_masked_vector_sets_pba_instead_of_delivering(env):
    env.q.writel(MSI_MSG_ADDR, 0)
    env.msix_program_vector(1, MSI_MSG_ADDR, 0xCAFEF00D, masked=True)
    env.bar_write32(MSIX_PBA_OFF, 0xFFFFFFFF)        # clear any pending bits

    env.raise_vector(1)
    got = env.q.readl(MSI_MSG_ADDR)
    assert got == 0, f"a masked vector was delivered anyway (RAM has 0x{got:08x})"
    assert env.pba() & (1 << 1), f"masked vector did not set its PBA bit (PBA 0x{env.pba():08x})"
    assert not env.pba() & (1 << 0), "vector 0 became pending without being raised"

    # unmasking does not replay it (no queueing in the device), but a fresh
    # raise after unmasking must go through
    env.msix_mask_vector(1, masked=False)
    env.raise_vector(1)
    got = env.q.readl(MSI_MSG_ADDR)
    assert got == 0xCAFEF00D, f"vector 1 did not deliver after unmasking (got 0x{got:08x})"


@test
def test_function_mask_sets_pba(env):
    env.q.writel(MSI_MSG_ADDR, 0)
    env.msix_program_vector(2, MSI_MSG_ADDR, 0x00C0FFEE)
    env.bar_write32(MSIX_PBA_OFF, 0xFFFFFFFF)
    env.msix_set_control(MSIX_CTRL_FMASK)

    env.raise_vector(2)
    assert env.q.readl(MSI_MSG_ADDR) == 0, "a function-masked device delivered a vector"
    assert env.pba() & (1 << 2), f"function mask did not set the PBA (PBA 0x{env.pba():08x})"

    env.msix_clear_control(MSIX_CTRL_FMASK)
    env.raise_vector(2)
    assert env.q.readl(MSI_MSG_ADDR) == 0x00C0FFEE, "vector 2 did not deliver after the function mask was cleared"


@test
def test_irq_mask_register_suppresses_raises(env):
    """
    IRQ_MASK filters the device's *intent* to raise; MSI-X enable decides whether
    that intent is delivered. Both are checked, because they fail differently:
    a missing mask filter shows up as a spurious interrupt, a missing delivery
    guard as a message in guest memory that nothing asked for.
    """
    env.q.writel(MSI_MSG_ADDR, 0)
    env.msix_program_vector(3, MSI_MSG_ADDR, 0x33333333)
    env.bar_write32(REG_IRQ_MASK, 0)

    # (1) MSI-X disabled: the raise is recorded but must not be delivered
    env.msix_clear_control(MSIX_CTRL_ENABLE)
    before = env.bar_read32(REG_IRQ_COUNT)
    env.raise_vector(3)
    assert env.bar_read32(REG_IRQ_COUNT) == before + 1, \
        "a raise with MSI-X disabled was not recorded at all"
    assert env.q.readl(MSI_MSG_ADDR) == 0, \
        "a vector was delivered while MSI-X was disabled"

    # (2) IRQ_MASK bit set: the raise is filtered out entirely
    before = env.bar_read32(REG_IRQ_COUNT)
    env.bar_write32(REG_IRQ_MASK, 1 << 3)
    env.raise_vector(3)
    assert env.bar_read32(REG_IRQ_COUNT) == before, "IRQ_MASK did not suppress the vector"

    # (3) mask cleared, MSI-X enabled: it counts and it is delivered
    env.bar_write32(REG_IRQ_MASK, 0)
    env.msix_set_control(MSIX_CTRL_ENABLE)
    env.raise_vector(3)
    assert env.bar_read32(REG_IRQ_COUNT) == before + 1, \
        "vector 3 did not raise after the mask was cleared"
    assert env.q.readl(MSI_MSG_ADDR) == 0x33333333, \
        "vector 3 did not deliver after the mask was cleared"

    # (4) an out-of-range vector index must be ignored, not counted
    before = env.bar_read32(REG_IRQ_COUNT)
    env.raise_vector(MSIX_VECTORS)
    assert env.bar_read32(REG_IRQ_COUNT) == before, "an out-of-range vector was counted"


@test
def test_bar_past_the_register_file_is_inert(env):
    """0x1000..0x3FFF is not the register file: writes there must not alias it."""
    env.bar_write32(REG_SCRATCH, 0xA5A5A5A5)
    for off in (0x0800, 0x3000, 0x3FFC):
        env.bar_write32(off, 0xFFFFFFFF)
    assert env.bar_read32(REG_SCRATCH) == 0xA5A5A5A5, \
        "a write outside the register file reached the register file"
    assert env.bar_read32(REG_MAGIC) == MAGIC_VALUE, "MAGIC changed after stray writes"


@test
def test_flr_resets_the_function(env):
    """
    Function Level Reset, driven entirely from configuration space.

    There is no way to ask qtest for a system reset: QEMU's own
    qtest_system_reset() sends a QMP command, and this client only speaks the
    qtest line protocol on a UNIX socket. But a PCIe function can always be
    reset by writing bit 15 of its Device Control register, which needs nothing
    but 0xCF8/0xCFC. That makes the device's reset path (the Resettable hold
    phase in device/my_pcie_dev.c) reachable in CI, and it is the same path a
    guest driver takes on unbind.

    This runs last on purpose: an FLR clears the MSI-X table for every vector,
    so the tests before it would have to re-program their vectors.
    """
    exp = env.pci.capabilities(env.bus, env.dev, env.fn)[CAP_ID_PCI_EXPRESS]

    # Without this assertion an unwired FLR would make the whole test vacuous:
    # the write below would be dropped by the read-only mask and every
    # "survived FLR" check would pass having reset nothing.
    devcap = env.cfg_read32(exp + PCI_EXP_DEVCAP)
    assert devcap & PCI_EXP_DEVCAP_FLR, \
        "the device does not advertise Function Level Reset"

    # Leave state behind for the reset to clear. IRQ_MASK gets a bit that is not
    # vector 0's, so that raising vector 0 below still counts.
    env.bar_write32(REG_SCRATCH, 0x12345678)
    env.bar_write32(REG_IRQ_MASK, 1 << 2)
    assert env.bar_read32(REG_SCRATCH) == 0x12345678, "setup did not stick"

    before = env.bar_read32(REG_IRQ_COUNT)
    env.raise_vector(0)
    assert env.bar_read32(REG_IRQ_COUNT) == before + 1, "setup did not raise anything"

    # Trigger it.
    env.cfg_write16(exp + PCI_EXP_DEVCTL, PCI_EXP_DEVCTL_BCR_FLR)

    # FLR clears the BAR assignment and the command register, exactly as a cold
    # reset does, so the function has to be mapped and enabled again before its
    # registers can be reached. That is pci_do_device_reset() being faithful to
    # the spec, not the device misbehaving -- and it is also why BAR0 reads
    # *before* this point are meaningless.
    env.reassign_bar()

    assert env.bar_read32(REG_SCRATCH) == 0, "SCRATCH survived FLR"
    assert env.bar_read32(REG_IRQ_MASK) == 0, "IRQ_MASK survived FLR"
    assert env.bar_read32(REG_IRQ_COUNT) == 0, "IRQ_COUNT survived FLR"
    assert env.bar_read32(REG_DMA_STATUS) == 0, "DMA_STATUS survived FLR"

    # The function is still itself, and still enumerable.
    vd = env.cfg_read32(0x00)
    assert vd & 0xFFFF == VENDOR_ID and vd >> 16 == DEVICE_ID, \
        f"the device lost its identity across FLR (got 0x{vd:08x})"
    assert env.bar_read32(REG_MAGIC) == MAGIC_VALUE, "MAGIC is wrong after FLR"
    assert env.bar_read32(REG_VERSION) == VERSION_VALUE, "VERSION is wrong after FLR"

    # The one that actually matters. msix_reset() zeroes the MSI-X table and
    # clears the enable bit, so a driver must re-program and re-enable -- but it
    # must NOT have to re-declare the vector as used, because msix_reset() never
    # touches dev->msix_entry_used. If msix_vector_use() were undone by reset,
    # the raise below would be counted and silently not delivered.
    env.q.writel(MSI_MSG_ADDR, 0)
    env.msix_program_vector(0, MSI_MSG_ADDR, 0x0BADF00D)
    env.msix_set_control(MSIX_CTRL_ENABLE)
    assert env.msix_control() & MSIX_CTRL_ENABLE, "MSI-X would not re-enable after FLR"

    env.raise_vector(0)
    got = env.q.readl(MSI_MSG_ADDR)
    assert got == 0x0BADF00D, \
        (f"the function could not deliver an interrupt after FLR (RAM has "
         f"0x{got:08x}) -- if this is 0x00000000 the MSI-X vector was "
         f"un-declared by reset and needs msix_vector_use() again in the "
         f"device's reset path")
    assert env.bar_read32(REG_IRQ_COUNT) == 1, "IRQ_COUNT did not restart at 0 after FLR"

    # A transfer still works end to end after the reset.
    env.q.writeq(DMA_SRC_ADDR, 0x0123456789ABCDEF)
    env.q.memset(DMA_DST_ADDR, 0, 8)
    env.clear_dma_status()
    status = env.do_dma(DMA_SRC_ADDR, DMA_DST_ADDR, 8)
    assert status & DMA_DONE and not status & DMA_ERROR, \
        f"a DMA after FLR failed (status 0x{status:x})"
    assert env.q.readq(DMA_DST_ADDR) == 0x0123456789ABCDEF, \
        "the DMA after FLR did not copy the data"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
def run(qemu_bin, extra_args, only=None, verbose=False):
    selected = [t for t in TESTS if only is None or t.__name__ == only or only in t.__name__]
    if not selected:
        print(f"no test matches {only!r}", file=sys.stderr)
        return 2

    # `-accel qtest` is what QEMU's own qtest suite passes (tests/qtest/
    # libqtest.c, qtest_qemu_args()), and it is not cosmetic: it replaces TCG
    # with QEMU's dummy-CPU accelerator, so nothing executes guest code and
    # machine init is far quicker. Everything this file asserts -- config space,
    # BARs, MSI-X, DMA through the address space -- is a device-model property
    # and does not need an emulated CPU. It also shrinks the startup window that
    # wait_for_device() exists to cover. Pass your own -accel in `extra` to
    # override it, e.g. `-- -accel tcg`, which is how that window gets tested.
    args = ["-M", "q35", "-display", "none", "-m", "256M", "-device", "my-pcie-dev"]
    if "-accel" not in extra_args:
        args += ["-accel", "qtest"]
    args += list(extra_args)
    results = []

    with Qtest(qemu_bin, args) as q:
        try:
            env = make_env(q)
        except (QtestError, AssertionError) as e:
            print(f"[test] fatal: {e}", file=sys.stderr)
            print("[test] QEMU said:", file=sys.stderr)
            print(q.stderr()[-2000:], file=sys.stderr)
            return 2

        print(f"[test] qemu        = {qemu_bin}")
        print(f"[test] device      = {VENDOR_ID:04x}:{DEVICE_ID:04x} at 00:{env.dev:02x}.{env.fn}")
        print(f"[test] BAR0        = 0x{BAR_BASE:08x} (size 0x{BAR_SIZE:x})")

        for fn in selected:
            try:
                fn(env)
            except (AssertionError, QtestError) as e:
                results.append((fn.__name__, False, str(e)))
                print(f"[test] FAIL {fn.__name__}: {e}")
                if verbose:
                    print(q.stderr()[-2000:])
            except Exception as e:                          # noqa: BLE001
                results.append((fn.__name__, False, f"{type(e).__name__}: {e}"))
                print(f"[test] FAIL {fn.__name__}: {type(e).__name__}: {e}")
            else:
                results.append((fn.__name__, True, ""))
                print(f"[test] ok   {fn.__name__}")

        qemu_stderr = q.stderr()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed

    width = max(len(name) for name, _, _ in results) + 2
    print("  " + "*" * (width + 12))
    print("  ** %-*s %-10s **" % (width, "TEST", "STATUS"))
    print("  " + "*" * (width + 12))
    for name, ok, msg in results:
        print("  ** %-*s %-10s **" % (width, name, "PASS" if ok else "FAIL"))
    print("  " + "*" * (width + 12))
    print("  ** %-*s %-10s **" % (width, f"TESTS={len(results)} PASS={passed} FAIL={failed}",
                                   ""))
    print("  " + "*" * (width + 12))

    if failed:
        print("\nFailures:")
        for name, ok, msg in results:
            if not ok:
                print(f"  {name}: {msg}")
        if qemu_stderr.strip():
            print("\nQEMU stderr:")
            print("\n".join("  " + ln for ln in qemu_stderr.strip().splitlines()[-30:]))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--qemu", default=os.environ.get("QEMU_BIN", "qemu-system-x86_64"),
                    help="the QEMU binary to test [QEMU_BIN]")
    ap.add_argument("--only", help="run only tests whose name contains this string")
    ap.add_argument("--list", action="store_true", help="list the tests and exit")
    ap.add_argument("-v", "--verbose", action="store_true", help="dump QEMU stderr on failure")
    ap.add_argument("extra", nargs="*", help="extra arguments for QEMU")
    args = ap.parse_args()

    if args.list:
        for t in TESTS:
            print(t.__name__)
        return 0
    return run(args.qemu, args.extra, only=args.only, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
