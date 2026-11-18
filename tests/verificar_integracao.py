"""Verifica os cinco cenários da atividade com a aplicação em Docker Compose."""
import argparse
import base64
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API = "http://127.0.0.1:8000"
RABBIT = "http://127.0.0.1:15672/api"
DEMO = f"Verificação {uuid4().hex[:8]}"


def request(url, payload=None, headers=None):
    headers = dict(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=15) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        content = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(content)
        except json.JSONDecodeError:
            return error.code, content


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for(check, message, timeout=50):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise RuntimeError(message)


def queue_stats(name):
    credentials = f"{os.getenv('RABBITMQ_USER', 'rapideiz')}:{os.getenv('RABBITMQ_PASSWORD', 'rapideiz123')}"
    token = base64.b64encode(credentials.encode()).decode()
    status, body = request(
        f"{RABBIT}/queues/{quote('/', safe='')}/{name}",
        headers={"Authorization": f"Basic {token}"},
    )
    if status == 404:
        return {}
    require(status == 200, f"RabbitMQ retornou HTTP {status} ao consultar {name}")
    return body


def submit(label, **changes):
    data = {
        "customer_name": f"{DEMO} {label}",
        "customer_phone": "11999990000",
        "delivery_address": "Rua de Teste, 100",
        "items": [{"product_id": "burger", "quantity": 2, "unit_price": "0.01"}],
        "payment_method": "pix",
        **changes,
    }
    code, body = request(f"{API}/api/orders", data)
    require(code == 202, f"Esperado HTTP 202, recebido HTTP {code}: {body}")
    require(body["status"] == "EM_PROCESSAMENTO", "Pedido deve sair em processamento")
    require(body["total"] == "59.80", "Total deve usar o catálogo do servidor")
    return body["pedido_id"]


def recent_orders():
    code, body = request(f"{API}/api/orders?limit=100")
    require(code == 200, f"Consulta de pedidos retornou HTTP {code}")
    return {order["id"]: order for order in body}


def is_status(order_id, expected):
    order = recent_orders().get(order_id, {})
    return order if order.get("status") == expected else None


def run_checks(compose):
    status, health = request(f"{API}/api/health")
    require(status == 200 and health["mysql"] and health["rabbitmq"], f"Health check: {health}")
    for queue in ("payments", "stock", "notifications"):
        wait_for(
            lambda: queue_stats(f"rapideiz.{queue}").get("consumers", 0) > 0,
            f"Fila {queue} sem consumidores",
        )
    status, _ = request(f"{API}/api/orders", {"customer_name": "   ", "items": []})
    require(status == 422, "A API deve rejeitar um pedido inválido")
    print("OK 1/5: MySQL e RabbitMQ online; validação HTTP e preço do servidor.")

    normal = submit("normal")
    order = wait_for(lambda: is_status(normal, "CONCLUIDO"), "Pedido normal não foi concluído")
    require(order["payment_status"] == "APROVADO", "Pagamento não avançou")
    require(order["stock_status"] == "SEPARADO", "Estoque não avançou")
    require(order["notification_status"] == "ENVIADA", "Resultado do pagamento não foi notificado")
    print(f"OK 2/5: pedido #{normal} concluiu pagamento, estoque e notificação.")

    compose("stop", "stock")
    try:
        baseline = queue_stats("rapideiz.stock").get("messages_ready", 0)
        retained = [submit(f"estoque {index}") for index in range(2)]
        wait_for(
            lambda: queue_stats("rapideiz.stock").get("messages_ready", 0) >= baseline + 2,
            "As mensagens não ficaram prontas na fila de estoque",
        )
        rows = recent_orders()
        require(
            all(rows[oid]["stock_status"] == "AGUARDANDO" for oid in retained),
            "Estoque avançou com worker parado",
        )
    finally:
        compose("start", "stock")
    def all_finished(order_ids):
        rows = recent_orders()
        return all(rows.get(oid, {}).get("status") == "CONCLUIDO" for oid in order_ids)

    wait_for(lambda: all_finished(retained), "Pedidos retidos não concluíram após a retomada")
    print(f"OK 3/5: pedidos {retained} ficaram retidos e concluíram após a retomada.")

    dlq = "rapideiz.payments.dlq"
    baseline = queue_stats(dlq).get("messages_ready", 0)
    failed = submit("falha técnica", simulate_failure=True)
    refused = submit("sem limite", payment_method="sem_limite")
    wait_for(lambda: is_status(failed, "FALHA_TECNICA"), "Falha não registrada")
    wait_for(lambda: is_status(refused, "RECUSADO"), "Recusa ou notificação não registrada")
    wait_for(
        lambda: queue_stats(dlq).get("messages_ready", 0) >= baseline + 2,
        "As duas mensagens rejeitadas não apareceram na DLQ",
    )
    print(f"OK 4/5: falha #{failed} e recusa #{refused} chegaram à DLQ de pagamentos.")

    replicas = len(compose("ps", "--status", "running", "-q", "payment").splitlines())
    require(replicas > 0, "Execute os workers de pagamento pelo Compose")
    compose("up", "-d", "--no-deps", "--scale", "payment=2", "payment")
    try:
        wait_for(
            lambda: queue_stats("rapideiz.payments").get("consumers", 0) == 2,
            "Duas réplicas de pagamentos não apareceram no RabbitMQ",
        )
        with ThreadPoolExecutor(max_workers=10) as pool:
            burst = set(pool.map(lambda i: submit(f"rajada {i}"), range(1, 11)))

        wait_for(lambda: all_finished(burst), "Os dez pedidos da rajada não concluíram")
        logs = compose("logs", "--no-color", "payment")
        acknowledgements = {}
        for instance, order_id in re.findall(r"\[(payments@[^\]]+)\] ACK pedido #(\d+)", logs):
            if int(order_id) in burst:
                acknowledgements.setdefault(instance, set()).add(int(order_id))
        require(
            len(acknowledgements) == 2 and set.union(*acknowledgements.values()) == burst,
            "Os logs não mostram os dez pedidos distribuídos entre as duas réplicas",
        )
        distribution = {instance: len(ids) for instance, ids in acknowledgements.items()}
        print(f"OK 5/5: 10 pedidos concluídos, distribuídos entre {distribution}.")
    finally:
        compose("up", "-d", "--no-deps", "--scale", f"payment={replicas}", "payment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", help="Nome do projeto Compose, quando personalizado")
    args = parser.parse_args()
    command = ["docker", "compose"]
    if args.project_name:
        command += ["--project-name", args.project_name]

    def compose(*args):
        return subprocess.check_output([*command, *args], cwd=ROOT, text=True)

    run_checks(compose)


if __name__ == "__main__":
    main()
