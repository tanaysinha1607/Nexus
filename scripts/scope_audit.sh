#!/usr/bin/env bash
# Shell wrapper executing python scripts/scope_audit.py
python3 "$(dirname "$0")/scope_audit.py" "$@"
