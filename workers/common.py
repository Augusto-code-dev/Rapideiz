import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

import pika
from sqlalchemy.exc import InterfaceError, OperationalError

# Mantém os comandos antigos (python workers/<arquivo>.py) funcionando.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.database import SessionLocal
from api.messaging import QUEUES, connect, declare_topology
from api.models import Order

logger = logging.getLogger(__name__)


class RejectMessage(Exception):
    """Erro definitivo que deve encaminhar a mensagem à DLQ."""


def locked_order(db, order_id):
    order = db.get(Order, order_id, with_for_update=True)
    if order is None:
        raise RejectMessage(f"Pedido #{order_id} inexistente")
    # Receber o evento comprova que ele chegou mesmo se a confirmação se perdeu.
    if order.status in {"PENDENTE_PUBLICACAO", "ERRO_PUBLICACAO"}:
        order.status = "RECEBIDO"
    return order


def decode_order_id(body):
    try:
        data = json.loads(body)
    except (ValueError, UnicodeError) as error:
        raise RejectMessage("Mensagem fora do formato JSON") from error
    if not isinstance(data, dict) or type(data.get("pedido_id")) is not int:
        raise RejectMessage("Mensagem sem ID de pedido válido")
    if data["pedido_id"] < 1:
        raise RejectMessage("ID de pedido deve ser positivo")
    return data["pedido_id"]


def run(queue_name, handler):
    queue = QUEUES[queue_name]
    instance = f"{queue_name}@{socket.gethostname()}:{os.getpid()}"
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{instance}] %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)

    def process(channel, method, properties, body):
        order_id = "?"
        try:
            order_id = decode_order_id(body)
            logger.info("Recebido pedido #%s / %s", order_id, method.routing_key)
            with SessionLocal.begin() as db:
                rejection = handler(db, channel, order_id, method.routing_key)
            if rejection:
                raise RejectMessage(rejection)
        except (pika.exceptions.AMQPError, OperationalError, InterfaceError):
            # Ao fechar a conexão, o broker devolve as mensagens sem ACK à fila.
            raise
        except RejectMessage as error:
            logger.warning("DLQ pedido #%s / %s", order_id, error)
            channel.basic_nack(method.delivery_tag, requeue=False)
        except Exception:
            logger.exception("DLQ pedido #%s / erro de processamento", order_id)
            channel.basic_nack(method.delivery_tag, requeue=False)
        else:
            channel.basic_ack(method.delivery_tag)
            logger.info("ACK pedido #%s / %s", order_id, method.routing_key)

    try:
        while True:
            try:
                with connect() as connection:
                    channel = connection.channel()
                    declare_topology(channel)
                    channel.confirm_delivery()
                    channel.basic_qos(prefetch_count=1)
                    channel.basic_consume(queue, process, auto_ack=False)
                    logger.info("Online na fila %s", queue)
                    channel.start_consuming()
            except (pika.exceptions.AMQPError, OperationalError, InterfaceError) as error:
                logger.warning("Reconectando em 3 segundos (%s)", type(error).__name__)
                time.sleep(3)
    except KeyboardInterrupt:
        logger.info("Encerrado")
