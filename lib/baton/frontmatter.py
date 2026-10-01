"""Parser for the YAML subset used in .baton/brief.md front matter.

Supports nested mappings by indentation, quoted or bare scalars, booleans,
integers, inline lists ([a, b]), empty mappings ({}) and # comments.
"""
import re

_LINE = re.compile(r"""^("[^"]+"|'[^']+'|.+?):(?:\s+(.*))?$""")


_FRONT = re.compile(r"\A---\n(.*?)^---[ \t]*\n?", re.S | re.M)


def split(text):
    if not text.startswith("---\n"):
        return "", text
    match = _FRONT.match(text)
    if not match:
        raise ValueError("front matter has no closing --- line")
    return match.group(1), text[match.end():].lstrip("\n")


def _strip_comment(line):
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index]
    return line


def _scalar(text):
    text = text.strip()
    if text == "{}":
        return {}
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_scalar(part) for part in inner.split(",")] if inner else []
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text


def parse(meta):
    root = {}
    stack = [(-1, root)]
    for raw in meta.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if line[indent:indent + 1] == "\t":
            raise ValueError("front matter must be indented with spaces, not tabs")
        match = _LINE.match(line.strip())
        if not match:
            raise ValueError(f"cannot parse front matter line: {raw.strip()!r}")
        key = match.group(1).strip("\"'")
        value = match.group(2)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value is None or value.strip() == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
        else:
            parent[key] = _scalar(value)
    return root
