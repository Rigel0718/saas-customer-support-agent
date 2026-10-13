from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (CheckConstraint("member_limit >= 0", name="member_limit_nonnegative"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    member_limit: Mapped[int]
    users: Mapped[list["User"]] = relationship(back_populates="organization", passive_deletes="all")
    subscription: Mapped["Subscription | None"] = relationship(back_populates="organization", passive_deletes="all")
    payments: Mapped[list["Payment"]] = relationship(back_populates="organization", passive_deletes="all")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner','admin','member')", name="user_role"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    role: Mapped[str] = mapped_column(String)
    organization: Mapped[Organization] = relationship(back_populates="users")


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint("plan IN ('free','pro','enterprise')", name="subscription_plan"),
        CheckConstraint("status IN ('active','past_due','expired','canceled')", name="subscription_status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), unique=True)
    plan: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization: Mapped[Organization] = relationship(back_populates="subscription")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("status IN ('succeeded','failed','pending')", name="payment_status"),
        CheckConstraint("(status = 'succeeded' AND paid_at IS NOT NULL) OR (status IN ('failed','pending') AND paid_at IS NULL)", name="payment_paid_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    organization: Mapped[Organization] = relationship(back_populates="payments")
