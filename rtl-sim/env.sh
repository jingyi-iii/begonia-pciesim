#!/usr/bin/env bash
# Unified environment entry point:   source env.sh
#
# Nothing in this file is specific to one machine -- there are no absolute
# paths, no user names and no assumptions about where you keep your projects.
# It resolves a toolchain in two steps and uses whichever it finds:
#
#   1. a self-contained toolchain bundled next to this checkout (a conda
#      environment, created by ./setup-env.sh), looked for at
#         $LITEPCIE_DEMO_TOOLS   if you exported it, otherwise
#         ../.tools             (the default: a sibling of this checkout)
#   2. otherwise, whatever `verilator` / `g++` / `python` are already on your
#      PATH -- e.g. from your distro, Homebrew, or an environment you activate
#      yourself before sourcing this file
#
# Overridable variables (all optional):
#   LITEPCIE_DEMO_TOOLS    where the bundled toolchain lives
#   LITEPCIE_DEMO_TMPDIR   scratch directory for Verilator's build artefacts
#   LITEPCIE_DEMO_HOME     HOME to use if the real one is not writable

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export DEMO_ROOT

# ---------------------------------------------------------------------------------
# 1. Locate the self-contained toolchain, if this checkout has one
# ---------------------------------------------------------------------------------
TOOLS_ROOT="${LITEPCIE_DEMO_TOOLS:-$DEMO_ROOT/../.tools}"
if [ -d "$TOOLS_ROOT" ]; then
    TOOLS_ROOT="$(cd "$TOOLS_ROOT" && pwd)"
else
    # No toolchain here. Still resolve the path so what we print has no `..` in
    # it -- but do not create anything: sourcing env.sh must not litter the
    # directory next to the checkout.
    TOOLS_ROOT="$(cd "$(dirname "$TOOLS_ROOT")" 2>/dev/null && pwd)/$(basename "$TOOLS_ROOT")"
fi
export TOOLS_ROOT

CONDA_ENV="$TOOLS_ROOT/env"
if [ -x "$CONDA_ENV/bin/verilator" ]; then
    export CONDA_PREFIX="$CONDA_ENV"
    export PATH="$CONDA_ENV/bin:$PATH"
    export LD_LIBRARY_PATH="$CONDA_ENV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
if [ -d "$TOOLS_ROOT/bin" ]; then
    export PATH="$TOOLS_ROOT/bin:$PATH"
fi

# ---------------------------------------------------------------------------------
# 2. A scratch directory big enough for Verilator, then a usable HOME
# ---------------------------------------------------------------------------------
# Verilator emits a lot of C++ intermediates and will happily fill a small
# tmpfs, so take the first candidate that exists, is writable and has ~1 GB free.
pick_tmpdir() {
    local d avail
    for d in "${LITEPCIE_DEMO_TMPDIR:-}" "${TMPDIR:-}" /tmp "$TOOLS_ROOT/tmp"; do
        [ -n "$d" ] || continue
        [ -d "$d" ] && [ -w "$d" ] || continue
        avail="$(df -Pk "$d" 2>/dev/null | awk 'NR==2 {print $4}')"
        if [ -n "$avail" ] && [ "$avail" -ge 1048576 ]; then
            printf '%s\n' "$d"
            return
        fi
    done
    # Nothing that already exists has room. Use scratch next to the toolchain if
    # this checkout has one, otherwise a fresh directory from the OS -- sourcing
    # this file must never invent a directory next to a checkout that has no
    # toolchain.
    if [ -d "$TOOLS_ROOT" ] && mkdir -p "$TOOLS_ROOT/tmp" 2>/dev/null; then
        printf '%s\n' "$TOOLS_ROOT/tmp"
        return
    fi
    mktemp -d 2>/dev/null || printf '%s\n' "${TMPDIR:-/tmp}"
}
TMPDIR="$(pick_tmpdir)"
if [ ! -d "$TMPDIR" ]; then
    mkdir -p "$TMPDIR" 2>/dev/null || TMPDIR="$(mktemp -d)"
fi
export TMPDIR

# pip and Verilator both want to write a cache under $HOME. Normally that is just
# your $HOME and nothing needs doing -- the checks are here because $HOME can be
# unset or read-only (containers, CI, a scrubbed sudo environment) and the
# resulting failure is otherwise very hard to read.
# Deliberately after the TMPDIR block, so a replacement HOME lands somewhere with
# room rather than inside a tiny tmpfs.
HOME_REDIRECTED=""
if [ -n "${LITEPCIE_DEMO_HOME:-}" ]; then
    export HOME="$LITEPCIE_DEMO_HOME"
    HOME_REDIRECTED=1
elif [ ! -w "${HOME:-/nonexistent}" ]; then
    if [ -d "$TOOLS_ROOT" ] && mkdir -p "$TOOLS_ROOT/home" 2>/dev/null; then
        export HOME="$TOOLS_ROOT/home"
        HOME_REDIRECTED=1
    elif NEW_HOME="$(mktemp -d 2>/dev/null)"; then
        if [ -n "$NEW_HOME" ]; then
            export HOME="$NEW_HOME"
            HOME_REDIRECTED=1
        fi
    fi
fi

# ---------------------------------------------------------------------------------
# 3. Make the LitePCIe sources and this checkout importable
# ---------------------------------------------------------------------------------
export PYTHONPATH="$DEMO_ROOT/vendor/litepcie-master:$DEMO_ROOT${PYTHONPATH:+:$PYTHONPATH}"

# ---------------------------------------------------------------------------------
# 4. Point the Verilator build at a usable C++ compiler
# ---------------------------------------------------------------------------------
# A conda-forge toolchain ships triple-prefixed compilers
# (x86_64-conda-linux-gnu-g++, aarch64-conda-linux-gnu-g++, x86_64-apple-darwin-
# g++, ...) and provides no file named plain `g++` in its bin directory, so naming
# "g++" makes Verilator's compile stage fail to find a compiler. Accept the
# prefixed spelling for any host triple, then fall back to whatever is on PATH.
find_cxx() {
    local c
    if [ -n "${CONDA_PREFIX:-}" ]; then
        for c in "$CONDA_PREFIX"/bin/*-g++ "$CONDA_PREFIX"/bin/g++; do
            [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
        done
    fi
    c="$(command -v g++ 2>/dev/null)" || return 1
    printf '%s\n' "$c"
}
if CXX_BIN="$(find_cxx)"; then
    export CXX="$CXX_BIN"
    if [ -x "${CXX_BIN%g++}gcc" ]; then
        export CC="${CXX_BIN%g++}gcc"
    fi
fi

# ---------------------------------------------------------------------------------
# 5. Doctor: say what was found, and what to do about anything that is missing
# ---------------------------------------------------------------------------------
echo "[env] checkout  = $DEMO_ROOT"
if [ -n "${CONDA_PREFIX:-}" ]; then
    echo "[env] toolchain = $CONDA_PREFIX (bundled)"
else
    echo "[env] toolchain = none bundled, using what is on PATH"
fi
echo "[env] TMPDIR    = $TMPDIR"
if [ -n "$HOME_REDIRECTED" ]; then
    echo "[env] HOME      = $HOME (redirected: the real one is not writable)"
fi
for t in python verilator make; do
    printf '[env] %-10s= %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo 'not found')"
done
if [ -n "${CXX:-}" ]; then
    echo "[env] CXX       = $CXX"
fi

MISSING=""
for t in python verilator make; do
    command -v "$t" >/dev/null 2>&1 || MISSING="$MISSING $t"
done
if [ -n "$MISSING" ]; then
    echo "[env] WARNING: not on PATH:$MISSING"
    echo "[env]          Either install them, or create the bundled toolchain:"
    echo "[env]              bash setup-env.sh"
fi

if command -v python >/dev/null 2>&1; then
    MISSING_PY="$(python -c 'import importlib.util as u; print(" ".join(m for m in ("cocotb", "migen", "litex") if u.find_spec(m) is None))' 2>/dev/null || true)"
    if [ -n "$MISSING_PY" ]; then
        echo "[env] WARNING: python module(s) missing:$MISSING_PY"
        echo "[env]          pip install cocotb litex     (litex pulls in migen)"
    fi
fi
