# Verilator + cocotb + LitePCIe minimal demo
#
#   make setup    create the bundled toolchain (only if you don't already have one)
#   make doctor   show which toolchain and tools are in use
#   make vendor   unpack the pinned LitePCIe sources into vendor/ (from cache/)
#   make rtl      generate Verilog from the LitePCIe sources
#   make sim      build with Verilator and run the cocotb tests (default target)
#   make waves    same as sim, plus a waveform dump
#   make report   run everything and render the HTML report
#   make clean    remove generated artefacts
#
# Every target runs inside the environment described by env.sh, which needs no
# machine-specific configuration. See README.md, section "Environment".
#
SHELL := /bin/bash
DEMO_ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

# `source` is a bash builtin, so the recipes must run under bash, and each one
# starts by loading the environment.
RUN = set -e; source $(DEMO_ROOT)/env.sh >/dev/null; cd $(DEMO_ROOT);

.PHONY: all setup vendor doctor rtl sim waves report clean

all: sim

setup:
	@bash $(DEMO_ROOT)/setup-env.sh

doctor:
	@source $(DEMO_ROOT)/env.sh
	@echo "[make] DEMO_ROOT = $(DEMO_ROOT)"

# The unpacked upstream tree is ~1.6 MB of third-party code and is not tracked
# by git; the pinned tarball in cache/ is, so `make sim` on a fresh clone can
# still recreate it without touching the network. `vendor/` is in .gitignore.
VENDOR_SRC  := $(DEMO_ROOT)/cache/litepcie.tar.gz
VENDOR_DONE := $(DEMO_ROOT)/vendor/litepcie-master/setup.py

vendor:
	@if [ -f $(VENDOR_DONE) ]; then \
	    echo "[vendor] vendor/litepcie-master is already unpacked"; \
	elif [ -f $(VENDOR_SRC) ]; then \
	    mkdir -p $(DEMO_ROOT)/vendor; \
	    tar xzf $(VENDOR_SRC) -C $(DEMO_ROOT)/vendor; \
	    echo "[vendor] unpacked cache/litepcie.tar.gz -> vendor/"; \
	else \
	    echo "[vendor] neither vendor/ nor cache/litepcie.tar.gz is present."; \
	    echo "[vendor] fetch the upstream sources yourself, e.g."; \
	    echo "[vendor]   mkdir -p vendor && curl -L \\"; \
	    echo "[vendor]     https://github.com/enjoy-digital/litepcie/archive/refs/heads/master.tar.gz \\"; \
	    echo "[vendor]     | tar xz -C vendor/"; \
	    exit 1; \
	fi

rtl: vendor
	@$(RUN) python rtl/gen_rtl.py --print-ports

sim: rtl
	@$(RUN) python run.py

waves: rtl
	@$(RUN) python run.py --waves

report: rtl
	@$(RUN) python viz/report.py

# Removes everything .gitignore calls generated -- including the exported
# Verilog, which `make rtl` writes and nothing hand-edits. It deliberately
# leaves vendor/ alone: that is an input, recreated by `make vendor`, and
# re-unpacking it costs nothing but is not "clean"-ing either.
clean:
	@$(RUN) python -c "import shutil, os; [shutil.rmtree(p, ignore_errors=True) for p in ['$(DEMO_ROOT)/sim_build', '$(DEMO_ROOT)/report']]; [os.remove(p) for p in ['$(DEMO_ROOT)/tb/dump.vcd', '$(DEMO_ROOT)/tb/results.xml', '$(DEMO_ROOT)/rtl/litepcie_tlp_packetizer.v', '$(DEMO_ROOT)/rtl/litepcie_tlp_packetizer.ports.json'] if os.path.exists(p)]"
	@echo "[clean] removed sim_build/, report/ and the generated RTL"
