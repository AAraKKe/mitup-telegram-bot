#!/usr/bin/env bash
set -ex
apt update
apt install -y pipx libpq-dev gcc gettext git
PIPX_BIN=$(pipx environment --value PIPX_BIN_DIR)
export PATH=$PIPX_BIN:$PATH

pipx install hatch
pipx install pre-commit

pre-commit install
pre-commit run --all-files

hatch run dev:validate

set +ex
