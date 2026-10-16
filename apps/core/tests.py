import io
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from pypdf import PdfWriter

from .models import Usuario, LeyFederal, FragmentoNormativo, Conversacion, Mensaje, RegistroFalloAPI
from .services.pdf_processor import procesar_pdf_en_memoria, calcular_hash_fragmento
from .services.chat_service import (
    generar_respuesta_ia_con_fallback,
    MENSAJE_SATURACION_APIS,
)


class UsuarioModelTest(TestCase):
    """
    Pruebas para el modelo Usuario y control de cuotas diarias (RF-1.1, RF-1.2).
    """
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            correo_electronico='test@plurione.com',
            password='password123',
            nombre='Juan',
            apellido='Pérez',
            rol=Usuario.Rol.REGISTRADO,
            cuota_diaria_mensajes=50
        )

    def test_creacion_usuario_defecto(self):
        self.assertEqual(self.usuario.rol, Usuario.Rol.REGISTRADO)
        self.assertEqual(self.usuario.cuota_diaria_mensajes, 50)
        self.assertEqual(self.usuario.mensajes_usados_hoy, 0)
        self.assertTrue(self.usuario.is_active)

    def test_verificar_y_reiniciar_cuota_diaria(self):
        # Simular consumo de mensajes hoy
        self.usuario.mensajes_usados_hoy = 15
        self.usuario.save()

        # Al verificar el mismo día, la cuota no cambia
        self.usuario.verificar_y_reiniciar_cuota_diaria()
        self.assertEqual(self.usuario.mensajes_usados_hoy, 15)

        # Simular que el último reset fue ayer
        self.usuario.fecha_ultimo_reset = timezone.now().date() - timedelta(days=1)
        self.usuario.save()

        # Debe reiniciar a 0 y actualizar la fecha
        self.usuario.verificar_y_reiniciar_cuota_diaria()
        self.assertEqual(self.usuario.mensajes_usados_hoy, 0)
        self.assertEqual(self.usuario.fecha_ultimo_reset, timezone.now().date())


class AutenticacionMiddlewareTest(TestCase):
    """
    Pruebas para verificar la protección global de rutas vía middleware (RF-1.4, RNF-02).
    """
    def setUp(self):
        self.client = Client()
        self.user = Usuario.objects.create_user(
            correo_electronico='registrado@plurione.com',
            password='Password123!',
            nombre='Carlos',
            apellido='López',
            rol=Usuario.Rol.REGISTRADO
        )

    def test_rutas_protegidas_redirigen_a_login(self):
        # Usuario anónimo intentando acceder al chat
        response = self.client.get(reverse('chat_interfaz'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_rutas_publicas_permitidas_sin_autenticacion(self):
        # Login y registro deben ser accesibles
        response_login = self.client.get(reverse('login'))
        self.assertEqual(response_login.status_code, 200)

        response_registro = self.client.get(reverse('registro'))
        self.assertEqual(response_registro.status_code, 200)

    def test_usuario_autenticado_accede_a_sus_rutas(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('chat_interfaz'))
        self.assertEqual(response.status_code, 200)


class ControlDeRolesTest(TestCase):
    """
    Pruebas para verificar el aislamiento y permisos de los 3 roles del sistema.
    """
    def setUp(self):
        self.client = Client()
        self.usuario_registrado = Usuario.objects.create_user(
            correo_electronico='usuario@test.com',
            password='Password123!',
            nombre='Usuario',
            apellido='Común',
            rol=Usuario.Rol.REGISTRADO
        )
        self.usuario_gestor = Usuario.objects.create_user(
            correo_electronico='gestor@test.com',
            password='Password123!',
            nombre='Gestor',
            apellido='Normativo',
            rol=Usuario.Rol.GESTOR
        )

    def test_usuario_registrado_no_puede_acceder_a_panel_admin(self):
        self.client.force_login(self.usuario_registrado)
        response = self.client.get(reverse('admin_dashboard'))
        # admin_required redirige a login con mensaje de error
        self.assertEqual(response.status_code, 302)

    def test_usuario_registrado_no_puede_acceder_a_panel_gestor(self):
        self.client.force_login(self.usuario_registrado)
        response = self.client.get(reverse('gestor_dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_gestor_puede_acceder_a_su_panel(self):
        self.client.force_login(self.usuario_gestor)
        response = self.client.get(reverse('gestor_dashboard'))
        self.assertEqual(response.status_code, 200)


class PDFProcessorTest(TestCase):
    """
    Pruebas de procesamiento de PDF en memoria (RF-2.1, RF-2.2, RNF-02).
    """
    def _crear_pdf_en_memoria(self, contenido="Artículo 1. La presente ley es de observancia general en toda la República Mexicana."):
        buffer = io.BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        # Escribir metadata básica
        writer.write(buffer)
        buffer.seek(0)
        return buffer

    def test_calcular_hash_fragmento(self):
        texto = "Texto legal de prueba"
        hash_calc = calcular_hash_fragmento(texto)
        self.assertEqual(len(hash_calc), 64)

    def test_rechazo_archivo_no_valido(self):
        archivo_invalido = io.BytesIO(b"No soy un archivo PDF real")
        with self.assertRaises(ValidationError):
            procesar_pdf_en_memoria(archivo_invalido)


class ChatServiceFallbackTest(TestCase):
    """
    Pruebas de conmutación automática de LLMs y tolerancia a fallos (RF-3.2, RF-3.3, RNF-04).
    """
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            correo_electronico='chat@test.com',
            password='Password123!',
            nombre='Tester',
            apellido='Chat'
        )

    def test_guardrail_contexto_vacio(self):
        respuesta, proveedor, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario="¿Qué dice el artículo 1?",
            contexto_normativo="",
            usuario=self.usuario
        )
        self.assertIn("No se encontró información suficiente", respuesta)
        self.assertFalse(conmutado)

    @patch('apps.core.services.chat_service.get_groq_client')
    def test_flujo_exitoso_groq(self, mock_get_groq):
        # Mock de respuesta de Groq
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "El artículo 123 establece el derecho al trabajo digno."
        mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_get_groq.return_value = mock_client

        contexto = "Artículo 123. Toda persona tiene derecho al trabajo digno y socialmente útil."
        pregunta = "¿Qué dice el artículo 123?"

        respuesta, proveedor, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario=pregunta,
            contexto_normativo=contexto,
            usuario=self.usuario
        )

        self.assertEqual(respuesta, "El artículo 123 establece el derecho al trabajo digno.")
        self.assertEqual(proveedor, Mensaje.ProveedorIA.GROQ)
        self.assertFalse(conmutado)
        self.assertEqual(RegistroFalloAPI.objects.count(), 0)

    @patch('apps.core.services.chat_service.get_gemini_client')
    @patch('apps.core.services.chat_service.get_groq_client')
    def test_conmutacion_fallback_a_gemini_por_fallo_groq(self, mock_get_groq, mock_get_gemini):
        # Groq falla con simulación de Rate Limit (HTTP 429)
        mock_groq = MagicMock()
        mock_groq.chat.completions.create.side_effect = Exception("Rate limit reached 429")
        mock_get_groq.return_value = mock_groq

        # Gemini responde con éxito
        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Respuesta generada por Gemini tras conmutación."
        mock_gemini.models.generate_content.return_value = mock_response
        mock_get_gemini.return_value = mock_gemini

        contexto = "Artículo 1. Disposición federal general."
        pregunta = "Explica el artículo 1"

        respuesta, proveedor, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario=pregunta,
            contexto_normativo=contexto,
            usuario=self.usuario
        )

        self.assertEqual(respuesta, "Respuesta generada por Gemini tras conmutación.")
        self.assertEqual(proveedor, Mensaje.ProveedorIA.GEMINI)
        self.assertTrue(conmutado)

        # Se debe registrar el fallo en RegistroFalloAPI con exitoso=True
        self.assertEqual(RegistroFalloAPI.objects.count(), 1)
        registro = RegistroFalloAPI.objects.first()
        self.assertEqual(registro.proveedor_solicitado, Mensaje.ProveedorIA.GROQ)
        self.assertEqual(registro.conmutado_a, Mensaje.ProveedorIA.GEMINI)
        self.assertTrue(registro.exitoso)

    @patch('apps.core.services.chat_service.get_gemini_client')
    @patch('apps.core.services.chat_service.get_groq_client')
    def test_ambos_proveedores_fallan_notificacion_amigable(self, mock_get_groq, mock_get_gemini):
        # Groq y Gemini fallan
        mock_groq = MagicMock()
        mock_groq.chat.completions.create.side_effect = Exception("Groq Timeout")
        mock_get_groq.return_value = mock_groq

        mock_gemini = MagicMock()
        mock_gemini.models.generate_content.side_effect = Exception("Gemini 429 Quota Exceeded")
        mock_get_gemini.return_value = mock_gemini

        contexto = "Artículo 1. Disposición legal."
        pregunta = "¿Qué dice la ley?"

        respuesta, proveedor, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario=pregunta,
            contexto_normativo=contexto,
            usuario=self.usuario
        )

        # Debe retornar mensaje amigable de saturación sin romper la sesión (RF-3.3)
        self.assertEqual(respuesta, MENSAJE_SATURACION_APIS)
        self.assertTrue(conmutado)

        # Registro del fallo de conmutación no exitosa
        self.assertEqual(RegistroFalloAPI.objects.count(), 1)
        registro = RegistroFalloAPI.objects.first()
        self.assertFalse(registro.exitoso)


class BusquedaHibridaTest(TestCase):
    """
    Pruebas para la búsqueda híbrida (Vectorial + Full-Text Search) con RRF.
    """
    def setUp(self):
        self.ley = LeyFederal.objects.create(
            nombre="Ley Federal del Trabajo",
            siglas="LFT",
            fecha_publicacion=timezone.now().date(),
            hash_documento="dummyhashlft1234567890abcdef1234567890abcdef1234567890abcdef1234",
            estado=LeyFederal.Estado.VIGENTE
        )
        # Vector dummy de 768 ceros con un valor inicial
        dummy_vector = [0.0] * 768
        dummy_vector[0] = 0.5
        self.fragmento = FragmentoNormativo.objects.create(
            ley=self.ley,
            contenido_fragmento="Artículo 123. Toda persona tiene derecho al trabajo digno y socialmente útil.",
            vector_embedding=dummy_vector,
            hash_fragmento="hashfragmento123dummyabcdef1234567890abcdef1234567890abcdef1234"
        )

    @patch('apps.core.services.chat_service.generar_vector_embedding')
    def test_busqueda_hibrida_recupera_fragmentos_y_citas(self, mock_generar_vector):
        dummy_query_vector = [0.0] * 768
        dummy_query_vector[0] = 0.5
        mock_generar_vector.return_value = dummy_query_vector

        from .services.chat_service import buscar_contexto_normativo
        contexto, citas = buscar_contexto_normativo("artículo 123 trabajo digno", top_k=2)

        self.assertIn("Ley Federal del Trabajo", contexto)
        self.assertIn("Artículo 123", contexto)
        self.assertEqual(len(citas), 1)
        self.assertEqual(citas[0]['siglas'], "LFT")
