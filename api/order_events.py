import json
import logging

from sqlalchemy import select
import pika

from .database import SessionLocal
from .messaging import ORDER_CREATED, connect, declare_topology, publish
from .models import Order

logger = logging.getLogger(__name__)


def publish_new_order(order_id: int):
    # O pedido pendente é a fonte do evento até o RabbitMQ confirmar a entrega.
    # O bloqueio impede que duas tentativas publiquem o mesmo pedido em paralelo.
    with SessionLocal.begin() as db:
        order = db.get(Order, order_id, with_for_update=True)
        if order.status != "PENDENTE_PUBLICACAO":
            return
        payload = {
            "pedido_id": order.id,
            "itens": json.loads(order.items_json),
            "total": str(order.total),
            "forma_pagamento": order.payment_method,
            "simular_falha": order.simulate_failure,
        }
        with connect() as connection:
            channel = connection.channel()
            declare_topology(channel)
            channel.confirm_delivery()
            publish(channel, ORDER_CREATED, payload)
        order.status = "RECEBIDO"


def retry_pending_orders(stop):
    while not stop.wait(3):
        try:
            with SessionLocal() as db:
                pending = db.scalars(
                    select(Order.id)
                    .where(Order.status == "PENDENTE_PUBLICACAO")
                    .order_by(Order.id)
                    .limit(25)
                ).all()
            for order_id in pending:
                if stop.is_set():
                    return
                publish_new_order(order_id)
        except pika.exceptions.AMQPError as error:
            logger.warning("Publicação pendente. RabbitMQ indisponível (%s).", type(error).__name__)
        except Exception:
            logger.exception("Erro ao retomar publicação de pedidos")
