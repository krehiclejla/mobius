#!/usr/bin/env sh
# Disposable image-replay fixture: restore one pinned Python dependency.
set -eu
case "${1:-}" in
  check)
    python3 -c 'from importlib.metadata import version; assert version("pyfiglet") == "1.0.4"'
    ;;
  apply)
    sudo -n python3 -m pip install --no-cache-dir pyfiglet==1.0.4
    ;;
  *) echo "usage: restore-python.sh check|apply" >&2; exit 2 ;;
esac
