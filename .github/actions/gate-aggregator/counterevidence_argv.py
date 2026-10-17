"""Versioned public contract for counterevidence argv syntax and freshness.

Consumers pinned to this gate commit may load ``counterevidence_argv_reason``
and the matching JSON vectors beside this file. Validation is syntactic only;
this module never executes Git or performs I/O.
"""

from __future__ import annotations


# Increment when accepted argv shapes or rejection results change.
COUNTEREVIDENCE_ARGV_CONTRACT_VERSION = 1

# Public rejection text template; kept byte-for-byte compatible with v1.
COUNTEREVIDENCE_ARGV_TEMPLATE = (
    'expected allowlisted argv is exactly 9 items: '
    '["git", "grep", "-n", "-F", "-e", <literal>, <head_sha>, "--", <pathspec>] '
    '(head_sha at index 6)'
)


def counterevidence_argv_reason(argv: list[str], head_sha: str) -> str | None:
    """Return the v1 reject reason unless argv is the allowlisted git-grep form.

    This checks argv syntax and equality with the current head SHA only; it
    does not execute Git. None means the argv is compliant.
    """
    if not isinstance(head_sha, str) or not head_sha:
        return (
            "counterevidence_command_not_allowlisted: head_sha is missing; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if not isinstance(argv, list) or len(argv) != 9:
        return (
            "counterevidence_command_not_allowlisted: argv length is not 9; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if any(not isinstance(item, str) for item in argv):
        return (
            "counterevidence_command_not_allowlisted: argv items must be strings; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    git, grep, flag_n, flag_f, flag_e, literal, sha, dash, pathspec = argv
    if git != "git":
        return (
            "counterevidence_command_not_allowlisted: argv[0] is not git; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if grep != "grep":
        return (
            "counterevidence_command_not_allowlisted: argv[1] is not grep; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if (flag_n, flag_f, flag_e, dash) != ("-n", "-F", "-e", "--"):
        return (
            "counterevidence_command_not_allowlisted: "
            "argv flags must be -n -F -e with -- at index 7; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if not literal or literal.startswith("-"):
        return (
            "counterevidence_command_not_allowlisted: "
            "argv[5] literal is empty or starts with -; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if sha != head_sha:
        return (
            "counterevidence_command_not_allowlisted: "
            "argv[6] is not the current head_sha; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if not pathspec or pathspec.startswith("-") or pathspec.startswith(":"):
        return (
            "counterevidence_command_not_allowlisted: "
            "argv[8] pathspec is empty or starts with - or :; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if pathspec.startswith("/"):
        return (
            "counterevidence_command_not_allowlisted: "
            "argv[8] pathspec must be relative, not absolute; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    if ".." in pathspec.split("/"):
        return (
            "counterevidence_command_not_allowlisted: "
            "argv[8] pathspec must not contain ..; "
            + COUNTEREVIDENCE_ARGV_TEMPLATE
        )
    return None
