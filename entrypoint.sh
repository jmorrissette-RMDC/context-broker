#!/bin/bash
# Context Broker — Entrypoint script
# REQ-CB §1.5: Wire package source from config.yml at container startup.
#
# Reads packages.source from config.yml and installs dependencies from
# the appropriate source before starting the application server.

set -e

CONFIG_FILE="${CONFIG_PATH:-/config/config.yml}"

if [ -f "$CONFIG_FILE" ]; then
    # Extract package source from config.yml using Python (available in the image)
    PKG_SOURCE=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    print(pkgs.get('source', 'pypi'))
except Exception:
    print('pypi')
" 2>/dev/null || echo "pypi")

    PKG_LOCAL_PATH=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    print(pkgs.get('local_path', '/app/packages'))
except Exception:
    print('/app/packages')
" 2>/dev/null || echo "/app/packages")

    PKG_DEVPI_URL=$(python3 -c "
import yaml, sys
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    url = pkgs.get('devpi_url')
    print(url if url else '')
except Exception:
    print('')
" 2>/dev/null || echo "")

    echo "Package source: $PKG_SOURCE"

    case "$PKG_SOURCE" in
        local)
            echo "Installing packages from local path: $PKG_LOCAL_PATH"
            pip install --user --no-cache-dir --no-index --find-links="$PKG_LOCAL_PATH" -r /app/requirements.txt
            ;;
        devpi)
            if [ -n "$PKG_DEVPI_URL" ]; then
                echo "Installing packages from devpi: $PKG_DEVPI_URL"
                pip install --user --no-cache-dir --index-url "$PKG_DEVPI_URL" -r /app/requirements.txt
            else
                echo "devpi_url not set, skipping package install"
            fi
            ;;
        pypi)
            # Packages already installed at build time; skip unless requirements changed
            echo "Package source is pypi — using build-time packages"
            ;;
        *)
            echo "Unknown package source: $PKG_SOURCE — using build-time packages"
            ;;
    esac
else
    echo "Config file not found at $CONFIG_FILE — using build-time packages"
fi

# Start the application
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
