from decimal import Decimal
from socket import gaierror
from unittest import TestCase
from unittest.mock import Mock, patch

import pika
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from api.database import Base
from api.main import create_order, dashboard
from api.messaging import ORDER_CREATED, PAYMENT_APPROVED, connect
from api.models import Notification, Order
from api.schemas import NewOrder
from workers import notification_worker, payment_worker, stock_worker


class OrderFlowTests(TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(engine.dispose)
        self.addCleanup(self.db.close)
        self.payload = {
            "customer_name": "  Cliente  ",
            "customer_phone": "11999990000",
            "delivery_address": "Rua de Teste, 100",
            "items": [{"product_id": "burger", "quantity": 2, "unit_price": "0.01"}],
        }

    def save_order(self, **changes):
        with patch("api.main.publish_new_order"):
            result = create_order(NewOrder(**{**self.payload, **changes}), self.db)
        order = self.db.get(Order, result["pedido_id"])
        order.status = "RECEBIDO"
        self.db.commit()
        return order

    def test_uses_catalog_and_strips_input(self):
        order = self.save_order()
        self.assertEqual(order.total, Decimal("59.80"))
        self.assertEqual(order.customer_name, "Cliente")
        with self.assertRaises(ValidationError):
            NewOrder(**{**self.payload, "customer_name": "   "})
        with self.assertRaises(ValidationError):
            NewOrder(**{**self.payload, "items": [{"product_id": "inexistente", "quantity": 1}]})

    def test_broker_failure_preserves_order_for_retry(self):
        with patch("api.main.publish_new_order", side_effect=pika.exceptions.AMQPConnectionError):
            with self.assertRaises(HTTPException) as error:
                create_order(NewOrder(**self.payload), self.db)
        self.assertEqual(error.exception.status_code, 503)
        order = self.db.get(Order, error.exception.detail["pedido_id"])
        self.assertEqual(order.status, "PENDENTE_PUBLICACAO")

    def test_unresolved_broker_address_is_a_connection_error(self):
        with patch("api.messaging.pika.BlockingConnection", side_effect=gaierror):
            with self.assertRaises(pika.exceptions.AMQPConnectionError):
                with connect():
                    self.fail("Uma conexão inválida não deve ser devolvida")

    def test_redeliveries_do_not_repeat_effects(self):
        order = self.save_order()
        channel = Mock()
        with patch("workers.payment_worker.PAYMENT_DELAY", 0):
            for _ in range(2):
                payment_worker.handle(self.db, channel, order.id, ORDER_CREATED)
                self.db.commit()
        channel.basic_publish.assert_called_once()

        # A entrega pode chegar fora da ordem quando há mais de um consumidor.
        for event in (PAYMENT_APPROVED, PAYMENT_APPROVED, ORDER_CREATED):
            notification_worker.handle(self.db, channel, order.id, event)
            self.db.commit()
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Notification)), 2)
        self.assertEqual(order.current_status(), "EM_PROCESSAMENTO")
        self.assertEqual(dashboard(self.db)["processing"], 1)

        stock_worker.handle(self.db, channel, order.id, ORDER_CREATED)
        self.db.commit()
        self.assertEqual(order.status, "CONCLUIDO")
        self.assertEqual(dashboard(self.db)["processing"], 0)

    def test_legacy_received_order_uses_steps_to_get_status(self):
        order = self.save_order()
        order.payment_status = "APROVADO"
        order.stock_status = "SEPARADO"
        order.notification_status = "ENVIADA"
        self.db.commit()
        self.assertEqual(order.current_status(), "CONCLUIDO")
        self.assertEqual(dashboard(self.db)["processing"], 0)
