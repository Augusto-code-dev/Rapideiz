const byId = (id) => document.getElementById(id);
const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const cart = new Map();
const statuses = {
  AGUARDANDO: "Aguardando",
  APROVADO: "Aprovado",
  CONCLUIDO: "Concluído",
  EM_PROCESSAMENTO: "Em processamento",
  ENVIADA: "Resultado registrado",
  ERRO_PUBLICACAO: "Erro de publicação",
  FALHA_TECNICA: "Falha técnica",
  PENDENTE_PUBLICACAO: "Aguardando envio à fila",
  RECEBIMENTO_REGISTRADO: "Recebimento registrado",
  RECUSADO: "Recusado",
  SEPARADO: "Separado",
};
let catalog = [];
let toastTimer;

function element(tag, content = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = content;
  if (className) node.className = className;
  return node;
}

function toast(message) {
  const notice = byId("toast");
  notice.textContent = message;
  notice.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => notice.classList.remove("show"), 4500);
}

function updateCart() {
  const total = catalog.reduce((sum, product) => {
    const cents = Math.round(Number(product.price) * 100);
    return sum + cents * (cart.get(product.id) || 0);
  }, 0);
  byId("sum").textContent = currency.format(total / 100);
  byId("send").disabled = cart.size === 0;

  for (const card of byId("menu").children) {
    const quantity = cart.get(card.dataset.product) || 0;
    card.querySelector(".qty").textContent = quantity;
    card.querySelector(".remove").disabled = quantity === 0;
    card.querySelector(".add").disabled = quantity === 30;
  }
}

function changeQuantity(productId, delta) {
  const next = (cart.get(productId) || 0) + delta;
  if (next === 0) cart.delete(productId);
  else cart.set(productId, next);
  updateCart();
}

function productCard(product) {
  const card = element("div", "", "product");
  card.dataset.product = product.id;
  const info = element("div");
  info.append(element("b", product.name), element("small", product.description));
  const emoji = element("span", product.emoji, "emoji");
  emoji.setAttribute("aria-hidden", "true");
  const remove = element("button", "−", "remove");
  const add = element("button", "+", "add");
  remove.type = add.type = "button";
  remove.setAttribute("aria-label", `Remover ${product.name}`);
  add.setAttribute("aria-label", `Adicionar ${product.name}`);
  remove.addEventListener("click", () => changeQuantity(product.id, -1));
  add.addEventListener("click", () => changeQuantity(product.id, 1));
  card.append(
    emoji, info, element("strong", currency.format(product.price)),
    remove, element("span", "0", "qty"), add
  );
  return card;
}

async function getJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("Serviço temporariamente indisponível.");
  return response.json();
}

async function sendOrder(payload) {
  const response = await fetch("/api/orders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (response.ok) return data;

  // O pedido já tem ID e será enviado quando o RabbitMQ voltar.
  if (data.detail?.pedido_id) return data.detail;
  if (Array.isArray(data.detail)) throw new Error("Confira os dados de entrega e os itens.");
  throw new Error(data.detail || "Não foi possível enviar o pedido.");
}

function badge(value) {
  const waiting = ["AGUARDANDO", "EM_PROCESSAMENTO", "PENDENTE_PUBLICACAO", "RECEBIMENTO_REGISTRADO"];
  const errors = ["RECUSADO", "FALHA_TECNICA", "ERRO_PUBLICACAO"];
  const variant = waiting.includes(value) ? " wait" : errors.includes(value) ? " error" : "";
  return element("span", statuses[value] || value, `badge${variant}`);
}

function orderRow(order) {
  const row = element("tr");
  row.append(
    element("td", `#${order.id}`),
    element("td", order.customer_name),
    element("td", currency.format(order.total))
  );
  for (const status of [order.status, order.payment_status, order.stock_status, order.notification_status]) {
    const cell = element("td");
    cell.append(badge(status));
    row.append(cell);
  }
  return row;
}

function showLatest(order) {
  const latest = byId("latest");
  if (!order) {
    latest.textContent = "Nenhum pedido ainda.";
    return;
  }
  const name = element("strong", `Pedido #${order.id} · ${order.customer_name} `);
  latest.replaceChildren(name, badge(order.status));
}

async function refreshOrders() {
  try {
    const [orders, metrics] = await Promise.all([
      getJson("/api/orders"),
      getJson("/api/dashboard"),
    ]);
    byId("total").textContent = metrics.total_orders;
    byId("approved").textContent = metrics.approved;
    byId("processing").textContent = metrics.processing;
    byId("refused").textContent = metrics.refused;
    if (orders.length) {
      byId("rows").replaceChildren(...orders.map(orderRow));
    } else {
      const row = element("tr");
      const cell = element("td", "Nenhum pedido.");
      cell.colSpan = 7;
      row.append(cell);
      byId("rows").replaceChildren(row);
    }
    showLatest(orders[0]);
    byId("refresh-error").hidden = true;
  } catch {
    byId("refresh-error").hidden = false;
  }
}

async function pollOrders() {
  await refreshOrders();
  setTimeout(pollOrders, 2500);
}

byId("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const items = [...cart].map(([product_id, quantity]) => ({ product_id, quantity }));
  if (!items.length) return;

  byId("send").disabled = true;
  byId("menu").inert = true;
  try {
    const order = await sendOrder({
      customer_name: byId("name").value,
      customer_phone: byId("phone").value,
      delivery_address: byId("address").value,
      items,
      payment_method: byId("payment").value,
      simulate_failure: byId("failure").checked,
    });
    toast(order.message);
    cart.clear();
    byId("failure").checked = false;
  } catch (error) {
    toast(error.message);
  } finally {
    byId("menu").inert = false;
    updateCart();
  }
});

byId("burst").addEventListener("click", async () => {
  const button = byId("burst");
  button.disabled = true;
  try {
    const results = await Promise.allSettled(
      Array.from({ length: 10 }, (_, index) => sendOrder({
        customer_name: `Teste Rajada ${index + 1}`,
        customer_phone: "11999990000",
        delivery_address: `Rua de Teste ${index + 1}, 100 - Centro`,
        items: [{ product_id: catalog[0].id, quantity: 1 }],
        payment_method: "pix",
      }))
    );
    const saved = results.filter((result) => result.status === "fulfilled");
    const published = saved.filter((result) => result.value.status === "EM_PROCESSAMENTO");
    toast(`${saved.length}/10 pedidos salvos · ${published.length} enviados à fila.`);
  } finally {
    button.disabled = false;
  }
});

async function loadCatalog() {
  try {
    catalog = await getJson("/api/catalog");
    byId("menu").replaceChildren(...catalog.map(productCard));
    byId("burst").disabled = catalog.length === 0;
    updateCart();
  } catch {
    byId("menu").textContent = "Cardápio indisponível. Recarregue a página para tentar novamente.";
  }
}

loadCatalog();
pollOrders();
