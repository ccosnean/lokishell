"""Shell integration that can restore its environment and remove itself."""
from pathlib import Path
import shlex
import shutil


def session(core):
    path = core.root() / 'settings.json'
    if not path.exists():
        return ''
    value = core.settings().get('shell_session')
    if value:
        return value
    # Existing stores have no session ID. Their directory survives normal edits.
    info = core.root().stat()
    return f'legacy:{info.st_dev}:{info.st_ino}'


ZSH = r'''
  typeset _loki_previous_loki=${functions[loki]-}
  _loki_unload() {
    chpwd_functions=("${(@)chpwd_functions:#_loki_refresh}")
    precmd_functions=("${(@)precmd_functions:#_loki_refresh}")
    if [[ -n $_loki_previous_loki ]]; then
      functions[loki]=$_loki_previous_loki
    else
      unfunction loki
    fi
    unset _loki_zsh_loaded _loki_session _loki_previous_loki
    unfunction _loki_refresh _loki_unload
  }
  typeset -ga chpwd_functions precmd_functions
  chpwd_functions+=(_loki_refresh)
  precmd_functions+=(_loki_refresh)
'''


BASH = r'''
  _loki_previous_loki="$(declare -f loki)" || :
  _loki_previous_cd="$(declare -f cd)" || :
  _loki_prompt_was_set=${PROMPT_COMMAND+x}
  _loki_unload() {
    local item
    if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == 'declare -a'* ]]; then
      local -a remaining=()
      for item in "${PROMPT_COMMAND[@]}"; do
        [[ $item == _loki_prompt ]] || remaining+=("$item")
      done
      PROMPT_COMMAND=("${remaining[@]}")
    else
      PROMPT_COMMAND=";${PROMPT_COMMAND-};"
      PROMPT_COMMAND=${PROMPT_COMMAND/;_loki_prompt;/;}
      PROMPT_COMMAND=${PROMPT_COMMAND#;}
      PROMPT_COMMAND=${PROMPT_COMMAND%;}
    fi
    if [[ -z $_loki_prompt_was_set && -z ${PROMPT_COMMAND[*]-} ]]; then
      unset PROMPT_COMMAND
    fi
    unset -f loki cd
    [[ -z $_loki_previous_loki ]] || eval "$_loki_previous_loki"
    [[ -z $_loki_previous_cd ]] || eval "$_loki_previous_cd"
    unset _loki_bash_loaded _loki_session _loki_previous_loki _loki_previous_cd _loki_prompt_was_set
    unset -f _loki_refresh _loki_prompt _loki_original_cd _loki_unload
  }
  if [[ -n $_loki_previous_cd ]]; then
    eval "_loki_original_cd${_loki_previous_cd#cd}"
  fi
  cd() {
    if declare -F _loki_original_cd >/dev/null; then
      _loki_original_cd "$@" || return
    else
      builtin cd "$@" || return
    fi
    _loki_refresh
  }
  _loki_prompt() {
    local loki_status=$?
    _loki_refresh
    return "$loki_status"
  }
  if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == 'declare -a'* ]]; then
    PROMPT_COMMAND=(_loki_prompt "${PROMPT_COMMAND[@]}")
  else
    PROMPT_COMMAND="_loki_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
  fi
'''


def hook(core, shell):
    if shell not in ('zsh', 'bash'):
        raise ValueError('Supported shells: zsh, bash.')
    executable = core.executable()
    # .zshenv precedes Homebrew's PATH initialization in .zprofile.
    direnv = (shutil.which('direnv')
              or shutil.which('direnv', path=str(Path(executable).parent)) or 'direnv')
    loki, direnv = shlex.quote(executable), shlex.quote(direnv)
    return f'''# LokiShell: prompts, directory changes, and fresh agent shells.
if [ -z "${{_loki_{shell}_loaded-}}" ]; then
  _loki_{shell}_loaded=1
  _loki_session={shlex.quote(session(core))}
  _loki_refresh() {{
    local loki_env
    eval "$({direnv} export {shell})"
    loki_env="$({loki} _effective-env --shell {shell} --session "${{_loki_session-}}")" || return
    eval "$loki_env"
  }}
{ZSH if shell == 'zsh' else BASH}
  loki() {{
    command {loki} "$@"
    local loki_rc=$?
    _loki_refresh
    case "$*" in
      reset|'-- reset'|'reset --'|'-- reset --')
        if [ "$loki_rc" = 0 ] && typeset -f _loki_unload >/dev/null; then
          _loki_unload
        fi ;;
    esac
    return "$loki_rc"
  }}
  _loki_refresh
fi
'''
