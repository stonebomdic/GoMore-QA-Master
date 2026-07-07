from scripts.crud_readback import diff_readback


def test_diff_readback_reports_only_written_keys():
    written = {"title": "A", "score": 5}
    readback = {"title": "A", "score": 9, "id": 1, "createdAt": "..."}  # server 加欄位不算
    assert diff_readback(written, readback) == ["score"]


def test_diff_readback_all_match_returns_empty():
    assert diff_readback({"title": "A"}, {"title": "A", "id": 1}) == []
