"""hekimyol — patient-facing Turkish symptom intake → deterministic triage → doctor pre-report.

Flow: consent (KVKK açık rıza, granular) → free-text complaint → de-identify (network
boundary) → route to a pathway → deterministic Q&A → patient recommendation + a stored
pre-consult report the doctor reads with a key (every read is audit-logged).
"""
import os
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, engine, llm, pathways

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
DOCTOR_KEY = os.environ.get("DOCTOR_KEY", "dev-doctor-key")
DISCLAIMER = ("Bu araç bir teşhis koymaz. Sizi doğru bölüme yönlendiren bir triyaj "
              "bilgilendirmesidir. Acil bir durumda 112'yi arayın.")

app = FastAPI(title="hekimyol")
edges = llm.get_edges()


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    pathways.load()


# ---------- schemas ----------
class Consent(BaseModel):
    health_data: bool
    store_report: bool


class StartReq(BaseModel):
    consent: Consent
    complaint: str
    pathway_slug: str | None = None  # patient's pick when routing is ambiguous


class AnswerReq(BaseModel):
    option_idx: int


# ---------- helpers ----------
def _node_view(pw: dict, nid: str, node: dict) -> dict:
    if node["type"] == "question":
        return {"done": False, "node_id": nid, "text": node["text"],
                "options": [o["label"] for o in node["options"]]}
    return {"done": True, "node_id": nid, "urgency": node["urgency"],
            "department": node["department"], "advice": node["patient_advice"],
            "bring": node.get("bring", []), "differential": node.get("differential", [])}


def _get_session(d, sid: str) -> db.Session:
    s = d.get(db.Session, sid)
    if not s:
        raise HTTPException(404, "oturum bulunamadı")
    return s


# ---------- patient API ----------
@app.get("/api/meta")
def meta():
    return {"disclaimer": DISCLAIMER, "pathways": pathways.summaries()}


@app.post("/api/session")
def start(req: StartReq):
    if not (req.consent.health_data and req.consent.store_report):
        raise HTTPException(400, "Devam etmek için her iki açık rıza onayı da gereklidir.")
    complaint_deid, redactions = edges.deidentify(req.complaint)
    slug = req.pathway_slug or edges.route(complaint_deid)
    if slug not in pathways.PATHWAYS:
        # can't route — let the patient choose
        return {"needs_pathway": True, "pathways": pathways.summaries(),
                "disclaimer": DISCLAIMER}
    pw = pathways.PATHWAYS[slug]
    sid = str(uuid.uuid4())
    with db.SessionLocal() as d:
        s = db.Session(id=sid, pathway_slug=slug, complaint_deid=complaint_deid)
        s.answers = []
        d.add(s)
        d.commit()
        db.log(d, "session_create", sid, detail=f"pathway={slug} redactions={redactions}")
        db.log(d, "consent", sid, detail="health_data=1 store_report=1")
    nid, node = engine.resolve(pw, [])
    return {"session_id": sid, "disclaimer": DISCLAIMER, **_node_view(pw, nid, node)}


@app.post("/api/session/{sid}/answer")
def answer(sid: str, req: AnswerReq):
    with db.SessionLocal() as d:
        s = _get_session(d, sid)
        pw = pathways.PATHWAYS[s.pathway_slug]
        answers = s.answers
        cur_nid, cur_node = engine.resolve(pw, answers)
        if cur_node["type"] == "outcome":
            return _outcome_response(d, s, pw, cur_nid, cur_node)
        try:
            engine.resolve(pw, answers + [[cur_nid, req.option_idx]])
        except engine.BadAnswer as e:
            raise HTTPException(400, str(e))
        answers.append([cur_nid, req.option_idx])
        s.answers = answers
        d.commit()
        nid, node = engine.resolve(pw, answers)
        if node["type"] == "outcome":
            return _outcome_response(d, s, pw, nid, node)
        return {"session_id": sid, **_node_view(pw, nid, node)}


def _outcome_response(d, s: db.Session, pw: dict, nid: str, node: dict) -> dict:
    rep = d.query(db.Report).filter_by(session_id=s.id).one_or_none()
    if not rep:
        md = edges.report(pathway=pw, complaint_deid=s.complaint_deid,
                          trail=engine.trail(pw, s.answers), outcome=node)
        rep = db.Report(id=str(uuid.uuid4()), session_id=s.id, markdown=md)
        d.add(rep)
        d.commit()
        db.log(d, "report_generate", s.id, detail=f"report={rep.id}")
    return {"session_id": s.id, "report_id": rep.id, **_node_view(pw, nid, node)}


# ---------- doctor API ----------
@app.get("/api/report/{rid}")
def get_report(rid: str, request: Request):
    if request.headers.get("x-doctor-key") != DOCTOR_KEY:
        raise HTTPException(401, "geçersiz hekim anahtarı")
    with db.SessionLocal() as d:
        rep = d.get(db.Report, rid)
        if not rep:
            raise HTTPException(404, "rapor bulunamadı")
        db.log(d, "report_access", rep.session_id, actor="doctor", detail=f"report={rid}")
        return {"id": rep.id, "session_id": rep.session_id, "created_at": rep.created_at,
                "markdown": rep.markdown}


# ---------- static ----------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/doctor")
def doctor():
    return FileResponse(os.path.join(STATIC, "doctor.html"))


@app.get("/health")
def health():
    return {"ok": True, "pathways": list(pathways.PATHWAYS)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
