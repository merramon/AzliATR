#!/bin/zsh
# Bidder Asset Test - double-click to start (Mac).
# First run on a new computer sets everything up automatically (needs internet, ~5 minutes, no admin password).
cd "$(dirname "$0")"
echo "=== Bidder Asset Test ==="

if ! .venv/bin/python -c "import pdfplumber, openpyxl, pypdfium2, rapidocr, onnxruntime" >/dev/null 2>&1; then
  echo "First-time setup on this computer - please wait (needs internet, about 5 minutes)..."
  UV="$(command -v uv)"
  [ -z "$UV" ] && UV="$HOME/.local/bin/uv"
  if [ ! -x "$UV" ]; then
    echo "Downloading the Python installer (uv)..."
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
  fi
  if [ ! -x "$UV" ]; then
    echo "Setup failed: could not download uv. Check the internet connection and try again."
    read -k1 "?Press any key to close..."; exit 1
  fi
  rm -rf .venv
  if ! "$UV" venv --python 3.14 .venv || ! "$UV" pip install --python .venv -r requirements.txt; then
    echo "Setup failed while installing Python packages. Check the internet connection and try again."
    read -k1 "?Press any key to close..."; exit 1
  fi
  echo "Preparing the scanner (one time)..."
  .venv/bin/python -c "from rapidocr import RapidOCR; RapidOCR()" >/dev/null 2>&1
  echo "Setup complete."
fi

.venv/bin/python tools/asset_test_app.py "$@"
