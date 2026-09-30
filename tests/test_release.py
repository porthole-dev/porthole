#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Release decisions must fail closed; a failed attempt keeps the old download."""
import copy
import datetime
import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
import porthole_cmd_release as release


def fixture():
    return {'schema': 1, 'id': '2026-09-28.1', 'device': 'google-taimen',
            'channel': 'edge', 'ui': 'phosh', 'init': 'systemd', 'date': '2026-09-28',
            'status': 'verified', 'build_url': 'https://example.org/build/1',
            'sources': {k: 'a'*40 for k in ('porthole', 'pmaports', 'pmbootstrap')},
            'builder_sha256': 'b'*64,
            'files': [{'name': 'rootfs.img.xz', 'size': 4, 'sha256': 'c'*64,
                       'role': 'rootfs', 'url': 'https://example.org/rootfs.img.xz'},
                      {'name': 'boot.img', 'size': 4, 'sha256': '9'*64, 'role': 'boot'},
                      {'name': 'dtbo.img', 'size': 364,
                       'sha256': 'fd9752b381403312bcdeda8bb8555f0ad964f3464e7bc4ed307e3cef018362da',
                       'role': 'dtbo'}],
            'packages': [{'name': 'kernel', 'version': '1-r0', 'sha256': 'd'*64}],
            'indexes': [{'url': 'https://example.org/APKINDEX.tar.gz', 'sha256': 'e'*64}],
            'checks': {k: True for k in ('boot-content', 'rootfs-packages', 'first-boot', 'firmware', 'export')}}


def rejects(fn, *args):
    try:
        fn(*args)
    except (ValueError, KeyError):
        return
    raise AssertionError('accepted invalid release evidence')


def test_release_contract():
    good = fixture()
    release.validate_manifest(good)
    policy = release.policies(ROOT)['google-taimen']
    assert policy['dtbo_sha256'] in (ROOT/'profiles/google-taimen/device.env').read_text()
    release.validate_manifest(good, policy)
    missing_dtbo = copy.deepcopy(good)
    missing_dtbo['files'] = [f for f in good['files'] if f['role'] != 'dtbo']
    rejects(release.validate_manifest, missing_dtbo, policy)
    wrong_dtbo = copy.deepcopy(good)
    wrong_dtbo['files'][-1]['sha256'] = '0'*64
    rejects(release.validate_manifest, wrong_dtbo, policy)
    for key, value in [('files', []), ('packages', []), ('indexes', []), ('sources', {}),
                       ('builder_sha256', ''), ('id', '../escape')]:
        bad = copy.deepcopy(good); bad[key] = value
        rejects(release.validate_manifest, bad)
    for check in good['checks']:
        bad = copy.deepcopy(good); bad['checks'][check] = False
        rejects(release.validate_manifest, bad)
    for name in ('../escape', '/absolute', 'bad name'):
        bad = copy.deepcopy(good); bad['files'][0]['name'] = name
        rejects(release.validate_manifest, bad)
    bad = copy.deepcopy(good); bad['files'] = bad['files'][:1]
    rejects(release.validate_manifest, bad)
    bad = copy.deepcopy(good); bad['files'] *= 2
    rejects(release.validate_manifest, bad)
    rejects(release.https, 'javascript:alert(1)')
    rejects(release.https, 'https://user:password@example.org/a')


def test_catalogue_and_hardware():
    good = fixture()
    policy = release.policies(ROOT)['google-taimen']
    report = {'schema': 1, 'device': good['device'], 'image_sha256': 'c'*64,
              'date': '2026-09-28', 'reviewer': 'human', 'kernel': 'verified kernel',
              'packages_sha256': 'f'*64, 'tests': {k: {'result': 'works', 'command': 'probe',
              'control': 'executed counter increased', 'evidence': 'https://example.org/test'}
              for k in policy['critical_tests']}}
    verdict = release.validate_report(report, good, policy, datetime.date(2026, 9, 28))
    assert verdict['critical_passed'] and not verdict['stale']
    assert release.validate_report(report, good, policy, datetime.date(2026, 11, 1))['stale']
    bad = copy.deepcopy(report); bad['image_sha256'] = '0'*64
    rejects(release.validate_report, bad, good, policy)
    bad = copy.deepcopy(report); bad['tests']['boot']['control'] = ''
    rejects(release.validate_report, bad, good, policy)
    rejects(release.validate_report, report, good, policy, datetime.date(2026, 9, 27))
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        repo = d/'repo'
        for device in ('google-cheetah', 'google-taimen'):
            profile = repo/'profiles'/device
            profile.mkdir(parents=True)
            profile.joinpath('device.env').write_bytes(
                (ROOT/'profiles'/device/'device.env').read_bytes())
        enabled = dict(policy, image_enabled=True, blocked_by=[])
        (repo/'profiles/google-taimen/release.json').write_text(json.dumps(enabled))
        release.write(d/'good.json', good)
        failed = {k: good[k] for k in ('schema','device','channel','ui','init','build_url')}
        failed.update(id='2026-09-29.1', date='2026-09-29', status='failed', reason='kernel mismatch')
        release.write(d/'failed.json', failed)
        release.write(d/'hardware.json', report)
        data = release.catalogue(repo, d, datetime.date(2026, 9, 29))
        page_dir = d/'site'
        release.markdown_catalogue(data, page_dir)
        text = (page_dir/'google-taimen.md').read_text()
        assert 'kernel mismatch' in text and good['files'][0]['url'] in text
        assert 'works' in text
        cheetah = (page_dir/'google-cheetah.md').read_text()
        assert 'build and release setup is still needed' in cheetah
        assert 'No published candidate for this device yet.' in cheetah
        assert 'Nura' not in (page_dir/'index.md').read_text()
        assert 'AI policy' not in cheetah
        downloads = release.markdown_downloads(data)
        assert 'No release images yet.' in downloads
        assert '../devices/google-taimen/' in downloads
        assert [row['device'] for row in data['devices']] == ['google-cheetah', 'google-taimen']
        stale = release.catalogue(repo, d, datetime.date(2026, 11, 1))
        assert good['files'][0]['url'] not in release.markdown_downloads(stale)
        blocked = release.catalogue(ROOT, d, datetime.date(2026, 9, 29))
        assert good['files'][0]['url'] not in release.markdown_downloads(blocked)
        (d/'hardware.json').unlink()
        untested = release.catalogue(repo, d, datetime.date(2026, 9, 29))
        assert not untested['devices'][1]['builds'][1]['download_ready']
        assert good['files'][0]['url'] not in release.markdown_downloads(untested)
        release.markdown_catalogue(untested, page_dir)
        assert good['files'][0]['url'] not in (page_dir/'google-taimen.md').read_text()
        release.markdown_catalogue(untested, page_dir, {'google-taimen': {'tag': 'google-taimen-candidate-6', 'date': '2026-09-30'}})
        assert 'Experimental candidate' in (page_dir/'index.md').read_text()
        assert 'Download experimental candidate google-taimen-candidate-6' in (page_dir/'google-taimen.md').read_text()
        assert 'No published candidate for this device' not in (page_dir/'google-taimen.md').read_text()
        assert not untested['devices'][1]['builds'][1]['download_ready']


def test_artifact_verification_checks_bytes_and_rejects_symlinks():
    good = fixture()
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root/'rootfs.img.xz').write_bytes(b'root')
        (root/'boot.img').write_bytes(b'boot')
        (root/'dtbo.img').write_bytes(b'dtbo')
        for item in good['files']:
            content = (root/item['name']).read_bytes()
            item['size'] = len(content)
            item['sha256'] = hashlib.sha256(content).hexdigest()
        release.verify_artifacts(good, root)
        (root/'boot.img').write_bytes(b'nope')
        rejects(release.verify_artifacts, good, root)
        (root/'outside').write_bytes(b'boot')
        (root/'boot.img').unlink()
        (root/'boot.img').symlink_to(root/'outside')
        rejects(release.verify_artifacts, good, root)


def test_plan_is_honest():
    plan = release.plan(ROOT)
    assert not plan['include']
    assert {r['device'] for r in plan['blocked']} == {'google-cheetah', 'google-taimen'}
    cheetah = next(r for r in plan['blocked'] if r['device'] == 'google-cheetah')
    assert cheetah['reasons'] == ['No reviewed release policy exists for this device.']


if __name__ == '__main__':
    test_release_contract()
    test_catalogue_and_hardware()
    test_artifact_verification_checks_bytes_and_rejects_symlinks()
    test_plan_is_honest()
    print('release: contracts, negative controls, freshness and catalogue passed')
