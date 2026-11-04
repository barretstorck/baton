"""Argparse declarations for every baton command.

The layer keeps today's behaviour: usage errors print the command's usage text
and exit 64, ``--help`` prints help to stdout and exits 0, and no
``SystemExit`` escapes.  Later tasks add one ``_declare_<command>`` per command
to ``build()``.
"""
from __future__ import annotations

import argparse
import functools
import sys
from typing import Callable

USAGE: dict[str, str] = {}


class Usage(Exception):
    """Raised instead of exiting; ``code`` is the exit status."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


class BatonParser(argparse.ArgumentParser):
    def __init__(self, *args, usage_text: str = "", lenient: bool = False,
                 **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)
        self.usage_text = usage_text
        self.lenient = lenient

    def error(self, message):
        sys.stderr.write((self.usage_text or self.format_usage()).rstrip("\n")
                         + "\n")
        raise Usage(64)

    def exit(self, status=0, message=None):
        if message:
            sys.stderr.write(message)
        raise Usage(status)


def digits(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")
    return int(text)


def _subparsers(parser: argparse.ArgumentParser) -> list[BatonParser]:
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                found.append(sub)
                found.extend(_subparsers(sub))
    return found


def _value_options(parser: argparse.ArgumentParser) -> set[str]:
    names: set[str] = set()
    for p in [parser, *_subparsers(parser)]:
        for action in p._actions:
            if action.option_strings and action.nargs != 0:
                names.update(o for o in action.option_strings
                             if o.startswith("--"))
    return names


def _has_own_options(parser: argparse.ArgumentParser) -> bool:
    return any(a.option_strings and "-h" not in a.option_strings
               for a in parser._actions)


def _has_subparsers(parser: argparse.ArgumentParser) -> bool:
    return any(isinstance(a, argparse._SubParsersAction)
               for a in parser._actions)


def _normalise(parser: BatonParser, argv: list[str]) -> list[str]:
    """Make today's hand parsing work: a value option takes the next word
    whatever it looks like, and a positional-only command takes every word as
    a value."""
    if (not _has_own_options(parser) and not _has_subparsers(parser)
            and argv not in (["-h"], ["--help"])):
        return ["--", *argv]
    values = _value_options(parser)
    out: list[str] = []
    i = 0
    while i < len(argv):
        word = argv[i]
        if word == "--":
            out.extend(argv[i:])
            break
        if word in values and i + 1 < len(argv):
            out.append(f"{word}={argv[i + 1]}")
            i += 2
            continue
        out.append(word)
        i += 1
    return out


def parse_with(parser: BatonParser, argv: list[str]) -> argparse.Namespace:
    argv = _normalise(parser, list(argv))
    if parser.lenient:
        opts, _ = parser.parse_known_args(argv)
        return opts
    return parser.parse_args(argv)


@functools.lru_cache(maxsize=None)
def build() -> BatonParser:
    top = BatonParser(prog="baton", usage_text="usage: baton <command> [args]")
    top.add_subparsers(dest="command", parser_class=BatonParser)
    return top


def _commands() -> dict[str, BatonParser]:
    for action in build()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def parse(command: str, argv: list[str]) -> argparse.Namespace:
    return parse_with(_commands()[command], argv)


def command(name: str) -> Callable:
    def decorate(handler: Callable) -> Callable:
        @functools.wraps(handler)
        def wrapper(args: list[str]) -> int:
            try:
                opts = parse(name, args)
            except Usage as exc:
                return exc.code
            return handler(opts)
        return wrapper
    return decorate


def fail(name: str) -> int:
    print(USAGE[name], file=sys.stderr)
    return 64
