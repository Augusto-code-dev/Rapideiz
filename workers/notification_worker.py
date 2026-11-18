if __package__:
    from .common import RejectMessage, locked_order, logger, run
else:
    from common import RejectMessage, locked_order, logger, run

from api.messaging import ORDER_CREATED, PAYMENT_APPROVED, PAYMENT_REFUSED
from api.models import Notification


MESSAGES = {
    ORDER_CREATED: "Pedido recebido",
    PAYMENT_APPROVED: "Pagamento aprovado",
    PAYMENT_REFUSED: "Pagamento recusado: limite insuficiente",
}


def handle(db, channel, order_id, event):
    if event not in MESSAGES:
        raise RejectMessage(f"Evento inesperado: {event}")

    # O bloqueio do pedido serializa notificações concorrentes do mesmo pedido.
    order = locked_order(db, order_id)
    if db.get(Notification, (order_id, event)):
        return

    message = MESSAGES[event]
    db.add(Notification(order_id=order_id, event=event, message=message))
    if event != ORDER_CREATED:
        order.notification_status = "ENVIADA"
    elif order.notification_status == "AGUARDANDO":
        order.notification_status = "RECEBIMENTO_REGISTRADO"
    order.status = order.current_status()
    logger.info("Notificação do pedido #%s / %s", order_id, message)


if __name__ == "__main__":
    run("notifications", handle)
