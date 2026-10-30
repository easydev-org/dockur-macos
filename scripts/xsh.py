#!/usr/bin/env python3
"""One place to run external commands, so quoting is not something to get right each time.

WHY THIS EXISTS. The macOS pipeline's failures have mostly not been logic. They have been
a shell eating something on the way to qemu:

  docker exec cap sh -c "printf 'screendump /tmp/...' | socat - UNIX-CONNECT:..."
      the outer layer expanded the path and the inner quoting collapsed; socat was not even
      in the image, so it failed twice over before being replaced with a file-based call

  heredoc through the tool layer
      "unterminated single quote", "unexpected LBrace", "EOF: executable file not found" -
      three separate times

  patch text containing a Python snippet
      one line with a semicolon and a quote aborted the whole patch

Each cost a twenty-minute CI run to find and was a syntax accident, not a decision. The cure
is not to be more careful with quotes; it is to stop asking a shell to carry data. Every
call here takes an argv list and runs with shell=False, so no metacharacter in any argument
is ever interpreted.

For the cases that genuinely need a shell inside a container - a pipeline, a compound
command - the script is written to a FILE and executed there, never inlined into another
quoted string. A file has no layer above it to escape through.

Modelled on easysandbox-windows/scripts/xsh.py, which reached the same conclusion from six
quoting faults of its own.
"""

import os
import shlex
import subprocess
import sys
import tempfile


class Result:
    """The outcome of one command, with everything a caller might want to inspect."""

    def __init__(self, argv, code, out, err):
        self.argv = argv
        self.code = code
        self.out = out
        self.err = err

    @property
    def ok(self):
        return self.code == 0

    @property
    def text(self):
        return (self.out or "") + (self.err or "")

    def __str__(self):
        return "%s -> %d\n  stdout: %s\n  stderr: %s" % (
            " ".join(shlex.quote(a) for a in self.argv),
            self.code,
            (self.out or "").strip()[:400],
            (self.err or "").strip()[:400],
        )


class Fail(Exception):
    """A command that mattered failed. Carries the Result so callers can inspect it."""

    def __init__(self, result):
        self.result = result
        super().__init__(str(result))


def run(argv, check=False, echo=True, input_text=None, timeout=None):
    """Run argv (a list) with no shell.

    Raises TypeError for a string, deliberately: the point is that arguments are not
    re-parsed, and accepting a string would quietly reintroduce the bug class this module
    exists to remove.
    """
    if isinstance(argv, (str, bytes)):
        raise TypeError("xsh.run takes a list of arguments, not a string: %r" % (argv,))
    argv = [str(a) for a in argv]
    if echo:
        print("$ " + " ".join(shlex.quote(a) for a in argv), flush=True)
    try:
        p = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        r = Result(argv, p.returncode, p.stdout, p.stderr)
    except FileNotFoundError as exc:
        r = Result(argv, 127, "", str(exc))
    except subprocess.TimeoutExpired:
        r = Result(argv, 124, "", "timed out after %ss" % timeout)

    if echo and (r.out or r.err):
        for line in (r.out or "").rstrip().splitlines()[-6:]:
            print("  " + line, flush=True)
        for line in (r.err or "").rstrip().splitlines()[-3:]:
            print("  ! " + line, flush=True)
    if check and not r.ok:
        raise Fail(r)
    return r


def need(condition, message):
    """Assert something the rest of the pipeline depends on."""
    if not condition:
        raise Fail(Result([message], 1, "", message))


def docker_script(container, script, check=False, echo=True, interpreter="sh"):
    """Run a shell script INSIDE a container, from a file, with no extra quoting layer.

    Passing a multi-line string through `docker exec ... sh -c "<script>"` is the nesting
    that cost runs here: the host expands variables meant for the container, and every
    backslash must survive two levels of quoting. A file removes both, and any error names
    a line in a file rather than an offset into an already-mangled string.
    """
    fd, path = tempfile.mkstemp(suffix=".sh", prefix="xsh-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(script)
        remote = "/tmp/" + os.path.basename(path)
        run(["docker", "cp", path, "%s:%s" % (container, remote)], check=True, echo=echo)
        return run(["docker", "exec", container, interpreter, remote],
                   check=check, echo=echo)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def main():
    """Command-line form, for a workflow step that wants exactly one command.

    xsh.py -- docker exec cap ls /frames
    """
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        sys.exit(__doc__)
    r = run(sys.argv[2:], echo=True)
    sys.exit(r.code)


if __name__ == "__main__":
    main()
