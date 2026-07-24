from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(gold: dict, prediction: dict) -> dict[str, float | int]:
    gold_by_num = {item["slide_num"]: item for item in gold["slides"]}
    pred_by_num = {item["slide_num"]: item for item in prediction["slides"]}
    true_positive = false_positive = false_negative = section_correct = discussed = 0
    for slide_num, expected in gold_by_num.items():
        actual = pred_by_num.get(slide_num, {"status": "unmentioned"})
        expected_discussed = expected["status"] == "discussed"
        actual_discussed = actual["status"] == "discussed"
        true_positive += int(expected_discussed and actual_discussed)
        false_positive += int(not expected_discussed and actual_discussed)
        false_negative += int(expected_discussed and not actual_discussed)
        if expected_discussed:
            discussed += 1
            section_correct += int(
                actual.get("global_section_id") in expected.get("acceptable_section_ids", [])
            )
    precision = true_positive / max(true_positive + false_positive, 1)
    recall = true_positive / max(true_positive + false_negative, 1)
    return {
        "precision_discussed": precision,
        "recall_discussed": recall,
        "exact_section_accuracy": section_correct / max(discussed, 1),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("gold", type=Path)
    parser.add_argument("prediction", type=Path)
    args = parser.parse_args()
    metrics = evaluate(
        json.loads(args.gold.read_text(encoding="utf-8")),
        json.loads(args.prediction.read_text(encoding="utf-8")),
    )
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

