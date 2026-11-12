"""What a Swedish text of the product says about its own language.

The interface's Swedish and every text written for a DSO follow docs/glossary.md, and no native
speaker has reviewed either yet. The glossary and docs/manual_sv.md say so; a text a DSO would file,
send or publish says it itself, with this one sentence: the templates carry it word for word
(tests/test_language_note.py compares them with it) and the texts built in code append it. A
publication carries it while it is a draft: signing is where the DSO takes the text as its own.

When a native speaker of the sector has reviewed a text, the sentence leaves that text and its
template, not this constant.
"""

from __future__ import annotations

NOT_REVIEWED_SV = ("Språket har inte granskats av någon med svenska som modersmål: granska texten innan den "
                   "skickas eller publiceras.")

__all__ = ["NOT_REVIEWED_SV"]
