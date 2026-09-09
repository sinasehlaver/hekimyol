web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
release: python -c "from app.db import init_db; from app.pathways import load; init_db(); load(); print('release ok')"
