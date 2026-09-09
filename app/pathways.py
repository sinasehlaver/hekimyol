"""Load + validate the deterministic decision graphs from pathways/*.yaml at startup.

A pathway is data, not code. Each node is a `question` (options, each pointing at the
next node) or an `outcome` (department + urgency + advice + differential + citation).
No DB table for the graph at MVP scale — see handoff "Neo4j rejected".
"""
import pathlib

import yaml

_DIR = pathlib.Path(__file__).resolve().parent.parent / "pathways"
_URGENCY = {"acil", "24_saat", "planli"}

PATHWAYS: dict[str, dict] = {}


def _validate(slug: str, p: dict) -> None:
    for key in ("slug", "title", "version", "source", "start", "nodes"):
        assert key in p, f"{slug}: missing '{key}'"
    assert p["slug"] == slug, f"{slug}: slug field mismatch ({p['slug']})"
    nodes = p["nodes"]
    assert p["start"] in nodes, f"{slug}: start node '{p['start']}' not defined"
    for nid, n in nodes.items():
        t = n.get("type")
        assert t in ("question", "outcome"), f"{slug}.{nid}: bad type {t!r}"
        if t == "question":
            assert n.get("text"), f"{slug}.{nid}: question has no text"
            assert n.get("options"), f"{slug}.{nid}: question has no options"
            for i, o in enumerate(n["options"]):
                assert o.get("label"), f"{slug}.{nid}[{i}]: option has no label"
                assert o.get("next") in nodes, f"{slug}.{nid}[{i}]: next '{o.get('next')}' not defined"
        else:
            assert n.get("urgency") in _URGENCY, f"{slug}.{nid}: urgency must be one of {_URGENCY}"
            assert n.get("department"), f"{slug}.{nid}: outcome has no department"
            assert n.get("patient_advice"), f"{slug}.{nid}: outcome has no patient_advice"


def load() -> dict[str, dict]:
    PATHWAYS.clear()
    for f in sorted(_DIR.glob("*.yaml")):
        p = yaml.safe_load(f.read_text(encoding="utf-8"))
        _validate(f.stem, p)
        PATHWAYS[p["slug"]] = p
    assert PATHWAYS, f"no pathways found in {_DIR}"
    return PATHWAYS


def summaries() -> list[dict]:
    return [{"slug": s, "title": p["title"]} for s, p in sorted(PATHWAYS.items())]
