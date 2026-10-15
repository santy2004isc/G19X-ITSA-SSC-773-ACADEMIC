# Agente Conversacional Inteligente para Consulta Normativa

> **Plataforma web monolítica basada en arquitectura RAG (Retrieval-Augmented Generation) para la localización, extracción y consulta semántica automatizada de las Leyes Federales Públicas de México.**

---

## Características Principales

* **Acceso Estrictamente Autenticado:** Protección total de rutas mediante middleware. No se permite interacción a usuarios invitados o anónimos.
* **Procesamiento de PDF en Memoria:** Ingesta e indexación de leyes sin almacenar archivos PDF de forma persistente en el servidor o base de datos.
* **Embeddings Locales:** Generación de vectores ejecutada localmente en el backend utilizando `sentence-transformers`, reduciendo la dependencia de APIs externas para la indexación.
* **Búsqueda Vectorial:** Almacenamiento y consulta de similitud vectorial optimizada en PostgreSQL mediante la extensión `pgvector`.
* **Resiliencia y Conmutación Automática (Fallback) de LLMs:**
  * Envío prioritario a **Groq Cloud API** para máxima velocidad.
  * Conmutación transparente hacia **Gemini API** ante eventos de Rate Limit (HTTP 429), Timeout o caída del servicio.
  * Notificación amigable al usuario en caso de saturación simultánea de ambos proveedores gratuitos.
* **Gestión de Roles Integrada:**
  * **Administrador:** Control de usuarios y monitoreo de APIs.
  * **Gestor de Base de Conocimiento:** Ingesta temporal de documentos PDF.
  * **Usuario Registrado:** Consultas en chat con historial persistente.

---

## Stack Tecnológico

* **Lenguaje:** Python 3.11+
* **Framework Web:** Django (Patrón MVT)
* **Base de Datos:** PostgreSQL con extensión `pgvector`
* **Vectorización Local:** `sentence-transformers`
* **Modelos de Lenguaje (Inferencia):** Groq Cloud API & Gemini API (Orquestados automáticamente por el sistema)
* **Frontend:** Django Templates + Tailwind CSS
* **Contenerización:** Docker & Docker Compose

---

## Estructura del Proyecto

```text
.
├── apps/
│   ├── core/
│   │   ├── forms/
│   │   ├── migrations/
│   │   ├── services/
│   │   ├── templates/
│   │   ├── views/
│   │   ├── __init__.py
│   │   ├── admin.py
│   │   ├── apps.py
│   │   ├── decorators.py
│   │   ├── models.py
│   │   ├── tests.py
│   │   └── urls.py
│   └── __init__.py
├── config/                  # Configuración principal de Django (settings, urls, wsgi)
│   ├── __init__.py
│   ├── asgi.py
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── .env.example             # Plantilla de variables de entorno
├── .gitignore
├── docker-compose.yml       # Orquestación de servicios (Web + PostgreSQL/pgvector)
├── Dockerfile               # Imagen para el entorno backend Django
├── manage.py
├── README.md                # Documentación del repositorio
└── requirements.txt         # Dependencias de Python
```

---

## Requisitos Previos

Asegúrate de tener instalados en tu sistema:

* **Docker Engine** (v20.10 o superior)
* **Docker Compose** (v2.0 o superior)
* **Git**

---

## Instalación y Configuración

### 1. Clona el repositorio

```bash
git clone https://github.com/santy2004isc/G19X-ITSA-SSC-773-ACADEMIC.git
cd G19X-ITSA-SSC-773-ACADEMIC
```

### 2. Configura las variables de entorno

Copia el archivo de ejemplo `.env.example` a `.env`:

```bash
cp .env.example .env
```

Edita `.env` agregando tus credenciales y llaves de API:

```env
# Configuración de Django
SECRET_KEY=tu_secret_key_de_django
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Base de Datos PostgreSQL + pgvector
POSTGRES_DB=normativa_db
POSTGRES_USER=normativa_user
POSTGRES_PASSWORD=normativa_password
POSTGRES_HOST=db
POSTGRES_PORT=5432

# LLM APIs
GROQ_API_KEY=tu_groq_api_key
GEMINI_API_KEY=tu_gemini_api_key
```

---

## Despliegue con Docker

### 1. Construir y levantar los contenedores

```bash
docker-compose up --build -d
```

### 2. Aplicar migraciones y crear superusuario

```bash
# Aplicar migraciones de la base de datos
docker-compose exec web python manage.py migrate

# Crear el usuario Administrador inicial
docker-compose exec web python manage.py createsuperuser
```

### 3. Acceder a la aplicación

Abre tu navegador e ingresa a: [http://localhost:8000](http://localhost:8000)

---

## Uso del Sistema

* **Autenticación:** Inicia sesión con tus credenciales o regístrate mediante el formulario público.
* **Carga de Documentos (Gestor):** Ingresa con rol de Gestor de Base de Conocimiento para subir archivos PDF de Leyes Federales. El archivo se procesará en memoria y se convertirá en vectores almacenados en `pgvector`.
* **Consulta en Chat (Usuario Registrado):** Realiza preguntas normativas en lenguaje natural (ej. *¿Qué establece el artículo 123 sobre el trabajo digno?*). El sistema devolverá la respuesta fundamentada con citas explícitas de la ley.

---

## Políticas de Seguridad y Privacidad

* **Cero Almacenamiento Persistente de PDF:** Los archivos subidos se leen únicamente en memoria volátil durante la fase de procesamiento y se descartan tras generar los vectores.
* **Protección de Datos:** Las contraseñas están cifradas con el sistema de hashing nativo de Django.
* **Control de Contexto:** Las consultas fuera del marco normativo federal mexicano son rechazadas automáticamente mediante guardrails en los prompts.

---

## Créditos y Licencia

Por definir.

*Todos los derechos reservados.*