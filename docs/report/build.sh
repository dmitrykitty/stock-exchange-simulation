#!/usr/bin/env bash
# Build interim_report.pdf next to this script with Tectonic (https://tectonic-typesetting.github.io).
# TECTONIC: path to the tectonic binary (default: tectonic on PATH). The first build downloads TeX packages.
set -euo pipefail
cd "$(dirname "$0")"
"${TECTONIC:-tectonic}" --untrusted --keep-logs interim_report.tex
