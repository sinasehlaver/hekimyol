#!/usr/bin/env bash
# start.sh — hızlıca deneme için: venv'i hazırla, uygulamayı sabit bir portta
# kalıcı bir SQLite veritabanı ile ayağa kaldır, tarayıcıda aç.
#
# Tek seferlik denemeler / demo içindir. Otomatik doğrulama için scripts/verify.sh
# kullanın (o, boş port + tek kullanımlık DB ile uçtan uca akışı test eder).
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"
export ADMIN_PASSWORD="${ADMIN_PASSWORD:-dev-admin-pass}"
export HEKIMYOL_LLM="${HEKIMYOL_LLM:-stub}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///hekimyol.db}"

# 1. venv + bağımlılıklar
if [ ! -x venv/bin/python ]; then
  echo "→ venv oluşturuluyor..."
  python3 -m venv venv
fi
if ! venv/bin/python -c "import fastapi, uvicorn, yaml, sqlalchemy" 2>/dev/null; then
  echo "→ bağımlılıklar kuruluyor..."
  venv/bin/pip install --quiet --upgrade pip
  venv/bin/pip install --quiet -r requirements.txt
fi

# 2. port boşsa devam, doluysa kullanıcıya bırak
if lsof -ti "tcp:$PORT" >/dev/null 2>&1; then
  echo "! $PORT portu dolu. Başka bir port için:  PORT=8010 ./start.sh"
  exit 1
fi

URL="http://127.0.0.1:$PORT"
cat <<EOF

  hekimyol çalışıyor
  ────────────────────────────────────────────
  Hasta arayüzü : $URL/
  Hekim konsolu : $URL/doctor      (anahtar yok — rapor kodu yeterli)
  Akış editörü  : $URL/admin       (parola: $ADMIN_PASSWORD)
  ────────────────────────────────────────────
  Durdurmak için Ctrl+C

EOF

# 3. tarayıcıyı aç (sunucu ayağa kalkınca), sonra sunucuyu ön planda çalıştır
( sleep 1.5; command -v open >/dev/null && open "$URL" || true ) &
exec venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload
