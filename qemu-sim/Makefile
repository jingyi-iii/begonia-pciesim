# begonia-pciesim / qemu-sim
#
#   make setup     create the bundled toolchain (meson/ninja/glib/pixman/gcc)
#   make doctor    show which toolchain, QEMU tree and binary are in use
#   make fetch     download + unpack the pinned QEMU sources into ../.qemu
#   make guest-img download the Alpine guest image used by `make guest`
#   make device    drop device/my_pcie_dev.c into the QEMU tree
#   make build     configure + ninja (the first build takes a while)
#   make test      functional test over qtest against the built binary (default)
#   make guest     boot a real Alpine Linux with the device, capture its view
#   make clean     remove the build directory (keeps the sources and the cache)
#   make distclean remove the whole QEMU tree, cache and logs
#
# Every target runs inside the environment described by env.sh, which needs no
# machine-specific configuration. See README.md.
#
SHELL := /bin/bash
DEMO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

# `source` is a bash builtin, so the recipes must run under bash, and each one
# starts by loading the environment: that is what puts meson, ninja and the
# compiler on PATH and what defines QEMU_SRC / QEMU_BUILD / QEMU_BIN.
#
# pipefail matters because configure and build both `tee` their output to a log.
# Without it, `set -e` would only see the exit status of `tee`, which always
# succeeds -- so a failed configure would sail straight on to ninja.
RUN = set -e; set -o pipefail; source $(DEMO_ROOT)/env.sh >/dev/null; cd $(DEMO_ROOT);

# x86_64-softmmu is the only target we need: it is the one that can run a
# machine, which is the whole point of the demo (a `-qtest` connection and,
# optionally, a real Linux guest). --disable-docs keeps the build from needing
# sphinx just to compile a device model. --disable-guest-agent drops a
# user-space program we never run.
#
# -Dfdt=disabled: libfdt is used for the device trees of the ARM/PPC/RISC-V
# machines, and this build only produces x86_64-softmmu, which never asks for
# one. Leaving it on 'auto' makes QEMU look for a system libfdt and, when it
# does not find one, *download* the dtc subproject -- a build-time network
# dependency that needs git. Turning it off costs this configuration nothing and
# keeps `make build` offline and reproducible. On a host that does have
# libfdt-dev and wants the normal behaviour:
#     make CONFIGURE_FLAGS="--target-list=x86_64-softmmu --disable-docs -Dfdt=auto" configure
CONFIGURE_FLAGS ?= --target-list=x86_64-softmmu --disable-docs --disable-guest-agent -Dfdt=disabled
NINJA_FLAGS     ?= -j$(shell nproc 2>/dev/null || echo 4)

PYTHON ?= $(shell command -v python3 2>/dev/null || command -v python)
ifndef PYTHON
$(error no python3 on PATH -- run `make setup` and `source env.sh` first)
endif

.PHONY: all setup doctor fetch guest-img device configure build test guest clean distclean

all: test

setup:
	@bash $(DEMO_ROOT)/setup-env.sh

doctor:
	@source $(DEMO_ROOT)/env.sh
	@echo "[make] DEMO_ROOT = $(DEMO_ROOT)"

fetch:
	@bash $(DEMO_ROOT)/scripts/fetch-qemu.sh

guest-img:
	@bash $(DEMO_ROOT)/scripts/fetch-guest.sh

# --- the device model ------------------------------------------------------
# device/my_pcie_dev.c is the single source of truth; this copies it into the
# QEMU tree and registers it in Kconfig and meson.build. Idempotent, so it is
# safe to run on every build -- which is what keeps the two in sync.
device:
	@$(RUN) $(PYTHON) scripts/install-device.py

device-check:
	@$(RUN) $(PYTHON) scripts/install-device.py --check

# --- build -----------------------------------------------------------------
# Both steps keep a log next to the sources. meson buries its real diagnosis in
# meson-logs/meson-log.txt and the configure summary is long, so "it failed" is
# almost never enough to go on -- and a shutdown of the terminal loses the lot.
configure: fetch device
	@$(RUN) mkdir -p "$$QEMU_BUILD"; \
	if [ -f "$$QEMU_BUILD/build.ninja" ]; then \
	    echo "[configure] $$QEMU_BUILD is already configured"; \
	else \
	    echo "[configure] $$QEMU_SRC/configure $(CONFIGURE_FLAGS)"; \
	    echo "[configure] log -> $$QEMU_DIR/configure.log"; \
	    ( cd "$$QEMU_BUILD" && "$$QEMU_SRC/configure" $(CONFIGURE_FLAGS) ) 2>&1 \
	        | tee "$$QEMU_DIR/configure.log"; \
	fi

build: configure
	@$(RUN) echo "[build] ninja $(NINJA_FLAGS)"; \
	echo "[build] log -> $$QEMU_DIR/build.log (only failures are worth reading)"; \
	ninja -C "$$QEMU_BUILD" $(NINJA_FLAGS) 2>&1 | tee "$$QEMU_DIR/build.log"

# --- tests -----------------------------------------------------------------
# The qtest suite needs nothing but the built binary: no guest OS, no KVM, no
# root. It is the part that can run unattended.
test: build
	@$(RUN) $(PYTHON) tests/test_pcie_dev.py --qemu "$$QEMU_BIN" $(TEST_FLAGS)

list:
	@$(RUN) $(PYTHON) tests/test_pcie_dev.py --list

# --- the manual check, automated -------------------------------------------
# Boots a real Linux with the device attached and captures what the guest's own
# PCI code makes of it. See scripts/guest-smoke.sh for what is checked.
guest: build guest-img
	@$(RUN) bash scripts/guest-smoke.sh $(GUEST_FLAGS)

# --- cleanup ---------------------------------------------------------------
clean:
	@$(RUN) if [ -d "$$QEMU_BUILD" ]; then rm -rf "$$QEMU_BUILD"; echo "[clean] removed $$QEMU_BUILD"; else echo "[clean] nothing to remove"; fi
	@$(RUN) rm -f guest-serial.log; true

distclean: clean
	@$(RUN) if [ -d "$$QEMU_DIR" ]; then rm -rf "$$QEMU_DIR"; echo "[distclean] removed $$QEMU_DIR"; fi
	@$(RUN) rm -rf "$(DEMO_ROOT)/cache" guest-serial.log
	@echo "[distclean] removed cache/ (the QEMU tarball and the guest image)"
