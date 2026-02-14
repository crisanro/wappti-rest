from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# Get the connection string from environment variables
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# Create the engine that communicates with PostgreSQL
# We add pool_pre_ping=True to help recover from dropped connections
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    pool_pre_ping=True
)

# Configure the session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for our models to inherit from
Base = declarative_base()

# Dependency to get a database session for each request
def get_db():
    """ Provides a database session and ensures it's closed after use. """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()