"""Curl installation boundaries, with local archives and private test environments."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import patch
import venv

import click
from tests.support import module

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'install.sh'
program = SCRIPT.read_text().split("<<'LOKI_INSTALL_PYTHON'\n", 1)[1].split('\nLOKI_INSTALL_PYTHON\n', 1)[0]
installer = types.ModuleType('loki_installer')
exec(compile(program, str(SCRIPT), 'exec'), installer.__dict__)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='loki-installer-')
        self.base = Path(self.temporary.name).resolve()
        self.prefix = self.base / 'prefix with spaces'
        self.version = module.VERSION
        self.archive = self.base / 'release.tar.gz'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name in ('bin', 'lib', 'tools', 'requirements.txt'):
                archive.add(ROOT / name, arcname=f'lokishell-{self.version}/{name}',
                            filter=lambda item: None if '__pycache__' in item.name or item.name.endswith('.pyc') else item)
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.info = (self.version, 'https://example.invalid/release.tar.gz', self.digest)
        self.url = f'https://github.com/ccosnean/lokishell/releases/download/v{self.version}/lokishell-{self.version}.tar.gz'
        self.release = {'tag_name': 'v' + self.version, 'assets': [{
            'name': f'lokishell-{self.version}.tar.gz', 'digest': 'sha256:' + self.digest,
            'browser_download_url': self.url}]}

    def tearDown(self):
        self.temporary.cleanup()

    def dependencies(self, build):
        # Use a real private venv and the test runner's Click, without PyPI or
        # installing anything into the contributor's global Python environment.
        venv.EnvBuilder(symlinks=True).create(build / 'venv')
        python = build / 'venv/bin/python'
        site = Path(subprocess.check_output([str(python), '-I', '-c',
                                             'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
        shutil.copytree(Path(click.__file__).parent, site / 'click')

    def install(self, **kwargs):
        with patch.object(installer, 'release_info', return_value=self.info), \
             patch.object(installer, 'download', side_effect=lambda url, target: shutil.copyfile(self.archive, target)), \
             patch.object(installer, 'prepare_environment', side_effect=self.dependencies), \
             redirect_stdout(io.StringIO()):
            return installer.install(self.prefix, **kwargs)

    def test_real_entrypoint_private_dependency_and_managed_command_in_spaced_prefix(self):
        entry = self.install()
        self.assertTrue(entry.is_symlink())
        self.assertEqual(subprocess.check_output([str(entry), '--version'], text=True).strip(), 'LokiShell ' + self.version)
        build = (self.prefix / 'share/lokishell/current').resolve()
        # The native launcher and direct Python execution must both work.
        self.assertEqual(subprocess.check_output([str(build / 'venv/bin/python'), str(entry), '--version'], text=True).strip(), 'LokiShell ' + self.version)
        fake = self.base / 'native-bin'
        fake.mkdir()
        (fake / 'firebase').write_text('#!/bin/sh\nprintf "%s\\n" "$XDG_CONFIG_HOME"\n')
        (fake / 'firebase').chmod(0o755)
        store = self.base / 'personas'
        env = {key: value for key, value in os.environ.items() if not key.startswith('LOKI_')}
        env.update(LOKI_HOME=str(store), XDG_CONFIG_HOME=str(self.base / 'independent-config'),
                   DIRENV_CONFIG=str(self.base / 'direnv'), PATH=str(fake) + os.pathsep + env['PATH'])
        for args in [('create', 'test'), ('--profile', 'test', 'tools', 'install', 'firebase')]:
            subprocess.run([str(entry), *args], env=env, check=True, capture_output=True)
        result = subprocess.run(['/bin/sh', '-c', 'eval "$("$1" _env test)"; printf "%s\\n" "$XDG_CONFIG_HOME"; firebase', 'sh', str(entry)],
                                env=env, check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), [str(self.base / 'independent-config'), str(store / 'profiles/test/xdg')])
        self.assertFalse((self.prefix / '.zshrc').exists())

    def test_rerun_is_idempotent_and_upgrade_keeps_previous_runtime_and_personas(self):
        entry = self.install()
        root = self.prefix / 'share/lokishell'
        previous = (root / 'current').resolve()
        with patch.object(installer, 'release_info', return_value=self.info), \
             patch.object(installer, 'download', side_effect=AssertionError('healthy installation was downloaded again')), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(installer.install(self.prefix), entry)
        self.assertEqual((root / 'current').resolve(), previous)
        # A replaced archive digest also stages a fresh runtime before activation.
        (previous / '.release.json').write_text(json.dumps({'version': self.version, 'sha256': 'old-digest'}))
        unrelated = self.prefix / 'share/unrelated/account'
        unrelated.parent.mkdir()
        unrelated.write_text('fictional-independent-session')
        self.install()
        self.assertNotEqual((root / 'current').resolve(), previous)
        self.assertTrue((previous / 'venv/bin/python').exists())
        self.assertEqual(unrelated.read_text(), 'fictional-independent-session')

    def test_bad_checksum_and_dependency_failure_leave_working_installation_intact(self):
        entry = self.install()
        root = self.prefix / 'share/lokishell'
        previous = (root / 'current').resolve()
        (previous / '.release.json').write_text('{}')
        with patch.object(installer, 'release_info', return_value=self.info), \
             patch.object(installer, 'download', side_effect=lambda url, target: target.write_bytes(b'bad archive')), \
             redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                installer.install(self.prefix)
        with patch.object(installer, 'release_info', return_value=self.info), \
             patch.object(installer, 'download', side_effect=lambda url, target: shutil.copyfile(self.archive, target)), \
             patch.object(installer, 'prepare_environment', side_effect=ValueError('dependency failure')), \
             redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'dependency failure'):
                installer.install(self.prefix)
        self.assertEqual((root / 'current').resolve(), previous)
        self.assertEqual(list((root / 'releases').iterdir()), [previous])
        self.assertIn(self.version, subprocess.check_output([str(entry), '--version'], text=True))

    def test_conflicting_executable_and_unmanaged_installation_directory_are_preserved(self):
        entry = self.prefix / 'bin/loki'
        entry.parent.mkdir(parents=True)
        entry.write_text('unrelated program')
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.install()
        self.assertEqual(entry.read_text(), 'unrelated program')
        entry.unlink()
        root = self.prefix / 'share/lokishell'
        root.mkdir(parents=True)
        (root / 'keep').write_text('unrelated data')
        with self.assertRaisesRegex(ValueError, 'not managed'):
            self.install()
        self.assertEqual((root / 'keep').read_text(), 'unrelated data')

    def test_activation_failure_rolls_back_and_rerun_repairs_a_broken_runtime(self):
        entry = self.install()
        root = self.prefix / 'share/lokishell'
        previous = (root / 'current').resolve()
        (previous / 'bin/loki').write_text('#!/bin/sh\nexit 1\n')
        replace = Path.replace
        def fail_entry_publish(path, destination):
            if destination == entry:
                raise OSError('entry publication failed')
            return replace(path, destination)
        with patch.object(Path, 'replace', fail_entry_publish):
            with self.assertRaisesRegex(OSError, 'entry publication failed'):
                self.install()
        self.assertEqual((root / 'current').resolve(), previous)
        self.assertEqual(list((root / 'releases').iterdir()), [previous])
        self.install()
        self.assertNotEqual((root / 'current').resolve(), previous)
        self.assertIn(self.version, subprocess.check_output([str(entry), '--version'], text=True))

    def test_unsafe_archive_paths_and_links_are_rejected_before_extraction(self):
        for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                           (f'lokishell-{self.version}/../../escape', tarfile.REGTYPE),
                           (f'lokishell-{self.version}/link', tarfile.SYMTYPE),
                           (f'lokishell-{self.version}/link', tarfile.LNKTYPE)]:
            with self.subTest(name=name, kind=kind):
                archive = self.base / 'unsafe.tar.gz'
                with tarfile.open(archive, 'w:gz') as output:
                    item = tarfile.TarInfo(name)
                    item.type = kind
                    item.linkname = '/outside'
                    output.addfile(item)
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    installer.unpack(archive, self.base / 'extracted', self.version)
                self.assertFalse((self.base / 'extracted').exists())

    def test_release_selection_requires_digest_and_expected_repository_asset(self):
        with patch.object(installer.subprocess, 'check_output', return_value=json.dumps(self.release).encode()) as request:
            self.assertEqual(installer.release_info(None), (self.version, self.url, self.digest))
            self.assertTrue(request.call_args.args[0][-1].endswith('/latest'))
            self.assertEqual(installer.release_info('v' + self.version), (self.version, self.url, self.digest))
            self.assertTrue(request.call_args.args[0][-1].endswith('/tags/v' + self.version))
        self.release['assets'][0]['digest'] = None
        with patch.object(installer.subprocess, 'check_output', return_value=json.dumps(self.release).encode()):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                installer.release_info(None)
        with self.assertRaisesRegex(ValueError, 'stable version'):
            installer.release_info('../main')

    def test_missing_prerequisite_fails_without_creating_installation(self):
        with patch.object(sys, 'argv', ['install.sh', '--prefix', str(self.prefix)]), \
             patch.object(installer.shutil, 'which', side_effect=lambda command: '/usr/bin/curl' if command == 'curl' else None):
            with self.assertRaisesRegex(ValueError, 'direnv is required'):
                installer.main()
        self.assertFalse(self.prefix.exists())


if __name__ == '__main__':
    unittest.main()
