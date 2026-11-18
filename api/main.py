import json
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

import pika
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .catalog import PRODUCTS
from .database import engine, get_db, initialize_database
from .messaging import connect
from .models import Order, order_status
from .order_events import publish_new_order, retry_pending_orders
from .schemas import NewOrder

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    stop = Event()
    publisher = Thread(target=retry_pending_orders, args=(stop,), daemon=True)
    publisher.start()
    try:
        yield
    finally:
        stop.set()
        publisher.join(timeout=6)


app = FastAPI(title="Rapideiz! API", lifespan=lifespan)


@app.get("/api/health")
def health(response: Response):
    checks = {"mysql": False, "rabbitmq": False}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["mysql"] = True
    except SQLAlchemyError:
        pass
    try:
        with connect():
            checks["rabbitmq"] = True
    except pika.exceptions.AMQPError:
        pass
    online = all(checks.values())
    if not online:
        response.status_code = 503
    return {"status": "online" if online else "degraded", **checks}


@app.get("/api/catalog")
def catalog():
    return [
        {"id": product_id, "name": name, "description": description, "price": str(price), "emoji": emoji}
        for product_id, (name, description, price, emoji) in PRODUCTS.items()
    ]


@app.post("/api/orders", status_code=202)
def create_order(data: NewOrder, db: Session = Depends(get_db)):
    total = Decimal("0.00")
    items = []
    for item in data.items:
        name, _, price, _ = PRODUCTS[item.product_id]
        total += price * item.quantity
        items.append({**item.model_dump(), "name": name, "unit_price": str(price)})

    order = Order(
        customer_name=data.customer_name,
        customer_phone=data.customer_phone,
        delivery_address=data.delivery_address,
        items_json=json.dumps(items, ensure_ascii=False),
        total=total,
        payment_method=data.payment_method,
        simulate_failure=data.simulate_failure,
    )
    try:
        db.add(order)
        db.flush()
        order_id = order.id
        db.commit()
    except SQLAlchemyError as error:
        logger.exception("Erro ao gravar pedido")
        raise HTTPException(503, "Banco indisponível. Tente novamente mais tarde.") from error

    try:
        publish_new_order(order_id)
    except (pika.exceptions.AMQPError, SQLAlchemyError) as error:
        logger.warning("Pedido %s aguardando publicação (%s)", order_id, type(error).__name__)
        raise HTTPException(
            503,
            {
                "message": f"Pedido #{order_id} salvo. O envio será retomado automaticamente.",
                "pedido_id": order_id,
                "status": "PENDENTE_PUBLICACAO",
            },
        ) from error

    return {
        "message": "Pedido recebido! Estamos processando.",
        "pedido_id": order_id,
        "status": "EM_PROCESSAMENTO",
        "total": str(total),
    }


@app.get("/api/orders")
def orders(limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    rows = db.scalars(select(Order).order_by(Order.id.desc()).limit(limit))
    return [
        {
            "id": order.id,
            "customer_name": order.customer_name,
            "items": json.loads(order.items_json),
            "total": str(order.total),
            "status": order.current_status(),
            "payment_status": order.payment_status,
            "stock_status": order.stock_status,
            "notification_status": order.notification_status,
            "created_at": order.created_at.isoformat() + "Z",
        }
        for order in rows
    ]


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    # A mesma regra de status atende pedidos novos e pedidos do banco anterior.
    counts = {"total_orders": 0, "processing": 0, "approved": 0, "refused": 0}
    fields = (Order.status, Order.payment_status, Order.stock_status, Order.notification_status)
    groups = db.execute(select(*fields, func.count()).group_by(*fields))
    for status, payment, stock, notification, count in groups:
        counts["total_orders"] += count
        if order_status(status, payment, stock, notification) in {
            "PENDENTE_PUBLICACAO", "EM_PROCESSAMENTO"
        }:
            counts["processing"] += count
        if payment == "APROVADO":
            counts["approved"] += count
        if payment == "RECUSADO":
            counts["refused"] += count
    return counts


@app.get("/")
def home():
    return FileResponse(FRONTEND / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
