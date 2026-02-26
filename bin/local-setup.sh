#!/usr/bin/env bash
# ------
# This script is inteded to run the same steps someone would run when
# first starting working on the project during CI to validate that
# everything works.
# ------

set -ex
apt update
apt install -y pipx libpq-dev gcc gettext git
PIPX_BIN=$(pipx environment --value PIPX_BIN_DIR)
export PATH=$PIPX_BIN:$PATH

pipx install hatch
# Workaround for virtualenv 21.0.0 breaking changes: https://github.com/pypa/hatch/issues/2193
pipx inject hatch "virtualenv<21.0.0" --force
pipx install pre-commit

pre-commit install
pre-commit run --all-files

# Since we are running most of the steps from validate, we will be skipping it to
# speed up the process. Uncomment to add it back
# hatch run dev:validate

set +ex
