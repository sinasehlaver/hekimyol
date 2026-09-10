"""Load + validate the deterministic decision graphs from pathways/*.yaml at startup.

A pathway is data, not code. Each node is a `question` (options, each pointing at the
next node) or an `outcome` (department + urgency + advice + differential + citation).
No DB table for the graph at MVP scale — see handoff "Neo4j rejected".
"""
import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_DIR = _ROOT / "pathways"
# Sidecars live OUTSIDE _DIR on purpose: load() globs pathways/*.yaml, so anything
# it must not pick up (backups, canvas layouts) has to sit in a sibling directory.
BACKUP_DIR = _ROOT / "pathway_backups"
LAYOUT_DIR = _ROOT / "pathway_layouts"
_URGENCY = {"acil", "24_saat", "planli"}
# Written in this order so an edited file still reads like the hand-authored ones.
KEY_ORDER = ("slug", "title", "version", "source", "start", "nodes")

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


# ---------- admin / flowchart-editor support ----------
def path(slug: str) -> pathlib.Path:
    """Path of an EXISTING pathway file. Never used to create new slugs."""
    f = _DIR / f"{slug}.yaml"
    if slug not in PATHWAYS or not f.is_file():
        raise KeyError(slug)
    return f


def validate(slug: str, parsed: dict) -> None:
    """Public wrapper around `_validate`. Raises AssertionError with the reason.

    Identical rules to the startup check — the editor must not be able to write a
    file that would fail the next boot.
    """
    _validate(slug, parsed)


def raw(slug: str) -> str:
    return path(slug).read_text(encoding="utf-8")


def dump(parsed: dict) -> str:
    ordered = {k: parsed[k] for k in KEY_ORDER if k in parsed}
    ordered.update({k: v for k, v in parsed.items() if k not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True)


def layout_path(slug: str) -> pathlib.Path:
    return LAYOUT_DIR / f"{slug}.json"


def read_layout(slug: str) -> dict | None:
    import json
    f = layout_path(slug)
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return None


def write_layout(slug: str, layout: dict) -> None:
    import json
    LAYOUT_DIR.mkdir(exist_ok=True)
    layout_path(slug).write_text(
        json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")


def backup(slug: str) -> pathlib.Path:
    """Copy the current file to pathway_backups/<slug>.<UTC ts>.yaml, return the copy."""
    import datetime as dt
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = BACKUP_DIR / f"{slug}.{ts}.yaml"
    dest.write_text(path(slug).read_text(encoding="utf-8"), encoding="utf-8")
    return dest
