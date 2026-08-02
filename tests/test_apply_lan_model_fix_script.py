from pathlib import Path


def test_apply_lan_model_fix_script_is_a_noop():
    script = Path("scripts/apply_lan_model_fix.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "No app bundle patching" in script
    assert "exit 0" in script
    assert "window.__CP_LAN_MODEL_FIX_V14__" not in script
    assert "python3 - <<'PY'" not in script
