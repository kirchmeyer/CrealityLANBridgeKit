from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'apply_lan_model_fix.sh'


def test_stock_app_patch_script_is_a_noop(tmp_path):
    bundle_dir = tmp_path / 'web'
    rel = 'deviceMgr/assets/C_bZROdP.js'
    path = bundle_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    original = 'window.__CP_LAN_MODEL_FIX_V9__ = false;\nfunction foo(deviceType) { return deviceType === 0; }\n'
    path.write_text(original, encoding='utf-8')

    env = dict(__import__('os').environ)
    env['ROOT'] = str(bundle_dir)
    result = subprocess.run(['bash', str(SCRIPT)], cwd=str(ROOT), env=env, check=True, capture_output=True, text=True)

    content = path.read_text(encoding='utf-8')
    assert content == original
    assert 'No app bundle patching' in result.stdout
    assert 'stock app remains untouched' in result.stdout.lower()


def test_stock_app_patch_script_leaves_multiple_bundles_untouched(tmp_path):
    bundle_dir = tmp_path / 'web'
    rels = [
        'deviceMgr/assets/C_bZROdP.js',
        'sendToPrinterPage/assets/Bl5CvdKl.js',
    ]
    originals = {}
    for rel in rels:
        path = bundle_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        original = f'window.__CP_LAN_MODEL_FIX_V9__ = false;\n// {rel}\n'
        originals[rel] = original
        path.write_text(original, encoding='utf-8')

    env = dict(__import__('os').environ)
    env['ROOT'] = str(bundle_dir)
    subprocess.run(['bash', str(SCRIPT)], cwd=str(ROOT), env=env, check=True)

    for rel in rels:
        content = (bundle_dir / rel).read_text(encoding='utf-8')
        assert content == originals[rel]
