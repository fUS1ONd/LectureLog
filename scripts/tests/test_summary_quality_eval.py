import math
import sys
import unittest
from pathlib import Path

# Добавляем родительскую папку scripts/ в путь импорта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from summary_quality_eval import compute_metrics, rank_candidates


class SummaryQualityEvalTests(unittest.TestCase):
    def test_identical_texts_have_perfect_fidelity(self):
        source = "Привет мир. Это тест."
        candidate = "Привет мир. Это тест."

        metrics = compute_metrics(source, candidate)

        self.assertAlmostEqual(metrics["word_seq_ratio_to_source"], 1.0, places=6)
        self.assertAlmostEqual(metrics["base_words_preserved_pct"], 100.0, places=6)
        self.assertAlmostEqual(metrics["rouge1_recall"], 1.0, places=6)
        self.assertAlmostEqual(metrics["rouge2_recall"], 1.0, places=6)
        self.assertAlmostEqual(metrics["fidelity_score"], 100.0, places=6)
        self.assertAlmostEqual(metrics["compression_vs_source_words_pct"], 0.0, places=6)

    def test_compressed_summary_has_lower_coverage(self):
        source = "Один два три четыре. Пять шесть семь восемь."
        candidate = "Один два три."

        metrics = compute_metrics(source, candidate)

        self.assertLess(metrics["base_words_preserved_pct"], 100.0)
        self.assertGreater(metrics["compression_vs_source_words_pct"], 0.0)
        self.assertLess(metrics["fidelity_score"], 100.0)

    def test_ranking_prefers_more_faithful_candidate(self):
        source = "alpha beta gamma delta"
        candidates = {
            "good": "alpha beta gamma delta",
            "bad": "foo bar baz",
        }

        ranked = rank_candidates(source, candidates)

        self.assertEqual(ranked[0]["name"], "good")
        self.assertEqual(ranked[1]["name"], "bad")
        self.assertGreater(ranked[0]["metrics"]["fidelity_score"], ranked[1]["metrics"]["fidelity_score"])


if __name__ == "__main__":
    unittest.main()
