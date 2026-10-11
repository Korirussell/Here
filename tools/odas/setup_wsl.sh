#!/usr/bin/env bash
# Build ODAS (github.com/introlab/odas, MIT) inside WSL2 Ubuntu at a pinned commit (SPEC §11).
#
#   wsl -d Ubuntu -- bash tools/odas/setup_wsl.sh          # from the repo root on Windows
#
# Result: ~/here/odas/build/bin/odaslive. Nothing from ODAS is copied into this repo.
# Build deps (once, needs sudo):
#   sudo apt-get install -y build-essential cmake git libfftw3-dev libconfig-dev libasound2-dev libpulse-dev socat
set -euo pipefail

ODAS_COMMIT=bcb845434495e293df3d48f1203b7a86e1852449     # master 2026-10 ("Add ODAS_DISABLE_INSTALL")
DIR=${ODAS_DIR:-$HOME/here/odas}

missing=()
for pkg in build-essential cmake git libfftw3-dev libconfig-dev libasound2-dev libpulse-dev socat; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if ((${#missing[@]})); then
    echo "missing packages: ${missing[*]}"
    echo "run: sudo apt-get install -y ${missing[*]}"
    exit 1
fi

if [ ! -d "$DIR/.git" ]; then
    mkdir -p "$(dirname "$DIR")"
    git clone https://github.com/introlab/odas.git "$DIR"
fi
cd "$DIR"
git fetch -q origin || true
git checkout -q -f "$ODAS_COMMIT"      # -f: drops the local patch below before re-applying it

# Local patch (not committed anywhere): ODAS's JSON sinks append with sprintf(buf, "%s...", buf, ...), overlapping
# source and destination (undefined behaviour). Ubuntu 26.04's glibc starts by clearing buf, so every tracked
# frame came out as a bare "}". Rewrite those calls to sprintf(buf + strlen(buf), "...", ...).
grep -rlE 'sprintf\([a-zA-Z_>.-]+ *, *"%s' src demo | while read -r f; do
    perl -0pi -e 's/sprintf\(\s*([A-Za-z_][\w>.-]*?)\s*,\s*"%s((?:[^"\\]|\\.)*)"\s*,\s*\1\s*([,)])/sprintf($1 + strlen($1), "$2"$3/g' "$f"
    grep -q '#include <string.h>' "$f" || sed -i '1i #include <string.h>' "$f"
done
left=$(grep -rcE 'sprintf\([a-zA-Z_>.-]+ *, *"%s[^"]*" *, *[a-zA-Z_>.-]+ *[,)]' src demo | awk -F: '{s+=$2} END {print s+0}')
echo "overlapping sprintf calls left after the patch: $left"

# ODAS declares cmake_minimum_required(2.4.6..3.16); CMake 4 refuses < 3.5 without the policy floor.
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DODAS_DISABLE_INSTALL=ON
cmake --build build -j"$(nproc)"
ls -l build/bin/odaslive
echo "ODAS built at $DIR/build/bin/odaslive (commit ${ODAS_COMMIT:0:7})"
