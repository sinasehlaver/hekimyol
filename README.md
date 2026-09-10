# hekimyol

Patient-facing Turkish symptom intake → **deterministic** triage-pathway engine →
patient recommendation + a structured pre-consult report for the doctor.

Navigation is a pure state machine over hand-authored clinical pathways
(`pathways/*.yaml`). An LLM is only ever touched at the edges (de-identification,
symptom routing, report prose) and those edges are stubbed for the MVP — see
`app/llm.py`. Identifiable free text is de-identified before it is stored or could
leave the region (KVKK Art. 9). Every doctor read of a report is audit-logged
(KVKK Art. 6(4)).

**Auth model.** The doctor console needs no key: knowing the report id (typed, or
scanned from the patient's QR code) is the credential, and every read still writes a
`report_access` audit row. The flow editor (`/admin`, `/api/admin/*`) is behind a
password login — `POST /api/admin/login` checks `ADMIN_PASSWORD` (default
`dev-admin-pass`) and sets a signed HttpOnly `hk_admin` cookie (HMAC-SHA256 over the
issue time with `ADMIN_SECRET`, 12-hour lifetime, stdlib only — no extra dependency).
`/admin` redirects to `/admin/login` without a valid cookie.

The intake asks no consent checkboxes — starting the flow is the consent act, and
`POST /api/session` accepts an optional `consent` object but never requires it. The
de-identification boundary and every audit row (including the `consent` row, written
as `implied` when no object is sent) are unchanged.

## Run locally

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
ADMIN_PASSWORD=dev ./venv/bin/uvicorn app.main:app --reload
# http://127.0.0.1:8000/         patient widget
# http://127.0.0.1:8000/doctor   doctor console  (no key; ?rid=<id> prefills, QR scan)
# http://127.0.0.1:8000/admin    flow editor     (password: dev)
```

Default DB is `sqlite:///hekimyol.db`. Set `DATABASE_URL` for Postgres. Every env
var has a safe dev default in code — copy `.env.example` to `.env` only if you want
to override them. No secret is committed to the repo.

## Verify (one entrypoint)

```bash
scripts/verify.sh
```

Boots the app on a free port against a throwaway SQLite DB, checks a session starts
with no `consent` field in the body, runs a full chest-pain flow to an ACİL outcome
over the HTTP API, checks PII is scrubbed from the stored report, that the report
endpoint serves it with no auth header, that the admin API rejects a missing/wrong
password and accepts the `hk_admin` cookie, and that the KVKK audit rows (including
`report_access`) were written.

## Hekim konsolu (`/doctor`)

Rapor kodunu elle girin, `/doctor?rid=<kod>` bağlantısını açın ya da **QR ile tara**
düğmesiyle hastanın QR kodunu okutun. QR okuma
[html5-qrcode](https://github.com/mebjas/html5-qrcode) (Apache-2.0) ile jsDelivr
CDN'den yüklenir — npm/derleme adımı yok:

```
https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js
```

Kamera açılamazsa (izin yok, cihaz yok, güvensiz köken) panel bir uyarı gösterir ve
fotoğraf yükleme seçeneği çalışmaya devam eder (`Html5Qrcode.scanFile`). Okunan
değer bir URL ise `rid` parametresi (yoksa son yol parçası) alınır; değilse metin
doğrudan rapor kodu sayılır.

## Akış editörü (`/admin`)

Triyaj akışlarını YAML'i elle düzenlemeden, görsel bir düğüm grafiği olarak
görüntüleyip düzenlemek için. `http://127.0.0.1:8000/admin` — geçerli oturum yoksa
`/admin/login` sayfasına yönlendirir; `ADMIN_PASSWORD` ile giriş yapıldığında sunucu
imzalı, HttpOnly bir `hk_admin` çerezi verir (12 saat) ve `/api/admin/*` bu çerezi
arar. Hekim rapor görüntüleme artık anahtar istemez.

Grafik editörü [Drawflow](https://github.com/jerosoler/drawflow) (MIT), jsDelivr
CDN'den yüklenir — derleme adımı, framework, bundler yok:

```
https://cdn.jsdelivr.net/npm/drawflow@0.0.60/dist/drawflow.min.js
https://cdn.jsdelivr.net/npm/drawflow@0.0.60/dist/drawflow.min.css
```

Eşleme: **soru** düğümü = 1 giriş + her seçenek için bir çıkış portu (çıkış *i* →
o seçeneğin `next`'i), **sonuç** düğümü = 1 giriş, 0 çıkış. Araç çubuğundan düğüm
ekleyip silebilir, seçili düğümü "Başlangıç yap" ile `start` olarak
işaretleyebilir, sağ paneldeki formdan metin/seçenek/aciliyet/bölüm/`bring`/
`differential` alanlarını düzenleyebilirsiniz.

**Kaydetme güvencesi** — üçü birden gerekir:

1. geçerli yönetici oturumu — `hk_admin` çerezi (aksi hâlde 401),
2. onay kutusuna akış adının (`chest_pain` gibi) **birebir** yazılması,
3. sunucunun `pathways.validate()` ile aynı açılış doğrulamasını çalıştırması —
   geçmezse **400** ve dosyaya hiçbir şey yazılmaz. (Bağlanmamış bir seçenek, var
   olmayan bir `next`, hatalı `urgency` → hepsi burada durur.)

Geçerli bir kayıtta önce mevcut dosyanın zaman damgalı yedeği
`pathway_backups/<slug>.<UTC>.yaml` altına alınır, sonra dosya yazılır ve
`pathways.load()` ile sıcak yeniden yüklenir; yükleme patlarsa dosya yedekten geri
alınır ve 500 döner. Her düzenleme `pathway_edit` denetim kaydı yazar.

Düğümlerin tuval konumları klinik dosyaya **yazılmaz**; `pathway_layouts/<slug>.json`
kenar dosyasında tutulur (yoksa başlangıç düğümünden BFS ile otomatik yerleşim).
`pathway_backups/` ve `pathway_layouts/` `pathways/*.yaml` glob'una takılmaz ve
git'e girmez.

> Editör yeni akış **oluşturmaz/silmez** — dört akış sabittir.
> Kaydetme `yaml.safe_dump` kullanır: veri birebir korunur ama dosyadaki
> `# TASLAK` yorum satırları ve elle verilmiş satır kırılmaları kaybolur.
> Yorumu korumak gerekiyorsa `source:` alanına taşıyın.

## Test

```bash
./venv/bin/pip install -r requirements-dev.txt   # httpx — TestClient için
./venv/bin/python -m unittest discover -s tests
./venv/bin/python -m app.engine   # engine self-check
./venv/bin/python -m app.llm      # de-id / router self-check
```

## Push to GitHub

```bash
gh repo create hekimyol --private --source=. --remote=origin --push
# or, if the repo already exists:
git remote add origin git@github.com:sinasehlaver/hekimyol.git
git push -u origin main
```

## Deploy (Heroku)

```bash
heroku create hekimyol
heroku addons:create heroku-postgresql:essential-0
heroku config:set ADMIN_PASSWORD="$(openssl rand -hex 12)" \
                  ADMIN_SECRET="$(openssl rand -hex 32)" HEKIMYOL_LLM=stub
git push heroku main
heroku open
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
