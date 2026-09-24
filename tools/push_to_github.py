"""Clone the handover bundle and push it to a GitHub repository.

    python tools/push_to_github.py --bundle foci-screen-ready.bundle
    python tools/push_to_github.py --bundle ../foci.bundle --into ~/code/foci --dry-run

A git bundle is a clone source, not a file to commit. Uploaded through GitHub's
web interface it becomes a binary blob and the project never appears, which is
the mistake this exists to prevent.

Run it yourself: the push needs an interactive GitHub sign-in, so it cannot be
done unattended. Git Credential Manager opens a browser the first time and
remembers it afterwards.

Replaces an earlier PowerShell version. Same steps, but it runs on macOS and
Linux too, and it avoids a Windows PowerShell trap that broke the original:
there, anything a native command writes to stderr is treated as a terminating
error, and `git bundle verify` reports *success* on stderr — so the script died
on a perfectly healthy bundle. Here, exit codes are the only signal.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "https://github.com/jh2774/MajBD-AFRL-LegalPressScreenerV1.git"

CYAN, RED, GREEN, YELLOW, RESET = "\033[36m", "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def step(text: str) -> None:
    print(f"\n{CYAN}==> {text}{RESET}", flush=True)


def fail(text: str) -> int:
    print(f"\n{RED}FAILED: {text}{RESET}", file=sys.stderr)
    return 1


def git(*args: str, cwd: Path | None = None, quiet: bool = False) -> int:
    """Run git, echoing its output. Returns the exit code; never raises."""
    proc = subprocess.run(["git", *args], cwd=cwd, text=True,
                          capture_output=True)
    if not quiet:
        for stream in (proc.stdout, proc.stderr):
            for line in (stream or "").splitlines():
                print(f"  {line}")
    return proc.returncode


def git_out(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    return proc.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", required=True, help="path to the .bundle file")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository URL")
    parser.add_argument("--into", default="foci-screen",
                        help="directory to clone into (must not exist)")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except the push")
    args = parser.parse_args(argv)

    if shutil.which("git") is None:
        return fail("git is not on PATH. Install it from https://git-scm.com/downloads")

    bundle = Path(args.bundle).expanduser()
    if not bundle.is_file():
        return fail(f"no bundle at: {bundle}")
    bundle = bundle.resolve()

    into = Path(args.into).expanduser()
    if into.exists():
        return fail(f"{into} already exists. Delete it, or pass --into with another path.")

    step("Checking the bundle")
    if git("bundle", "verify", str(bundle)) != 0:
        return fail("the bundle is damaged. Download it again.")

    step(f"Cloning to {into}")
    if git("clone", "--branch", args.branch, str(bundle), str(into)) != 0:
        return fail("clone failed.")

    # The clone's origin points at the bundle file; repoint it at GitHub.
    git("remote", "set-url", "origin", args.repo, cwd=into, quiet=True)

    step("What will be pushed")
    print(git_out("log", "--oneline", "-5", cwd=into))
    tracked = len(git_out("ls-files", cwd=into).splitlines())
    print(f"\n{tracked} files, head {git_out('rev-parse', '--short', 'HEAD', cwd=into)}")

    step("Checking this is additive, not a rewrite")
    if git("fetch", "origin", args.branch, cwd=into, quiet=True) != 0:
        print(f"{YELLOW}  Could not read the remote branch; it may be empty or "
              f"unreachable. The push will create it.{RESET}")
    else:
        remote_head = git_out("rev-parse", "FETCH_HEAD", cwd=into)
        ahead = git_out("rev-list", "--count", "FETCH_HEAD..HEAD", cwd=into)
        if git("merge-base", "--is-ancestor", remote_head, "HEAD",
               cwd=into, quiet=True) == 0:
            print(f"{GREEN}  Remote tip {remote_head[:7]} is an ancestor: the push "
                  f"fast-forwards, adding {ahead} commit(s).{RESET}")
        else:
            print(f"{YELLOW}  The remote has commits this bundle does not contain.\n"
                  f"  Nothing has been pushed. Merge them first:{RESET}")
            print(f"    cd {into}\n"
                  f"    git fetch origin {args.branch}\n"
                  f"    git merge FETCH_HEAD\n"
                  f"    git push origin {args.branch}")
            return 2

    if args.dry_run:
        step("Stopping before the push (--dry-run)")
        print(f"When ready:  cd {into} && git push origin {args.branch}")
        return 0

    step("Pushing (a browser may open for GitHub sign-in)")
    if git("push", "origin", args.branch, cwd=into) != 0:
        return fail(
            "push rejected. If it says 'non-fast-forward', run:\n"
            f"    cd {into}\n"
            f"    git fetch origin {args.branch} && git merge FETCH_HEAD\n"
            f"    git push origin {args.branch}")

    step("Done")
    print(f"Repository: {args.repo.removesuffix('.git')}")
    print("Watch the Actions tab: this run is the first build of both Docker images.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
