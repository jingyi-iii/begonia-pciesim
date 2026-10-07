#!/usr/bin/env bash
# Unified environment entry point:   source env.sh
#
# Nothing here is specific to one machine -- no absolute paths, no user names.
# Like rtl-sim/env.sh it resolves what it needs in two steps and degrades
# gracefully, and sourcing it must have no side effects (it never creates a
# directory, not even next to the checkout).
#
#   1. the toolchain bundled next to this checkout -- a conda environment named
#      `qemu` inside the shared toolchain root, created by ./setup-env.sh:
#         $QEMU_DEMO_TOOLS      if you exported it, otherwise
#         ../.tools             (the default: a sibling of this checkout, the
#                                same root rtl-sim uses)
#   2. otherwise whatever `meson` / `ninja` / `qemu-system-x86_64` are already
#      on PATH
#
# Overridable variables (all optional):
#   QEMU_DEMO_TOOLS        shared toolchain root          [<checkout>/../.tools]
#   QEMU_DEMO_QEMU_DIR     QEMU source + build tree       [<checkout>/../.qemu]
#   QEMU_DEMO_TMPDIR       scratch for the build          [first with room]
#   QEMU_DEMO_HOME         HOME to use if the real one is not writable

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export DEMO_ROOT

# ---------------------------------------------------------------------------------
# 1. Locate the shared toolchain root, if this checkout has one
# ---------------------------------------------------------------------------------
TOOLS_ROOT="${QEMU_DEMO_TOOLS:-$DEMO_ROOT/../.tools}"
if [ -d "$TOOLS_ROOT" ]; then
    TOOLS_ROOT="$(cd "$TOOLS_ROOT" && pwd)"
else
    # Resolve the path anyway so what we print has no `..` in it, but do not
    # create anything: sourcing env.sh must not litter the parent directory.
    TOOLS_ROOT="$(cd "$(dirname "$TOOLS_ROOT")" 2>/dev/null && pwd)/$(basename "$TOOLS_ROOT")"
fi
export TOOLS_ROOT

QEMU_ENV="$TOOLS_ROOT/envs/qemu"
if [ -x "$QEMU_ENV/bin/meson" ]; then
    export QEMU_ENV
    export PATH="$QEMU_ENV/bin:$PATH"
    # QEMU's configure finds glib/pixman/zlib through pkg-config, and the
    # resulting binary still needs those shared objects at run time.
    export PKG_CONFIG_PATH="$QEMU_ENV/lib/pkgconfig:$QEMU_ENV/share/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    export LD_LIBRARY_PATH="$QEMU_ENV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

# ---------------------------------------------------------------------------------
# 2. Locate the QEMU source + build tree
# ---------------------------------------------------------------------------------
QEMU_DIR="${QEMU_DEMO_QEMU_DIR:-$DEMO_ROOT/../.qemu}"
if [ -d "$QEMU_DIR" ]; then
    QEMU_DIR="$(cd "$QEMU_DIR" && pwd)"
else
    QEMU_DIR="$(cd "$(dirname "$QEMU_DIR")" 2>/dev/null && pwd)/$(basename "$QEMU_DIR")"
fi
export QEMU_DIR

# The freshly built binary, once there is one. `make sim` uses it; env.sh only
# reports whether it exists.
QEMU_BUILD="$QEMU_DIR/build"
QEMU_BIN="$QEMU_BUILD/qemu-system-x86_64"
export QEMU_BUILD QEMU_BIN

# The unpacked sources, if `make fetch` has run. Found by globbing rather than
# hardcoding the version, so bumping the pin in scripts/fetch-qemu.sh is enough.
QEMU_SRC=""
if [ -d "$QEMU_DIR" ]; then
    for d in "$QEMU_DIR"/qemu-*/; do
        [ -f "${d}configure" ] && QEMU_SRC="${d%/}"
    done
fi
if [ -n "$QEMU_SRC" ]; then
    export QEMU_SRC
else
    QEMU_SRC="$QEMU_DIR/qemu-<version>"
    export QEMU_SRC
fi

# ---------------------------------------------------------------------------------
# 3. A scratch directory big enough to build QEMU, then a usable HOME
# ---------------------------------------------------------------------------------
# Qt/meson/ninja and the compiler all use TMPDIR, and this machine's /tmp is a
# 10 MB tmpfs, so take the first candidate that exists, is writable and has room.
pick_tmpdir() {
    local d avail
    for d in "${QEMU_DEMO_TMPDIR:-}" "${TMPDIR:-}" "$TOOLS_ROOT/tmp" /tmp; do
        [ -n "$d" ] || continue
        [ -d "$d" ] && [ -w "$d" ] || continue
        avail="$(df -Pk "$d" 2>/dev/null | awk 'NR==2 {print $4}')"
        if [ -n "$avail" ] && [ "$avail" -ge 2097152 ]; then   # 2 GB: QEMU is big
            printf '%s\n' "$d"
            return
        fi
    done
    # Nothing that exists has room. Use scratch next to the toolchain when this
    # checkout has one, otherwise a fresh directory from the OS.
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

# pip and ccache want a writable HOME. Normally that is just $HOME and nothing
# needs doing -- these checks are for containers, CI, or a scrubbed sudo
# environment, where the failure is otherwise very hard to read.
HOME_REDIRECTED=""
if [ -n "${QEMU_DEMO_HOME:-}" ]; then
    export HOME="$QEMU_DEMO_HOME"
    HOME_REDIRECTED=1
elif [ ! -w "${HOME:-/nonexistent}" ]; then
    if [ -d "$TOOLS_ROOT" ] && mkdir -p "$TOOLS_ROOT/home" 2>/dev/null; then
        export HOME="$TOOLS_ROOT/home"
        HOME_REDIRECTED=1
    elif NEW_HOME="$(mktemp -d 2>/dev/null)" && [ -n "$NEW_HOME" ]; then
        export HOME="$NEW_HOME"
        HOME_REDIRECTED=1
    fi
fi

# ---------------------------------------------------------------------------------
# 3b. Point the build at a usable C and C++ compiler
# ---------------------------------------------------------------------------------
# A conda-forge toolchain ships triple-prefixed compilers
# (x86_64-conda-linux-gnu-gcc, aarch64-conda-linux-gnu-gcc, ...) and provides no
# file named plain `cc`, `gcc`, `c++` or `g++` in its bin directory, so leaving
# the choice to PATH either fails outright or silently picks up the host
# compiler -- which then cannot find the glib/pixman headers that live in this
# prefix.
#
# Both compilers matter: QEMU's configure defaults to `c++` and meson.build does
# `cxx = meson.get_compiler('cpp')` unconditionally, so a machine with no system
# C++ compiler (this one) fails to configure without CXX. The host triple is
# not assumed; any `*-gcc` / `*-g++` in the prefix is accepted.
find_compiler() {
    local suffix="$1" c
    if [ -n "${QEMU_ENV:-}" ]; then
        for c in "$QEMU_ENV"/bin/*-"$suffix" "$QEMU_ENV"/bin/"$suffix"; do
            [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
        done
    fi
    command -v "$suffix" 2>/dev/null
}
if CC_BIN="$(find_compiler gcc || find_compiler cc)"; then
    export CC="$CC_BIN"
fi
if CXX_BIN="$(find_compiler g++ || find_compiler c++)"; then
    export CXX="$CXX_BIN"
fi

# ---------------------------------------------------------------------------------
# 4. Doctor: say what was found, and what to do about anything that is missing
# ---------------------------------------------------------------------------------
echo "[env] checkout  = $DEMO_ROOT"
if [ -n "${QEMU_ENV:-}" ]; then
    echo "[env] toolchain = $QEMU_ENV (bundled)"
else
    echo "[env] toolchain = none bundled, using what is on PATH"
fi
echo "[env] QEMU tree = $QEMU_DIR$([ -d "$QEMU_DIR" ] || echo "  (not fetched yet)")"
echo "[env] QEMU src  = $QEMU_SRC$([ -f "$QEMU_SRC/configure" ] || echo "  (not fetched yet)")"
echo "[env] qemu bin  = $QEMU_BIN$([ -x "$QEMU_BIN" ] || echo "  (not built yet)")"
echo "[env] TMPDIR    = $TMPDIR"
[ -n "$HOME_REDIRECTED" ] && echo "[env] HOME      = $HOME (redirected: the real one is not writable)"

for t in meson ninja python; do
    printf '[env] %-10s= %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo 'not found')"
done
printf '[env] %-10s= %s\n' "cc" "$(command -v ${CC:-cc} 2>/dev/null || echo 'not found')"
printf '[env] %-10s= %s\n' "c++" "$(command -v ${CXX:-c++} 2>/dev/null || echo 'not found')"
if [ -x "$QEMU_BIN" ]; then
    printf '[env] %-10s= %s\n' "qemu" "$("$QEMU_BIN" --version 2>/dev/null | head -1)"
fi

MISSING=""
for t in meson ninja; do
    command -v "$t" >/dev/null 2>&1 || MISSING="$MISSING $t"
done
# The compiler check is about what the build will actually use, not about what
# `cc` happens to resolve to: a conda prefix has no un-prefixed `cc` at all.
{ [ -n "${CC:-}" ] && command -v "$CC" >/dev/null 2>&1; } || MISSING="$MISSING cc"
{ [ -n "${CXX:-}" ] && command -v "$CXX" >/dev/null 2>&1; } || MISSING="$MISSING c++"
if [ -n "$MISSING" ]; then
    echo "[env] WARNING: not available:$MISSING"
    echo "[env]          QEMU needs meson + ninja + a C and C++ compiler to build."
    echo "[env]          Either install them, or create the bundled environment:"
    echo "[env]              bash setup-env.sh"
fi
if [ ! -d "$QEMU_DIR" ]; then
    echo "[env] NOTE: no QEMU source tree yet -- run \`make fetch\` (see README)."
fi
