#!/usr/bin/env bash
#
# Verilator + cocotb + LitePCIe minimal demo
# Bootstrap the self-contained toolchain (Verilator / g++ / make / Python).
#
# You only need this if your machine does not already have a usable Verilator
# and cocotb. If it does, skip this script entirely: `source env.sh` will use
# whatever is on your PATH.
#
#   bash setup-env.sh                       # create the toolchain, then verify it
#   bash setup-env.sh --fetch-micromamba    # ... installing micromamba first if needed
#   bash setup-env.sh --channel conda-forge --pip-index https://pypi.org/simple
#
# Nothing here is tied to a particular machine or user: every path is derived
# from the location of this script, and every location is overridable.
#
# Environment variables (all optional):
#   LITEPCIE_DEMO_TOOLS        where to create the toolchain   [<checkout>/../.tools]
#   LITEPCIE_DEMO_CONDA_CHANNEL conda channel or URL           [conda-forge]
#   LITEPCIE_DEMO_PIP_INDEX    pip index URL                   [pip's own default]
#   LITEPCIE_DEMO_PYTHON       python version to install       [3.12]
#   MAMBA_ROOT_PREFIX          conda package cache root         [<tools>/mamba-root]
#
set -euo pipefail

DEMO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
TOOLS_ROOT="${LITEPCIE_DEMO_TOOLS:-$DEMO_ROOT/../.tools}"
CONDA_ENV="$TOOLS_ROOT/env"
CHANNEL="${LITEPCIE_DEMO_CONDA_CHANNEL:-conda-forge}"
PIP_INDEX="${LITEPCIE_DEMO_PIP_INDEX:-}"
PY_VERSION="${LITEPCIE_DEMO_PYTHON:-3.12}"
FETCH=0
FORCE=0
while [ $# -gt 0 ]; do
    case "$1" in
        --channel)          CHANNEL="$2"; shift 2 ;;
        --pip-index)        PIP_INDEX="$2"; shift 2 ;;
        --python)           PY_VERSION="$2"; shift 2 ;;
        --tools)            TOOLS_ROOT="$2"; CONDA_ENV="$TOOLS_ROOT/env"; shift 2 ;;
        --fetch-micromamba) FETCH=1; shift ;;
        --force)            FORCE=1; shift ;;
        -h|--help)          awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' \
                                "${BASH_SOURCE[0]:-$0}"; exit 0 ;;
        *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

say() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

# The default toolchain location is a sibling of this checkout, so it still has a
# `..` in it. Normalise it once, here, so everything printed below is a real path.
mkdir -p "$TOOLS_ROOT"
TOOLS_ROOT="$(cd "$TOOLS_ROOT" && pwd)"
CONDA_ENV="$TOOLS_ROOT/env"

# ---------------------------------------------------------------------------------
# 1. Find a conda-family installer (mamba / micromamba / conda), or fetch one
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

# Map `uname` to the platform tag micromamba's download API expects.
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
    url="${LITEPCIE_DEMO_MICROMAMBA_URL:-https://micro.mamba.pm/api/micromamba/$plat/latest}"

    say "fetching micromamba for $plat ..."
    say "  $url"
    mkdir -p "$TOOLS_ROOT/bin"
    tmp="$(mktemp -d)"
    if ! curl -fsSL "$url" -o "$tmp/micromamba.tar.bz2"; then
        rm -rf "$tmp"
        die "download failed. Set LITEPCIE_DEMO_MICROMAMBA_URL to a mirror, or install
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

    c) skip the bundled toolchain altogether and use your own:
         - install Verilator >= 5.0 (needs --timing support) and a C++ compiler
         - pip install cocotb litex
         - then just `source env.sh`
HINT
        exit 1
    fi
fi
say "installer: $MM"

# ---------------------------------------------------------------------------------
# 2. Create the conda environment
# ---------------------------------------------------------------------------------
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$TOOLS_ROOT/mamba-root}"
mkdir -p "$MAMBA_ROOT_PREFIX"

if [ -x "$CONDA_ENV/bin/verilator" ] && [ "$FORCE" = 0 ]; then
    say "$CONDA_ENV already exists (use --force to recreate it)"
else
    if [ "$FORCE" = 1 ] && [ -d "$CONDA_ENV" ]; then
        say "removing the existing environment at $CONDA_ENV"
        "$MM" env remove -y -p "$CONDA_ENV" || rm -rf "$CONDA_ENV"
    fi
    say "creating $CONDA_ENV with python=$PY_VERSION verilator make pip ..."
    # `verilator` from conda-forge pulls in a complete gcc toolchain, which is why
    # no separate compiler package is requested here.
    if ! "$MM" create -y -p "$CONDA_ENV" \
            -c "$CHANNEL" --override-channels \
            "python=$PY_VERSION" pip make verilator; then
        die "environment creation failed. If the channel is unreachable, retry with a
       mirror, e.g. --channel https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge"
    fi
fi

# ---------------------------------------------------------------------------------
# 3. Python packages (cocotb and LiteX, which brings in migen)
# ---------------------------------------------------------------------------------
PY="$CONDA_ENV/bin/python"
[ -x "$PY" ] || die "no python inside $CONDA_ENV"

PIP_ARGS=(--no-input)
if [ -n "$PIP_INDEX" ]; then
    PIP_ARGS+=(-i "$PIP_INDEX")
fi

say "installing cocotb + litex ..."
"$PY" -m pip install "${PIP_ARGS[@]}" --upgrade cocotb litex

# ---------------------------------------------------------------------------------
# 4. Verify
# ---------------------------------------------------------------------------------
say "verifying ..."
"$CONDA_ENV/bin/verilator" --version
"$PY" -c 'import cocotb, migen, litex; print("cocotb", cocotb.__version__)'

if ! "$CONDA_ENV/bin/verilator" --help 2>&1 | grep -q -- --timing; then
    say "WARNING: this Verilator does not advertise --timing, which cocotb needs."
    say "         Install Verilator >= 5.0."
fi

cat <<EOF

[setup] done. Toolchain: $CONDA_ENV

  Next:
    source env.sh
    make report          # regression suite + report/index.html

  If you keep your projects elsewhere, point the environment at the toolchain:
    export LITEPCIE_DEMO_TOOLS=$TOOLS_ROOT

EOF
