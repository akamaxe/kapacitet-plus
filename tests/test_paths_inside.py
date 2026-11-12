"""paths.inside: the one place an identifier from a request becomes a path, and the identifiers'
own forms. A name is checked twice: its form (a pattern that ends where the name does) and that
the joined, normalised path stays inside the folder it was joined to."""

from __future__ import annotations

import os

import pytest

from kapacitet.paths import inside


def test_a_name_stays_inside_the_folder_it_is_joined_to(tmp_path):
    assert inside(tmp_path, "abc-123") == tmp_path / "abc-123"
    assert inside(tmp_path, "runs", "2026Q4", "publication") == tmp_path / "runs" / "2026Q4" / "publication"
    assert inside(tmp_path, "embed/index.html") == tmp_path / "embed" / "index.html"
    # normalised before the test: a detour that comes back is the plain path
    assert inside(tmp_path, "a", "..", "b") == tmp_path / "b"
    for parts in (("..",), ("..", "x"), ("a", "..", "..", "x"), ("",), (".",), ("a/../../x",),
                  (os.sep + "abs",), (str(tmp_path.parent / "other"),), (str(tmp_path) + "x",)):
        with pytest.raises(ValueError, match="not inside"):
            inside(tmp_path, *parts)
    # a folder whose name starts like the base is not the base
    sibling = tmp_path.parent / (tmp_path.name + "-evil")
    with pytest.raises(ValueError):
        inside(tmp_path, os.path.relpath(sibling / "x", tmp_path))


def test_an_identifier_ends_where_it_should():
    """`^...$` with re.match lets a final newline through; these name folders and files."""
    from kapacitet import brand, datasets
    from kapacitet.study import cases, valid_id
    from kapacitet.study import runs as run_ids

    assert valid_id("S1") and not valid_id("S1\n") and not valid_id("S1/..")
    assert run_ids.valid("2026Q4") and not run_ids.valid("2026Q4\n")
    assert run_ids.valid("2026-11") and not run_ids.valid("2026-11\n")
    assert cases.CASE_ID.match("K-2026.17") and not cases.CASE_ID.match("K-2026.17\n")
    assert brand.ACCENT.match("#0a5c9e") and not brand.ACCENT.match("#0a5c9e\n")
    for bad in ("abc-123\n", "../abc", "ab", "ABC-123", ""):
        with pytest.raises(datasets.DatasetError):
            datasets.folder(bad)
    assert datasets.folder("abc-123").name == "abc-123"


def test_a_record_or_a_sealed_copy_asked_for_by_a_path_does_not_exist(tmp_path):
    from kapacitet.seals import FolderChain
    from kapacitet.store.files import Records

    records = Records(tmp_path / "requests")
    (tmp_path / "requests").mkdir()
    (tmp_path / "secret.json.gz").write_bytes(b"not a record")
    assert records.get("../secret") is None and records.get("0123456789ab") is None
    chain = FolderChain(tmp_path / "evidence")
    (tmp_path / "evidence" / "sealed" / "decision").mkdir(parents=True)
    (tmp_path / "evidence" / "private.json").write_text("{}", encoding="utf-8")
    chain.copy_put("decision", "d1", b'{"a": 1}')
    assert chain.copy_get("decision", "d1") == b'{"a": 1}'
    assert chain.copy_get("decision", "../../private") is None
    assert chain.copy_get("..", "private") is None
    with pytest.raises(ValueError):
        chain.copy_put("decision", "../../private", b"{}")
    chain.copy_delete("decision", "../../private")              # nothing to delete, and nothing deleted
    assert (tmp_path / "evidence" / "private.json").exists()


def test_a_network_is_kept_only_under_the_contracts_own_table_names(tmp_path, monkeypatch):
    """`create` wrote one file per key of what it was handed. The API lets only the contract's
    tables through; the function now refuses the rest itself, before anything is written."""
    from kapacitet import datasets

    monkeypatch.setattr(datasets, "DATASETS", tmp_path / "datasets")
    files = {"buses": "bus_id,kind\nS,substation\n", "../../outside": "x\n1\n"}
    with pytest.raises(datasets.DatasetError) as refused:
        datasets.create("Trial", files)
    assert refused.value.code == "not_in_contract" and "../../outside" in str(refused.value)
    assert not (tmp_path / "datasets").exists() and not list(tmp_path.rglob("outside*"))


def test_what_a_chain_owes_or_keeps_comes_in_line_order(tmp_path):
    """The markers of the tokens still owed were listed in the folder's own order, which on Linux
    changed with the names of the temporary files written beside them: line 2 before line 1."""
    from kapacitet.seals import PENDING_SUFFIX, TOKEN_SUFFIX, FolderChain

    chain = FolderChain(tmp_path / "evidence")
    for seq in (10, 2, 1, 33):
        chain.stamp_pending(seq, f"owed {seq}")
    assert list(chain.stamps_pending()) == [1, 2, 10, 33]
    for seq in (33, 2):
        chain.stamp_put(seq, b"token")
    assert list(chain.stamps()) == [2, 33] and list(chain.stamps_pending()) == [1, 10]
    assert PENDING_SUFFIX != TOKEN_SUFFIX
