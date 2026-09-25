"""Check that the capacity report refuses incorrect accounting and cap breaches."""
from scripts.campaign_load_test import reconcile


def _sample(pairs, selected):
    return ({"counts": {"cvs": 2, "jds": 2, "pairs": pairs,
                        "terminal_pairs": sum(pairs.values())}},
            {"jds": [{"jd_key": "a", "status": "COMPLETED", "stage3_cap": 1,
                      "counts": {"SUCCESS": selected, "CUTOFF_EXCLUDED": 2 - selected}},
                     {"jd_key": "b", "status": "COMPLETED", "stage3_cap": 1,
                      "counts": {"FILTER_REJECTED": 2}}]})


def test_reconcile_complete_campaign():
    status, jds = _sample({"SUCCESS": 1, "CUTOFF_EXCLUDED": 1,
                           "FILTER_REJECTED": 2}, 1)
    report = reconcile(status, jds)
    assert report["reconciled"] is True
    assert report["expected_pairs"] == 4
    assert report["selected_pairs"] == 1


def test_reconcile_rejects_missing_pair_and_excess_shortlist():
    status, jds = _sample({"SUCCESS": 2, "FILTER_REJECTED": 2}, 2)
    assert reconcile(status, jds)["reconciled"] is False
    status["counts"]["pairs"] = {"SUCCESS": 1, "FILTER_REJECTED": 2}
    assert reconcile(status, jds)["reconciled"] is False
