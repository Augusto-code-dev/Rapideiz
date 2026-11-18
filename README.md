# Rapideiz! Delivery

Projeto da Atividade 04 de Message Broker. O navegador envia pedidos para uma API FastAPI. A API confirma a publicação de `pedido.criado` no RabbitMQ e responde HTTP 202 enquanto os workers processam o pedido separadamente. O MySQL guarda o pedido e o acompanhamento das etapas.

## Executar com Docker Compose

1. Abra o Docker Desktop e aguarde o serviço iniciar.
2. Na pasta que contém `docker-compose.yml`, execute os comandos a seguir no PowerShell.

   ```powershell
   Copy-Item .env.example .env
   docker compose up -d --build --wait
   ```

   A cópia de `.env.example` é opcional para a demonstração local. O Compose tem os mesmos valores iniciais.

3. Abra o [site](http://localhost:8000) e acompanhe os logs dos workers.

   ```powershell
   docker compose logs -f payment stock notification
   ```

| Serviço | Endereço |
| --- | --- |
| Site | http://localhost:8000 |
| Swagger | http://localhost:8000/docs |
| Saúde da API | http://localhost:8000/api/health |
| RabbitMQ Management | http://localhost:15672 |
| MySQL | 127.0.0.1:3307 |

O exemplo usa `rapideiz` como usuário do RabbitMQ e do MySQL e `rapideiz123` como senha local desses serviços. O Compose publica as portas apenas na própria máquina.

## Demonstrar os cinco requisitos

1. **Operação web e publicação.** Finalize um pedido no site e observe o HTTP 202 na aba Rede do navegador. A resposta chega após a confirmação do broker. O pagamento, o estoque e as notificações continuam em segundo plano.
2. **Roteamento e coreografia.** Compare o ID do pedido nos três logs. Pagamentos publica `pagamento.aprovado` ou `pagamento.recusado`. Notificações recebe tanto a criação quanto o resultado.
3. **Persistência e ACK manual.** Confira as filas duráveis no RabbitMQ Management e os registros de ACK no terminal. Cada worker confirma uma mensagem após concluir seu processamento. A produção usa mensagens persistentes e confirmação de publicação.
4. **Queda, retomada e DLQ.** Rode `docker compose stop stock`, crie dois pedidos e confira `Ready` em `rapideiz.stock`. Rode `docker compose start stock` e confira a conclusão dos dois pedidos. Em seguida, selecione **Cartão sem limite (teste de DLQ)** ou marque **Simular falha técnica**. A mensagem original vai para `rapideiz.payments.dlq`. A recusa também produz o evento de domínio usado por notificações.
5. **Duas réplicas.** Rode `docker compose up -d --scale payment=2` e clique em **Criar rajada de 10 pedidos**. Confira os IDs e os nomes das duas instâncias com `docker compose logs -f payment`. Para retornar a uma réplica, rode `docker compose up -d --scale payment=1`.

## Roteamento

| Exchange | Routing key | Fila |
| --- | --- | --- |
| `rapideiz.events` (topic) | `pedido.criado` | `rapideiz.payments`, `rapideiz.stock`, `rapideiz.notifications` |
| `rapideiz.events` (topic) | `pagamento.*` | `rapideiz.notifications` |
| `rapideiz.dlx` (direct) | `payments.dlq` | `rapideiz.payments.dlq` |
| `rapideiz.dlx` (direct) | `stock.dlq` | `rapideiz.stock.dlq` |
| `rapideiz.dlx` (direct) | `notifications.dlq` | `rapideiz.notifications.dlq` |

Pagamentos, separação de estoque e notificações são simulados. O worker de notificações registra cada evento no MySQL. Estoque recebe a criação do pedido independentemente do resultado do pagamento. O painel considera concluído o pedido aprovado com estoque separado e resultado registrado. Com o RabbitMQ temporariamente indisponível, a API informa o ID salvo no HTTP 503 e retoma o envio em segundo plano.

## Verificação automatizada e execução local

Use Python 3.10 ou superior. Em uma cópia que já tenha uma pasta `.venv` de outro computador, crie o ambiente em outro diretório ou recrie a pasta. O ZIP contém apenas os arquivos do projeto.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tests\verificar_integracao.py
```

Execute o teste com o Compose ativo. O teste cria pedidos de demonstração, para e retoma o estoque, sobe duas réplicas de pagamento e restaura o número inicial de réplicas.

Para executar o Python diretamente, suba somente a infraestrutura com `docker compose up -d --wait mysql rabbitmq`. Em quatro terminais, use o Python do ambiente virtual para rodar `-m uvicorn api.main:app`, `-m workers.payment_worker`, `-m workers.stock_worker` e `-m workers.notification_worker`. Inicie a API primeiro para preparar o banco. Cada terminal extra de pagamento conta como uma réplica local.

Para parar o ambiente Docker preservando os dados, use `docker compose stop`.
