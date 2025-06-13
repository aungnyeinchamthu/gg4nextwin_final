import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

# --- TEMPORARY DEBUGGING STEP ---
# PASTE YOUR FULL DATABASE URL FROM RAILWAY INSIDE THE QUOTES BELOW
DATABASE_URL = "postgresql://postgres:fUvnhBOYsgxpUcOZFELemOekEtSsMAxU@hopper.proxy.rlwy.net:20186/railway"


# We are temporarily bypassing the environment variable check to test the connection
if DATABASE_URL == "postgresql://postgres:fUvnhBOYsgxpUcOZFELemOekEtSsMAxU@hopper.proxy.rlwy.net:20186/railway":
    raise ValueError("You forgot to paste the Database URL into the database.py file.")

engine = create_async_engine(DATABASE_URL)

AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False
)

Base = declarative_base()