from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def order_status(status, payment, stock, notification):
    if status in {"PENDENTE_PUBLICACAO", "ERRO_PUBLICACAO"}:
        return status
    if payment == "FALHA_TECNICA":
        return "FALHA_TECNICA"
    if notification == "ENVIADA":
        if payment == "RECUSADO":
            return "RECUSADO"
        if payment == "APROVADO" and stock == "SEPARADO":
            return "CONCLUIDO"
    return "EM_PROCESSAMENTO"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(30))
    delivery_address: Mapped[str] = mapped_column(String(255))
    items_json: Mapped[str] = mapped_column(Text)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    payment_method: Mapped[str] = mapped_column(String(40))
    simulate_failure: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="PENDENTE_PUBLICACAO")
    payment_status: Mapped[str] = mapped_column(String(40), default="AGUARDANDO")
    stock_status: Mapped[str] = mapped_column(String(40), default="AGUARDANDO")
    notification_status: Mapped[str] = mapped_column(String(40), default="AGUARDANDO")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    def current_status(self):
        return order_status(
            self.status, self.payment_status, self.stock_status, self.notification_status
        )


class Notification(Base):
    __tablename__ = "notifications"

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    event: Mapped[str] = mapped_column(String(60), primary_key=True)
    message: Mapped[str] = mapped_column(String(255))
