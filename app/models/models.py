"""
Database models for the Network Monitoring System.
"""
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index, Enum, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class ProviderStatus(str, PyEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class CheckType(str, PyEnum):
    AUTO = "auto"
    PING = "ping"
    TCP = "tcp"
    HTTP = "http"
    DNS = "dns"


class Provider(Base):
    """Represents a network provider/host to monitor."""
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    ip_address = Column(String(45), nullable=False, index=True)  # IPv6 compatible
    description = Column(String(500), nullable=True)
    group_name = Column(String(100), nullable=True, index=True)
    current_status = Column(Enum(ProviderStatus), default=ProviderStatus.ONLINE)
    check_type = Column(Enum(CheckType), default=CheckType.AUTO, nullable=False)
    check_port = Column(Integer, nullable=True)
    check_path = Column(String(255), nullable=True)
    dns_expected_value = Column(String(255), nullable=True)
    maintenance_mode = Column(Integer, default=0)
    maintenance_note = Column(String(255), nullable=True)
    maintenance_started_at = Column(DateTime, nullable=True)
    maintenance_window_start = Column(DateTime, nullable=True)
    maintenance_window_end = Column(DateTime, nullable=True)
    offline_since = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0)
    last_checked = Column(DateTime, nullable=True)
    response_time = Column(Integer, nullable=True)  # in milliseconds
    last_check_method = Column(String(50), nullable=True)
    last_error = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    status_logs = relationship("StatusLog", back_populates="provider", cascade="all, delete-orphan")
    alert_logs = relationship("AlertLog", back_populates="provider", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Provider(id={self.id}, name='{self.name}', ip='{self.ip_address}', status='{self.current_status}')>"


class StatusLog(Base):
    """Logs the status of providers over time."""
    __tablename__ = "status_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    status = Column(Enum(ProviderStatus), nullable=False)
    response_time = Column(Integer, nullable=True)  # in milliseconds
    check_method = Column(String(50), nullable=True)
    details = Column(String(500), nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider = relationship("Provider", back_populates="status_logs")

    # Composite index for efficient history queries
    __table_args__ = (
        Index('idx_status_logs_provider_timestamp', 'provider_id', 'timestamp'),
    )

    def __repr__(self):
        return f"<StatusLog(id={self.id}, provider_id={self.provider_id}, status='{self.status}')>"


class AlertLog(Base):
    """Logs sent alerts for audit and history purposes."""
    __tablename__ = "alert_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    status_change = Column(String(50), nullable=False)  # "up" or "down"
    message = Column(String(1000), nullable=True)
    sent_at = Column(DateTime, default=datetime.utcnow, index=True)

    # Relationships
    provider = relationship("Provider", back_populates="alert_logs")

    def __repr__(self):
        return f"<AlertLog(id={self.id}, provider_id={self.provider_id}, change='{self.status_change}')>"


class User(Base):
    """User accounts for authentication."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Integer, default=1)  # SQLite doesn't have native boolean
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}')>"


class AppSetting(Base):
    """Simple key-value application settings storage."""
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AppSetting(key='{self.key}')>"
