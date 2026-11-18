if __package__:
    from .common import RejectMessage, locked_order, run
else:
    from common import RejectMessage, locked_order, run

from api.messaging import ORDER_CREATED


def handle(db, channel, order_id, event):
    if event != ORDER_CREATED:
        raise RejectMessage(f"Evento inesperado: {event}")

    order = locked_order(db, order_id)
    order.stock_status = "SEPARADO"
    order.status = order.current_status()


if __name__ == "__main__":
    run("stock", handle)
