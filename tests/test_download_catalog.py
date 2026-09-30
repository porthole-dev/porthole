#!/usr/bin/env python3
"""Exercise public image catalog generation without network or real releases."""
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))


def test_bundle_and_history():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        script = root / '.github/scripts/site-downloads.py'
        script.parent.mkdir(parents=True)
        script.write_bytes((ROOT / '.github/scripts/site-downloads.py').read_bytes())
        digest = 'a' * 64
        names = ['boot.img', 'google-taimen.img.gz', 'SHA256SUMS', 'INSTALL.md', 'device.json',
                 'google-taimen-install.zip', 'BUNDLE-SHA256SUMS', 'google-taimen-native.zip', 'NATIVE-BUNDLE-SHA256SUMS']
        assets = [{'name': name, 'size': 1024, 'digest': 'sha256:' + digest,
                   'browser_download_url': 'https://fixture.invalid/' + name} for name in names]
        latest = dict(tag_name='google-taimen-candidate-6', name='Pixel 2 XL candidate 6',
                      published_at='2026-09-30', draft=False, assets=assets)
        older = dict(latest, tag_name='google-taimen-candidate-5', name='Pixel 2 XL candidate 5')
        data = {
            'https://api.github.com/repos/porthole-dev/pmos-packages/releases?per_page=100': b'[]',
            'https://api.github.com/repos/porthole-dev/pmaports/releases?per_page=100': json.dumps([latest, older]).encode(),
            'https://fixture.invalid/device.json': b'{"device":"google-taimen","name":"Pixel 2 XL"}',
            'https://fixture.invalid/SHA256SUMS': (digest + '  boot.img\n' + digest + '  google-taimen.img.gz\n').encode(),
            'https://fixture.invalid/INSTALL.md': b'# Install\n\nExisting manual guide\n',
            'https://fixture.invalid/NATIVE-BUNDLE-SHA256SUMS': (digest + '  google-taimen-native.zip\n').encode(),
            'https://fixture.invalid/BUNDLE-SHA256SUMS': (digest + '  google-taimen-install.zip\n').encode(),
        }
        def fetch(request, timeout):
            return io.BytesIO(data[request.full_url])
        with patch('urllib.request.urlopen', fetch):
            runpy.run_path(str(script), run_name='__main__')
            text = (root / '.run/public-downloads/images.md').read_text()
            assert json.loads((root / '.run/public-downloads/devices.json').read_text())['google-taimen']['tag'] == latest['tag_name']
            assert 'bash install.sh' in text and 'powershell -NoProfile' in text
            assert 'No Python required' in text
            assert 'python3 google-taimen-install.zip' in text
            assert 'Previous candidate: google-taimen-candidate-5' in text
            assert text.count('<details>') == text.count('</details>') == 3
            assert 'Manual installation and recovery' in text
            assert 'boot.img' in text and 'Existing manual guide' in text
            data['https://fixture.invalid/BUNDLE-SHA256SUMS'] = (('b' * 64) + '  google-taimen-install.zip\n').encode()
            try:
                runpy.run_path(str(script), run_name='__main__')
            except SystemExit as error:
                assert 'Bundle checksum mismatch' in str(error)
            else:
                raise AssertionError('Catalog advertised a mismatching bundle')


if __name__ == '__main__':
    test_bundle_and_history()
    print('catalog: complete bundle, manual fallback, retained history and mismatch rejection passed')
