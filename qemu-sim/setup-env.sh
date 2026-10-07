#!/usr/bin/env bash
#
# begonia-pciesim / qemu-sim
# Bootstrap the self-contained toolchain needed to build QEMU from source:
# meson + ninja + pkg-config + glib + pixman + a C compiler.
#
# You only need this if your machine does not already have those. If it does,
# skip this script: `source env.sh` will use whatever is on your PATH.
#
#   bash setup-env.sh                       # create the environment, then verify it
#   bash setup-env.sh --fetch-micromamba     # ... installing micromamba first if needed
#   bash setup-env.sh --channel conda-forge
#
# Nothing here is tied to a particular machine or user: every path is derived
# from the location of this script, and every location is overridable.
#
# The environment is created *inside* the shared toolchain root, as
# `envs/qemu`, next to rtl-sim's `env`. Both are siblings of the checkouts, so
# neither checkout has to know where they live.
#
# Environment variables (all optional):
#   QEMU_DEMO_TOOLS          shared toolchain root     [<checkout>/../.tools]
#   QEMU_DEMO_CONDA_CHANNEL  conda channel or URL      [conda-forge]
#   QEMU_DEMO_PYTHON         python version            [3.12]
#   QEMU_DEMO_MICROMAMBA_URL micromamba download URL   [micro.mamba.pm/api/...]
#                            -- the escape hatch when the network blocks it;
#                            point it at a mirror, e.g. a Tsinghua one
#   MAMBA_ROOT_PREFIX        conda package cache root  [<tools>/mamba-root]
#
# Only QEMU_DEMO_TOOLS is shared with env.sh -- exporting it before `source
# env.sh` affects both. The rest are halves of one picture: this script is about
# *creating* the toolchain (which channel, which python, where to download
# micromamba from), while env.sh is about *using* whatever is already there and
# adds QEMU_DEMO_QEMU_DIR (where the QEMU source and build tree go),
# QEMU_DEMO_TMPDIR and QEMU_DEMO_HOME. README §7 lists all seven in one place.
#
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TOOLS_ROOT="${QEMU_DEMO_TOOLS:-$DEMO_ROOT/../.tools}"
QEMU_ENV="$TOOLS_ROOT/envs/qemu"
CHANNEL="${QEMU_DEMO_CONDA_CHANNEL:-conda-forge}"
PY_VERSION="${QEMU_DEMO_PYTHON:-3.12}"
FETCH=0
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --channel)          CHANNEL="$2"; shift 2 ;;
        --python)           PY_VERSION="$2"; shift 2 ;;
        --tools)            TOOLS_ROOT="$2"; QEMU_ENV="$TOOLS_ROOT/envs/qemu"; shift 2 ;;
        --fetch-micromamba) FETCH=1; shift ;;
        --force)            FORCE=1; shift ;;
        -h|--help)          awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' \
                                "${BASH_SOURCE[0]:-$0}"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

mkdir -p "$TOOLS_ROOT"
TOOLS_ROOT="$(cd "$TOOLS_ROOT" && pwd)"
QEMU_ENV="$TOOLS_ROOT/envs/qemu"

# ---------------------------------------------------------------------------------
# 1. Find a conda-family installer (mamba / micromamba / conda)
# ---------------------------------------------------------------------------------
find_installer() {
    local c
    for c in "$TOOLS_ROOT/bin/micromamba" "$TOOLS_ROOT/bin/mamba"; do
        [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
    done
    for c in micromamba mamba conda; do
        if command -v "$c" >/dev/null 2>&1; then
            printf '%s\n' "$(command -v "$c")"
            return 0
        fi
    done
    return 1
}

micromamba_platform() {
    case "$(uname -s)" in
        Linux)  case "$(uname -m)" in x86_64|amd64) echo linux-64 ;;  aarch64|arm64) echo linux-aarch64 ;; esac ;;
        Darwin) case "$(uname -m)" in arm64) echo osx-arm64 ;;          x86_64) echo osx-64 ;; esac ;;
    esac
}

fetch_micromamba() {
    local plat url tmp
    plat="$(micromamba_platform)"
    [ -n "$plat" ] || die "unsupported platform $(uname -s)/$(uname -m); install micromamba or conda by hand"
    url="${QEMU_DEMO_MICROMAMBA_URL:-https://micro.mamba.pm/api/micromamba/$plat/latest}"

    say "fetching micromamba for $plat ..."
    mkdir -p "$TOOLS_ROOT/bin"
    tmp="$(mktemp -d)"
    if ! curl -fsSL "$url" -o "$tmp/micromamba.tar.bz2"; then
        rm -rf "$tmp"
        die "download failed. Set QEMU_DEMO_MICROMAMBA_URL to a mirror, or install
       micromamba / mamba / conda yourself and re-run without --fetch-micromamba."
    fi
    tar xjf "$tmp/micromamba.tar.bz2" -C "$tmp" bin/micromamba \
        || die "could not extract bin/micromamba (is bzip2 available?)"
    mv "$tmp/bin/micromamba" "$TOOLS_ROOT/bin/micromamba"
    chmod +x "$TOOLS_ROOT/bin/micromamba"
    rm -rf "$tmp"
}

MM="$(find_installer || true)"
if [ -z "$MM" ]; then
    if [ "$FETCH" = 1 ]; then
        fetch_micromamba
        MM="$TOOLS_ROOT/bin/micromamba"
    else
        cat >&2 <<'HINT'
[setup] ERROR: no micromamba / mamba / conda found.

  Pick one of:

    a) let this script fetch a static micromamba binary
         bash setup-env.sh --fetch-micromamba

    b) install one yourself, then re-run this script
         micromamba   https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html
         miniforge    https://github.com/conda-forge/miniforge    (provides `mamba`)

    c) skip the bundled toolchain and use your own: install meson, ninja,
       pkg-config, the glib and pixman development packages and a C compiler,
       then just `source env.sh`
HINT
        exit 1
    fi
fi
say "installer: $MM"

# ---------------------------------------------------------------------------------
# 2. Create the environment
# ---------------------------------------------------------------------------------
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$TOOLS_ROOT/mamba-root}"
mkdir -p "$MAMBA_ROOT_PREFIX"

# gcc_linux-64 / gxx_linux-64 bring in binutils and the sysroot, so the
# compiler is self-contained rather than borrowed from the host.
PKGS=(python="$PY_VERSION" meson ninja pkg-config glib pixman zlib flex bison gcc_linux-64 gxx_linux-64)

if [ -x "$QEMU_ENV/bin/meson" ] && [ "$FORCE" = 0 ]; then
    say "$QEMU_ENV already exists (use --force to recreate it)"
else
    if [ "$FORCE" = 1 ] && [ -d "$QEMU_ENV" ]; then
        say "removing the existing environment at $QEMU_ENV"
        "$MM" env remove -y -p "$QEMU_ENV" || rm -rf "$QEMU_ENV"
    fi
    say "creating $QEMU_ENV with ${PKGS[*]} ..."
    if ! "$MM" create -y -p "$QEMU_ENV" -c "$CHANNEL" --override-channels "${PKGS[@]}"; then
        die "environment creation failed. If the channel is unreachable, retry with a
       mirror, e.g. --channel https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge"
    fi
fi

# ---------------------------------------------------------------------------------
# 3. Verify
# ---------------------------------------------------------------------------------
say "verifying ..."
[ -x "$QEMU_ENV/bin/meson" ] || die "no meson inside $QEMU_ENV"
# Verify the compiler the way env.sh will actually pick it: a conda-forge
# toolchain has no un-prefixed `cc`, only `x86_64-conda-linux-gnu-gcc` and
# friends, so looking for `cc` alone would report the *host* compiler here and
# hide the fact that the prefix has none.
pick_compiler() {
    local name="$1" c
    for c in "$QEMU_ENV"/bin/*-"$name" "$QEMU_ENV"/bin/"$name"; do
        [ -x "$c" ] && { printf '%s\n' "$c"; return 0; }
    done
    command -v "$name" 2>/dev/null
}
CC_BIN="$(pick_compiler gcc || pick_compiler cc || true)"
CXX_BIN="$(pick_compiler g++ || pick_compiler c++ || true)"

# pkg-config needs to be pointed at this prefix: we do not activate the conda
# environment, we only put its bin/ on PATH, so its activation hook never runs.
export PKG_CONFIG_PATH="$QEMU_ENV/lib/pkgconfig:$QEMU_ENV/share/pkgconfig"
"$QEMU_ENV/bin/meson" --version
"$QEMU_ENV/bin/ninja" --version
"$QEMU_ENV/bin/pkg-config" --modversion glib-2.0
"$QEMU_ENV/bin/pkg-config" --modversion pixman-1
if [ -n "$CC_BIN" ]; then
    "$CC_BIN" --version | head -1
else
    say "WARNING: no C compiler found in $QEMU_ENV or on PATH"
fi
if [ -n "$CXX_BIN" ]; then
    "$CXX_BIN" --version | head -1
else
    say "WARNING: no C++ compiler found in $QEMU_ENV or on PATH -- QEMU needs one"
fi

cat <<EOF

[setup] done. Toolchain: $QEMU_ENV

  Next:
    source env.sh
    make fetch           # download + unpack the pinned QEMU sources
    make test            # build QEMU with the device, then run the functional test

  If you keep your projects elsewhere, point the environment at this toolchain:
    export QEMU_DEMO_TOOLS=$TOOLS_ROOT

EOF
