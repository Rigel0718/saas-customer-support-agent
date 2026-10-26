import json

import pytest

from evaluation.dataset import CASE_IDS, EvaluationError, load_bundle


def test_fixed_dataset_and_separate_gt():
    bundle = load_bundle()
    assert [c.case_id for c in bundle.dataset.cases] == CASE_IDS
    assert [len(c.users) for c in bundle.dataset.cases] == [2] * 5
    assert [c.member_limit for c in bundle.dataset.cases] == [3, 2, 3, 3, 2]
    assert bundle.truth("C04").expected_outcome == "needs_information"
    assert bundle.truth("C04").primary_causes == []
    assert len(bundle.truth("C05").primary_causes) == 3
    assert len(bundle.provenance["gt"]["sha256"]) == 64
    assert "primary_causes" not in bundle.dataset.model_dump_json()
    assert len(bundle.provenance["policies"]["files"]) == 3


@pytest.mark.parametrize(
    "bad", ["missing", "duplicate", "cause", "excluded", "policy", "date", "facts"]
)
def test_bad_dataset_rejected_before_side_effects(tmp_path, bad):
    bundle = load_bundle()
    cases = bundle.dataset.model_dump()
    gt = bundle.gt.model_dump()
    if bad == "missing":
        gt["cases"].pop()
    elif bad == "duplicate":
        cases["cases"][1]["case_id"] = "C01"
    elif bad == "cause":
        gt["cases"][4]["primary_causes"].pop()
    elif bad == "excluded":
        gt["cases"][0]["excluded_causes"].append("permission_denied")
    elif bad == "policy":
        gt["cases"][0]["policy_document_ids"] = ["invented"]
    elif bad == "date":
        cases["cases"][0]["subscription"]["expires_offset_days"] = -30
    else:
        gt["cases"][0]["required_facts"] = []
    a, b = tmp_path / "cases.json", tmp_path / "gt.json"
    a.write_text(json.dumps(cases))
    b.write_text(json.dumps(gt))
    writes, llm_calls = [], []
    with pytest.raises(EvaluationError, match="invalid_dataset_or_gt"):
        load_bundle(a, b)
        writes.append(True)
        llm_calls.append(True)
    assert writes == llm_calls == []
