import io
import json
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfWriter

from .models import Usuario, LeyFederal, FragmentoNormativo, Conversacion, Mensaje, RegistroFalloAPI
from .forms.auth_forms import RegistroForm
from .forms.gestor_forms import CargarDocumentoForm
from .services.pdf_processor import (
    _fragmentar_texto_estructurado,
    extraer_articulo_fragmento,
    procesar_pdf_en_memoria,
    calcular_hash_fragmento,
    extraer_metadatos_primera_pagina,
)
from .services.chat_service import (
    clasificar_mensaje,
    generar_respuesta_ia_con_fallback,
    MENSAJE_SATURACION_APIS,
)


class FrontendFormValidationTest(TestCase):
    def test_registro_exige_password_con_letra_numero_y_caracter_especial(self):
        datos = {
            'nombre': 'Ana',
            'apellido': 'García',
            'correo_electronico': 'ana@example.com',
            'password': 'Password123!',
            'password_confirmacion': 'Password123!',
        }
        self.assertTrue(RegistroForm(data=datos).is_valid())

        for password in ('password1', 'Password!', 'Password123'):
            datos['password'] = password
            datos['password_confirmacion'] = password
            form = RegistroForm(data=datos)
            self.assertFalse(form.is_valid(), password)
            self.assertIn('password', form.errors, password)

    def test_nombre_de_ley_acepta_hasta_500_caracteres(self):
        datos = {
            'nombre': 'L' * 500,
            'siglas': 'LF',
            'fecha_publicacion': '2025-01-01',
            'fecha_ultima_reforma': '',
            'estado': LeyFederal.Estado.VIGENTE,
        }
        archivo = SimpleUploadedFile('ley.pdf', b'%PDF-1.4', content_type='application/pdf')
        form = CargarDocumentoForm(data=datos, files={'archivo_pdf': archivo})
        self.assertTrue(form.is_valid(), form.errors)

        datos['nombre'] += 'L'
        archivo = SimpleUploadedFile('ley.pdf', b'%PDF-1.4', content_type='application/pdf')
        form = CargarDocumentoForm(data=datos, files={'archivo_pdf': archivo})
        self.assertFalse(form.is_valid())
        self.assertIn('nombre', form.errors)


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

    @patch('apps.core.views.gestor.gestor_views.generar_vector_embedding')
    @patch('apps.core.views.gestor.gestor_views.procesar_pdf_en_memoria')
    def test_carga_no_vuelve_a_indexar_contenido_ya_registrado(self, mock_procesar, mock_embedding):
        self.client.force_login(self.usuario_gestor)
        LeyFederal.objects.create(
            nombre='Ley ya cargada',
            fecha_publicacion=timezone.now().date(),
            hash_documento='a' * 64,
            hash_contenido='b' * 64,
        )
        mock_procesar.return_value = ('c' * 64, 'b' * 64, ['Texto ya indexado'])

        response = self.client.post(
            reverse('gestor_cargar_documento'),
            {
                'nombre': 'Copia de la ley',
                'siglas': 'CDL',
                'fecha_publicacion': '2020-01-01',
                'fecha_ultima_reforma': '',
                'estado': LeyFederal.Estado.VIGENTE,
                'archivo_pdf': SimpleUploadedFile('ley.pdf', b'contenido pdf', content_type='application/pdf'),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(LeyFederal.objects.count(), 1)
        mock_embedding.assert_not_called()

    @patch('apps.core.views.gestor.gestor_views.generar_vector_embedding')
    @patch('apps.core.views.gestor.gestor_views.procesar_pdf_en_memoria')
    def test_edicion_con_mismo_contenido_conserva_fragmentos(self, mock_procesar, mock_embedding):
        self.client.force_login(self.usuario_gestor)
        ley = LeyFederal.objects.create(
            nombre='Ley existente',
            fecha_publicacion=timezone.now().date(),
            hash_documento='a' * 64,
            hash_contenido='b' * 64,
        )
        FragmentoNormativo.objects.create(
            ley=ley,
            contenido_fragmento='Artículo 1. Texto vigente.',
            vector_embedding=[0.0] * 768,
            hash_fragmento='c' * 64,
        )
        mock_procesar.return_value = ('d' * 64, 'b' * 64, ['Artículo 1. Texto vigente.'])

        response = self.client.post(
            reverse('gestor_editar_ley', args=[ley.id]),
            {
                'nombre': ley.nombre,
                'siglas': '',
                'fecha_ultima_reforma': '2025-01-01',
                'estado': LeyFederal.Estado.VIGENTE,
                'archivo_pdf': SimpleUploadedFile('ley.pdf', b'contenido pdf', content_type='application/pdf'),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ley.fragmentos.count(), 1)
        mock_embedding.assert_not_called()

    @patch('apps.core.views.gestor.gestor_views.generar_vector_embedding', return_value=[0.0] * 768)
    def test_fragmento_repetido_se_deduplica_en_una_ley_y_se_acepta_en_otra(self, mock_embedding):
        from .views.gestor.gestor_views import _crear_fragmentos_normativos

        ley_a = LeyFederal.objects.create(
            nombre='Ley A',
            fecha_publicacion=timezone.now().date(),
            hash_documento='1' * 64,
        )
        ley_b = LeyFederal.objects.create(
            nombre='Ley B',
            fecha_publicacion=timezone.now().date(),
            hash_documento='2' * 64,
        )
        repetido = 'Artículo 1. Texto legal compartido.'

        FragmentoNormativo.objects.bulk_create(
            _crear_fragmentos_normativos(ley_a, [repetido, repetido])
        )
        FragmentoNormativo.objects.bulk_create(
            _crear_fragmentos_normativos(ley_b, [repetido])
        )

        self.assertEqual(FragmentoNormativo.objects.count(), 2)
        self.assertEqual(ley_a.fragmentos.count(), 1)
        self.assertEqual(ley_b.fragmentos.count(), 1)
        self.assertEqual(ley_a.fragmentos.get().articulo, '1')
        self.assertEqual(mock_embedding.call_count, 2)


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
        self.assertNotEqual(calcular_hash_fragmento(texto, 1), calcular_hash_fragmento(texto, 2))
        self.assertEqual(
            calcular_hash_fragmento(texto, 1),
            calcular_hash_fragmento(f" {texto}  ", 1),
        )

    def test_extrae_numero_de_articulo_y_articulo_bis(self):
        self.assertEqual(extraer_articulo_fragmento("Artículo 12. Texto."), "12")
        self.assertEqual(extraer_articulo_fragmento("ARTÍCULO 12 bis. Texto."), "12 Bis")
        self.assertEqual(extraer_articulo_fragmento("Texto sin encabezado."), "")

        chunks = _fragmentar_texto_estructurado(
            "Artículo 1. Descanso semanal.\n\nArtículo 2 Bis. Duración.",
            chunk_size=100,
        )
        self.assertEqual(len(chunks), 2)
        self.assertEqual(extraer_articulo_fragmento(chunks[0]), "1")
        self.assertEqual(extraer_articulo_fragmento(chunks[1]), "2 Bis")

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_extrae_sugerencias_de_la_primera_pagina(self, mock_pdf_reader):
        pagina = MagicMock()
        pagina.extract_text.return_value = (
            'LEY FEDERAL DEL TRABAJO (LFT)\n'
            'Publicada en el Diario Oficial de la Federación el 1 de abril de 1970.\n'
            'Última reforma publicada el 24 de enero de 2024.'
        )
        mock_pdf_reader.return_value.pages = [pagina]
        archivo = io.BytesIO(b'contenido PDF')
        archivo.name = 'ley.pdf'

        metadatos = extraer_metadatos_primera_pagina(archivo)

        self.assertEqual(metadatos['nombre'], 'LEY FEDERAL DEL TRABAJO (LFT)')
        self.assertEqual(metadatos['siglas'], 'LFT')
        self.assertEqual(metadatos['fecha_publicacion'], '1970-04-01')
        self.assertEqual(metadatos['fecha_ultima_reforma'], '2024-01-24')

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_siglas_se_forman_del_nombre_y_fechas_separan_publicacion_de_reforma(self, mock_pdf_reader):
        pagina = MagicMock()
        pagina.extract_text.return_value = (
            'LEY GENERAL DE LOS DERECHOS DE NIÑAS, NIÑOS Y ADOLESCENTES\n'
            '1 de diciembre de 2014\n'
            'TEXTO VIGENTE\n'
            'Última reforma publicada el 11 de enero de 2021.'
        )
        mock_pdf_reader.return_value.pages = [pagina]
        archivo = io.BytesIO(b'contenido PDF')
        archivo.name = 'ley.pdf'

        metadatos = extraer_metadatos_primera_pagina(archivo)

        self.assertEqual(metadatos['siglas'], 'LGDNNA')
        self.assertEqual(metadatos['fecha_publicacion'], '2014-12-01')
        self.assertEqual(metadatos['fecha_ultima_reforma'], '2021-01-11')

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_no_inventa_fecha_de_reforma_si_no_se_indica(self, mock_pdf_reader):
        pagina = MagicMock()
        pagina.extract_text.return_value = (
            'CÓDIGO CIVIL FEDERAL\n'
            'Publicado en el Diario Oficial de la Federación el 26 de mayo de 1928.\n'
            'TEXTO VIGENTE'
        )
        mock_pdf_reader.return_value.pages = [pagina]
        archivo = io.BytesIO(b'contenido PDF')
        archivo.name = 'codigo.pdf'

        metadatos = extraer_metadatos_primera_pagina(archivo)

        self.assertEqual(metadatos['fecha_publicacion'], '1928-05-26')
        self.assertEqual(metadatos['fecha_ultima_reforma'], '')

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_nombre_oficial_largo_solicita_captura_manual(self, mock_pdf_reader):
        pagina = MagicMock()
        pagina.extract_text.return_value = (
            'LEY PARA LA ORGANIZACIÓN Y FUNCIONAMIENTO DE LAS AUTORIDADES '
            'COMPETENTES EN MATERIA DE DESARROLLO SOCIAL Y ECONOMICO'
        )
        mock_pdf_reader.return_value.pages = [pagina]
        archivo = io.BytesIO(b'contenido PDF')
        archivo.name = 'ley.pdf'

        metadatos = extraer_metadatos_primera_pagina(archivo)

        self.assertTrue(metadatos['requiere_captura_manual'])

    def test_rechazo_archivo_no_valido(self):
        archivo_invalido = io.BytesIO(b"No soy un archivo PDF real")
        with self.assertRaises(ValidationError):
            procesar_pdf_en_memoria(archivo_invalido)

    def test_fragmentacion_cubre_texto_largo_y_respeta_limite(self):
        texto = " ".join(f"palabra{i}" for i in range(300))

        chunks = _fragmentar_texto_estructurado(texto, chunk_size=120, overlap=20)
        palabras_extraidas = iter(" ".join(chunks).split())

        self.assertTrue(all(len(chunk) <= 120 for chunk in chunks))
        for palabra in texto.split():
            self.assertIn(palabra, palabras_extraidas)

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_hash_contenido_no_cambia_por_bytes_o_metadatos_del_pdf(self, mock_pdf_reader):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Artículo 1.\nTexto de la ley."
        mock_pdf_reader.return_value.pages = [mock_page]

        hash_archivo_a, hash_contenido_a, chunks_a = procesar_pdf_en_memoria(io.BytesIO(b"pdf con metadata A"))
        hash_archivo_b, hash_contenido_b, chunks_b = procesar_pdf_en_memoria(io.BytesIO(b"pdf con metadata B"))

        self.assertNotEqual(hash_archivo_a, hash_archivo_b)
        self.assertEqual(hash_contenido_a, hash_contenido_b)
        self.assertEqual(chunks_a, chunks_b)


class ChatIntentTest(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(
            correo_electronico='chat-intent@test.com',
            password='Password123!',
            nombre='Usuario',
            apellido='Registrado',
            rol=Usuario.Rol.REGISTRADO,
        )
        self.gestor = Usuario.objects.create_user(
            correo_electronico='gestor-intent@test.com',
            password='Password123!',
            nombre='Usuario',
            apellido='Gestor',
            rol=Usuario.Rol.GESTOR,
        )
        self.client = Client()

    def test_clasifica_saludos_aclaraciones_y_consultas_juridicas(self):
        self.assertEqual(clasificar_mensaje('Hola, buenas tardes'), 'saludo')
        self.assertEqual(clasificar_mensaje('No es lo que dije'), 'juridico')
        self.assertEqual(clasificar_mensaje('Gracias'), 'cortesia')
        self.assertEqual(
            clasificar_mensaje('¿Qué derechos tengo si me despidieron?'),
            'juridico',
        )
        self.assertEqual(clasificar_mensaje('¿Cuánto cuesta un boleto?'), 'juridico')

    @patch('apps.core.services.chat_service.generar_respuesta_ia_con_fallback')
    @patch('apps.core.services.chat_service.buscar_contexto_normativo')
    def test_saludo_usuario_no_consulta_leyes_ni_usa_ia(self, mock_buscar, mock_generar):
        self.client.force_login(self.usuario)
        response = self.client.post(
            reverse('chat_api_enviar'),
            data='{"mensaje":"Hola, buenas tardes"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['respuesta']['proveedor'])
        self.assertEqual(response.json()['respuesta']['citas'], [])
        self.assertIn('¡Hola!', response.json()['respuesta']['contenido'])
        mock_buscar.assert_not_called()
        mock_generar.assert_not_called()

    @patch('apps.core.services.chat_service.generar_respuesta_ia_con_fallback')
    @patch('apps.core.services.chat_service.buscar_contexto_normativo')
    def test_mensaje_no_social_del_gestor_consulta_normativa(self, mock_buscar, mock_generar):
        mock_buscar.return_value = ('Contexto legal', [])
        mock_generar.return_value = ('No se encontró información', Mensaje.ProveedorIA.GROQ, False)
        self.client.force_login(self.gestor)
        response = self.client.post(
            reverse('gestor_enviar_mensaje_api'),
            data='{"mensaje":"¿Cuánto cuesta un boleto?"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        mock_buscar.assert_called_once_with('¿Cuánto cuesta un boleto?')
        mock_generar.assert_called_once()

    def test_endpoint_rechaza_mensajes_invalidos_o_muy_largos(self):
        self.client.force_login(self.usuario)
        url = reverse('chat_api_enviar')

        for body in (b'{', b'{"mensaje":12}', b'{"mensaje":"   "}', b'{"mensaje":"' + b'a' * 4001 + b'"}'):
            response = self.client.generic(
                'POST',
                url,
                data=body,
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 400)

    @patch('apps.core.services.chat_service.generar_respuesta_ia_con_fallback')
    @patch('apps.core.services.chat_service.buscar_contexto_normativo')
    def test_consulta_juridica_si_consulta_la_base_normativa(self, mock_buscar, mock_generar):
        mock_buscar.return_value = (
            'Contexto legal',
            [{'ley': 'Ley Federal', 'siglas': 'LF', 'articulo': '123'}],
        )
        mock_generar.return_value = ('Respuesta jurídica', Mensaje.ProveedorIA.GROQ, False)
        self.client.force_login(self.usuario)
        response = self.client.post(
            reverse('chat_api_enviar'),
            data='{"mensaje":"¿Qué derechos tengo si me despidieron?"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        mock_buscar.assert_called_once_with('¿Qué derechos tengo si me despidieron?')
        mock_generar.assert_called_once()
        self.assertEqual(response.json()['respuesta']['citas'][0]['ley'], 'Ley Federal')
        self.assertEqual(response.json()['respuesta']['citas'][0]['articulo'], '123')

    @patch('apps.core.services.chat_service.generar_respuesta_ia_con_fallback')
    @patch('apps.core.services.chat_service.buscar_contexto_normativo')
    def test_consultas_sobre_lft_imss_e_isr_buscan_normativa(self, mock_buscar, mock_generar):
        preguntas = (
            '¿Qué dice la LFT sobre el descanso semanal?',
            '¿Cuántos días de incapacidad da el IMSS?',
            '¿Cómo se calcula el ISR?',
        )
        mock_buscar.return_value = ('Contexto legal', [])
        mock_generar.return_value = ('Respuesta jurídica', Mensaje.ProveedorIA.GROQ, False)
        self.client.force_login(self.usuario)

        for pregunta in preguntas:
            response = self.client.post(
                reverse('chat_api_enviar'),
                data=json.dumps({'mensaje': pregunta}),
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 200)

        self.assertEqual(
            [llamada.args[0] for llamada in mock_buscar.call_args_list],
            list(preguntas),
        )
        self.assertEqual(mock_generar.call_count, len(preguntas))


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
            articulo="123",
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
        self.assertIn("· Art. 123 ---", contexto)
        self.assertIn("Artículo 123", contexto)
        self.assertEqual(len(citas), 1)
        self.assertEqual(citas[0]['siglas'], "LFT")
        self.assertEqual(citas[0]['articulo'], "123")
