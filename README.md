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

## Estructura del Proyecto

```text
.
├── k8s/                        # Manifiestos de Kubernetes
│   ├── app-deployment.yaml     # Despliegue de la aplicación FastAPI
│   ├── app-service.yaml        # Servicio de la aplicación
│   ├── db-deployment.yaml      # Despliegue de la BD PostgreSQL
│   ├── db-pvc.yaml             # Persistent Volume Claim de la BD
│   ├── db-service.yaml         # Servicio para conectar a la BD
│   ├── ingress.yaml            # Configuración de Ingress (Nginx)
│   └── secret.yaml             # Secretos y credenciales de la BD
├── scripts/                    # Scripts de utilidad
│   ├── generate_openapi.py     # Script para regenerar openapi.json
│   ├── run_tests.ps1           # Script de PowerShell para correr tests con Docker
│   └── run_tests.sh            # Script de bash para correr tests con Docker
├── src/                        # Código de producción de la aplicación
│   ├── main.py                 # Punto de entrada de la aplicación FastAPI
│   ├── api/                    # Capa de presentación y ruteo HTTP
│   │   ├── error_handlers.py   # Manejadores globales de excepciones de dominio
│   │   ├── health.py           # Endpoints de Liveness y Readiness probes
│   │   ├── orders.py           # Endpoint legacy/auxiliar de creación directa
│   │   ├── dependencies/       # Inyección de dependencias de la API
│   │   │   ├── admin_orders.py # Dependencias específicas de administración
│   │   │   └── metrics.py      # Dependencias de filtrado de métricas
│   │   └── routers/            # Controladores/Routers divididos
│   │       ├── admin_orders.py # Endpoints exclusivos para administradores (Órdenes)
│   │       ├── metrics.py      # Endpoints de reportes y exportación a CSV (Métricas)
│   │       └── orders.py       # Endpoints principales de Órdenes (compradores/vendedores)
│   ├── clients/                # Clientes HTTP para comunicarse con otros microservicios
│   │   ├── cart_client.py      # Cliente para el microservicio de Carrito
│   │   ├── inventory_client.py # Cliente para el microservicio de Inventario
│   │   ├── notifications_client.py # Cliente para el microservicio de Notificaciones
│   │   └── payments_client.py  # Cliente para el microservicio de Pagos
│   ├── core/                   # Módulos fundamentales y compartidos
│   │   ├── config.py           # Configuración central (Pydantic Settings)
│   │   ├── database.py         # Configuración del motor y sesión de SQLAlchemy
│   │   ├── dependencies.py     # Dependencias de DB y extracción de usuario
│   │   ├── logging.py          # Configuración del logger estructurado JSON
│   │   ├── observability.py    # Middlewares de correlación y trazas
│   │   ├── telemetry.py        # Configuración de métricas y coleccionista Prometheus
│   │   └── utils.py            # Utilidades generales
│   ├── exceptions/             # Excepciones de dominio personalizadas
│   │   └── __init__.py         # Mapeo de errores de negocio a códigos HTTP
│   ├── models/                 # Modelos ORM (SQLAlchemy)
│   │   ├── idempotency_key.py  # Registro y control de solicitudes idempotentes
│   │   ├── order.py            # Modelo principal de Orden
│   │   ├── order_item.py       # Ítems individuales dentro de una orden
│   │   ├── order_status.py     # Enums de los estados permitidos
│   │   ├── order_status_history.py # Historial logístico y auditoría de estados
│   │   └── state_machine.py    # Lógica y transiciones de la máquina de estados
│   ├── repositories/           # Capa de acceso a datos / Patrón Repository
│   │   ├── idempotency_repository.py # CRUD para Idempotency Keys
│   │   ├── metrics_repository.py     # Consultas analíticas complejas de la BD
│   │   └── order_repository.py       # Operaciones de persistencia de órdenes e ítems
│   ├── schemas/                # Schemas de validación de datos (Pydantic)
│   │   ├── address.py          # Validación de direcciones físicas
│   │   ├── admin_orders.py     # Query params para endpoints de administración
│   │   ├── error.py            # Estructura RFC-7807 (Problem Details)
│   │   ├── metrics.py          # Formato de salida de reportes analíticos
│   │   ├── order.py            # Datos de orden recibidos y entregados
│   │   ├── status.py           # Cambios de estado y códigos de seguimiento
│   │   └── webhook.py          # Payloads esperados de notificaciones externas
│   └── services/               # Lógica de negocio (Sagas e interacciones complejas)
│       ├── metrics_service.py  # Procesamiento y cálculo de métricas
│       └── order_service/      # Lógica de órdenes modularizada por acción
│           ├── admin.py        # Acciones especiales para el administrador
│           ├── cancel.py       # Flujo de cancelaciones (comprador, vendedor)
│           ├── create.py       # Checkout Saga orchestrator y reserva de stock
│           ├── get.py          # Consultas estructuradas de órdenes
│           ├── update.py       # Execution de transiciones de estados
│           └── webhooks.py     # Procesamiento de webhooks de pagos/reembolsos
├── tests/                      # Suite de pruebas automatizadas
│   ├── conftest.py             # Fixtures globales (Base de datos real y cliente mock)
│   ├── test_orders.py          # Pruebas integrales de flujo e idempotencia concurrente
│   └── ...                     # Otros archivos de tests unitarios y de integración
├── Dockerfile                  # Receta multi-stage para producción
├── Dockerfile.test             # Receta para correr tests en aislamiento
├── docker-compose.test.yml     # Compose para inicializar Postgres y los tests
├── openapi.json                # Especificación OpenAPI auto-generada
└── requirements.txt            # Dependencias del proyecto
```

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

### Compradores (Buyers)

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `POST` | `/orders` | `Idempotency-Key`, `X-User-Id` | Crea una nueva orden, recupera el carrito, reserva el stock e inicializa la Saga en estado `PENDIENTE_DE_PAGO`. |
| `GET` | `/orders/{order_id}` | `X-User-Id` | Recupera el detalle completo de una orden específica (requiere ser el dueño). |
| `GET` | `/orders` | `X-User-Id` | Lista las órdenes del usuario comprador con paginación (`page`, `size`) y filtros (`order_status`, `seller_id`). |
| `POST` | `/orders/{order_id}/cancel` | `X-User-Id` | Cancela una orden completa (permitido únicamente antes del despacho). |
| `POST` | `/orders/{order_id}/items/{item_id}/cancel` | `X-User-Id` | Cancela un ítem específico dentro de la orden. |
| `POST` | `/orders/{order_id}/items/{item_id}/confirm-delivery` | `X-User-Id` | Confirma la recepción del ítem por parte del comprador. |

### Vendedores (Sellers)

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `GET` | `/orders/sales` | `X-User-Id` | Lista las órdenes que contienen ítems vendidos por el usuario (filtrando solo sus ítems). |
| `GET` | `/orders/sales/{order_id}` | `X-User-Id` | Recupera una orden de venta específica, mostrando únicamente los productos del vendedor. |
| `PATCH` | `/orders/sales/{order_id}/items/{item_id}/status` | `X-User-Id` (Seller Role) | Actualiza el estado logístico del ítem (`CONFIRMADA` → `EN_PREPARACION` → `ENVIADA` → `ENTREGADA`). Permite adjuntar un `tracking_code`. |
| `POST` | `/orders/sales/{order_id}/items/{item_id}/cancel` | `X-User-Id` (Seller Role) | Cancela un ítem específico por parte del vendedor. |

### Webhooks e Integraciones Internas

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `POST` | `/orders/{order_id}/payment-webhook` | Ninguno (Interno) | Resuelve la Saga basándose en el resultado del pago (`status: SUCCESS \| FAILED`). Aplica compensación si falla. |
| `POST` | `/orders/{order_id}/refund-webhook` | Ninguno (Interno) | Callback del Gateway de Pagos para notificar reembolsos y actualizar los estados correspondientes. |
| `PATCH` | `/orders/{order_id}/status` | Ninguno (Interno) | Actualización administrativa del estado global de la orden. |

### Administración (Admin Role)

Requieren rol de administrador (`require_admin_role`).

#### Gestión de Órdenes

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `GET` | `/orders/admin/orders` | `X-User-Id` (Admin) | Lista todas las órdenes del sistema con filtros y paginación. |
| `GET` | `/orders/admin/orders/{order_id}` | `X-User-Id` (Admin) | Detalle completo de cualquier orden en el sistema sin restricciones de propiedad. |

#### Métricas y Reportes

| Método | Ruta | Headers Requeridos | Descripción |
|--------|------|--------------------|-------------|
| `GET` | `/orders/admin/metrics/orders/summary` | `X-User-Id` (Admin) | Resumen financiero (ingresos, totales, órdenes activas, distribución por estado). |
| `GET` | `/orders/admin/metrics/products/top-selling` | `X-User-Id` (Admin) | Ranking de productos más vendidos. |
| `GET` | `/orders/admin/metrics/categories/sales` | `X-User-Id` (Admin) | Ventas agregadas y distribuidas por categorías de productos. |
| `GET` | `/orders/admin/metrics/orders/export` | `X-User-Id` (Admin) | Exporta una sección específica de métricas a CSV. |

---

### Exportación de Métricas a CSV

El endpoint `GET /orders/admin/metrics/orders/export` devuelve **un CSV por invocación**, según `metric_type`.

Para exportar las métricas de órdenes del backoffice, se deben realizar **tres llamadas** con el mismo `start_date` y `end_date`:

| `metric_type` | Archivo generado | Contenido |
|---------------|------------------|-----------|
| `orders`      | `metrics_orders.csv`    | Serie temporal diaria (no incluye totales ni distribución por estado) |
| `products`    | `metrics_products.csv`  | Top productos más vendidos (hasta 1000 filas) |
| `categories`  | `metrics_categories.csv`| Ventas agrupadas y acumuladas por categoría |

> [!NOTE]
> Las métricas de usuarios registrados pertenecen al microservicio de usuarios. El requisito opcional de exportar datos de métricas se cubre como descarga individual por sección.

### Monitoreo y Health Checks

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/livez` | Liveness probe — responde siempre `200 OK`. |
| `GET` | `/readyz` | Readiness probe — verifica conexión a la DB y la salud de los servicios Carrito, Inventario y Pagos. |
| `GET` | `/metrics` | Exporta métricas de Prometheus para su raspado (Scraping). |
| `GET` | `/docs` | Swagger UI (autogenerado por FastAPI). |
| `GET` | `/redoc` | ReDoc (autogenerado por FastAPI). |

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
