#!/bin/zsh
set -eu
cd -- "${0:A:h}"

bundled_python="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
if [[ -x .venv/bin/python ]]; then
  python_exe=.venv/bin/python
elif [[ -x "$bundled_python" ]] && "$bundled_python" -c 'import numpy, openpyxl, PIL' 2>/dev/null; then
  python_exe="$bundled_python"
else
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
  python_exe=.venv/bin/python
fi
exec "$python_exe" app.py --open "$@"
