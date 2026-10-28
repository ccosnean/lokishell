"""Shell startup and complete reset with disposable installed CLI layouts."""
import os
from pathlib import Path
import pty
import shlex
import shutil
import subprocess
import sys
import unittest

from tests.support import LokiFixture, LOKI, ROOT


class ShellTests(LokiFixture):
    initial_tools = ()

    def setUp(self):
        super().setUp()
        # Match Homebrew's layout, including a launcher with an absolute Python.
        prefix = self.base / 'installed loki'
        bindir = prefix / 'bin'
        bindir.mkdir(parents=True)
        libexec = prefix / 'libexec'
        libexec.mkdir()
        for name in ('lib', 'tools'):
            (libexec / name).symlink_to(ROOT / name, target_is_directory=True)
        self.installed_loki = bindir / 'loki'
        self.test_home = self.base / 'shell-home'
        self.test_home.mkdir()
        launcher = LOKI.read_text().split('\n', 1)[1]
        # Never let a reset test inspect or edit the user's real startup files.
        launcher = launcher.replace('    entrypoint()',
                                    '    from unittest.mock import patch\n'
                                    f'    with patch.object(Path, "home", return_value=Path({str(self.test_home)!r})):\n'
                                    '        entrypoint()')
        self.installed_loki.write_text('#!' + sys.executable + '\n' + launcher)
        self.installed_loki.chmod(0o700)
        self.direnv = shutil.which('direnv')
        (bindir / 'direnv').symlink_to(self.direnv)
        self.early_bin = self.base / 'early-bin'
        self.early_bin.mkdir()
        for name in ('bash', 'git', 'mktemp', 'rm'):
            (self.early_bin / name).symlink_to(shutil.which(name))
        self.dot = self.base / 'zdot'
        self.dot.mkdir()

    def test_startup_finds_adjacent_direnv_before_path_initialization(self):
        project = self.projects['personal']
        (project / '.envrc').write_text('export PROJECT_SETTING=fresh-start\n')
        subprocess.run([self.direnv, 'allow', str(project)], env=self.env, check=True,
                       capture_output=True)
        for shell in ('zsh', 'bash'):
            with self.subTest(shell=shell):
                shell_path = shutil.which(shell)
                if not shell_path:
                    continue
                startup = f'eval "$({shlex.quote(str(self.installed_loki))} hook {shell})"\n'
                command = 'printf "%s\\n" "$PROJECT_SETTING" "$LOKI_PROFILE" "$PATH"'
                env = dict(self.env, PATH=str(self.early_bin), ZDOTDIR=str(self.dot))
                if shell == 'zsh':
                    (self.dot / '.zshenv').write_text(startup)
                    args = [shell_path, '-d', '-c', command]
                else:
                    args = [shell_path, '--noprofile', '--norc', '-c', startup + command]
                result = subprocess.run(args, cwd=project, env=env, text=True,
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.splitlines(),
                                 ['fresh-start', 'personal', str(self.early_bin)], result.stderr)
                self.assertNotIn('command not found', result.stderr)

    def shells(self):
        for name in ('zsh', 'bash'):
            if path := shutil.which(name):
                yield name, path, False
                if name == 'bash':
                    yield 'bash-array', path, True
        if shutil.which('bash') != '/bin/bash' and Path('/bin/bash').exists():
            yield 'system-bash', '/bin/bash', False

    def reset_shell(self, label, shell_path, array_prompt=False, answer='y\nRESET LOKISHELL\n', external=False):
        shell = 'zsh' if label == 'zsh' else 'bash'
        store = self.base / (label + '-store')
        env = dict(self.env, LOKI_HOME=str(store), ZDOTDIR=str(self.dot), SHELL=shell_path)
        for args in [('use', 'personal', '--global', '--no-shell'), ('tools', 'install', 'github'), ('setup',)]:
            subprocess.run([str(self.installed_loki), *args], cwd=self.base, env=env,
                           text=True, capture_output=True, check=True)
        project = self.base / (label + '-project')
        project.mkdir()
        (project / '.envrc').write_text('export PROJECT_KEEP=preserved\n')
        subprocess.run([str(self.installed_loki), 'use', 'personal', '--no-shell'],
                       cwd=project, env=env, text=True, capture_output=True, check=True)
        cli = shlex.quote(str(self.installed_loki))
        before = 'user_prompt() { :; }; later_prompt() { :; }; loki() { echo original-loki; };\n'
        if shell == 'zsh':
            before += 'chpwd_functions=(user_prompt); precmd_functions=(user_prompt);\n'
            later = 'precmd_functions+=(later_prompt)'
            preserved = '''[[ "${precmd_functions[*]}" == 'user_prompt later_prompt' ]] || exit 41
[[ "${chpwd_functions[*]}" == user_prompt ]] || exit 42'''
        else:
            before += 'cd() { builtin cd "$@"; };\n'
            before += 'PROMPT_COMMAND=(user_prompt);\n' if array_prompt else 'PROMPT_COMMAND=user_prompt;\n'
            later = 'PROMPT_COMMAND+=(later_prompt)' if array_prompt else 'PROMPT_COMMAND="$PROMPT_COMMAND;later_prompt"'
            preserved = '''[[ "$(declare -f cd)" == *'builtin cd'* ]] || exit 43
[[ "${PROMPT_COMMAND[*]}" == 'user_prompt later_prompt' || "$PROMPT_COMMAND" == 'user_prompt;later_prompt' ]] || exit 44'''
        script = before + f'eval "$({cli} hook {shell})"\n' + later + '\n'
        script += '[[ $LOKI_PROFILE == personal && $GH_TOKEN == loki-login-required ]] || exit 31\n'
        if external:
            # Simulate another terminal resetting and setting up again before our next prompt.
            script += f'command {cli} reset || exit 32\ncommand {cli} setup || exit 33\n_loki_refresh\n'
        else:
            script += 'loki reset\nreset_status=$?\n'
        if answer.startswith('n'):
            script += '''[[ $reset_status != 0 ]] || exit 34
typeset -f _loki_refresh >/dev/null || exit 35
[[ $LOKI_PROFILE == personal && $GH_TOKEN == loki-login-required ]] || exit 36
'''
        else:
            script += '''typeset -f _loki_refresh >/dev/null && exit 37
typeset -f _loki_unload >/dev/null && exit 38
typeset -f _loki_prompt >/dev/null && exit 39
[[ -z ${_loki_session+x}${_loki_zsh_loaded+x}${_loki_bash_loaded+x} ]] || exit 40
[[ -z ${LOKI_PROFILE+x}${LOKI_ENV_STATE+x} && $GH_TOKEN == wrong-inherited-token ]] || exit 45
[[ "$(loki)" == original-loki ]] || exit 46
''' + preserved + '\n'
            script += f'command {cli} --version\n'
        script += 'echo shell-check-passed\n'
        args = [shell_path, '-df', '-c', script] if shell == 'zsh' else [shell_path, '--noprofile', '--norc', '-c', script]
        master, slave = pty.openpty()
        try:
            os.write(master, answer.encode())
            result = subprocess.run(args, cwd=project, env=env, stdin=slave, text=True,
                                    capture_output=True, timeout=20)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('shell-check-passed', result.stdout)
        self.assertEqual(store.exists(), external or answer.startswith('n'))
        if not answer.startswith('n'):
            self.assertEqual((project / '.envrc').read_text(), 'export PROJECT_KEEP=preserved\n')

    def test_reset_unloads_current_shell_and_preserves_user_hooks(self):
        for label, path, array in self.shells():
            with self.subTest(shell=label):
                self.reset_shell(label, path, array)

    def test_cancelled_reset_keeps_shell_active(self):
        for label, path, array in self.shells():
            with self.subTest(shell=label):
                self.reset_shell(label, path, array, answer='n\n')

    def test_reset_in_another_terminal_unloads_even_after_new_setup(self):
        for label, path, array in self.shells():
            with self.subTest(shell=label):
                self.reset_shell(label, path, array, external=True)

    def test_existing_direnv_works_before_during_and_after_reset(self):
        # Exercise real direnv hooks in both installation orders, not mock hooks.
        for label, shell_path, array in self.shells():
            for direnv_first in (True, False):
                with self.subTest(shell=label, direnv_first=direnv_first):
                    self.direnv_lifecycle(label, shell_path, array, direnv_first)

    def direnv_lifecycle(self, label, shell_path, array, direnv_first):
        shell = 'zsh' if label == 'zsh' else 'bash'
        case = self.base / f'{label}-{direnv_first}'
        case.mkdir()
        store = case / 'store'
        parent = case / 'parent'
        project = parent / 'project'
        project.mkdir(parents=True)
        (project / 'bin').mkdir()
        unrelated = case / 'unrelated'
        unrelated.mkdir()
        config = case / 'direnv'
        config.mkdir()
        originals = {
            config / 'direnvrc': 'project_helper() { export HELPER_SETTING=custom; }\n',
            parent / '.envrc': 'export PARENT_SETTING=parent\n',
            project / '.envrc': 'source_up\ndotenv .env\nsource_env extra.envrc\nPATH_add bin\nproject_helper\n',
            project / '.env': 'PROJECT_SETTING=dotenv\n',
            project / 'extra.envrc': 'export SOURCED_SETTING=sourced\n',
            unrelated / '.envrc': 'export UNRELATED_SETTING=untouched\n',
            project / 'keep.txt': 'project data\n',
        }
        for path, content in originals.items():
            path.write_text(content)
        env = dict(self.env, LOKI_HOME=str(store), DIRENV_CONFIG=str(config),
                   ZDOTDIR=str(self.dot), SHELL=shell_path)
        for folder in (parent, project, unrelated):
            subprocess.run([self.direnv, 'allow', str(folder)], env=env,
                           capture_output=True, check=True)
        # Establish that this is an already working, independently approved
        # direnv project before Loki creates or changes anything in it.
        probe = '''import os
assert os.environ['PARENT_SETTING'] == 'parent'
assert os.environ['PROJECT_SETTING'] == 'dotenv'
assert os.environ['SOURCED_SETTING'] == 'sourced'
assert os.environ['HELPER_SETTING'] == 'custom'
assert os.environ['PATH'].split(os.pathsep)[0] == os.path.join(os.getcwd(), 'bin')
assert 'LOKI_PROFILE' not in os.environ
'''
        before = subprocess.run([self.direnv, 'exec', str(project), sys.executable, '-c', probe],
                                cwd=project, env=env, text=True, capture_output=True)
        self.assertEqual(before.returncode, 0, before.stderr)
        for args, cwd in [(('use', 'personal', '--global', '--no-shell'), case),
                          (('tools', 'install', 'github'), case),
                          (('use', 'personal', '--no-shell'), project)]:
            subprocess.run([str(self.installed_loki), *args], cwd=cwd, env=env,
                           capture_output=True, check=True)
        cli = shlex.quote(str(self.installed_loki))
        native = f'eval "$({shlex.quote(self.direnv)} hook {shell})"\n'
        loki = f'eval "$({cli} hook {shell})"\n'
        script = 'PROMPT_COMMAND=()\n' if array else ''
        script += native + loki if direnv_first else loki + native
        # Noninteractive test scripts do not reach an actual prompt. Invoke the
        # installed callbacks in their real order; zsh's native hook needs -i.
        if shell == 'zsh':
            script += 'prompt() { local callback; for callback in "${precmd_functions[@]}"; do "$callback"; done; }\n'
        else:
            script += 'prompt() { local callback; for callback in "${PROMPT_COMMAND[@]}"; do eval "$callback"; done; }\n'
        script += '''native_before="$(typeset -f _direnv_hook)"
check_project() {
  [[ $PARENT_SETTING == parent && $PROJECT_SETTING == dotenv && $SOURCED_SETTING == sourced && $HELPER_SETTING == custom ]] || return 1
}
'''
        script += f'cd {shlex.quote(str(project))}\nprompt\n'
        script += '''check_project || exit 51
[[ $LOKI_PROFILE == personal && $GH_TOKEN == loki-login-required ]] || exit 52
'''
        script += f'[[ $PATH == {shlex.quote(str(project / "bin"))}:* ]] || exit 53\n'
        script += '''loki clean || exit 54
prompt
check_project || exit 55
'''
        script += f'loki use personal --no-shell || exit 56\n'
        script += '''loki reset || exit 57
[[ "$(typeset -f _direnv_hook)" == "$native_before" ]] || exit 58
typeset -f _loki_refresh >/dev/null && exit 59
'''
        # Reset invalidates approval of the edited file. An explicit allow is
        # expected, and must suffice without Loki or its deleted persona store.
        script += f'{shlex.quote(self.direnv)} allow . || exit 60\nprompt\n'
        script += '''check_project || exit 61
[[ -z ${LOKI_PROFILE+x}${LOKI_ENV_STATE+x} && $GH_TOKEN == wrong-inherited-token ]] || exit 62
'''
        script += f'cd {shlex.quote(str(unrelated))}\nprompt\n'
        script += '''[[ $UNRELATED_SETTING == untouched && -z ${PROJECT_SETTING+x}${PARENT_SETTING+x}${SOURCED_SETTING+x}${HELPER_SETTING+x} ]] || exit 63
'''
        script += f'[[ $PATH != *{shlex.quote(str(project / "bin"))}* ]] || exit 64\n'
        script += f'cd {shlex.quote(str(project))}\nprompt\ncheck_project || exit 65\n'
        script += 'echo direnv-lifecycle-passed\n'
        args = [shell_path, '-dfi', '-c', script] if shell == 'zsh' else [shell_path, '--noprofile', '--norc', '-c', script]
        master, slave = pty.openpty()
        try:
            os.write(master, b'y\nRESET LOKISHELL\n')
            result = subprocess.run(args, cwd=case, env=env, stdin=slave,
                                    text=True, capture_output=True, timeout=30)
        finally:
            os.close(master)
            os.close(slave)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('direnv-lifecycle-passed', result.stdout)
        self.assertFalse(store.exists())
        for path, content in originals.items():
            self.assertEqual(path.read_bytes(), content.encode(), str(path))


if __name__ == '__main__':
    unittest.main()
