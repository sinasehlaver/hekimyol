"""hekimyol — patient-facing Turkish symptom intake → deterministic triage → doctor pre-report.

Flow: free-text complaint → de-identify (network boundary) → route to a pathway →
deterministic Q&A → patient recommendation + a stored pre-consult report the doctor
reads by report id (no key — every read is still audit-logged).

Auth split: the report view is open (knowing the report id is the credential); the
flow editor (`/admin`, `/api/admin/*`) is behind a password login that sets a signed
HttpOnly `hk_admin` cookie (stdlib HMAC, no extra dependency).

Consent is implied by starting the intake — there is no checkbox gate in the UI and
none server-side — but the de-identification boundary and the KVKK audit rows
(`consent`, `session_create`, `report_generate`, `report_access`) are unchanged.
"""
import base64
import hashlib
import hmac
import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, engine, llm, pathways
from .starting_points import STARTING_POINTS

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "dev-admin-pass")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "dev-admin-secret-change-me")
ADMIN_COOKIE = "hk_admin"
ADMIN_TTL = 12 * 3600
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
    consent: Consent | None = None  # optional: intake is implied-consent, UI has no gate
    complaint: str
    pathway_slug: str | None = None  # patient's pick when routing is ambiguous


class AnswerReq(BaseModel):
    option_idx: int


class PathwaySaveReq(BaseModel):
    parsed: dict
    layout: dict = {}
    confirm: str = ""


class AdminLoginReq(BaseModel):
    password: str = ""


# ---------- helpers ----------
def _node_view(pw: dict, nid: str, node: dict) -> dict:
    if node["type"] == "question":
        return {"done": False, "node_id": nid, "text": node["text"],
                "options": [o["label"] for o in node["options"]]}
    return {"done": True, "node_id": nid, "urgency": node["urgency"],
            "department": node["department"], "advice": node["patient_advice"],
            "bring": node.get("bring", []), "differential": node.get("differential", [])}


# ---------- admin session cookie (stdlib HMAC — no extra dependency) ----------
def _sign(payload: str) -> str:
    mac = hmac.new(ADMIN_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def _make_token() -> str:
    issued_at = str(int(time.time()))
    return f"{issued_at}.{_sign(issued_at)}"


def _valid_token(tok: str | None) -> bool:
    if not tok or "." not in tok:
        return False
    issued_at, _, sig = tok.partition(".")
    if not hmac.compare_digest(sig, _sign(issued_at)):
        return False
    try:
        age = time.time() - int(issued_at)
    except ValueError:
        return False
    return -60 <= age <= ADMIN_TTL   # -60: tolerate a little clock skew


def _require_admin(request: Request) -> None:
    if not _valid_token(request.cookies.get(ADMIN_COOKIE)):
        raise HTTPException(401, "oturum gerekli")


def _get_session(d, sid: str) -> db.Session:
    s = d.get(db.Session, sid)
    if not s:
        raise HTTPException(404, "oturum bulunamadı")
    return s


# ---------- patient API ----------
@app.get("/api/meta")
def meta():
    return {"disclaimer": DISCLAIMER, "pathways": pathways.summaries(),
            "starting_points": STARTING_POINTS}


@app.post("/api/session")
def start(req: StartReq):
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
        # KVKK Art. 6(4) access log — kept even though the UI no longer asks.
        consent_detail = ("health_data=1 store_report=1"
                          if req.consent and req.consent.health_data and req.consent.store_report
                          else "implied")
        db.log(d, "consent", sid, detail=consent_detail)
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
# No auth: the report id (a UUID handed to the patient / encoded in the QR) is the
# credential. The KVKK Art. 6(4) access row is still written on every read.
@app.get("/api/report/{rid}")
def get_report(rid: str, request: Request):
    with db.SessionLocal() as d:
        rep = d.get(db.Report, rid)
        if not rep:
            raise HTTPException(404, "rapor bulunamadı")
        db.log(d, "report_access", rep.session_id, actor="doctor", detail=f"report={rid}")
        return {"id": rep.id, "session_id": rep.session_id, "created_at": rep.created_at,
                "markdown": rep.markdown}


# ---------- admin auth ----------
@app.post("/api/admin/login")
def admin_login(req: AdminLoginReq, response: Response):
    if not hmac.compare_digest((req.password or "").encode(), ADMIN_PASSWORD.encode()):
        raise HTTPException(401, "geçersiz parola")
    response.set_cookie(ADMIN_COOKIE, _make_token(), httponly=True,
                        samesite="lax", path="/", max_age=ADMIN_TTL)
    return {"ok": True}


@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


# ---------- admin API (flowchart editor) ----------
# Password login → signed HttpOnly cookie: MVP, not RBAC. The write path has a
# second, independent safeguard (typed slug + server-side validation + backup).
@app.get("/api/admin/pathways")
def admin_list(request: Request):
    _require_admin(request)
    return [{"slug": s, "title": p["title"], "version": p.get("version"),
             "node_count": len(p["nodes"])}
            for s, p in sorted(pathways.PATHWAYS.items())]


@app.get("/api/admin/pathway/{slug}")
def admin_get(slug: str, request: Request):
    _require_admin(request)
    if slug not in pathways.PATHWAYS:
        raise HTTPException(404, f"akış bulunamadı: {slug}")
    return {"parsed": pathways.PATHWAYS[slug], "raw": pathways.raw(slug),
            "layout": pathways.read_layout(slug)}


@app.put("/api/admin/pathway/{slug}")
def admin_put(slug: str, req: PathwaySaveReq, request: Request):
    _require_admin(request)
    # (1) the slug must already exist — the editor never creates or deletes pathways.
    if slug not in pathways.PATHWAYS:
        raise HTTPException(404, f"akış bulunamadı: {slug}")
    # (2) typed-slug confirmation, exact match.
    if req.confirm != slug:
        raise HTTPException(400, f"Onay metni eşleşmiyor — '{slug}' yazmalısınız.")
    # (3) the same validator that guards startup. A broken graph is never written.
    try:
        pathways.validate(slug, req.parsed)
    except AssertionError as e:
        raise HTTPException(400, f"Akış geçersiz: {e}")
    except Exception as e:  # malformed structure (wrong types, missing dicts, ...)
        raise HTTPException(400, f"Akış geçersiz: {type(e).__name__}: {e}")

    target = pathways.path(slug)
    bak = pathways.backup(slug)
    target.write_text(pathways.dump(req.parsed), encoding="utf-8")
    try:
        pathways.load()  # hot-reload; re-validates every file on disk
    except Exception as e:
        target.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
        pathways.load()
        raise HTTPException(500, f"Yeniden yükleme başarısız, dosya geri alındı: {e}")

    pathways.write_layout(slug, req.layout or {})
    with db.SessionLocal() as d:
        db.log(d, "pathway_edit", actor="doctor",
               detail=f"pathway={slug} nodes={len(req.parsed['nodes'])} backup={bak.name}")
    return {"ok": True, "backup": bak.name, "parsed": pathways.PATHWAYS[slug],
            "raw": pathways.raw(slug), "layout": pathways.read_layout(slug)}


# ---------- static ----------
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/doctor")
def doctor():
    return FileResponse(os.path.join(STATIC, "doctor.html"))


@app.get("/admin")
def admin(request: Request):
    if not _valid_token(request.cookies.get(ADMIN_COOKIE)):
        return RedirectResponse("/admin/login", status_code=302)
    return FileResponse(os.path.join(STATIC, "admin.html"))


@app.get("/admin/login")
def admin_login_page():
    return FileResponse(os.path.join(STATIC, "admin-login.html"))


@app.get("/health")
def health():
    return {"ok": True, "pathways": list(pathways.PATHWAYS)}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
