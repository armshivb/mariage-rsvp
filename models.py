from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Setting(Base):
    __tablename__ = "settings"
    key   = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class Guest(Base):
    __tablename__ = "guests"

    id = Column(Integer, primary_key=True, index=True)
    prenom = Column(String, nullable=False, default="")
    nom = Column(String, nullable=False, default="")
    code = Column(String, unique=True, nullable=False, index=True)
    response = Column(String, default="pending")  # pending / yes / no
    plus_one = Column(Integer, default=0)          # nombre d'accompagnants
    message = Column(Text, default="")
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
