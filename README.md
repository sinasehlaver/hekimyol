# hekimyol

Patient-facing Turkish symptom intake → **deterministic** triage-pathway engine →
patient recommendation + a structured pre-consult report for the doctor.

Navigation is a pure state machine over hand-authored clinical pathways
(`pathways/*.yaml`). An LLM is only ever touched at the edges (de-identification,
symptom routing, report prose) and those edges are stubbed for the MVP — see
`app/llm.py`. Identifiable free text is de-identified before it is stored or could
leave the region (KVKK Art. 9). Every doctor read of a report is audit-logged
(KVKK Art. 6(4)).

## Run locally

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
DOCTOR_KEY=dev ./venv/bin/uvicorn app.main:app --reload
# http://127.0.0.1:8000/         patient widget
# http://127.0.0.1:8000/doctor   doctor console  (key: dev)
```

Default DB is `sqlite:///hekimyol.db`. Set `DATABASE_URL` for Postgres.

## Verify (one entrypoint)

```bash
scripts/verify.sh
```

Boots the app on a free port against a throwaway SQLite DB, runs a full chest-pain
flow to an ACİL outcome over the HTTP API, checks PII is scrubbed from the stored
report, that the doctor endpoint rejects a missing key, and that the KVKK audit
rows were written.

## Test

```bash
./venv/bin/python -m unittest discover -s tests
./venv/bin/python -m app.engine   # engine self-check
./venv/bin/python -m app.llm      # de-id / router self-check
```

## Deploy (Heroku)

```bash
heroku create hekimyol
heroku addons:create heroku-postgresql:essential-0
heroku config:set DOCTOR_KEY="$(openssl rand -hex 16)" HEKIMYOL_LLM=stub
git push heroku main
```

`Procfile` runs uvicorn bound to `$PORT`; the `release` phase creates tables and
validates the pathway YAML on every deploy. `app.json` declares the Postgres addon
and config vars for review apps.

## Pathways shipped

`chest_pain`, `headache`, `abdominal_pain`, `dyspnea` — all marked **TASLAK
(draft)**; they encode red-flag routing to `acil` / `24_saat` / `planli` but must
be reviewed against Turkish specialty-society guidelines + a physician before any
real patient use.

## What's stubbed / deferred

- **LLM edges** — deterministic stubs (regex de-id, keyword router, template
  report). Swap `StubEdges` in `app/llm.py` for a self-hosted in-region model, or a
  frontier API on the de-identified JSON once a KVKK standard contract is on file.
- **Name de-identification** — regex catches TCKN / phone / e-mail / dates, not
  person names. Add a TR NER pass before real traffic.
- **Voice intake** — text only for now.
- **Schema migrations** — `create_all`, no Alembic yet.
- **KVKK region** — Heroku's EU region is still outside Turkey; fine for a
  friendly-network pilot, not for GA. The de-id boundary is the thing that makes
  that survivable in the meantime.
