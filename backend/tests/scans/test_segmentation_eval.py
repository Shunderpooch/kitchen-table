from backend.src.scans.segmentation_eval import _pk, _windowdiff, score_stack, split_of


def test_perfect_and_one_per_page_scores():
    ref = [0, 0, 1, 2, 2, 2]
    perfect = score_stack(ref, ref, [False] * 5, k=2)
    assert perfect["boundary_f1"] == 1 and perfect["pk"] == 0 and perfect["doc_exact_f1"] == 1

    split = score_stack(ref, list(range(6)), [False] * 5, k=2)
    assert split["boundary_recall"] == 1 and split["boundary_precision"] == 2 / 5
    assert split["join_recall"] == 0 and split["wrong_joins"] == 0


def test_wrong_join_counted_and_caught_by_review():
    ref = [0, 0, 1]
    hyp = [0, 0, 0]  # page 3 wrongly joined
    caught = score_stack(ref, hyp, [False, True], k=1)
    assert caught["wrong_joins"] == 1 and caught["wrong_joins_caught_by_review"] == 1
    assert caught["review_rate"] == 1


def test_pk_and_windowdiff_match_hand_counts():
    ref, hyp = [0, 0, 1, 1], [0, 0, 0, 1]
    # Pairs (i, i+2): (0,2) ref differ / hyp same -> error; (1,3) both differ -> ok.
    assert _pk(ref, hyp, 2) == 0.5
    # Boundaries after page: ref [0,1,0], hyp [0,0,1]; windows of 2: [1 vs 0], [1 vs 1].
    assert _windowdiff([0, 1, 0], [0, 0, 1], 2) == 0.5


def test_doc_outcomes():
    from backend.src.scans.segmentation_eval import doc_outcomes

    ref = [0, 0, 1, 2, 2, 3]
    hyp = [0, 0, 1, 1, 2, 3]  # doc 1 absorbed doc 2's first page; doc 2 also split
    assert doc_outcomes(ref, hyp) == ["exact", "merged", "split_and_merged", "exact"]


def test_census_and_evaluation_end_to_end(tmp_path, layout, onedrive):
    from sqlalchemy import create_engine

    from backend.src.scans.segmentation_eval import doc_units, evaluate_segmentation, load_census
    from backend.src.scans.text_index import index_documents

    from .test_grouping import RESTAURANT_BILL, RESTAURANT_SLIP
    from .test_text_index import scanned_pdf

    folder = onedrive / "Documents" / "receipts" / "restaurants"
    scanned_pdf(folder / "2026-10-03_19-42_marigold.pdf", pages=2)
    scanned_pdf(folder / "2026-10-04_cafe.pdf", pages=1)
    texts = iter([RESTAURANT_BILL, RESTAURANT_SLIP, "Cafe Nero\n12 Main St Boston MA 02110\nTotal 4.00"])
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}")
    index_documents(engine, layout, lambda image: next(texts), roots=[folder])

    docs, coverage = load_census(engine, layout, roots=[folder])
    # The fixture's placeholder PDF cannot be read: on disk but not indexed.
    assert (coverage.files, coverage.indexed) == (3, 2)
    report = evaluate_segmentation(docs, doc_units(docs, layout), rounds=5)
    assert report["systems"]["rules"]["doc_exact_f1"][0] == 1.0
    assert report["systems"]["one_per_page"]["join_recall"][0] == 0.0
    assert report["breakdown"]["length"]["2 pages"]["exact"][0] == 1.0


def test_split_is_stable_and_about_one_fifth():
    keys = [f"Documents/receipts/x/{i}.pdf" for i in range(2000)]
    assert split_of(keys[0]) == split_of(keys[0].replace("/", "\\"))
    share = sum(split_of(k) == "test" for k in keys) / len(keys)
    assert 0.15 < share < 0.25
