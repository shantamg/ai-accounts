#!/usr/bin/python3
"""Use one local Claude/Codex account pair per workspace."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Optional


BINDING = ".ai-account.local"
PROVIDERS = ("claude", "codex")
HOME_VARIABLE = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}
AUTH_OVERRIDES = {
    "claude": (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
        "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID",
        "ANTHROPIC_ORGANIZATION_ID", "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
    ),
    "codex": (
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
    ),
}
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")


class AccountError(Exception):
    """A local account could not be configured or selected."""


def config_dir() -> Path:
    return Path.home() / ".config" / "ai-accounts"


def _key(label: str) -> str:
    if not LABEL.fullmatch(label):
        raise AccountError("Use a company label of up to 40 letters, digits, underscores or hyphens.")
    return label.lower()


def _read_private(path: Path) -> Optional[str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AccountError(f"Could not open the local account file {path}.") from exc
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise AccountError(f"{path} must be a private, owner-only account file (chmod 600).")
        data = handle.read(4097)
        if len(data) > 4096:
            raise AccountError(f"Local account file {path} is too large.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AccountError(f"Invalid text in local account file {path}.") from exc


def _write_private(path: Path, content: str, replace: bool) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content.encode("utf-8"))
            handle.flush()
            if replace:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError as exc:
            raise AccountError(f"{path} already exists. Use --replace to change it.") from exc
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Profile:
    label: str
    claude: Optional[Path]
    codex: Optional[Path]

    def home(self, provider: str) -> Path:
        configured = getattr(self, provider)
        return configured or Path.home() / f".{provider}"

    def environment_changes(self, provider: str) -> dict[str, Optional[str]]:
        changes = dict.fromkeys((*AUTH_OVERRIDES[provider], HOME_VARIABLE[provider]))
        configured = getattr(self, provider)
        if configured is not None:
            changes[HOME_VARIABLE[provider]] = str(configured)
        return changes

    def environment(self, provider: str, base: Mapping[str, str]) -> dict[str, str]:
        result = dict(base)
        for key, value in self.environment_changes(provider).items():
            if value is None:
                result.pop(key, None)
            else:
                result[key] = value
        return result

    def command(self, provider: str, command: list[str]) -> list[str]:
        if provider == "codex":
            return [command[0], *self.codex_options(), *command[1:]]
        return command

    def codex_options(self) -> list[str]:
        if self.codex is None:
            return []
        return ["-c", 'cli_auth_credentials_store="file"']

    def tmux_command(self, provider: str, command: list[str]) -> list[str]:
        # tmux inherits its server's environment, even when the client was
        # started with a different environment. Apply changes inside the pane.
        changes = self.environment_changes(provider)
        unset = [arg for key, value in changes.items() if value is None for arg in ("-u", key)]
        set_values = [f"{key}={value}" for key, value in changes.items() if value is not None]
        return ["/usr/bin/env", *unset, *set_values, *self.command(provider, command)]

    def session_options(self, provider: str) -> dict[str, Path]:
        if provider == "claude":
            return {"projects_root": self.home(provider) / "projects"}
        return {"sessions_root": self.home(provider) / "sessions"}


def _profile_path(label: str) -> Path:
    return config_dir() / "profiles" / f"{_key(label)}.json"


def _configured_home(value: Optional[str], provider: str) -> Optional[Path]:
    if value is None or value == "default":
        return None
    if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
        raise AccountError(f"The {provider} directory must be absolute, or use 'default'.")
    path = Path(value).expanduser().resolve()
    if path == (Path.home() / f".{provider}").resolve():
        return None
    return path


def _validate_home(home: Path, provider: str) -> None:
    info = home.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise AccountError(f"The {provider} directory must be owned by you and writable only by you.")


def _stored_home(value: Optional[str], provider: str) -> Optional[Path]:
    if value is None:
        return None
    home = Path(value)
    if not home.is_absolute() or home.resolve() != home:
        raise AccountError(f"The {provider} directory has moved. Update the account profile.")
    return home


def load_profile(label: str) -> Profile:
    data = _read_private(_profile_path(label))
    if data is None:
        raise AccountError(f"Account profile {label} is missing. Set it up with ai-accounts profile.")
    try:
        fields = json.loads(data)
        profile = Profile(
            fields["label"],
            _stored_home(fields["claude"], "claude"),
            _stored_home(fields["codex"], "codex"),
        )
        if _key(profile.label) != _key(label):
            raise ValueError("Label does not match profile filename")
    except (KeyError, TypeError, ValueError) as exc:
        raise AccountError(f"Invalid account profile {label}.") from exc
    for provider in PROVIDERS:
        home = getattr(profile, provider)
        if home is not None:
            try:
                _validate_home(home, provider)
            except OSError as exc:
                raise AccountError(f"The {provider} directory for {label} is unavailable.") from exc
    return profile


def _git(directory: Path, argument: str, *values: str) -> Optional[Path]:
    result = subprocess.run(
        ["/usr/bin/git", "-C", str(directory), "rev-parse", "--path-format=absolute", argument, *values],
        capture_output=True, text=True, check=False,
    )
    return Path(result.stdout.rstrip("\n")) if result.returncode == 0 else None


def _directories(directory: Path) -> Iterator[Path]:
    """Search up to the nearest repository root or personal home."""
    for candidate in (directory, *directory.parents):
        if candidate != directory and candidate in {Path.home(), Path("/")}:
            return
        yield candidate
        git_marker = candidate / ".git"
        if git_marker.is_file():
            # A linked worktree inherits a binding from its primary checkout.
            # A submodule's common directory has a different shape.
            common_dir = _git(candidate, "--git-common-dir")
            if common_dir and common_dir.name == ".git" and common_dir.parent != candidate:
                yield common_dir.parent
            return
        if git_marker.is_dir():
            return


def project_profile(directory: str | Path) -> Optional[Profile]:
    for candidate in _directories(Path(directory).resolve()):
        label = _read_private(candidate / BINDING)
        if label is not None:
            return load_profile(label.strip())
    return None


def _save_binding(directory: Path, profile: Profile, replace: bool) -> None:
    # Keep this machine's assignment out of version control in any Git project.
    exclude = _git(directory, "--git-path", "info/exclude")
    if exclude is not None:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a+", encoding="utf-8") as handle:
            handle.seek(0)
            if BINDING not in handle.read().splitlines():
                handle.write(f"\n{BINDING}\n")
    _write_private(directory / BINDING, f"{profile.label}\n", replace)


def _profiles() -> Iterator[Profile]:
    for path in sorted((config_dir() / "profiles").glob("*.json")):
        yield load_profile(path.stem)


def _binary(provider: str) -> str:
    binary = shutil.which(provider)
    if binary is None:
        raise AccountError(f"{provider} is not installed or is missing from PATH.")
    return binary


def _signed_in(profile: Profile, provider: str) -> bool:
    binary = shutil.which(provider)
    if binary is None:
        return False
    args = ["auth", "status"] if provider == "claude" else ["login", "status"]
    try:
        result = subprocess.run(
            profile.command(provider, [binary, *args]),
            cwd=Path.home(), env=profile.environment(provider, os.environ),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _create_profile(company: str, dirs: Mapping[str, Optional[str]], replace: bool = False) -> Profile:
    path = _profile_path(company)
    homes = {}
    for provider in PROVIDERS:
        value = dirs.get(provider)
        if value is None:
            value = str(config_dir() / "accounts" / _key(company) / provider)
            Path(value).mkdir(parents=True, exist_ok=True, mode=0o700)
        home = _configured_home(value, provider)
        if home is not None:
            _validate_home(home, provider)
        homes[provider] = str(home) if home is not None else None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_private(path, json.dumps({"label": company, **homes}) + "\n", replace)
    return load_profile(company)


def _login(profile: Profile, provider: str) -> bool:
    """Run the provider's sign-in for a profile and report whether it worked."""
    binary = _binary(provider)
    args = ["auth", "login"] if provider == "claude" else ["login"]
    print(f"Signing in to {provider} for {profile.label}...", file=sys.stderr, flush=True)
    # Run account setup outside the workspace and its provider config.
    subprocess.run(
        profile.command(provider, [binary, *args]),
        cwd=Path.home(), env=profile.environment(provider, os.environ), check=False,
    )
    if _signed_in(profile, provider):
        print(f"Signed in to {provider} for {profile.label}.", file=sys.stderr)
        return True
    print(f"{provider} sign-in for {profile.label} did not complete.", file=sys.stderr)
    return False


def _ask(prompt: str) -> str:
    print(prompt, file=sys.stderr, end="", flush=True)
    return input().strip()


def _offer_other_providers(profile: Profile, provider: str) -> None:
    for other in PROVIDERS:
        if other != provider and shutil.which(other) and not _signed_in(profile, other):
            if _ask(f"Also sign in to {other} for {profile.label} now? [y/N] ").lower() in {"y", "yes"}:
                _login(profile, other)


def _add_profile_interactively(provider: str) -> Profile:
    existing = {_key(profile.label) for profile in _profiles()}
    while True:
        company = _ask("New account name (e.g. Work; Enter to cancel): ")
        if not company:
            raise AccountError("No account selected.")
        if not LABEL.fullmatch(company):
            print("Use up to 40 letters, digits, underscores or hyphens.", file=sys.stderr)
        elif company.lower() in existing:
            print(f"{company} already exists; pick it from the list instead.", file=sys.stderr)
        else:
            break
    profile = _create_profile(company, {})
    print(f"Created account {profile.label}.", file=sys.stderr)
    return profile


def _choose_profile(directory: Path, provider: str, local_model: bool) -> Profile:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise AccountError(f"No account bound in {directory}. Run ai-accounts bind COMPANY there.")
    profiles = list(_profiles())
    ready = {profile.label: local_model or _signed_in(profile, provider) for profile in profiles}
    print(f"Choose an account for {directory}", file=sys.stderr)
    for number, profile in enumerate(profiles, 1):
        note = "" if ready[profile.label] else f"  (not signed in to {provider}; choose to sign in)"
        print(f"  {number}. {profile.label}{note}", file=sys.stderr)
    add_number = len(profiles) + 1
    print(f"  {add_number}. + Add a new account", file=sys.stderr)
    selection = _ask("Account (number or name, Enter to cancel): ").lower()
    chosen = None
    for number, profile in enumerate(profiles, 1):
        if selection in {str(number), profile.label.lower()}:
            chosen = profile
    if chosen is None and selection in {str(add_number), "+", "add", "new"}:
        chosen = _add_profile_interactively(provider)
        ready[chosen.label] = local_model
    if chosen is None:
        raise AccountError("No account selected.")
    if not ready[chosen.label]:
        if not _login(chosen, provider):
            raise AccountError(f"Not signed in. Retry with: ai-accounts login {chosen.label} {provider}")
        _offer_other_providers(chosen, provider)
    return chosen


def _launch_directory(provider: str, args: list[str]) -> Path:
    directory = Path.cwd()
    if provider != "codex":
        return directory
    # Handle Codex's working directory flag on both interactive and exec paths.
    flags = iter(args)
    for arg in flags:
        if arg == "--":
            break
        if arg in {"-C", "--cd"}:
            value = next(flags, "")
        elif arg.startswith(("-C", "--cd=")):
            value = arg[2:].lstrip("=") if arg.startswith("-C") else arg[5:]
        else:
            continue
        if not value:
            raise AccountError("Codex's working directory flag requires a directory.")
        directory = Path(value).resolve()
    if not directory.is_dir():
        raise AccountError(f"Working directory is unavailable: {directory}")
    return directory


def _exec(profile: Profile, provider: str, args: list[str]) -> None:
    binary = _binary(provider)
    command = profile.command(provider, [binary, *args])
    os.execve(binary, command, profile.environment(provider, os.environ))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile_parser = commands.add_parser("profile", help="Set up the Claude and Codex directories for a company.")
    profile_parser.add_argument("company")
    for provider in PROVIDERS:
        profile_parser.add_argument(f"--{provider}-dir", help="Absolute directory or 'default' for the existing CLI login.")
    profile_parser.add_argument("--replace", action="store_true")
    add = commands.add_parser("add", help="Create a company profile and sign in to Claude and Codex.")
    add.add_argument("company")
    binding = commands.add_parser("bind", help="Save a company binding in this directory.")
    binding.add_argument("company")
    binding.add_argument("--replace", action="store_true")
    commands.add_parser("show", help="Show the effective company binding here.")
    commands.add_parser("status", help="Check sign-in status for all configured profiles.")
    login = commands.add_parser("login", help="Sign in to one provider in a company profile.")
    login.add_argument("company")
    login.add_argument("provider", choices=PROVIDERS)
    run = commands.add_parser("run", help="Launch a provider using the current workspace binding.")
    run.add_argument("provider", choices=PROVIDERS)
    run.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    try:
        if args.command == "profile":
            _create_profile(
                args.company,
                {provider: getattr(args, f"{provider}_dir") for provider in PROVIDERS},
                args.replace,
            )
            print(f"Configured {args.company}.")
        elif args.command == "add":
            profile = _create_profile(args.company, {})
            print(f"Created account {profile.label}.", file=sys.stderr)
            for provider in PROVIDERS:
                if shutil.which(provider):
                    _login(profile, provider)
        elif args.command == "bind":
            profile = load_profile(args.company)
            _save_binding(Path.cwd(), profile, args.replace)
            print(f"Bound {Path.cwd()} to {profile.label}.")
        elif args.command == "show":
            profile = project_profile(Path.cwd())
            print(profile.label if profile else "No account bound here.")
        elif args.command == "status":
            for profile in _profiles():
                statuses = [
                    f"{provider}: {'signed in' if _signed_in(profile, provider) else 'unavailable'}"
                    for provider in PROVIDERS
                ]
                print(f"{profile.label}  {' | '.join(statuses)}")
        elif args.command == "login":
            login_args = ["auth", "login"] if args.provider == "claude" else ["login"]
            # Run account setup outside the workspace and its provider config.
            os.chdir(Path.home())
            _exec(load_profile(args.company), args.provider, login_args)
        else:
            provider_args = args.args[1:] if args.args[:1] == ["--"] else args.args
            informational_args = provider_args
            if args.provider == "claude" and provider_args[:1] == ["--dangerously-skip-permissions"]:
                informational_args = provider_args[1:]
            if informational_args in (["--version"], ["-V"], ["--help"], ["-h"]):
                binary = _binary(args.provider)
                os.execv(binary, [binary, *provider_args])
            directory = _launch_directory(args.provider, provider_args)
            profile = project_profile(directory)
            if profile is None:
                profile = _choose_profile(directory, args.provider, "--oss" in provider_args)
                root = _git(directory, "--show-toplevel") or directory
                _save_binding(root, profile, replace=False)
            print(f"Using {profile.label} for {args.provider}.", file=sys.stderr, flush=True)
            _exec(profile, args.provider, provider_args)
    except (AccountError, OSError, EOFError) as exc:
        print(f"ai-accounts: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
