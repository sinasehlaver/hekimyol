"""No network. Loads the real pathways, walks known paths, checks the de-id boundary."""
import unittest

from app import engine, llm, pathways

pathways.load()


class PathwayData(unittest.TestCase):
    def test_all_pathways_valid_and_loaded(self):
        self.assertEqual(
            set(pathways.PATHWAYS),
            {"chest_pain", "headache", "abdominal_pain", "dyspnea"},
        )

    def test_every_option_next_and_outcome_reachable(self):
        for slug, pw in pathways.PATHWAYS.items():
            nodes = pw["nodes"]
            reached_outcome = False
            for nid, n in nodes.items():
                if n["type"] == "outcome":
                    continue
                for o in n["options"]:
                    self.assertIn(o["next"], nodes, f"{slug}.{nid} -> {o['next']}")
                    if nodes[o["next"]]["type"] == "outcome":
                        reached_outcome = True
            self.assertTrue(reached_outcome, f"{slug}: no option leads to an outcome")


class Traversal(unittest.TestCase):
    def test_chest_pain_ischemic_features_go_acil(self):
        pw = pathways.PATHWAYS["chest_pain"]
        # q_now: yes -> q_features: "en az biri var" (idx 0) -> o_acil
        nid, node = engine.resolve(pw, [["q_now", 0], ["q_features", 0]])
        self.assertEqual(nid, "o_acil")
        self.assertEqual(node["urgency"], "acil")

    def test_chest_pain_pleuritic_go_planli(self):
        pw = pathways.PATHWAYS["chest_pain"]
        nid, node = engine.resolve(pw, [
            ["q_now", 1], ["q_when_stopped", 1], ["q_features", 1],
            ["q_tearing", 1], ["q_risk", 1], ["q_pleuritic", 0],
        ])
        self.assertEqual(node["urgency"], "planli")

    def test_mismatched_answer_raises(self):
        pw = pathways.PATHWAYS["headache"]
        with self.assertRaises(engine.BadAnswer):
            engine.resolve(pw, [["q_fever", 0]])  # start is q_thunderclap

    def test_trail_records_risk_flags(self):
        pw = pathways.PATHWAYS["chest_pain"]
        t = engine.trail(pw, [["q_now", 0], ["q_features", 0]])
        self.assertEqual(t[0]["risk_flag"], "aktif_agri")
        self.assertEqual(t[1]["risk_flag"], "iskemik_ozellik")


class DeidBoundary(unittest.TestCase):
    def test_pii_scrubbed_before_leaving(self):
        e = llm.StubEdges()
        clean, n = e.deidentify(
            "Adım Ayşe Yılmaz, TC 10000000146, 0555 444 33 22, ayse@example.com, "
            "12.03.2026 tarihinde göğsüm ağrıdı")
        for token in ("[TCKN]", "[TELEFON]", "[EPOSTA]", "[TARIH]"):
            self.assertIn(token, clean)
        self.assertGreaterEqual(n, 4)

    def test_router_picks_pathway(self):
        e = llm.StubEdges()
        self.assertEqual(e.route("karnımın sağ alt tarafı ağrıyor"), "abdominal_pain")
        self.assertIsNone(e.route("belirsiz bir şikayet"))


if __name__ == "__main__":
    unittest.main()
