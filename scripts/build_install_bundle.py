"""Assemble an explicit allowlist of installable files; never archive the checkout."""
from pathlib import Path
import hashlib
import json
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build_bundle():
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    wheel = ROOT / 'dist' / f'pegasus_bridge-{version}-py3-none-any.whl'
    if not wheel.is_file():
        raise SystemExit('Run uv build before packaging.')
    with zipfile.ZipFile(wheel) as archive:
        required = {'pegasus_bridge/server.py', 'pegasus_bridge/installation.py',
                    'pegasus_bridge/preview.html', 'pegasus_bridge/preview_runtime.py',
                    'pegasus_bridge/authoring.py', 'pegasus_bridge/workflow_tools.py',
                    'pegasus_bridge/validation.py', 'pegasus_bridge/batch.py',
                    'pegasus_bridge/audio.py', 'pegasus_bridge/project.py', 'pegasus_bridge/qa.py'}
        if not required <= set(archive.namelist()):
            raise SystemExit('Incomplete wheel.')
    files = {
        wheel.name: wheel,
        'install.ps1': ROOT / 'scripts/install.ps1',
        'INSTALL.md': ROOT / 'docs/install.md',
        'preview.md': ROOT / 'docs/preview.md',
        'workflows.md': ROOT / 'docs/workflows.md',
        'examples/preview/preview.json': ROOT / 'examples/preview/preview.json',
        'examples/preview/scene.lua': ROOT / 'examples/preview/scene.lua',
    }
    for path in sorted((ROOT/'examples/workflows').glob('*')):
        if path.is_file():files['examples/workflows/'+path.name]=path
    checksums = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
    target = ROOT / 'dist' / f'pegasus-mcp-{version}-windows.zip'
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
        archive.writestr('SHA256SUMS.json', json.dumps(checksums, indent=2) + '\n')
    print(json.dumps({'bundle': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                      'files': len(files) + 1}, ensure_ascii=False))
    return target


if __name__ == '__main__':
    build_bundle()
