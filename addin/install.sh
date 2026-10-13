#!/bin/bash
# Installs the helper PowerPoint uses to make robots (run once, and again if you move this folder).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROBOT_DIR="$(dirname "$HERE")"
PYTHON="${PYTHON:-$(command -v python3)}"
OFFICE_DIR="$HOME/Library/Group Containers/UBF8T346G9.Office/RobotTalker"
DEST="$HOME/Library/Application Scripts/com.microsoft.Powerpoint"

if ! "$PYTHON" -c "import numpy, PIL, pptx" 2>/dev/null; then
    echo "Missing Python packages. Install them with:"
    echo "  $PYTHON -m pip install -r \"$ROBOT_DIR/requirements.txt\""
    exit 1
fi

mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
sed -e "s|__ROBOT_DIR__|$ROBOT_DIR|" -e "s|__PYTHON__|$PYTHON|" -e "s|__OFFICE_DIR__|$OFFICE_DIR|" \
    "$HERE/RobotTalker.applescript" > "$TMP/RobotTalker.applescript"
osacompile -o "$DEST/RobotTalker.scpt" "$TMP/RobotTalker.applescript"
echo "Installed $DEST/RobotTalker.scpt"
echo "  robot folder: $ROBOT_DIR"
echo "  python:       $PYTHON"
