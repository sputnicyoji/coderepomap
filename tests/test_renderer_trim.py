"""renderer's binary-search trims must cut exactly where the old pop loops did.

The old loops, copied verbatim below, re-encoded the whole text once per
dropped line. The trims replaced them for speed and must not change a byte.
"""
import random

import pytest

from coderepomap.core.renderer import _trim_l1, _trim_l2, _trim_l3, count_tokens

# "none" is not a tiktoken encoding, so count_tokens falls back to len // 4.
try:
    import tiktoken  # noqa: F401
    ENCODINGS = ["cl100k_base", "none"]
except ImportError:
    ENCODINGS = ["none"]


def _old_l1(lines, max_tokens, encoding):
    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and len(lines) > 10:
        lines.pop(-2)
        output = "\n".join(lines)
    return output


def _old_l2(lines, max_tokens, encoding):
    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and lines:
        while lines and not lines[-1].startswith("##"):
            lines.pop()
        if lines:
            lines.pop()
        output = "\n".join(lines)
    return output


def _old_l3(lines, max_tokens, encoding):
    output = "\n".join(lines)
    while count_tokens(output, encoding) > max_tokens and len(lines) > 5:
        lines.pop()
        output = "\n".join(lines)
    return output


WORDS = ["Manager", "Init()", "->", "(calls)", "rank: 0.12", "Player", "  ", "数据", "x"]


def _random_lines(rng, n):
    lines = []
    for _ in range(n):
        r = rng.random()
        prefix = "## " if r < 0.1 else "### " if r < 0.25 else "- " if r < 0.6 else ""
        lines.append(prefix + " ".join(rng.choice(WORDS) for _ in range(rng.randint(0, 8))))
    return lines


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("new, old", [(_trim_l1, _old_l1), (_trim_l2, _old_l2), (_trim_l3, _old_l3)])
def test_trim_matches_pop_loop(new, old, encoding):
    rng = random.Random(20261026)
    for _ in range(60):
        lines = _random_lines(rng, rng.randint(0, 80))
        full = count_tokens("\n".join(lines), encoding)
        for max_tokens in {-1, 0, 1, full // 3, full // 2, full - 1, full, full + 5}:
            assert new(list(lines), max_tokens, encoding) == old(list(lines), max_tokens, encoding)
