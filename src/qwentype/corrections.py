"""Correction rules: fixed replacements for words the recognizer keeps getting wrong.

One rule per line, `wrong -> right` (`=>` and `→` work too); lines starting with `#` are comments.
Several misrecognized forms can share a rule: `库伯内提斯 | 酷伯内特斯 -> Kubernetes`. An empty right
side deletes the wrong form.

Matching ignores case, and a space in a wrong form also matches no space or several (`cloud code`
matches "CloudCode"). Where a wrong form starts or ends with a Latin letter or digit, it only matches
whole words there, so `AI -> A.I.` leaves "said" alone; next to CJK text no boundary is needed. All
rules are applied in one pass, longest wrong form first, so one rule's output is never rewritten by
another rule.
"""

from __future__ import annotations

import functools
import re

_ARROW = re.compile(r"->|=>|→")
_SPACES = re.compile(r"\s+")
_LATIN_WORD_CHAR = re.compile(r"[A-Za-z0-9]")


def _rule_lines(text: str):
    """(line number, line) of each non-empty, non-comment line."""
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line and not line.startswith("#"):
            yield number, line


def parse_rules(text: str) -> list[tuple[str, str]]:
    """'a | b -> c' lines -> [(a, c), (b, c)], in file order. Lines without an arrow are skipped."""
    rules: list[tuple[str, str]] = []
    for _number, line in _rule_lines(text):
        parts = _ARROW.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        right = parts[1].strip()
        for wrong in parts[0].split("|"):
            wrong = _SPACES.sub(" ", wrong.strip())
            if wrong and wrong != right:
                rules.append((wrong, right))
    return rules


def invalid_lines(text: str) -> list[int]:
    """Line numbers (1-based) of rule lines without an arrow or without a wrong form."""
    bad = []
    for number, line in _rule_lines(text):
        parts = _ARROW.split(line, maxsplit=1)
        if len(parts) != 2 or not any(w.strip() for w in parts[0].split("|")):
            bad.append(number)
    return bad


def _pattern(wrong: str) -> str:
    pattern = r"\s*".join(re.escape(word) for word in wrong.split(" "))
    if _LATIN_WORD_CHAR.match(wrong[0]):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if _LATIN_WORD_CHAR.match(wrong[-1]):
        pattern += r"(?![A-Za-z0-9])"
    return pattern


class Corrector:
    def __init__(self, rules: list[tuple[str, str]]) -> None:
        self._rights: list[str] = []
        seen: set[str] = set()
        alternatives = []
        # Longest first, so "cloud code" wins over "cloud"; sorted() is stable, so for the same
        # wrong form the first rule in the file wins.
        for wrong, right in sorted(rules, key=lambda rule: -len(rule[0])):
            if wrong.casefold() in seen:
                continue
            seen.add(wrong.casefold())
            alternatives.append(f"(?P<r{len(self._rights)}>{_pattern(wrong)})")
            self._rights.append(right)
        self._regex = re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None

    def __bool__(self) -> bool:
        return self._regex is not None

    def apply(self, text: str) -> str:
        if self._regex is None or not text:
            return text
        return self._regex.sub(lambda m: self._rights[int((m.lastgroup or "r0")[1:])], text)


@functools.lru_cache(maxsize=4)
def corrector(text: str) -> Corrector:
    """The compiled rules of a settings text (cached: the text rarely changes)."""
    return Corrector(parse_rules(text))


def apply(text: str, rules_text: str) -> str:
    return corrector(rules_text).apply(text) if rules_text.strip() else text
