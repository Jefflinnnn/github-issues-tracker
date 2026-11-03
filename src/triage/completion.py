"""Shell completion for ``triage``, and turning it on in a startup file.

The scripts complete the commands people use (not ``completion`` itself),
the repositories you've summarized after ``triage summary`` and ``triage
browse``, the flags after ``--``, and the model names after ``--model``.
They get repository names from ``triage list --names``, so the history
database is the one source. There is one script each for bash and zsh on
macOS and Linux, and for PowerShell on Windows.
"""

import pathlib
import shutil
import subprocess
import sys
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
      COMPREPLY=($(compgen -W $'setup\nsummary\nbrowse\nlist' -- "$cur")) ;;
    browse)
      COMPREPLY=($(compgen -W "$(triage list --names 2>/dev/null)" -- "$cur"))
      ;;
    summary)
      case $prev in
        --model)
          COMPREPLY=($(compgen -W $'haiku\nsonnet\nopus' -- "$cur")) ;;
        --limit|--budget-usd|--workers) ;;
        *)
          if [[ $cur == -* ]]; then
            local flags=$'--limit\n--model\n--budget-usd\n--workers'
            COMPREPLY=($(compgen -W "$flags" -- "$cur"))
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
    'browse:look through the last run'"'"'s issues'
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
    browse)
      repos=(${(f)"$(triage list --names 2>/dev/null)"})
      compadd -a repos ;;
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

POWERSHELL_SCRIPT = r"""# PowerShell completion for triage.
# Load it with: triage completion powershell | Out-String | Invoke-Expression
Register-ArgumentCompleter -Native -CommandName triage -ScriptBlock {
  param($wordToComplete, $commandAst, $cursorPosition)
  # The words typed so far, without triage itself or the word being typed.
  $before = @($commandAst.CommandElements | Select-Object -Skip 1 |
    ForEach-Object { $_.ToString() })
  if ($wordToComplete -and $before.Count -gt 0) {
    $before = @($before | Select-Object -First ($before.Count - 1))
  }
  $command = @($before | Where-Object { $_ -notlike '-*' })[0]
  $previous = if ($before.Count -gt 0) { $before[-1] } else { '' }
  if (-not $command) {
    $choices = 'setup', 'summary', 'browse', 'list'
  } elseif ($command -eq 'browse') {
    $choices = @(triage list --names 2>$null)
  } elseif ($command -ne 'summary') {
    $choices = @()
  } elseif ($previous -eq '--model') {
    $choices = 'haiku', 'sonnet', 'opus'
  } elseif ($previous -in '--limit', '--budget-usd', '--workers') {
    $choices = @()
  } elseif ($wordToComplete -like '-*') {
    $choices = '--limit', '--model', '--budget-usd', '--workers'
  } else {
    $choices = @(triage list --names 2>$null)
  }
  $choices | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
    [System.Management.Automation.CompletionResult]::new(
      $_, $_, 'ParameterValue', $_)
  }
}
"""

SCRIPTS = {
    "bash": BASH_SCRIPT,
    "zsh": ZSH_SCRIPT,
    "powershell": POWERSHELL_SCRIPT,
}

# Written above the eval line so you can tell where it came from.
MARKER = "# triage: Tab completion"


def detect_shell(
    environ: Mapping[str, str], platform: str = sys.platform
) -> str | None:
    """Returns the user's shell if completion supports it.

    Args:
        environ: Environment variables; ``SHELL`` is read.
        platform: ``sys.platform``; on Windows, without ``SHELL`` set (as
            Git Bash sets it), the shell is PowerShell.

    Returns:
        ``"bash"``, ``"zsh"`` or ``"powershell"``, or None for any other
        or unset shell.
    """
    shell = pathlib.PurePath(environ.get("SHELL", "")).name
    if shell.removesuffix(".exe") in ("bash", "zsh"):
        return shell.removesuffix(".exe")
    if not shell and platform == "win32":
        return "powershell"
    return None


def startup_file(
    shell: str, environ: Mapping[str, str], home: pathlib.Path
) -> pathlib.Path:
    """Returns the startup file a new interactive shell reads.

    Args:
        shell: ``"bash"``, ``"zsh"`` or ``"powershell"``.
        environ: Environment variables; zsh honors ``ZDOTDIR``.
        home: The user's home folder.

    Returns:
        ``~/.zshrc`` (or ``$ZDOTDIR/.zshrc``) for zsh, ``~/.bashrc`` for
        bash, and PowerShell's ``$PROFILE`` for PowerShell.
    """
    if shell == "zsh":
        return pathlib.Path(environ.get("ZDOTDIR") or home) / ".zshrc"
    if shell == "powershell":
        return powershell_profile(home)
    return home / ".bashrc"


def powershell_profile(home: pathlib.Path) -> pathlib.Path:
    """Returns the profile PowerShell runs at start, ``$PROFILE``.

    PowerShell knows where it is, Documents moved to OneDrive included, so
    it is asked: PowerShell 7 (``pwsh``) first, then Windows PowerShell.

    Args:
        home: The user's home folder, for when neither answers.

    Returns:
        The profile's path; it may not exist yet.
    """
    for program in ("pwsh", "powershell"):
        if shutil.which(program) is None:
            continue
        try:
            found = subprocess.run(
                [
                    program,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$PROFILE",
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if found:
            return pathlib.Path(found)
    return (
        home / "Documents" / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
    )


def setup_lines(shell: str) -> str:
    """Returns the lines that turn on completion in a startup file.

    Args:
        shell: ``"bash"``, ``"zsh"`` or ``"powershell"``.

    Returns:
        The marker comment and the line that loads the script,
        newline-terminated.
    """
    if shell == "powershell":
        load = "triage completion powershell | Out-String | Invoke-Expression"
        return f"{MARKER}\n{load}\n"
    return f'{MARKER}\neval "$(triage completion {shell})"\n'


def is_installed(path: pathlib.Path, shell: str) -> bool:
    """Returns whether a startup file already loads triage completion.

    Args:
        path: The startup file; it may not exist.
        shell: ``"bash"``, ``"zsh"`` or ``"powershell"``.

    Returns:
        True if the file mentions ``triage completion <shell>``.
    """
    if not path.is_file():
        return False
    return f"triage completion {shell}" in path.read_text(encoding="utf-8")


def install(path: pathlib.Path, shell: str) -> None:
    """Appends the completion lines to a startup file, creating it if needed.

    Args:
        path: The startup file; its folder is made if needed, as
            PowerShell's may not exist yet.
        shell: ``"bash"``, ``"zsh"`` or ``"powershell"``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    separator = "" if not existing or existing.endswith("\n") else "\n"
    # Unix line endings even on Windows: Git Bash's bash reads a carriage
    # return as part of the command, and PowerShell takes either.
    with path.open("a", encoding="utf-8", newline="\n") as startup:
        startup.write(
            f"{separator}\n{setup_lines(shell)}"
            if existing
            else setup_lines(shell)
        )
