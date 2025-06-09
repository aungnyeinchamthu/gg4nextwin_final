import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# Load the database URL from Railway's environment variables
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("No DATABASE_URL environment variable set")

# Create the async engine for connecting to the database
engine = create_async_engine(DATABASE_URL)

# Create a configured "Session" class
# autocommit=False and autoflush=False are standard for async sessions
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
)

# This is the base class our ORM models will inherit from
Base = declarative_base()

# Dependency to get a DB session in our application
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session