#!/usr/bin/env bash
# Tek giriş noktası: boş bir SQLite DB + boş portta uygulamayı ayağa kaldır,
# uçtan uca bir triyaj akışını API üzerinden yürüt, hekim raporunu anahtarla çek,
# KVKK denetim kaydının yazıldığını doğrula. Ağ yok, LLM yok (stub).
set -euo pipefail
cd "$(dirname "$0")/.."

PY=./venv/bin/python
[ -x "$PY" ] || { echo "venv yok — kurulum:"; echo "  python3 -m venv venv && ./venv/bin/pip install -r requirements.txt"; exit 1; }

PORT="$(../.claude/scripts/freeport.sh)"
TMP="$(mktemp -d)"
trap 'kill ${SRV:-0} 2>/dev/null || true; rm -rf "$TMP"' EXIT

export DATABASE_URL="sqlite:///$TMP/verify.db"
export DOCTOR_KEY="verify-key"
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

def call(path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if data else "GET",
                                headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

# 1. consent required
try:
    call("/api/session", {"consent": {"health_data": True, "store_report": False},
                          "complaint": "göğsüm ağrıyor"})
    raise SystemExit("FAIL: eksik rıza kabul edildi")
except urllib.error.HTTPError as e:
    assert e.code == 400, e.code

# 2. full chest-pain flow to an ACİL outcome
d = call("/api/session", {"consent": {"health_data": True, "store_report": True},
                          "complaint": "2 saattir göğsümde baskı var, TC 10000000146, tel 0555 444 33 22"})
assert d["node_id"] == "q_now", d
sid = d["session_id"]
d = call(f"/api/session/{sid}/answer", {"option_idx": 0})   # q_now: evet
assert d["node_id"] == "q_features", d
d = call(f"/api/session/{sid}/answer", {"option_idx": 0})   # q_features: en az biri var
assert d["done"] and d["urgency"] == "acil", d
assert d["department"] == "Acil Servis", d
rid = d["report_id"]

# 3. doctor cannot read without key
try:
    call(f"/api/report/{rid}")
    raise SystemExit("FAIL: anahtarsız rapor okundu")
except urllib.error.HTTPError as e:
    assert e.code == 401, e.code

# 4. doctor reads with key; PII must be scrubbed from the stored report
rep = call(f"/api/report/{rid}", headers={"x-doctor-key": "verify-key"})
md = rep["markdown"]
assert "[TCKN]" in md and "[TELEFON]" in md, "PII rapora sızmış"
assert "10000000146" not in md and "444 33 22" not in md, "ham PII rapora sızmış"
assert "Acil Servis" in md and "Kaynak" in md, md[:200]

# 5. audit log has the mandatory KVKK entries
h = call("/health")
assert set(h["pathways"]) == {"chest_pain", "headache", "abdominal_pain", "dyspnea"}, h

print("verify OK — akış ACİL sonuca ulaştı, PII temizlendi, rapor anahtarla çekildi")
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
