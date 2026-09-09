"""Pure, deterministic graph traversal. No LLM, no I/O, no DB.

State = the ordered list of answers `[(node_id, option_idx), ...]`. The cursor is
re-derived from that list every call, so a traversal is fully reproducible and
auditable (handoff: "graph traversal itself pure/deterministic").
"""


class BadAnswer(Exception):
    pass


def resolve(pathway: dict, answers: list) -> tuple[str, dict]:
    """Walk `answers` from the start node. Return (current_node_id, node_dict).

    Stops early (ignoring trailing answers) if an outcome node is reached.
    Raises BadAnswer if an answer doesn't match the node it claims to answer.
    """
    nodes = pathway["nodes"]
    nid = pathway["start"]
    for ans_nid, opt_idx in answers:
        node = nodes[nid]
        if node["type"] == "outcome":
            break
        if ans_nid != nid:
            raise BadAnswer(f"expected an answer for {nid!r}, got {ans_nid!r}")
        opts = node["options"]
        if not (0 <= opt_idx < len(opts)):
            raise BadAnswer(f"{nid}: option index {opt_idx} out of range")
        nid = opts[opt_idx]["next"]
    return nid, nodes[nid]


def trail(pathway: dict, answers: list) -> list[dict]:
    """Human-readable Q&A trail for the doctor report."""
    nodes = pathway["nodes"]
    out = []
    nid = pathway["start"]
    for ans_nid, opt_idx in answers:
        node = nodes[nid]
        if node["type"] != "question" or ans_nid != nid:
            break
        opt = node["options"][opt_idx]
        out.append({"q": node["text"], "a": opt["label"], "risk_flag": opt.get("risk_flag")})
        nid = opt["next"]
    return out


def demo() -> None:
    p = {
        "slug": "t", "title": "t", "version": 1, "source": "t", "start": "q1",
        "nodes": {
            "q1": {"type": "question", "text": "?", "options": [
                {"label": "yes", "next": "o_bad", "risk_flag": "red"},
                {"label": "no", "next": "q2"}]},
            "q2": {"type": "question", "text": "?", "options": [
                {"label": "a", "next": "o_ok"}]},
            "o_bad": {"type": "outcome", "urgency": "acil", "department": "Acil",
                      "patient_advice": "git"},
            "o_ok": {"type": "outcome", "urgency": "planli", "department": "Dahiliye",
                     "patient_advice": "randevu"},
        },
    }
    nid, node = resolve(p, [["q1", 0]])
    assert nid == "o_bad" and node["urgency"] == "acil", nid
    nid, node = resolve(p, [["q1", 1], ["q2", 0]])
    assert nid == "o_ok", nid
    # trailing answers past an outcome are ignored, not an error
    nid, _ = resolve(p, [["q1", 0], ["o_bad", 0]])
    assert nid == "o_bad"
    try:
        resolve(p, [["q2", 0]])
    except BadAnswer:
        pass
    else:
        raise AssertionError("expected BadAnswer for mismatched node")
    assert trail(p, [["q1", 1], ["q2", 0]]) == [
        {"q": "?", "a": "no", "risk_flag": None},
        {"q": "?", "a": "a", "risk_flag": None},
    ]
    print("engine.demo OK")


if __name__ == "__main__":
    demo()
