"""Akış editörü (/admin) — YAML⇄graph round-trip + the save safeguard.

No network. Env is set the way scripts/verify.sh sets it, BEFORE app.* is imported
(app/db.py reads DATABASE_URL and app/main.py reads ADMIN_PASSWORD at import time).

Admin auth is a password login that sets a signed HttpOnly `hk_admin` cookie;
`TestClient` keeps cookies on the instance, so `setUp` logs in once.

Every test that can touch pathways/*.yaml restores the original text in tearDown —
the clinical files must be left byte-identical.
"""
import copy
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="hekimyol-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/admin-test.db"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["HEKIMYOL_LLM"] = "stub"

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main, pathways  # noqa: E402

SLUG = "chest_pain"

pathways.load()


def _minimal(slug: str) -> dict:
    return {"slug": slug, "title": "t", "version": 1, "source": "s", "start": "q1",
            "nodes": {
                "q1": {"type": "question", "text": "?", "options": [
                    {"label": "evet", "next": "o1"}]},
                "o1": {"type": "outcome", "urgency": "acil", "department": "Acil",
                       "patient_advice": "git"}}}


class YamlGraphRoundTrip(unittest.TestCase):
    """parsed -> yaml.safe_dump -> re-parse must be structurally identical.

    This is what makes the editor safe: the graph the browser sends back is the
    same object shape the loader validates, so a save can't quietly reshape a file.
    """

    def test_roundtrip_is_stable_for_every_shipped_pathway(self):
        for slug in ("chest_pain", "headache", "abdominal_pain", "dyspnea"):
            with self.subTest(slug=slug):
                original = yaml.safe_load(pathways.raw(slug))
                again = yaml.safe_load(pathways.dump(original))
                self.assertEqual(original, again)
                # a second pass is a fixed point (dump is idempotent)
                self.assertEqual(again, yaml.safe_load(pathways.dump(again)))

    def test_dump_preserves_key_order(self):
        parsed = yaml.safe_load(pathways.raw(SLUG))
        text = pathways.dump(parsed)
        top = [line.split(":")[0] for line in text.splitlines()
               if line and not line[0].isspace() and ":" in line]
        self.assertEqual(top[:6], list(pathways.KEY_ORDER))

    def test_dump_keeps_turkish_characters_unescaped(self):
        self.assertIn("Göğüs", pathways.dump(yaml.safe_load(pathways.raw(SLUG))))


class Validate(unittest.TestCase):
    def test_accepts_a_shipped_pathway(self):
        pathways.validate(SLUG, yaml.safe_load(pathways.raw(SLUG)))  # no raise

    def test_rejects_dangling_option_next(self):
        p = _minimal("x")
        p["nodes"]["q1"]["options"][0]["next"] = "o_yok"
        with self.assertRaises(AssertionError) as cm:
            pathways.validate("x", p)
        self.assertIn("o_yok", str(cm.exception))

    def test_rejects_unwired_option(self):
        p = _minimal("x")
        p["nodes"]["q1"]["options"][0]["next"] = None   # editor leaves this on a new port
        with self.assertRaises(AssertionError):
            pathways.validate("x", p)

    def test_rejects_missing_start(self):
        p = _minimal("x")
        p["start"] = "yok"
        with self.assertRaises(AssertionError):
            pathways.validate("x", p)

    def test_rejects_bad_urgency(self):
        p = _minimal("x")
        p["nodes"]["o1"]["urgency"] = "hemen"
        with self.assertRaises(AssertionError):
            pathways.validate("x", p)


class AdminApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.client.__enter__()          # runs the startup hook (init_db + load)
        r = self.client.post("/api/admin/login", json={"password": "test-admin"})
        self.assertEqual(r.status_code, 200, r.text)   # cookie now lives on the client
        self.original = pathways.raw(SLUG)
        self.parsed = yaml.safe_load(self.original)
        self.before = set(p.name for p in pathways.BACKUP_DIR.glob(f"{SLUG}.*.yaml")) \
            if pathways.BACKUP_DIR.is_dir() else set()
        lp = pathways.layout_path(SLUG)
        self.layout_before = lp.read_text(encoding="utf-8") if lp.is_file() else None

    def tearDown(self):
        # the clinical file must be left exactly as it was found
        pathways.path(SLUG).write_text(self.original, encoding="utf-8")
        pathways.load()
        if pathways.BACKUP_DIR.is_dir():
            for f in pathways.BACKUP_DIR.glob(f"{SLUG}.*.yaml"):
                if f.name not in self.before:
                    f.unlink()
        # ...and so must the layout sidecar — a test's fake positions must not
        # become the doctor's canvas.
        lp = pathways.layout_path(SLUG)
        if self.layout_before is None:
            lp.unlink(missing_ok=True)
        else:
            lp.write_text(self.layout_before, encoding="utf-8")
        self.client.__exit__(None, None, None)

    def _put(self, body):
        return self.client.put(f"/api/admin/pathway/{SLUG}", json=body)

    # ---- auth ----
    def test_login_with_wrong_password_is_401_and_sets_no_cookie(self):
        fresh = TestClient(main.app)
        r = fresh.post("/api/admin/login", json={"password": "yanlis"})
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("hk_admin", fresh.cookies)
        self.assertEqual(fresh.get("/api/admin/pathways").status_code, 401)

    # ---- read side ----
    def test_list_requires_auth(self):
        self.assertEqual(TestClient(main.app).get("/api/admin/pathways").status_code, 401)
        r = self.client.get("/api/admin/pathways")
        self.assertEqual(r.status_code, 200)
        self.assertEqual({p["slug"] for p in r.json()},
                         {"chest_pain", "headache", "abdominal_pain", "dyspnea"})
        self.assertEqual([p for p in r.json() if p["slug"] == SLUG][0]["node_count"],
                         len(self.parsed["nodes"]))

    def test_get_returns_parsed_raw_and_layout(self):
        r = self.client.get(f"/api/admin/pathway/{SLUG}")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["parsed"]["start"], self.parsed["start"])
        self.assertEqual(d["raw"], self.original)
        self.assertIn("layout", d)

    # ---- the three-part write safeguard ----
    def test_put_without_auth_is_401(self):
        r = TestClient(main.app).put(
            f"/api/admin/pathway/{SLUG}",
            json={"parsed": self.parsed, "layout": {}, "confirm": SLUG})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(pathways.raw(SLUG), self.original)

    def test_put_with_wrong_confirm_is_400(self):
        for bad in ("", "chest-pain", "CHEST_PAIN", "chest_pain "):
            with self.subTest(confirm=bad):
                r = self._put({"parsed": self.parsed, "layout": {}, "confirm": bad})
                self.assertEqual(r.status_code, 400)
                self.assertIn(SLUG, r.json()["detail"])
        self.assertEqual(pathways.raw(SLUG), self.original)

    def test_put_with_invalid_graph_is_400_and_writes_nothing(self):
        broken = copy.deepcopy(self.parsed)
        broken["nodes"][broken["start"]]["options"][0]["next"] = "o_olmayan"
        r = self._put({"parsed": broken, "layout": {}, "confirm": SLUG})
        self.assertEqual(r.status_code, 400)
        self.assertIn("o_olmayan", r.json()["detail"])
        self.assertEqual(pathways.raw(SLUG), self.original)
        self.assertFalse(any(f.name not in self.before
                             for f in pathways.BACKUP_DIR.glob(f"{SLUG}.*.yaml"))
                         if pathways.BACKUP_DIR.is_dir() else False)

    def test_put_with_slug_mismatch_is_400(self):
        wrong = copy.deepcopy(self.parsed)
        wrong["slug"] = "headache"
        r = self._put({"parsed": wrong, "layout": {}, "confirm": SLUG})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(pathways.raw(SLUG), self.original)

    def test_valid_noop_save_is_200_backs_up_reloads_and_audits(self):
        layout = {nid: {"x": 10 * i, "y": 20 * i}
                  for i, nid in enumerate(self.parsed["nodes"])}
        r = self._put({"parsed": self.parsed, "layout": layout, "confirm": SLUG})
        self.assertEqual(r.status_code, 200, r.text)
        d = r.json()

        # a timestamped backup of the PREVIOUS file exists and matches it byte for byte
        bak = pathways.BACKUP_DIR / d["backup"]
        self.assertTrue(bak.is_file())
        self.assertEqual(bak.read_text(encoding="utf-8"), self.original)

        # the written file re-parses to the same graph and is hot-reloaded in memory
        self.assertEqual(yaml.safe_load(pathways.raw(SLUG)), self.parsed)
        self.assertEqual(pathways.PATHWAYS[SLUG], self.parsed)
        self.assertEqual(d["parsed"], self.parsed)

        # canvas positions go to the sidecar, never into the clinical YAML
        self.assertEqual(pathways.read_layout(SLUG), layout)
        self.assertNotIn("pos_x", pathways.raw(SLUG))

        # KVKK-style audit row for the edit
        from app import db
        with db.SessionLocal() as s:
            rows = [a for a in s.query(db.AuditLog).all() if a.event == "pathway_edit"]
        self.assertTrue(rows)
        self.assertEqual(rows[-1].actor, "doctor")
        self.assertIn(f"pathway={SLUG}", rows[-1].detail)

    def test_edit_then_traversal_still_works(self):
        edited = copy.deepcopy(self.parsed)
        edited["nodes"]["q_now"]["options"][0]["label"] = "Evet, ağrım sürüyor"
        r = self._put({"parsed": edited, "layout": {}, "confirm": SLUG})
        self.assertEqual(r.status_code, 200, r.text)
        from app import engine
        nid, node = engine.resolve(pathways.PATHWAYS[SLUG], [["q_now", 0], ["q_features", 0]])
        self.assertEqual(nid, "o_acil")
        self.assertEqual(node["urgency"], "acil")

    def test_unknown_slug_is_404_and_cannot_create_a_pathway(self):
        r = self.client.put("/api/admin/pathway/yeni_akis",
                            json={"parsed": _minimal("yeni_akis"), "layout": {},
                                  "confirm": "yeni_akis"})
        self.assertEqual(r.status_code, 404)
        self.assertFalse((pathways._DIR / "yeni_akis.yaml").exists())

    def test_admin_page_is_served(self):
        # unauthenticated -> redirected to the login page, never the editor
        anon = TestClient(main.app)
        r = anon.get("/admin", follow_redirects=False)
        self.assertIn(r.status_code, (302, 307))
        self.assertTrue(r.headers["location"].endswith("/admin/login"), r.headers)
        self.assertEqual(anon.get("/admin/login").status_code, 200)

        r = self.client.get("/admin")
        self.assertEqual(r.status_code, 200)
        self.assertIn("drawflow", r.text)


if __name__ == "__main__":
    unittest.main()
