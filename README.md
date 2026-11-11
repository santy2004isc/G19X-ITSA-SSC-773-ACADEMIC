# Agente Conversacional para Consulta Normativa (ACCN)

Plataforma web Django para consultar leyes federales mexicanas mediante búsqueda híbrida (texto y vectores) y respuestas fundamentadas en los documentos cargados.

## Funcionalidades

- Registro e inicio de sesión con roles de administrador, gestor y usuario registrado.
- Administración de cuentas, documentos normativos y registros de fallos de proveedores.
- Carga y reemplazo de PDF por gestores. La extracción, fragmentación y generación de embeddings se ejecutan en segundo plano con Celery y Redis; el panel muestra el estado y los errores de indexación.
- El PDF se conserva temporalmente en el almacenamiento compartido entre el servidor web y el worker, y se elimina cuando el procesamiento termina o falla. Los textos, fragmentos y vectores resultantes se guardan en PostgreSQL.
- Embeddings locales de 768 dimensiones con `sentence-transformers`; recuperación híbrida mediante `pgvector` y búsqueda de texto completo en español de PostgreSQL.
- Deduplicación por hash del archivo y del texto, fragmentación con identificación de artículos y disposiciones transitorias.
- Chat con historial, clasificación de saludos/cortesías, cuota diaria para usuarios registrados, fallback entre Groq y Gemini y respuestas transmitidas en tiempo real mediante SSE.
- Listados administrativos paginados, controles de método/CSRF en acciones que cambian datos y soporte de tema claro/oscuro.

## Tecnologías

| Componente | Tecnología |
|---|---|
| Backend | Python 3.11, Django 5 |
| Base de datos | PostgreSQL 16, `pgvector` |
| Procesamiento en segundo plano | Celery, Redis |
| Embeddings | `sentence-transformers` |
| Modelos generativos | Groq y Google Gemini |
| Interfaz | Django Templates, Tailwind CSS compilado |
| Desarrollo/despliegue local | Docker Compose |

## Requisitos

- Docker Engine y Docker Compose v2.
- Git.
- Node.js y npm solo si se modificarán o compilarán los estilos.
- Claves de API de Groq y/o Gemini para realizar consultas con modelos generativos.

## Instalación con Docker Compose

Desde la raíz del repositorio:

1. Crea el archivo de entorno:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Edita `.env`. Configura una `SECRET_KEY` propia, las credenciales de PostgreSQL y las claves de API. Para ejecutar Django directamente en Windows, `POSTGRES_HOST=127.0.0.1` apunta al puerto local de PostgreSQL. **Docker Compose configura `POSTGRES_HOST=db` para los contenedores web y worker**, por lo que se puede conservar el valor local de la plantilla.

   Para desarrollo local con el Compose del proyecto, conserva `CELERY_BROKER_URL=redis://redis:6379/0`. El worker y el servidor web comparten el volumen `media_data` para procesar temporalmente los PDF.

3. Construye e inicia PostgreSQL, Redis, el servidor Django y el worker:

   ```powershell
   docker compose up --build -d
   ```

4. Aplica las migraciones y crea el primer administrador:

   ```powershell
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

5. Abre <http://localhost:8000/>. El sitio principal ofrece el login y registro; el panel administrativo nativo de Django está en <http://localhost:8000/admin/>.

Para observar el procesamiento asíncrono:

```powershell
docker compose logs -f worker
```

Los PDF pendientes se comparten mediante un volumen local de Docker. En un despliegue con varios hosts, reemplaza ese volumen por almacenamiento compartido accesible tanto al servidor web como a los workers.

## Configuración

Las variables se leen desde el entorno o desde `.env` con `python-decouple`. Variables principales:

| Variable | Uso |
|---|---|
| `SECRET_KEY` | Clave de Django. Obligatoria en producción; no usar una clave `django-insecure-`. |
| `DEBUG` | Modo de desarrollo (`True` por defecto). |
| `ALLOWED_HOSTS` | Hosts permitidos separados por comas. |
| `CSRF_TRUSTED_ORIGINS` | Orígenes CSRF separados por comas. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT` | Conexión PostgreSQL. El host predeterminado local es `127.0.0.1`; Compose lo sobrescribe con `db` en web y worker. |
| `CELERY_BROKER_URL` | Broker Celery; en Compose, `redis://redis:6379/0`. |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Minutos de inactividad antes de expirar la sesión (30 por defecto). |
| `GROQ_API_KEY`, `GEMINI_API_KEY` | Credenciales opcionales de los proveedores de IA. |
| `GROQ_MODEL`, `GEMINI_MODEL` | Modelos configurados para Groq y Gemini. |

En producción, configura `DEBUG=False`, una clave secreta fuerte, hosts/orígenes correctos y HTTPS. La configuración activa cookies seguras y HSTS cuando `DEBUG` está desactivado.

## Administración de Django

Los modelos `Usuario`, `LeyFederal`, `FragmentoNormativo`, `Conversacion`, `Mensaje` y `RegistroFalloAPI` están registrados en `/admin/`. La administración de usuarios incluye campos de contraseña y guarda las contraseñas usando el hash de Django.

Los paneles propios están limitados por rol:

- Administrador: `/admin-panel/`, gestión de usuarios y `/admin-panel/fallos-api/`.
- Gestor: `/gestor-panel/`, carga/edición de leyes y chat de prueba.
- Usuario registrado: `/chat/` y `/perfil/`.

## Pruebas

La suite actual tiene 59 pruebas en `apps/core/tests.py`. Cubre el registro de modelos y las vistas/hash de contraseñas en Django Admin, autenticación, redirecciones seguras, CSRF y permisos, paginación, carga y reproceso asíncronos, errores visibles de indexación, extracción/fragmentación de PDF, chat con streaming SSE, cuotas, fallback y búsqueda híbrida.

Con los servicios de Docker iniciados, ejecuta:

```powershell
docker compose exec web python manage.py test apps.core
```

O desde el equipo anfitrión, si tiene instaladas las dependencias y PostgreSQL disponible en `127.0.0.1`:

```powershell
python manage.py test apps.core
```

### Prueba funcional de punta a punta

Este recorrido comprueba el flujo principal desde el inicio de los servicios hasta una respuesta del chat fundamentada en un PDF:

1. **Inicia los servicios** desde la raíz del proyecto:

   ```powershell
   docker compose up --build -d
   docker compose ps
   ```

   Verifica que `db`, `redis`, `web` y `worker` estén activos. Si es la primera ejecución, aplica las migraciones y crea el administrador:

   ```powershell
   docker compose exec web python manage.py migrate
   docker compose exec web python manage.py createsuperuser
   ```

2. **Crea un usuario gestor.** Inicia sesión con el superusuario en <http://localhost:8000/>. En el panel de administración de la aplicación, abre **Crear Usuario**, registra una cuenta y asígnale el rol **Gestor de la Base de Conocimiento**.

3. **Carga un documento normativo.** Cierra sesión, entra con la cuenta gestora y abre **Cargar documento** desde `/gestor-panel/`. Selecciona un PDF de una ley federal, completa los metadatos solicitados y envíalo. La carga se guarda y la extracción, fragmentación y creación de embeddings se ejecutan en segundo plano.

4. **Confirma el resultado del procesamiento.** En `/gestor-panel/`, revisa el estado de indexación de la ley hasta que aparezca como **Lista**. Para observar la ejecución del worker:

   ```powershell
   docker compose logs -f worker
   ```

   Si el estado cambia a **Error**, el dashboard muestra el detalle del procesamiento; revisa también los logs del worker con `docker compose logs worker --tail 100`.

5. **Prueba el registro y el chat.** Cierra sesión de gestor, crea una cuenta desde **Registro** y entra con ella. Abre `/chat/` y pregunta por un artículo o disposición incluida en el PDF cargado. La respuesta debe aparecer progresivamente; al terminar, comprueba las citas normativas y que el intercambio aparezca en el historial.

6. **Comprueba la persistencia.** Recarga la conversación o vuelve a seleccionarla desde el historial: los mensajes y las citas deben conservarse. Para validar cuotas, envía consultas jurídicas con una cuenta registrada y revisa el contador de mensajes restantes.

7. **Ejecuta la suite automatizada** para verificar las funciones cubiertas por tests:

   ```powershell
   docker compose exec web python manage.py test apps.core
   ```

   Para detener los servicios al terminar:

   ```powershell
   docker compose down
   ```

## Estilos

El CSS compilado que sirve Django está en `static/css/dist.css`; su fuente es `static/src/input.css`.

```powershell
npm install
npm run build-css
```

## Estructura principal

```text
apps/core/
  forms/                 Formularios de autenticación, usuarios y gestor
  migrations/            Esquema PostgreSQL y sus índices
  services/              Procesamiento PDF, ingesta, búsqueda y chat
  tasks.py               Tareas Celery
  templates/             Vistas HTML agrupadas por rol
  views/                 Vistas de autenticación, admin, gestor y usuario
  admin.py               Registro y configuración de Django Admin
  middleware.py          Autenticación y expiración por inactividad
  models.py              Usuarios, leyes, fragmentos, conversaciones y eventos
  tests.py               Pruebas automatizadas
config/
  celery.py              Inicialización de Celery
  settings.py            Configuración Django y variables de entorno
docker-compose.yml       PostgreSQL, Redis, servidor web y worker
```

## Seguridad y alcance

La plataforma es una herramienta académica de consulta y no sustituye asesoría jurídica profesional. Las respuestas dependen de la calidad y vigencia de las leyes PDF cargadas. No se recomienda cargar documentos sensibles: los mensajes del chat y los fragmentos se almacenan en la base de datos. Protege las claves de API y el archivo `.env`; no lo publiques ni lo agregues al repositorio.

## Licencia

Desarrollado con fines académicos y de investigación. *Todos los derechos reservados.*

## Asistencia con IA

Para esta actualización se utilizó un asistente de IA (Copilot SDK en VS Code).