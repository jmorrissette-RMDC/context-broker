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

# ── REQ-001 §10: Install StateGraph packages (AE + TE) ──────────────
# Read the stategraph_packages list from config, defaulting to both standard packages.
SG_PACKAGES=$(python3 -c "
import yaml
try:
    with open('$CONFIG_FILE') as f:
        cfg = yaml.safe_load(f)
    pkgs = cfg.get('packages', {})
    sg_list = pkgs.get('stategraph_packages', ['context-broker-ae', 'context-broker-te'])
    print(' '.join(sg_list))
except Exception:
    print('context-broker-ae context-broker-te')
" 2>/dev/null || echo "context-broker-ae context-broker-te")

echo "Installing StateGraph packages: $SG_PACKAGES"

for pkg in $SG_PACKAGES; do
    case "$PKG_SOURCE" in
        local)
            # Look for pre-built wheels in /app/stategraph-wheels/ first (built in Dockerfile),
            # then fall back to /app/packages/ (volume mount for development overrides).
            SG_WHEEL_DIR="/app/stategraph-wheels"
            if [ -d "$SG_WHEEL_DIR" ]; then
                echo "Installing $pkg from built wheels: $SG_WHEEL_DIR"
                pip install --user --no-cache-dir --no-index --find-links="$SG_WHEEL_DIR" "$pkg" || echo "Warning: failed to install $pkg"
            else
                echo "Installing $pkg from local packages: $PKG_LOCAL_PATH"
                pip install --user --no-cache-dir --no-index --find-links="$PKG_LOCAL_PATH" "$pkg" || echo "Warning: failed to install $pkg"
            fi
            ;;
        devpi)
            if [ -n "$PKG_DEVPI_URL" ]; then
                pip install --user --no-cache-dir --index-url "$PKG_DEVPI_URL" "$pkg" || echo "Warning: failed to install $pkg from devpi"
            fi
            ;;
        pypi)
            pip install --user --no-cache-dir "$pkg" || echo "Warning: failed to install $pkg from pypi"
            ;;
    esac
done

# Ensure /data subdirectories exist with correct ownership.
# /data is a volume mount (host-owned). The app needs writable subdirs.
mkdir -p /data/downloads 2>/dev/null || true

# Start the application
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
