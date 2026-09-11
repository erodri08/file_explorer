#!/bin/bash
# Double-click this file in Finder to launch Media Manager.
# (First run: macOS may warn about an unidentified developer —
#  right-click > Open, then confirm. See README for details.)

cd "$(dirname "$0")"
VENV_DIR=".venv"

find_good_python() {
    local candidates=(
        /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
        /opt/homebrew/opt/python-tk@3.14/bin/python3.14
        /usr/local/bin/python3.14 /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11
        /opt/homebrew/bin/python3 /usr/local/bin/python3
        python3.14 python3.13 python3.12 python3.11 python3
    )
    for candidate in "${candidates[@]}"; do
        if command -v "$candidate" &> /dev/null; then
            tk_version=$("$candidate" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null)
            if [ -n "$tk_version" ]; then
                is_ok=$(awk -v v="$tk_version" 'BEGIN { print (v >= 8.6) ? "yes" : "no" }')
                if [ "$is_ok" = "yes" ]; then
                    echo "$candidate"
                    return 0
                fi
            fi
        fi
    done
    return 1
}

if [ -x "$VENV_DIR/bin/python3" ]; then
    tk_version=$("$VENV_DIR/bin/python3" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null)
    is_ok=$(awk -v v="${tk_version:-0}" 'BEGIN { print (v >= 8.6) ? "yes" : "no" }')
    if [ "$is_ok" != "yes" ]; then
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -x "$VENV_DIR/bin/python3" ]; then
    BASE_PYTHON=$(find_good_python)
    if [ -z "$BASE_PYTHON" ]; then
        echo "----------------------------------------------------------------"
        echo "No Python with a modern Tk (8.6+) was found."
        echo "This app needs one to draw its window correctly on macOS."
        echo ""
        echo "Easiest fix, in Terminal:"
        echo "  brew install python-tk"
        echo ""
        echo "Then run this launcher again."
        echo "----------------------------------------------------------------"
        osascript -e 'display alert "Modern Tk not found" message "Media Manager needs a Python with Tk 8.6 or newer to display its window (macOS'\''s built-in Python has a known bug that shows a blank window instead). In Terminal, run: brew install python-tk — then try again. See README.md for details."'
        echo "Press Return to close this window."
        read
        exit 1
    fi

    echo "Setting up (first run only) using: $BASE_PYTHON"
    "$BASE_PYTHON" -m venv "$VENV_DIR"
    if [ $? -ne 0 ]; then
        echo "Could not create a virtual environment. See README.md for manual setup steps."
        osascript -e 'display alert "Setup failed" message "Could not set up Media Manager automatically. Please see the Troubleshooting section of README.md for manual setup steps."'
        echo "Press Return to close this window."
        read
        exit 1
    fi
fi

echo "Checking dependencies..."
"$VENV_DIR/bin/python3" -m pip install --quiet --upgrade pip --disable-pip-version-check
"$VENV_DIR/bin/python3" -m pip install --quiet -r requirements.txt --disable-pip-version-check

if [ $? -ne 0 ]; then
    echo "Dependency install failed. See README.md for manual setup steps."
    osascript -e 'display alert "Setup failed" message "Could not install the app'\''s dependencies. Please see the Troubleshooting section of README.md."'
    echo "Press Return to close this window."
    read
    exit 1
fi

echo "Starting Media Manager..."
"$VENV_DIR/bin/python3" media_manager.py

if [ $? -ne 0 ]; then
    echo ""
    echo "The app closed with an error. Press Return to close this window."
    read
fi
