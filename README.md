# Bazaar — Orders Microservice

Microservicio encargado de orquestar el proceso de compra (Checkout) dentro del ecosistema **Bazaar**. Implementa el patrón **Saga Orquestado** para manejar de forma robusta e idempotente la creación de órdenes, la reserva de stock y el cobro distribuido a través de múltiples dominios.

## Stack

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.11+, FastAPI, Pydantic, SQLAlchemy |
| Base de datos | PostgreSQL (configurable por env vars) |
| Contenedor | Docker (multi-stage, non-root) |
| Orquestación | Kubernetes (DigitalOcean) |
| CI/CD | GitHub Actions → GHCR → DOKS |

## Arquitectura y Flujo (Saga)

El microservicio de Órdenes interactúa sincrónicamente (vía REST) con los microservicios de **Carrito** e **Inventario**, y asincrónicamente mediante Webhooks con el **Gateway de Pagos**.

### Gestión de Idempotencia (`Idempotency-Key`)

Para garantizar la consistencia transaccional y evitar cobrar o descontar stock dos veces ante reintentos de red, se exige una cabecera `Idempotency-Key` (UUID v4) en la creación de órdenes. Si la aplicación cliente reintenta una creación con la misma clave, Órdenes detectará la duplicidad y devolverá la orden previamente creada sin reprocesar reservas ni vaciar carritos.

### Flujo de Pago (Webhook)

La finalización de la compra se da mediante el endpoint de `payment-webhook`. Dependiendo del resultado (`SUCCESS` o `FAILED`), el microservicio realiza acciones de confirmación o compensación:
- **Éxito:** Cambia el estado a `CONFIRMADA` y limpia el carrito del usuario. El stock ya fue descontado en la fase de reserva, por lo que no es necesario aplicar commit al inventario.
- **Fallo:** Cambia el estado a `PAGO_RECHAZADO` y ejecuta una transacción de **compensación** llamando al endpoint de "release stock" del inventario para liberar los productos reservados.

### Expiración de órdenes pendientes

Si una orden queda en `PENDIENTE_DE_PAGO` y no llega un webhook de pago dentro
del tiempo configurado, un worker liviano del propio `orders-service` la marca
como `PAGO_RECHAZADO`, cancela sus items y llama a `inventory-service` para
devolver el stock reservado.

Variables de configuración:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PENDING_PAYMENT_EXPIRATION_ENABLED` | `true` | Activa el worker de expiración |
| `PENDING_PAYMENT_EXPIRATION_MINUTES` | `30` | Edad mínima para expirar órdenes pendientes |
| `PENDING_PAYMENT_EXPIRATION_BATCH_SIZE` | `50` | Cantidad máxima por iteración |
| `PENDING_PAYMENT_EXPIRATION_INTERVAL_SECONDS` | `300` | Frecuencia del worker |

### Integración con notificaciones

`orders-service` llama de forma best-effort a `notifications-service` cuando una
orden o item cambia de estado. Si notificaciones está caído, el flujo principal
de la orden no se rompe; solo se loguea el error.

La URL interna se configura con `NOTIFICATIONS_SERVICE_URL`, cuyo valor esperado
en Kubernetes es:

```txt
http://notifications-service
```

---

## Endpoints disponibles

### Órdenes

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `POST` | `/orders` | `Idempotency-Key`, `X-User-Id` | Crea una nueva orden, recupera el carrito, reserva el stock e inicializa la Saga en estado `PENDIENTE_DE_PAGO`. |
| `GET` | `/orders/{order_id}` | `X-User-Id` | Recupera el detalle completo de una orden específica (requiere ser el dueño). |
| `GET` | `/orders` | `X-User-Id` | Lista las órdenes del usuario. Soporta paginación (`page`, `size`) y filtros (`order_status`, `seller_id`). |
| `GET` | `/orders/sales` | `X-User-Id` | Lista las órdenes donde el usuario es vendedor. Devuelve cada orden únicamente con los ítems de ese vendedor. |

### Webhooks y Administración (Sellers)

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `POST` | `/orders/{order_id}/payment-webhook` | (Comunicación Interna) | Resuelve la Saga basándose en el resultado del pago (`status: SUCCESS | FAILED`). Aplica transacciones de compensación en caso de fallo. |
| `PATCH` | `/orders/{order_id}/status` | `X-User-Id` (Seller Role) | Actualiza el estado de la orden en el flujo logístico (`CONFIRMADA` → `EN_PREPARACION` → `ENVIADA` → `ENTREGADA`). Permite adjuntar un `tracking_code`. Se rige por validaciones estrictas en su máquina de estados. |

### Health Checks

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/livez` | Liveness probe — siempre 200 |
| GET | `/readyz` | Readiness probe — verifica conexión a DB y a los microservicios de carrito, inventario y pagos |
| GET | `/docs` | Swagger UI (auto-generado) |
| GET | `/redoc` | ReDoc (auto-generado) |


### Exportación de métricas (admin)

`GET /admin/metrics/orders/export` devuelve **un CSV por invocación**, según `metric_type`.

Para exportar las métricas de órdenes mostradas en el backoffice, realizar **tres llamadas**
con el mismo `start_date` y `end_date`:

| metric_type   | Archivo generado        | Contenido                                      |
|---------------|-------------------------|------------------------------------------------|
| `orders`      | `metrics_orders.csv`    | Serie temporal diaria (no incluye totales ni distribución por estado) |
| `products`    | `metrics_products.csv`  | Top productos (hasta 1000)                     |
| `categories`  | `metrics_categories.csv`| Ventas por categoría                           |

Requisito optativo HU “Exportar datos de métricas”: cubierto como descarga inmediata por sección;
un único CSV con todo el panel no está implementado en esta versión.


---

## Tests

La suite completa consta de más de 90 tests de integración (Happy Paths, manejo de fallos externos como timeout o HTTP 500, testeo de la State Machine y reintentos idempotentes).
Los tests corren contra una **base de datos PostgreSQL real** usando Docker Compose, mockeando únicamente los microservicios externos vía `respx`.

El archivo `docker-compose.test.yml` levanta:
1. Un contenedor **PostgreSQL 16** como base de datos.
2. Un contenedor **test-runner** que ejecuta `pytest` contra esa base de datos.

### Correr los tests

```bash
# O directamente con docker compose
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit
```

> Los contenedores se limpian automáticamente al finalizar.

## Docker

```bash
docker build -t orders-service .
docker run -p 8000:8000 --env-file .env orders-service
```

## Kubernetes

Los manifiestos en `k8s/` despliegan el microservicio junto con su propia base de datos PostgreSQL dentro del cluster.

### Variables de entorno requeridas

| Entorno / Secret | Descripción |
|------------------|-------------|
| `CART_SERVICE_URL` | URL base del microservicio de carrito (ej. `http://cart-service:8000`) |
| `INVENTORY_SERVICE_URL` | URL base del microservicio de inventario |
| `NOTIFICATIONS_SERVICE_URL` | URL base del microservicio de notificaciones |
| `PAYMENTS_SERVICE_URL` | URL base del microservicio de pagos |
| `PENDING_PAYMENT_EXPIRATION_ENABLED` | Activa expiración automática de órdenes pendientes |
| `PENDING_PAYMENT_EXPIRATION_MINUTES` | Minutos antes de expirar una orden pendiente |
| `PENDING_PAYMENT_EXPIRATION_BATCH_SIZE` | Batch máximo de expiración |
| `PENDING_PAYMENT_EXPIRATION_INTERVAL_SECONDS` | Intervalo del worker de expiración |
| `DB_USER` | Usuario de la base de datos |
| `DB_PASSWORD` | Contraseña de la base de datos |
| `DB_NAME` | Nombre de la base de datos |

## Conexión a la base de datos

Al iniciar, la aplicación espera a que la base de datos esté disponible usando **backoff exponencial** antes de ejecutar las migraciones mediante Alembic y aceptar tráfico. Esto evita crasheos durante el arranque del pod cuando la BD aún está inicializándose.
