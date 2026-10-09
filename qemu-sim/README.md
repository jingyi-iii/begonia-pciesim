# qemu-sim

A PCIe endpoint device model built into QEMU, driven two ways.

A custom PCIe endpoint modelled for QEMU and compiled in from source, driven by a qtest suite
that needs no OS and by a real Alpine Linux guest that enumerates it. QEMU supplies the Root
Complex, ECAM configuration space, MSI/MSI-X and an address space for DMA; the device model is
ours.

## Install

No root and no KVM. Everything builds as a normal user, and QEMU falls back to TCG. A system
toolchain is optional. `setup-env.sh` builds a conda environment next to the checkout with
meson, ninja, glib, pixman and gcc.

```bash
cd qemu-sim
source env.sh          # tells you what it found and what is missing

make fetch             # ~135 MB download, verified against a sha256 pin
make test              # configure + build QEMU, then run the 18 qtest tests
```

The first `make test` compiles QEMU and takes 10-20 minutes on 8 cores.

## Use

`device/my_pcie_dev.c` is a PCIe endpoint with one 16 KB memory BAR, a small register file,
real DMA through `pci_dma_read`/`pci_dma_write`, 4 MSI-X vectors, and the identity
`0x1AF4:0x1000`. That id is virtio-net's legacy one, so change it before shipping anything
real.

The register file is the first 4 KB of the BAR. The MSI-X table is mapped inside the same BAR,
so ordinary stores program it. Writes are read-modify-written, so a byte store changes one
byte. Everything past 4 KB reads 0. Writes there are ignored.

`make test` runs 18 assertions over configuration space, the BAR, the register file, DMA and
the MSI-X state machine, plus FLR. It needs only the built binary. A failure names the property
that broke.

`make guest` boots Alpine, lets the kernel enumerate the device, and prints one
machine-checkable line:

```
VERDICT: PASS - Linux enumerated 1af4:1000 as 0000:00:03.0
```

The QEMU 11 API changes, the register map, the full environment overrides and troubleshooting
are in [NOTES.md](NOTES.md).

## License

MIT
