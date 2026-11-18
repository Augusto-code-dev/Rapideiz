import os
import time

if __package__:
    from .common import RejectMessage, locked_order, run
else:
    from common import RejectMessage, locked_order, run

from api.messaging import ORDER_CREATED, PAYMENT_APPROVED, PAYMENT_REFUSED, publish


PAYMENT_DELAY = float(os.getenv("PAYMENT_DELAY_SECONDS", "0.5"))
REFUSAL_REASON = "Limite insuficiente"
FAILURE_REASON = "Falha técnica simulada"


def handle(db, channel, order_id, event):
    if event != ORDER_CREATED:
        raise RejectMessage(f"Evento inesperado: {event}")

    time.sleep(PAYMENT_DELAY)  # Atraso da simulação, visível no teste de rajada.
    order = locked_order(db, order_id)
    if order.payment_status == "APROVADO":
        return None
    if order.payment_status == "RECUSADO":
        return REFUSAL_REASON
    if order.payment_status == "FALHA_TECNICA" or order.simulate_failure:
        order.payment_status = "FALHA_TECNICA"
        order.status = order.current_status()
        return FAILURE_REASON

    payload = {"pedido_id": order_id}
    if order.payment_method == "sem_limite":
        publish(channel, PAYMENT_REFUSED, {**payload, "motivo": REFUSAL_REASON})
        order.payment_status = "RECUSADO"
        order.status = order.current_status()
        return REFUSAL_REASON

    publish(
        channel,
        PAYMENT_APPROVED,
        {**payload, "total": str(order.total), "forma_pagamento": order.payment_method},
    )
    order.payment_status = "APROVADO"
    order.status = order.current_status()
    return None


if __name__ == "__main__":
    run("payments", handle)
