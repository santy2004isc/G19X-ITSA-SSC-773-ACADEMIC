import uuid
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from pgvector.django import VectorField
from django.utils import timezone

# ==========================================
# 1. MANAGER Y MODELO DE USUARIO
# ==========================================

class UsuarioManager(BaseUserManager):
    """
    Manager personalizado para el modelo Usuario utilizando el correo electrónico
    como identificador principal.
    """
    def create_user(self, correo_electronico, password=None, **extra_fields):
        if not correo_electronico:
            raise ValueError('El correo electrónico es obligatorio.')
        correo_electronico = self.normalize_email(correo_electronico)
        user = self.model(correo_electronico=correo_electronico, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, correo_electronico, password=None, **extra_fields):
        extra_fields.setdefault('rol', Usuario.Rol.ADMINISTRADOR)
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('activo', True)

        if extra_fields.get('is_staff') is not True:
            raise ValueError('El superusuario debe tener is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('El superusuario debe tener is_superuser=True.')

        return self.create_user(correo_electronico, password, **extra_fields)


class Usuario(AbstractBaseUser, PermissionsMixin):
    """
    Tabla: usuarios
    Gestión de cuentas para los roles del sistema (Administrador, Gestor, Usuario Registrado).
    """
    class Rol(models.TextChoices):
        ADMINISTRADOR = 'ADMINISTRADOR', 'Administrador'
        GESTOR = 'GESTOR', 'Gestor de la Base de Conocimiento'
        REGISTRADO = 'REGISTRADO', 'Usuario Registrado'

    correo_electronico = models.EmailField(
        max_length=254, 
        unique=True, 
        verbose_name='Correo Electrónico'
    )
    nombre = models.CharField(max_length=60, verbose_name='Nombre(s)')
    apellido = models.CharField(max_length=60, verbose_name='Apellidos')
    rol = models.CharField(
        max_length=30, 
        choices=Rol.choices, 
        default=Rol.REGISTRADO, 
        verbose_name='Rol'
    )
    cuota_diaria_mensajes = models.IntegerField(
        default=50, 
        verbose_name='Cuota diaria de mensajes'
    )
    mensajes_usados_hoy = models.IntegerField(
        default=0, 
        verbose_name='Mensajes usados hoy'
    )
    fecha_ultimo_reset = models.DateField(default=timezone.now, verbose_name='Fecha de último reset de cuota')
    activo = models.BooleanField(default=True, verbose_name='Cuenta activa')
    is_staff = models.BooleanField(default=False, verbose_name='Es Staff')
    fecha_registro = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha de registro'
    )

    objects = UsuarioManager()

    USERNAME_FIELD = 'correo_electronico'
    REQUIRED_FIELDS = ['nombre', 'apellido']

    class Meta:
        db_table = 'usuarios'
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'

    @property
    def is_active(self):
        return self.activo

    def __str__(self):
        return f"{self.nombre} {self.apellido} ({self.correo_electronico})"

    def verificar_y_reiniciar_cuota_diaria(self):
        """
        Verifica si cambió el día. De ser así, reinicia el contador de mensajes.
        """
        hoy = timezone.now().date()
        fecha_reset = self.fecha_ultimo_reset
        if hasattr(fecha_reset, 'date'):
            fecha_reset = fecha_reset.date()

        if fecha_reset != hoy:
            self.mensajes_usados_hoy = 0
            self.fecha_ultimo_reset = hoy
            self.save(update_fields=['mensajes_usados_hoy', 'fecha_ultimo_reset'])


# ==========================================
# 2. TABLA: leyes_federales
# ==========================================

class LeyFederal(models.Model):
    """
    Tabla: leyes_federales
    Registro principal de las leyes ingresadas al sistema.
    """
    class Estado(models.TextChoices):
        VIGENTE = 'VIGENTE', 'Vigente'
        ABROGADA = 'ABROGADA', 'Abrogada'

    usuario = models.ForeignKey(
        Usuario, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='leyes_registradas',
        verbose_name='Usuario Gestor'
    )
    nombre = models.CharField(
        max_length=500, 
        unique=True, 
        verbose_name='Nombre oficial'
    )
    siglas = models.CharField(
        max_length=50, 
        null=True, 
        blank=True, 
        db_index=True, 
        verbose_name='Siglas/Abreviatura'
    )
    fecha_publicacion = models.DateField(verbose_name='Fecha de publicación')
    fecha_ultima_reforma = models.DateField(null=True, blank=True, verbose_name='Fecha de última reforma')
    version_actual = models.IntegerField(
        default=1, 
        verbose_name='Versión actual'
    )
    hash_documento = models.CharField(
        max_length=64, 
        unique=True, 
        verbose_name='Hash SHA-256 del documento'
    )
    estado = models.CharField(
        max_length=20, 
        choices=Estado.choices, 
        default=Estado.VIGENTE, 
        verbose_name='Estado normativo'
    )
    fecha_creacion = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha de creación'
    )

    class Meta:
        db_table = 'leyes_federales'
        verbose_name = 'Ley Federal'
        verbose_name_plural = 'Leyes Federales'

    def __str__(self):
        return self.siglas if self.siglas else self.nombre[:50]


# ==========================================
# 3. TABLA: fragmentos_normativos
# ==========================================

class FragmentoNormativo(models.Model):
    """
    Tabla: fragmentos_normativos
    Fragmentos de texto (chunks) vectorizados con sentence-transformers (dim=768).
    """
    ley = models.ForeignKey(
        LeyFederal, 
        on_delete=models.CASCADE, 
        related_name='fragmentos',
        verbose_name='Ley asociada'
    )
    contenido_fragmento = models.TextField(verbose_name='Contenido del fragmento')
    vector_embedding = VectorField(
        dimensions=768, 
        verbose_name='Vector Embedding (768 dims)'
    )
    hash_fragmento = models.CharField(
        max_length=64, 
        unique=True, 
        verbose_name='Hash SHA-256 del fragmento'
    )
    fecha_creacion = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha de creación'
    )

    class Meta:
        db_table = 'fragmentos_normativos'
        verbose_name = 'Fragmento Normativo'
        verbose_name_plural = 'Fragmentos Normativos'

    def __str__(self):
        return f"Fragmento {self.id} - Ley: {self.ley.siglas or self.ley.id}"


# ==========================================
# 4. TABLA: conversacion
# ==========================================

class Conversacion(models.Model):
    """
    Tabla: conversacion
    Contenedor del historial de chat de usuarios autenticados.
    """
    id = models.UUIDField(
        primary_key=True, 
        default=uuid.uuid4, 
        editable=False
    )
    usuario = models.ForeignKey(
        Usuario, 
        on_delete=models.CASCADE, 
        related_name='conversaciones',
        verbose_name='Usuario'
    )
    titulo = models.CharField(
        max_length=200, 
        default="Consulta Normativa", 
        verbose_name='Título de la conversación'
    )
    fecha_creacion = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha de inicio'
    )

    class Meta:
        db_table = 'conversacion'
        verbose_name = 'Conversación'
        verbose_name_plural = 'Conversaciones'

    def __str__(self):
        return f"{self.titulo} - {self.usuario.correo_electronico}"


# ==========================================
# 5. TABLA: mensajes
# ==========================================

class Mensaje(models.Model):
    """
    Tabla: mensajes
    Detalle de consultas y respuestas generadas.
    """
    class Emisor(models.TextChoices):
        USUARIO = 'USUARIO', 'Usuario'
        SISTEMA = 'SISTEMA', 'Sistema'

    class ProveedorIA(models.TextChoices):
        GROQ = 'GROQ', 'Groq Cloud'
        GEMINI = 'GEMINI', 'Google Gemini'

    conversacion = models.ForeignKey(
        Conversacion, 
        on_delete=models.CASCADE, 
        related_name='mensajes',
        verbose_name='Conversación'
    )
    emisor = models.CharField(
        max_length=20, 
        choices=Emisor.choices, 
        verbose_name='Emisor'
    )
    contenido_texto = models.TextField(verbose_name='Contenido del mensaje')
    proveedor_ia = models.CharField(
        max_length=20, 
        choices=ProveedorIA.choices, 
        null=True, 
        blank=True, 
        verbose_name='Proveedor de IA seleccionado'
    )
    conmutado_fallback = models.BooleanField(
        default=False, 
        verbose_name='¿Conmutado por Fallback?'
    )
    citas = models.JSONField(
        null=True, 
        blank=True, 
        verbose_name='Citas normativas'
    )
    fecha_envio = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha de envío'
    )

    class Meta:
        db_table = 'mensajes'
        verbose_name = 'Mensaje'
        verbose_name_plural = 'Mensajes'

    def __str__(self):
        return f"[{self.emisor}] {self.conversacion.id} - {self.fecha_envio.strftime('%Y-%m-%d %H:%M')}"


# ==========================================
# 6. TABLA: registros_fallos_api
# ==========================================

class RegistroFalloAPI(models.Model):
    """
    Tabla: registros_fallos_api
    Auditoría de conmutación y monitoreo de límites de tasa (Rate Limits).
    """
    class ProveedorIA(models.TextChoices):
        GROQ = 'GROQ', 'Groq Cloud'
        GEMINI = 'GEMINI', 'Google Gemini'

    usuario = models.ForeignKey(
        Usuario, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='fallos_api',
        verbose_name='Usuario'
    )
    mensaje = models.ForeignKey(
        Mensaje, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='fallos_api',
        verbose_name='Mensaje asociado'
    )
    proveedor_solicitado = models.CharField(
        max_length=20, 
        choices=ProveedorIA.choices, 
        verbose_name='Proveedor solicitado'
    )
    codigo_error = models.CharField(
        max_length=50, 
        null=True, 
        blank=True, 
        verbose_name='Código de error / Excepción'
    )
    conmutado_a = models.CharField(
        max_length=20, 
        choices=ProveedorIA.choices, 
        null=True, 
        blank=True, 
        verbose_name='Conmutado a (Respaldo)'
    )
    exitoso = models.BooleanField(
        default=False, 
        verbose_name='¿Conmutación exitosa?'
    )
    fecha_evento = models.DateTimeField(
        auto_now_add=True, 
        verbose_name='Fecha del evento'
    )

    class Meta:
        db_table = 'registros_fallos_api'
        verbose_name = 'Registro de Fallo de API'
        verbose_name_plural = 'Registros de Fallos de API'

    def __str__(self):
        return f"Fallo {self.proveedor_solicitado} ({self.codigo_error}) - {self.fecha_evento}"