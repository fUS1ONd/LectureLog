import json
from pathlib import Path

from scripts.evaluate_slide_alignment import evaluate


def test_evaluator_detects_perfect_and_corrupted_prediction() -> None:
    root = Path("tests/fixtures/slide_alignment")
    gold = json.loads((root / "golden/synthetic.json").read_text())
    prediction = json.loads((root / "predictions/synthetic.json").read_text())
    assert evaluate(gold, prediction)["precision_discussed"] == 1.0
    prediction["slides"][1]["status"] = "discussed"
    assert evaluate(gold, prediction)["false_positive"] == 1

