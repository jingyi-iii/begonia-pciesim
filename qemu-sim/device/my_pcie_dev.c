/*
 * begonia-pciesim / qemu-sim -- a minimal PCIe endpoint model for QEMU.
 *
 * This is the smallest device that still exercises what a pure RTL testbench
 * cannot give you -- real configuration space, a memory BAR, an address space
 * for DMA, and MSI-X.
 *
 * BAR0, 16 KB, split in three:
 *
 *     0x0000 - 0x0FFF   register file, handled by my_mmio_ops below
 *     0x1000 - 0x103F   MSI-X vector table (4 entries x 16 bytes), by msix_init()
 *     0x2000 - 0x2007   MSI-X PBA (1 x 8 bytes), also by msix_init()
 *
 * Anything else inside the BAR (0x1040-0x1FFF, 0x2008-0x3FFF) is a hole: it
 * lands in my_mmio_ops and is rejected there. One function, no legacy INTx, no
 * expansion ROM, no hotplug support.
 *
 * Register file (32-bit, little endian, offsets relative to BAR0):
 *
 *   0x00  R    MAGIC        0x50434945, so a guest can tell the device is alive
 *   0x04  R    VERSION      0x00010001
 *   0x08  RW   SCRATCH      free scratch register
 *   0x0C  W1C  DMA_STATUS   bit 0 busy, bit 1 done, bit 2 error; write to clear
 *   0x10  W    DOORBELL     write anything to start the transfer below
 *   0x14  RW   DMA_SRC      guest physical source address   (32-bit, see below)
 *   0x18  RW   DMA_DST      guest physical destination address
 *   0x1C  RW   DMA_LEN      bytes to copy, 1..4096
 *   0x20  W    IRQ_RAISE    write a vector index to raise that MSI-X vector
 *   0x24  RW   IRQ_MASK     bit N set = ignore vector N in IRQ_RAISE
 *   0x28  R    IRQ_COUNT    vectors raised since reset
 *
 * On DOORBELL the device performs a real DMA through the PCI address space:
 * pci_dma_read() into a bounce buffer and pci_dma_write() back out -- the same
 * path a driver-written ring buffer takes -- and then raises vector 0. Doing it
 * synchronously inside the write handler is what e1000 and friends do; a
 * production device would hand it to a bottom half.
 *
 * Deliberate simplifications, all of them visible to a user:
 *   - DMA_SRC / DMA_DST are 32-bit, which keeps the register map simple.
 *     That covers any guest with < 4 GB of RAM (including the tests here); a
 *     real device needs a hi/lo pair, or a 64-bit descriptor in memory.
 *   - The DMA is a single contiguous copy; no scatter/gather descriptors.
 *   - Reads and writes are level-triggered MMIO only; there is no MSI (only
 *     MSI-X), no AER, no SR-IOV, no power management capability.
 *
 * Function Level Reset is the one piece of PCIe machinery this device does
 * implement, because it is otherwise impossible to exercise the reset path at
 * all from a test that only has configuration space and guest memory -- see
 * my_pcie_dev_config_write().
 *
 * ---------------------------------------------------------------------------
 * Notes for anyone copying an older PCIe device template (this device was
 * written from one; several of its lines do not compile on QEMU 11.1.2).
 * Verified against the upstream v11.1.2 sources:
 *
 *   1. There is no TYPE_PCIE_DEVICE any more. A PCIe function now declares the
 *      INTERFACE_PCIE_DEVICE *interface* instead, and hw/pci/pci.c:2300 turns
 *      that into QEMU_PCI_CAP_EXPRESS -- as long as the device does NOT also
 *      claim INTERFACE_CONVENTIONAL_PCI_DEVICE. This is what e1000e, nvme and
 *      xhci do, so we follow them.
 *   2. msix_init() takes ten arguments now: the table and PBA each get their
 *      own MemoryRegion *and* BAR number, plus a capability offset, plus an
 *      Error**. The eight-argument form is the pre-4.0 API.
 *   3. DeviceClass has no `reset` member any more, so `dc->reset = my_reset`
 *      does not compile either. Reset is the Resettable interface now; see the
 *      long comment above my_pcie_dev_reset_hold() below. This one is easy to
 *      get wrong in a way that still builds: the shim that remains,
 *      device_class_set_legacy_reset(), wipes the whole phase table and is
 *      documented as being for leaf classes only.
 *   4. A common reg_read() dereferences the address space with
 *      *(uint32_t *)(regs + addr), which ignores both `size` and alignment.
 *      A byte write at SCRATCH+1 would clobber three bytes and go through an
 *      unaligned pointer, so the register file below does a real
 *      read-modify-write instead.
 *   5. msix_notify() is a no-op unless the device has declared the vector with
 *      msix_vector_use() -- see its comment, and my_realize().
 *   6. pcie_endpoint_cap_init() itself downgrades the function type to
 *      PCI_EXP_TYPE_RC_END (0x9) when the device sits on an express root bus,
 *      which is exactly where `-device my-pcie-dev` lands on q35. That is
 *      deliberate (the comment in hw/pci/pcie.c cites Windows Code 10), not a
 *      mistake in this device -- tests/test_pcie_dev.py accepts both.
 */
#include "qemu/osdep.h"
#include "qemu/module.h"
#include "qemu/log.h"
#include "qom/object.h"
#include "hw/core/qdev.h"           /* DeviceClass -- this used to be named
                                      * hw/qdev-core.h, and it is also where
                                      * ResettableClass comes in from */
#include "hw/pci/pci.h"
#include "hw/pci/pci_device.h"      /* pci_dma_read/write, INTERFACE_PCIE_DEVICE */
#include "hw/pci/pcie.h"
#include "hw/pci/msix.h"
#include "system/dma.h"
#include "qapi/error.h"

#define TYPE_MY_PCIE_DEV "my-pcie-dev"
OBJECT_DECLARE_TYPE(MyPcieDev, MyPcieDevClass, MY_PCIE_DEV)

/*
 * The device ids: 0x1AF4 is Red Hat's vendor id and 0x1000 is the
 * *legacy* virtio network device id. Harmless for a demo that instantiates
 * exactly one device, but a real device must not squat on a registered id --
 * change both before shipping anything.
 */
#define MY_VENDOR_ID        0x1AF4
#define MY_DEVICE_ID        0x1000
#define MY_SUBSYSTEM_ID     0x0001
#define MY_REVISION         0x01

/*
 * Capability offsets in configuration space. The PCIe capability of an
 * endpoint is PCI_EXP_VER2_SIZEOF (0x3C) bytes long, so 0x80 runs to 0xBB and
 * the 12-byte MSI-X capability at 0xC0 cannot overlap it. Both are also below
 * 0x100, so a guest that only walks the first 256 config bytes still sees them.
 */
#define MY_PCIE_CAP_OFF     0x80
#define MY_MSIX_CAP_OFF     0xC0

#define MY_BAR_INDEX        0u
#define MY_BAR_SIZE         0x4000u
#define MY_REG_SIZE         0x1000u
#define MY_MSIX_TABLE_OFF   0x1000u
#define MY_MSIX_PBA_OFF     0x2000u
#define MY_MSIX_VECTORS     4
#define MY_MAX_DMA_LEN      4096u

#define REG_MAGIC           0x00
#define REG_VERSION         0x04
#define REG_SCRATCH         0x08
#define REG_DMA_STATUS      0x0C
#define REG_DOORBELL        0x10
#define REG_DMA_SRC         0x14
#define REG_DMA_DST         0x18
#define REG_DMA_LEN         0x1C
#define REG_IRQ_RAISE       0x20
#define REG_IRQ_MASK        0x24
#define REG_IRQ_COUNT       0x28

#define REG_MAGIC_VALUE     0x50434945u      /* "PCIE" */
#define REG_VERSION_VALUE   0x00010001u      /* 1.1 */

#define DMA_STATUS_BUSY     (1u << 0)
#define DMA_STATUS_DONE     (1u << 1)
#define DMA_STATUS_ERROR    (1u << 2)

/* The vector a completed DMA raises. */
#define MY_DMA_VECTOR       0u

/*
 * The class struct exists for exactly one reason: the three-phase reset API
 * needs somewhere to stash the parent class's phases, so that our hold phase
 * can chain to it. Everything else this device does lives in the instance
 * struct below.
 *
 * (The alternative is to keep OBJECT_DECLARE_SIMPLE_TYPE and put the phases in
 * a file-scope variable. That works, since a QOM class is a singleton, but it
 * is not what docs/devel/reset.rst shows, and it breaks the moment anyone
 * subclasses this device.)
 */
struct MyPcieDevClass {
    PCIDeviceClass parent_class;

    /* Filled in by resettable_class_set_parent_phases() at class_init time. */
    ResettablePhases parent_phases;
};

struct MyPcieDev {
    PCIDevice parent_obj;

    MemoryRegion mmio;              /* BAR0 */
    uint8_t regs[MY_REG_SIZE];      /* the register file, as the guest sees it */

    uint32_t dma_status;
    uint32_t irq_count;
};

/* ------------------------------------------------------------------------ */
/* Register file                                                             */
/* ------------------------------------------------------------------------ */

static void my_do_dma(MyPcieDev *s);
static void my_raise_vector(MyPcieDev *s, uint32_t vector);

/* Some registers are computed rather than stored, so a byte read at offset 1
 * of MAGIC still returns the right byte of 0x50434945. */
static uint32_t my_reg_read32(MyPcieDev *s, hwaddr off)
{
    switch (off) {
    case REG_MAGIC:
        return REG_MAGIC_VALUE;
    case REG_VERSION:
        return REG_VERSION_VALUE;
    case REG_DMA_STATUS:
        return s->dma_status;
    case REG_IRQ_COUNT:
        return s->irq_count;
    default:
        /* SCRATCH, DMA_SRC, DMA_DST, DMA_LEN, IRQ_MASK: plain storage, and the
         * backing store is what makes partial writes work. */
        return ldl_le_p(&s->regs[off]);
    }
}

static void my_reg_write32(MyPcieDev *s, hwaddr off, uint32_t val)
{
    switch (off) {
    case REG_SCRATCH:
    case REG_DMA_SRC:
    case REG_DMA_DST:
    case REG_DMA_LEN:
    case REG_IRQ_MASK:
        stl_le_p(&s->regs[off], val);
        break;
    case REG_DMA_STATUS:
        /* Write-to-clear: a driver must be able to acknowledge the done/error
         * bits, otherwise a second transfer could never be told apart from the
         * first. Any write clears the whole register, which is what the
         * do_dma() helper in the test relies on. */
        s->dma_status = 0;
        break;
    default:
        /* MAGIC, VERSION and IRQ_COUNT are read-only; DOORBELL and IRQ_RAISE
         * are commands and never reach here. Unused offsets inside the
         * register file are inert. */
        break;
    }
}

/*
 * Every access is checked against the register file before anything else: the
 * BAR is 16 KB but the registers only occupy the first 4 KB, so a stray write
 * at 0x3000 must not alias a register.
 *
 * A 4-byte access has to start on a register boundary; a narrower one may land
 * anywhere inside a register.
 */
static bool my_access_ok(hwaddr addr, unsigned size)
{
    if (size > 4 || addr + size > MY_REG_SIZE) {
        return false;
    }
    if (size == 4 && (addr & 0x3u)) {
        return false;
    }
    return true;
}

static uint64_t my_mmio_read(void *opaque, hwaddr addr, unsigned size)
{
    MyPcieDev *s = opaque;
    hwaddr reg;
    unsigned shift;
    uint32_t val;
    uint64_t mask;

    if (!my_access_ok(addr, size)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: read of %u byte(s) at 0x%" HWADDR_PRIx
                      " is outside the register file, returning 0\n",
                      TYPE_MY_PCIE_DEV, size, addr);
        return 0;
    }

    reg   = addr & ~0x3u;
    shift = (addr & 0x3u) * 8;
    val   = my_reg_read32(s, reg);

    mask = (size >= 4) ? 0xFFFFFFFFull : ((1ull << (size * 8)) - 1ull);
    return (val >> shift) & mask;
}

static void my_mmio_write(void *opaque, hwaddr addr, uint64_t val, unsigned size)
{
    MyPcieDev *s = opaque;
    hwaddr reg;
    unsigned shift;
    uint32_t merged;

    if (!my_access_ok(addr, size)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: write of %u byte(s) at 0x%" HWADDR_PRIx
                      " is outside the register file, ignored\n",
                      TYPE_MY_PCIE_DEV, size, addr);
        return;
    }

    reg   = addr & ~0x3u;
    shift = (addr & 0x3u) * 8;

    /*
     * DOORBELL and IRQ_RAISE are write-only *commands*: their value is the
     * command, not data to store, so they are handled before the merge. Only a
     * full-width aligned write counts; anything else is a driver bug and is
     * reported rather than silently executing half a doorbell.
     */
    if (reg == REG_DOORBELL || reg == REG_IRQ_RAISE) {
        if (size == 4 && shift == 0) {
            if (reg == REG_DOORBELL) {
                my_do_dma(s);
            } else {
                my_raise_vector(s, (uint32_t)val);
            }
        } else {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "%s: %u-byte write at 0x%" HWADDR_PRIx " to the "
                          "write-only register at 0x%" HWADDR_PRIx
                          " must be a 4-byte aligned write, ignored\n",
                          TYPE_MY_PCIE_DEV, size, addr, reg);
        }
        return;
    }

    /* Read-modify-write the containing register so that a byte write at
     * SCRATCH+1 changes exactly one byte. */
    merged = my_reg_read32(s, reg);
    if (size >= 4) {
        merged = (uint32_t)val;
    } else {
        uint32_t mask = ((1u << (size * 8)) - 1u) << shift;
        merged = (merged & ~mask) | (((uint32_t)val << shift) & mask);
    }
    my_reg_write32(s, reg, merged);
}

static const MemoryRegionOps my_mmio_ops = {
    .read = my_mmio_read,
    .write = my_mmio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 4,
    },
};

/* ------------------------------------------------------------------------ */
/* DMA                                                                       */
/* ------------------------------------------------------------------------ */

/*
 * A real DMA through the device's own address space -- the same
 * pci_dma_read()/pci_dma_write() pair a driver's descriptor ring ends up in.
 * Nothing here touches guest RAM directly, so an IOMMU (or a bus that has not
 * been bus-master-enabled yet) is honoured rather than bypassed.
 */
static void my_do_dma(MyPcieDev *s)
{
    PCIDevice *pci = PCI_DEVICE(s);
    uint32_t src = ldl_le_p(&s->regs[REG_DMA_SRC]);
    uint32_t dst = ldl_le_p(&s->regs[REG_DMA_DST]);
    uint32_t len = ldl_le_p(&s->regs[REG_DMA_LEN]);
    g_autofree uint8_t *buf = NULL;

    s->dma_status = DMA_STATUS_BUSY;

    if (len == 0 || len > MY_MAX_DMA_LEN) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: rejecting a DMA of %u bytes (allowed range 1..%u)\n",
                      TYPE_MY_PCIE_DEV, len, MY_MAX_DMA_LEN);
        s->dma_status = DMA_STATUS_ERROR;
        return;
    }

    buf = g_malloc(len);

    if (pci_dma_read(pci, src, buf, len) != MEMTX_OK) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: DMA source 0x%08x (%u bytes) is not readable\n",
                      TYPE_MY_PCIE_DEV, src, len);
        s->dma_status = DMA_STATUS_ERROR;
        return;
    }
    if (pci_dma_write(pci, dst, buf, len) != MEMTX_OK) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: DMA destination 0x%08x (%u bytes) is not writable\n",
                      TYPE_MY_PCIE_DEV, dst, len);
        s->dma_status = DMA_STATUS_ERROR;
        return;
    }

    s->dma_status = DMA_STATUS_DONE;

    /* The transfer is done; raise the completion vector. */
    my_raise_vector(s, MY_DMA_VECTOR);
}

/* ------------------------------------------------------------------------ */
/* Interrupts                                                                */
/* ------------------------------------------------------------------------ */

/*
 * Two independent gates, and they fail differently:
 *
 *   IRQ_MASK  -- the device's own filter on whether it *wants* to raise.
 *                A masked vector does not even count.
 *   msix_enabled() -- whether that intent can be delivered as a message.
 *                msix_notify() does not check this bit (it only looks at the
 *                per-vector and function masks), so a device that skipped the
 *                check would post messages even with MSI-X switched off. The
 *                vector is still counted, so a driver can see that the device
 *                asked -- which is what an INTx fallback would have shown.
 *
 * msix_notify() itself handles the masked cases properly: it parks the vector
 * in the PBA instead of sending, which is the behaviour the PCIe spec asks for.
 */
static void my_raise_vector(MyPcieDev *s, uint32_t vector)
{
    PCIDevice *pci = PCI_DEVICE(s);

    if (vector >= MY_MSIX_VECTORS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: vector %u is out of range (0..%u), ignored\n",
                      TYPE_MY_PCIE_DEV, vector, MY_MSIX_VECTORS - 1);
        return;
    }
    if (ldl_le_p(&s->regs[REG_IRQ_MASK]) & (1u << vector)) {
        return;
    }

    s->irq_count++;

    if (msix_enabled(pci)) {
        msix_notify(pci, vector);
    }
}

/* ------------------------------------------------------------------------ */
/* qdev/QOM plumbing                                                         */
/* ------------------------------------------------------------------------ */

/*
 * Configuration space writes.
 *
 * pci_default_write_config() is enough for the standard header, but a write to
 * a *capability* register is never visible to a device that does not override
 * this hook -- and Function Level Reset needs exactly that: a driver resets the
 * function by writing bit 15 of the PCIe Device Control register, which lives
 * at exp_cap + PCI_EXP_DEVCTL.
 *
 * The order matters and is the nvme/igb/virtio-pci pattern: apply the write
 * first (BCR_FLR is a "write 1 to trigger, read back 0" bit, so the default
 * handler is also what makes the bit observable to the helper), then let
 * pcie_cap_flr_write_config() test-and-clear it and call pci_device_reset().
 * Note this calls pci_default_write_config() directly rather than chaining
 * through a saved parent pointer: TYPE_PCI_DEVICE's config_write *is*
 * pci_default_write_config(), which is what every PCI device in the tree
 * assumes.
 */
static void my_pcie_dev_config_write(PCIDevice *dev, uint32_t address,
                                     uint32_t val, int len)
{
    pci_default_write_config(dev, address, val, len);
    pcie_cap_flr_write_config(dev, address, val, len);
}

static void my_pcie_dev_realize(PCIDevice *pci_dev, Error **errp)
{
    MyPcieDev *s = MY_PCIE_DEV(pci_dev);
    int i, ret;

    memory_region_init_io(&s->mmio, OBJECT(s), &my_mmio_ops, s,
                          "my-pcie-mmio", MY_BAR_SIZE);
    pci_register_bar(pci_dev, MY_BAR_INDEX,
                     PCI_BASE_ADDRESS_SPACE_MEMORY |
                     PCI_BASE_ADDRESS_MEM_TYPE_64,
                     &s->mmio);

    /*
     * pcie_endpoint_cap_init() takes no Error** and reports its own failures,
     * so a negative return is all we get; failing realize is still the right
     * response, because a device without its PCIe capability is not a device
     * this demo is about.
     */
    if (pcie_endpoint_cap_init(pci_dev, MY_PCIE_CAP_OFF) < 0) {
        error_setg(errp, "%s: failed to initialise the PCIe capability",
                   TYPE_MY_PCIE_DEV);
        return;
    }

    /*
     * FLR is opt-in. pcie_endpoint_cap_init() does not do this for you: without
     * it the Device Capabilities register has no Function Level Reset bit and
     * Device Control bit 15 is not even writable, so nothing -- driver or test
     * -- can reset the function. igb, nvme and virtio-pci each call this
     * themselves for the same reason.
     */
    pcie_cap_flr_init(pci_dev);

    /*
     * The table and the PBA both live in BAR0, hence the same MemoryRegion
     * twice, each with its own offset. msix_init() checks that neither range
     * overlaps the other or runs past the end of the BAR -- which is why
     * MY_BAR_SIZE is 16 KB and not, say, 4 KB.
     */
    ret = msix_init(pci_dev, MY_MSIX_VECTORS,
                    &s->mmio, MY_BAR_INDEX, MY_MSIX_TABLE_OFF,
                    &s->mmio, MY_BAR_INDEX, MY_MSIX_PBA_OFF,
                    MY_MSIX_CAP_OFF, errp);
    if (ret < 0) {
        return;
    }

    /*
     * msix_notify() refuses to send a vector that has not been declared used
     * (dev->msix_entry_used), so a device that never calls this would look
     * like it raises interrupts and delivers nothing. We support all our
     * vectors always, so declare all of them.
     */
    for (i = 0; i < MY_MSIX_VECTORS; i++) {
        msix_vector_use(pci_dev, i);
    }
}

/*
 * Reset.
 *
 * An older device template writes `dc->reset = my_reset`. That member is gone
 * in QEMU 11: a device is resettable through the Resettable interface, with
 * three phases
 * (enter / hold / exit), and a class that overrides any of them has to chain to
 * its parent's phase itself. Two things are worth knowing here.
 *
 * First, why the shim is a trap. device_class_set_legacy_reset() does exist and
 * is a one-line substitute -- 383 files in hw/ still use it. But hw/core/qdev.c
 * documents it as being for *leaf* classes, because it overwrites phases.enter,
 * phases.hold and phases.exit wholesale rather than chaining. It builds, and it
 * would work today for this device, and it would quietly stop chaining the
 * moment somebody derived from my-pcie-dev. Doing it properly costs the class
 * struct above and four lines.
 *
 * Second, why only `hold` is overridden. enter runs before the object's own
 * children are reset and exit after: both are for coordinated multi-object
 * resets (a bus and its devices, say), and this device has no children to
 * coordinate with. Passing NULL leaves the parent's implementations in place
 * untouched -- which is not the same as passing a no-op function.
 *
 * Note what this function does *not* have to do: re-declare the MSI-X vectors.
 * PCIDevice's own reset (pci_do_device_reset() -> msix_reset()) clears the
 * MSI-X enable bit and the pending bits and re-masks every vector, but it never
 * touches dev->msix_entry_used -- so the msix_vector_use() calls from realize
 * survive. If it did clear them, the function would come back from a reset able
 * to count interrupts but no longer able to deliver them, and the device would
 * look fine right up until the first guest reboot.
 * test_flr_resets_the_function() is what pins this down.
 */
static void my_pcie_dev_reset_hold(Object *obj, ResetType type)
{
    MyPcieDev *s = MY_PCIE_DEV(obj);
    MyPcieDevClass *k = MY_PCIE_DEV_GET_CLASS(obj);

    if (k->parent_phases.hold) {
        k->parent_phases.hold(obj, type);
    }

    memset(s->regs, 0, sizeof(s->regs));
    s->dma_status = 0;
    s->irq_count = 0;
}

static void my_pcie_dev_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    PCIDeviceClass *k = PCI_DEVICE_CLASS(klass);
    MyPcieDevClass *mk = MY_PCIE_DEV_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Begonia PCIe demo endpoint";

    resettable_class_set_parent_phases(rc, NULL, my_pcie_dev_reset_hold,
                                       NULL, &mk->parent_phases);

    k->realize = my_pcie_dev_realize;
    k->config_write = my_pcie_dev_config_write;
    k->vendor_id = MY_VENDOR_ID;
    k->device_id = MY_DEVICE_ID;
    k->subsystem_vendor_id = MY_VENDOR_ID;
    k->subsystem_id = MY_SUBSYSTEM_ID;
    k->revision = MY_REVISION;
    k->class_id = PCI_CLASS_STORAGE_OTHER;
}

static const TypeInfo my_pcie_dev_info = {
    .name          = TYPE_MY_PCIE_DEV,
    .parent        = TYPE_PCI_DEVICE,
    .instance_size = sizeof(MyPcieDev),
    .class_size    = sizeof(MyPcieDevClass),
    .class_init    = my_pcie_dev_class_init,
    /*
     * Declaring the interface (rather than parenting to a "pcie-device" type,
     * which no longer exists) is what makes hw/pci/pci.c set
     * QEMU_PCI_CAP_EXPRESS, and therefore what lets pcie_endpoint_cap_init()
     * succeed. Claiming INTERFACE_CONVENTIONAL_PCI_DEVICE as well would switch
     * that off again.
     */
    .interfaces    = (const InterfaceInfo[]) {
        { INTERFACE_PCIE_DEVICE },
        { }
    },
};

static void my_pcie_dev_register_types(void)
{
    type_register_static(&my_pcie_dev_info);
}

type_init(my_pcie_dev_register_types)
