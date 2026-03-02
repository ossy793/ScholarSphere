from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from .config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,   # verify connections before use
    pool_size=10,         # keep 10 persistent connections
    max_overflow=20,      # allow up to 20 extra connections under load
    pool_recycle=1800,    # recycle connections every 30 min to avoid timeout drops
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables():
    """Create all tables. Called on app startup."""
    Base.metadata.create_all(bind=engine)
