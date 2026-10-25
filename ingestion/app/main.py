from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session


from app.database import get_db
from app.services.earthquake_ingestion import EarthquakeIngestionService

app = FastAPI()


@app.get("/")
def health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))

    return {
        "status": "Connected to DB"
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "healthy", "database": "connected"}


@app.post("/ingestion/earthquakes/run")
def ingest_earthquakes(db: Session = Depends(get_db)):
    service = EarthquakeIngestionService(db)
    try:
        return service.run_all()
    finally:
        service.close()
