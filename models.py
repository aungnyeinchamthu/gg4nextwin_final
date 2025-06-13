# models.py
from sqlalchemy import Column, BigInteger, String, Numeric, TIMESTAMP, text, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from database import Base

class User(Base):
    __tablename__ = "users"
    user_id = Column(BigInteger, primary_key=True)
    telegram_username = Column(String(255), nullable=True)
    rank = Column(String(50), nullable=False, server_default='Bronze')
    cumulative_deposit = Column(Numeric(15, 2), nullable=False, server_default=text('0.00'))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('now()'))
    transactions = relationship("Transaction", back_populates="user")

class Transaction(Base):
    __tablename__ = "transactions"
    transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey('users.user_id'), nullable=False)
    request_id = Column(String(20), nullable=False, unique=True)
    type = Column(String(50), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    status = Column(String(50), nullable=False, server_default='pending')
    admin_id = Column(BigInteger, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    admin_chat_id = Column(BigInteger, nullable=True)
    admin_message_id = Column(Integer, nullable=True)
    
    # --- NEW FIELD ---
    xbet_id_from_user = Column(String(255), nullable=True)
    
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('now()'))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('now()'))
    user = relationship("User", back_populates="transactions")
    