import json
import os
from contextlib import contextmanager

import pika

from . import database  # Carrega a configuração local antes de abrir conexões.


EVENTS_EXCHANGE = "rapideiz.events"
DEAD_LETTER_EXCHANGE = "rapideiz.dlx"
ORDER_CREATED = "pedido.criado"
PAYMENT_APPROVED = "pagamento.aprovado"
PAYMENT_REFUSED = "pagamento.recusado"
QUEUES = {
    "payments": "rapideiz.payments",
    "stock": "rapideiz.stock",
    "notifications": "rapideiz.notifications",
}


@contextmanager
def connect():
    parameters = pika.ConnectionParameters(
        host=os.getenv("RABBITMQ_HOST", "127.0.0.1"),
        port=int(os.getenv("RABBITMQ_PORT", "5672")),
        virtual_host=os.getenv("RABBITMQ_VHOST", "/"),
        credentials=pika.PlainCredentials(
            os.getenv("RABBITMQ_USER", "rapideiz"),
            os.getenv("RABBITMQ_PASSWORD", "rapideiz123"),
        ),
        heartbeat=30,
        socket_timeout=3,
        stack_timeout=5,
        blocked_connection_timeout=5,
    )
    try:
        connection = pika.BlockingConnection(parameters)
    except OSError as error:
        # A parada do serviço Docker também pode tornar seu nome inacessível no DNS.
        raise pika.exceptions.AMQPConnectionError("RabbitMQ indisponível") from error
    try:
        yield connection
    finally:
        if connection.is_open:
            try:
                connection.close()
            except (pika.exceptions.AMQPError, OSError):
                pass


def declare_topology(channel):
    channel.exchange_declare(exchange=EVENTS_EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(exchange=DEAD_LETTER_EXCHANGE, exchange_type="direct", durable=True)

    for name, queue in QUEUES.items():
        dead_key = f"{name}.dlq"
        channel.queue_declare(queue=f"{queue}.dlq", durable=True)
        channel.queue_bind(
            queue=f"{queue}.dlq", exchange=DEAD_LETTER_EXCHANGE, routing_key=dead_key
        )
        channel.queue_declare(
            queue=queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
                "x-dead-letter-routing-key": dead_key,
            },
        )
        channel.queue_bind(queue=queue, exchange=EVENTS_EXCHANGE, routing_key=ORDER_CREATED)

    channel.queue_bind(
        queue=QUEUES["notifications"], exchange=EVENTS_EXCHANGE, routing_key="pagamento.*"
    )


def publish(channel, routing_key, payload):
    channel.basic_publish(
        exchange=EVENTS_EXCHANGE,
        routing_key=routing_key,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=f"{payload['pedido_id']}:{routing_key}",
            type=routing_key,
        ),
        mandatory=True,
    )
