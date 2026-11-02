"""Shell completion for ``triage``, and turning it on in a startup file.

The scripts complete the commands people use (not ``completion`` itself),
the repositories you've summarized after ``triage summary``, and the model
names after ``--model``. They get repository names from ``triage list
--names``, so the history database is the one source.
"""

import pathlib
from collections.abc import Mapping

BASH_SCRIPT = r"""# bash completion for triage.
# Load it with: eval "$(triage completion bash)"
_triage() {
  local cur=${COMP_WORDS[COMP_CWORD]} prev=${COMP_WORDS[COMP_CWORD-1]}
  local command="" i
  for (( i = 1; i < COMP_CWORD; i++ )); do
    case ${COMP_WORDS[i]} in
      -*) ;;
      *) command=${COMP_WORDS[i]}; break ;;
    esac
  done
  local IFS=$'\n'
  case $command in
    "")
      COMPREPLY=($(compgen -W $'setup\nsummary\nlist' -- "$cur")) ;;
    summary)
      case $prev in
        --model)
          COMPREPLY=($(compgen -W $'haiku\nsonnet\nopus' -- "$cur")) ;;
        --limit|--budget-usd|--workers) ;;
        *)
          if [[ $cur == -* ]]; then
            COMPREPLY=($(compgen -W $'--limit\n--model\n--budget-usd\n--workers' \
              -- "$cur"))
          else
            COMPREPLY=($(compgen -W "$(triage list --names 2>/dev/null)" \
              -- "$cur"))
          fi ;;
      esac ;;
  esac
}
complete -o default -F _triage triage
"""

ZSH_SCRIPT = r"""#compdef triage
# zsh completion for triage.
# Load it with: eval "$(triage completion zsh)"
(( $+functions[compdef] )) || { autoload -Uz compinit && compinit; }

_triage() {
  local -a commands repos
  local command="" i
  commands=(
    'setup:save your GitHub token, AWS keys and model'
    'summary:fetch a repository'"'"'s issues and summarize them'
    'list:show the repositories you'"'"'ve summarized'
  )
  for (( i = 2; i < CURRENT; i++ )); do
    case ${words[i]} in
      -*) ;;
      *) command=${words[i]}; break ;;
    esac
  done
  case $command in
    "")
      _describe -t commands 'triage command' commands ;;
    summary)
      case ${words[CURRENT-1]} in
        --model) compadd haiku sonnet opus ;;
        --limit|--budget-usd|--workers) ;;
        *)
          if [[ ${words[CURRENT]} == -* ]]; then
            compadd -- --limit --model --budget-usd --workers
          else
            repos=(${(f)"$(triage list --names 2>/dev/null)"})
            compadd -a repos
          fi ;;
      esac ;;
  esac
}
compdef _triage triage
"""

SCRIPTS = {"bash": BASH_SCRIPT, "zsh": ZSH_SCRIPT}

# Written above the eval line so you can tell where it came from.
MARKER = "# triage: Tab completion"


def detect_shell(environ: Mapping[str, str]) -> str | None:
    """Returns the user's shell if completion supports it.

    Args:
        environ: Environment variables; ``SHELL`` is read.

    Returns:
        ``"bash"`` or ``"zsh"``, or None for any other or unset shell.
    """
    shell = pathlib.PurePath(environ.get("SHELL", "")).name
    return shell if shell in SCRIPTS else None


def startup_file(
    shell: str, environ: Mapping[str, str], home: pathlib.Path
) -> pathlib.Path:
    """Returns the startup file a new interactive shell reads.

    Args:
        shell: ``"bash"`` or ``"zsh"``.
        environ: Environment variables; zsh honors ``ZDOTDIR``.
        home: The user's home folder.

    Returns:
        ``~/.zshrc`` (or ``$ZDOTDIR/.zshrc``) for zsh, ``~/.bashrc`` for
        bash.
    """
    if shell == "zsh":
        return pathlib.Path(environ.get("ZDOTDIR") or home) / ".zshrc"
    return home / ".bashrc"


def setup_lines(shell: str) -> str:
    """Returns the lines that turn on completion in a startup file.

    Args:
        shell: ``"bash"`` or ``"zsh"``.

    Returns:
        The marker comment and the eval line, newline-terminated.
    """
    return f'{MARKER}\neval "$(triage completion {shell})"\n'


def is_installed(path: pathlib.Path, shell: str) -> bool:
    """Returns whether a startup file already loads triage completion.

    Args:
        path: The startup file; it may not exist.
        shell: ``"bash"`` or ``"zsh"``.

    Returns:
        True if the file mentions ``triage completion <shell>``.
    """
    if not path.is_file():
        return False
    return f"triage completion {shell}" in path.read_text(encoding="utf-8")


def install(path: pathlib.Path, shell: str) -> None:
    """Appends the completion lines to a startup file, creating it if needed.

    Args:
        path: The startup file.
        shell: ``"bash"`` or ``"zsh"``.
    """
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    separator = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as startup:
        startup.write(
            f"{separator}\n{setup_lines(shell)}"
            if existing
            else setup_lines(shell)
        )
