import os
import re
from dataclasses import dataclass

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".heic",
    ".bmp", ".dng",
}

_PATTERNS = [
    ("private-ip", re.compile(
        r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))"
        r"\.\d{1,3}\.\d{1,3}(?![\d.])")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    ("host-path", re.compile(
        r"(?<![\w.])/(?:home|mnt|Users|root|srv|media|volume\d*)/[^\s\"'`)]+")),
    ("internal-domain", re.compile(
        r"\b(?!(?:self|this|cls)\.)[\w-]+(?:\.[\w-]+)*"
        r"\.(?:local|lan|internal|corp|home\.arpa)\b(?![\w.(-])")),
]
_EMAIL_OK = re.compile(
    r"@(?:users\.noreply\.github\.com|anthropic\.com|example\.(?:com|org|net))$")


@dataclass(frozen=True)
class Finding:
    kind: str
    line: int


def load_terms(path=None):
    path = path or os.environ.get("BATON_PRIVATE_TERMS") or os.path.expanduser(
        "~/.baton/private-terms.txt")
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _matches(kind, pattern, line):
    for match in pattern.finditer(line):
        text = match.group(0)
        if kind == "email" and (_EMAIL_OK.search(text) or text.startswith("git@")):
            continue
        yield text


def scan_text(text, terms, allow):
    findings = []
    lowered_terms = [term.lower() for term in terms]
    for number, line in enumerate(text.splitlines(), start=1):
        for literal in allow:
            line = line.replace(literal, "")
        for kind, pattern in _PATTERNS:
            if any(True for _ in _matches(kind, pattern, line)):
                findings.append(Finding(kind, number))
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            findings.append(Finding("private-term", number))
    return findings


def describe(findings):
    return [f"{finding.kind} on line {finding.line}" for finding in findings]
