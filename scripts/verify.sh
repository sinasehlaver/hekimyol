#!/usr/bin/env bash
# Tek giriş noktası: boş bir SQLite DB + boş portta uygulamayı ayağa kaldır,
# uçtan uca bir triyaj akışını API üzerinden yürüt (rıza alanı olmadan da başlamalı),
# hekim raporunu anahtarsız çek (rapor kodu yeterli; erişim yine loglanır),
# akış editörünün parola/çerez oturumunu ve KVKK denetim kaydını doğrula.
# Ağ yok, LLM yok (stub).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=./venv/bin/python
[ -x "$PY" ] || { echo "venv yok — kurulum:"; echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"; exit 1; }

PORT="$(../.claude/scripts/freeport.sh)"
TMP="$(mktemp -d)"
trap 'kill ${SRV:-0} 2>/dev/null || true; rm -rf "$TMP"' EXIT

export DATABASE_URL="sqlite:///$TMP/verify.db"
export ADMIN_PASSWORD="verify-admin"
export HEKIMYOL_LLM="stub"

./venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SRV=$!

for _ in $(seq 1 50); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null && break
  sleep 0.2
done

BASE="http://127.0.0.1:$PORT"
"$PY" - "$BASE" <<'PY'
import json, sys, urllib.request

base = sys.argv[1]

def raw(path, body=None, headers=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                method=method or ("POST" if data else "GET"),
                                headers={"content-type": "application/json", **(headers or {})})
    return urllib.request.urlopen(req)


def call(path, body=None, headers=None, method=None):
    with raw(path, body, headers, method) as r:
        return json.load(r)


def status(path, body=None, headers=None, method=None):
    """HTTP status code only — expects a 4xx without raising."""
    try:
        with raw(path, body, headers, method) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

# 1. no consent field at all — intake still starts (rıza artık zımnî, UI'da kutu yok)
d0 = call("/api/session", {"complaint": "göğsüm ağrıyor"})
assert d0.get("session_id"), d0
assert d0.get("node_id"), d0

# 2. full chest-pain flow to an ACİL outcome
d = call("/api/session", {"complaint": "2 saattir göğsümde baskı var, TC 10000000146, tel 0555 444 33 22"})
assert d["node_id"] == "q_now", d
sid = d["session_id"]
d = call(f"/api/session/{sid}/answer", {"option_idx": 0})   # q_now: evet
assert d["node_id"] == "q_features", d
d = call(f"/api/session/{sid}/answer", {"option_idx": 0})   # q_features: en az biri var
assert d["done"] and d["urgency"] == "acil", d
assert d["department"] == "Acil Servis", d
rid = d["report_id"]

# 3. the report is readable with no auth at all — the report id IS the credential
#    (the KVKK report_access row is still written; asserted from the DB below)
rep = call(f"/api/report/{rid}")
assert rep.get("markdown"), rep

# 4. PII must be scrubbed from the stored report
md = rep["markdown"]
assert "[TCKN]" in md and "[TELEFON]" in md, "PII rapora sızmış"
assert "10000000146" not in md and "444 33 22" not in md, "ham PII rapora sızmış"
assert "Acil Servis" in md and "Kaynak" in md, md[:200]

# 5. admin editor auth: password login -> signed HttpOnly hk_admin cookie
assert status("/api/admin/pathways") == 401, "editör oturumsuz açıldı"
assert status("/api/admin/login", {"password": "yanlis"}) == 401, "yanlış parola kabul edildi"
with raw("/api/admin/login", {"password": "verify-admin"}) as r:
    assert r.status == 200, r.status
    setc = r.headers.get_all("Set-Cookie") or []
cookie = next((c.split(";")[0] for c in setc if c.startswith("hk_admin=")), None)
assert cookie, f"hk_admin çerezi yok: {setc}"
assert "HttpOnly" in "".join(setc), setc
pws = call("/api/admin/pathways", headers={"Cookie": cookie})
assert {p["slug"] for p in pws} == {"chest_pain", "headache", "abdominal_pain", "dyspnea"}, pws
assert status("/admin") in (200, 302), "admin sayfası"

# 6. pathway set is intact
h = call("/health")
assert set(h["pathways"]) == {"chest_pain", "headache", "abdominal_pain", "dyspnea"}, h

print("verify OK — akış ACİL sonuca ulaştı, PII temizlendi, "
      "rapor anahtarsız okundu, editör parola+çerez ile korunuyor")
PY

# audit log satırlarını doğrudan DB'den kontrol et
"$PY" - "$TMP/verify.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
events = [r[0] for r in c.execute("select event from audit_log order by id")]
for need in ("session_create", "consent", "report_generate", "report_access"):
    assert need in events, f"denetim kaydı eksik: {need} ({events})"
print("audit OK —", events)
PY
