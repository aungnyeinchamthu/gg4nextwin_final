import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # In a real app, you'd raise an error, but for now, we'll allow it to run without a DB if not set.
    print("WARNING: DATABASE_URL is not set. Database features will fail.")
    engine = None
    AsyncSessionLocal = None
else:
    engine = create_async_engine(DATABASE_URL)
    AsyncSessionLocal = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
    )

Base = declarative_base()