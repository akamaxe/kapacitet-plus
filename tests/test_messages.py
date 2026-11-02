"""Every sentence the engine or the API hands the interface has words in all three languages.

The engine keeps its English wording next to a code (`importer/validate.py` MESSAGES, the queue's
ERRORS, the API's API_ERRORS, the request's reasons); the interface says it with `chk.msg.<code>`,
`err.<code>` and `req.reason.<code>` in web/src/lib/i18n/{en,es,sv}.ts. A new code without its
three translations, or a translation that drops one of the sentence's values, fails here.
Standard library and the package only: no data needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

I18N = Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "i18n"
ENTRY = re.compile(r'^  "([^"]+)": "((?:[^"\\]|\\.)*)",\s*(//.*)?$')
LANGS = ("en", "es", "sv")


def _dict(lang: str) -> dict[str, str]:
    out = {}
    for line in (I18N / f"{lang}.ts").read_text(encoding="utf-8").splitlines():
        m = ENTRY.match(line)
        if m:
            out[m.group(1)] = json.loads('"' + m.group(2) + '"')
    return out


def _names(template: str) -> list[str]:
    """Placeholder names, with any format spec dropped: '{v:.0f}' and '{v}' are both 'v'."""
    return sorted(re.findall(r"\{(\w+)(?:[:!][^}]*)?\}", template))


def _codes() -> list[tuple[str, str]]:
    from kapacitet.api.inbox import ERRORS as INBOX
    from kapacitet.datasets import ERRORS as DATASETS
    from kapacitet.api.main import API_ERRORS
    from kapacitet.api.setup import ERRORS as SETUP
    from kapacitet.engine.delivery import ERRORS as DELIVERY
    from kapacitet.engine.rules import REASONS as RULE_REASONS
    from kapacitet.engine.queue import ERRORS
    from kapacitet.engine.request import _REASONS
    from kapacitet.importer.validate import MESSAGES

    pairs = [(f"chk.msg.{c}", t) for c, t in MESSAGES.items()]
    pairs += [(f"err.{c}", t) for c, t in {**API_ERRORS, **ERRORS, **DELIVERY, **INBOX, **DATASETS, **SETUP}.items()]
    pairs += [(f"req.reason.{c}", t) for c, t in _REASONS.items()]
    pairs += [(f"inbox.reason.{c}", t) for c, t in RULE_REASONS.items()]
    return pairs


@pytest.mark.parametrize("lang", LANGS)
def test_every_engine_message_code_has_a_label_in_three_languages(lang):
    words = _dict(lang)
    missing = [key for key, _ in _codes() if key not in words]
    assert not missing, f"{lang}: no words for {missing}"


@pytest.mark.parametrize("lang", LANGS)
def test_a_translated_message_keeps_the_values_of_the_engine_sentence(lang):
    words = _dict(lang)
    off = [key for key, template in _codes() if key in words and _names(words[key]) != _names(template)]
    assert not off, f"{lang}: placeholders differ from the engine's sentence in {off}"


def test_queue_and_api_codes_do_not_collide_with_different_words():
    from kapacitet.api.main import API_ERRORS
    from kapacitet.engine.queue import ERRORS

    shared = set(API_ERRORS) & set(ERRORS)
    assert all(API_ERRORS[c] == ERRORS[c] for c in shared), f"same code, different sentence: {shared}"


def test_no_code_means_two_different_sentences():
    """The interface looks every refusal up as err.<code>: two sources with one code and two
    sentences would show one of them the other's words."""
    from kapacitet.api.inbox import ERRORS as INBOX
    from kapacitet.api.main import API_ERRORS
    from kapacitet.api.setup import ERRORS as SETUP
    from kapacitet.datasets import ERRORS as DATASETS
    from kapacitet.engine.delivery import ERRORS as DELIVERY
    from kapacitet.engine.queue import ERRORS

    seen: dict[str, str] = {}
    clashes = []
    for source in (API_ERRORS, ERRORS, DELIVERY, INBOX, DATASETS, SETUP):
        for code, text in source.items():
            if code in seen and seen[code] != text:
                clashes.append(code)
            seen.setdefault(code, text)
    assert not clashes, f"same code, different sentences: {clashes}"
