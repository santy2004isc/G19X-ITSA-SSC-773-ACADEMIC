import io
import json
from datetime import timedelta
import time
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from pypdf import PdfWriter

from .models import Usuario, LeyFederal, FragmentoNormativo, Conversacion, Mensaje, RegistroFalloAPI
from .admin import UsuarioAdminCreationForm
from .forms.auth_forms import LoginForm, RegistroForm
from .forms.admin_forms import CrearUsuarioAdminForm, EditarUsuarioAdminForm
from .forms.gestor_forms import CargarDocumentoForm, EditarPerfilGestorForm
from .forms.profile_forms import EditarPerfilForm
from .forms.usuario_forms import EditarPerfilUsuarioForm
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
    generar_respuesta_ia_stream,
    MENSAJE_SATURACION_APIS,
    preparar_contexto_conversacion,
    resumir_historial_mensajes,
)


class DjangoAdminRegistrationTest(TestCase):
    def test_todos_los_modelos_del_dominio_estan_registrados(self):
        from django.contrib import admin

        for model in (
            Usuario,
            LeyFederal,
            FragmentoNormativo,
            Conversacion,
            Mensaje,
            RegistroFalloAPI,
        ):
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)

    def test_formulario_admin_guarda_contrasena_con_hash(self):
        from .admin import UsuarioAdminCreationForm

        form = UsuarioAdminCreationForm(data={
            'correo_electronico': 'nuevo-admin@test.com',
            'nombre': 'Nuevo',
            'apellido': 'Administrador',
            'rol': Usuario.Rol.REGISTRADO,
            'activo': True,
            'is_staff': False,
            'is_superuser': False,
            'password1': 'Password123!',
            'password2': 'Password123!',
        })

        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        self.assertTrue(usuario.check_password('Password123!'))

    def test_vistas_admin_de_usuario_se_renderizan(self):
        administrador = Usuario.objects.create_user(
            correo_electronico='admin-site@test.com',
            password='Password123!',
            nombre='Admin',
            apellido='Sitio',
            rol=Usuario.Rol.ADMINISTRADOR,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(administrador)

        alta = self.client.get(reverse('admin:core_usuario_add'))
        cambio = self.client.get(reverse('admin:core_usuario_change', args=[administrador.pk]))

        self.assertEqual(alta.status_code, 200)
        self.assertContains(alta, 'password1')
        self.assertEqual(cambio.status_code, 200)

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

    def test_registro_normaliza_correo_a_minusculas_y_sin_espacios_laterales(self):
        datos = {
            'nombre': 'Ana',
            'apellido': 'García',
            'correo_electronico': '  Ana.Ejemplo@Example.COM  ',
            'password': 'Password123!',
            'password_confirmacion': 'Password123!',
        }

        form = RegistroForm(data=datos)

        self.assertTrue(form.is_valid(), form.errors)
        usuario = form.save()
        self.assertEqual(usuario.correo_electronico, 'ana.ejemplo@example.com')

    def test_todos_los_formularios_de_usuario_normalizan_correo(self):
        formularios = (
            LoginForm,
            RegistroForm,
            CrearUsuarioAdminForm,
            EditarUsuarioAdminForm,
            EditarPerfilUsuarioForm,
            EditarPerfilForm,
            EditarPerfilGestorForm,
            UsuarioAdminCreationForm,
        )
        for formulario in formularios:
            with self.subTest(formulario=formulario.__name__):
                instancia = formulario(data={
                    'correo_electronico': '  Ana.Ejemplo@Example.COM  ',
                })
                instancia.full_clean()

                self.assertEqual(instancia.cleaned_data['correo_electronico'], 'ana.ejemplo@example.com')

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

    def test_guardar_usuario_directamente_normaliza_correo(self):
        usuario = Usuario(
            correo_electronico='  MiCorreo@Example.COM  ',
            nombre='Correo',
            apellido='Normalizado',
        )
        usuario.set_password('Password123!')

        usuario.save()

        self.assertEqual(usuario.correo_electronico, 'micorreo@example.com')
        usuario.refresh_from_db()
        self.assertEqual(usuario.correo_electronico, 'micorreo@example.com')

    def test_manager_normaliza_correo_al_crear_usuario(self):
        usuario = Usuario.objects.create_user(
            correo_electronico='  OtroCorreo@Example.COM  ',
            password='Password123!',
            nombre='Correo',
            apellido='Creado',
        )

        self.assertEqual(usuario.correo_electronico, 'otrocorreo@example.com')

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

    def test_login_no_redirige_a_un_host_externo(self):
        response = self.client.post(
            f"{reverse('login')}?next=https://sitio-malicioso.example",
            {
                'correo_electronico': self.user.correo_electronico,
                'password': 'Password123!',
            },
        )

        self.assertRedirects(response, reverse('router'), fetch_redirect_response=False)

    def test_login_conserva_una_ruta_local_como_destino(self):
        response = self.client.post(
            f"{reverse('login')}?next=/destino-seguro/",
            {
                'correo_electronico': self.user.correo_electronico,
                'password': 'Password123!',
            },
        )

        self.assertRedirects(response, '/destino-seguro/', fetch_redirect_response=False)

    def test_usuario_autenticado_accede_a_sus_rutas(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('chat_interfaz'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/static/js/chat_engine.js?v=2')

    def test_sesion_expirada_por_inactividad_redirige_a_login(self):
        self.client.force_login(self.user)
        sesion = self.client.session
        sesion['ultima_actividad'] = time.time() - 31 * 60
        sesion.save()

        with override_settings(SESSION_IDLE_TIMEOUT_SECONDS=30 * 60):
            response = self.client.get(reverse('chat_interfaz'))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
        self.assertNotIn('_auth_user_id', self.client.session)


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
        self.usuario_admin = Usuario.objects.create_user(
            correo_electronico='admin@test.com',
            password='Password123!',
            nombre='Administradora',
            apellido='Sistema',
            rol=Usuario.Rol.ADMINISTRADOR
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

    def test_dashboard_renderiza_estado_indexacion_y_endpoint_de_seguimiento(self):
        self.client.force_login(self.usuario_gestor)
        ley = LeyFederal.objects.create(
            usuario=self.usuario_gestor,
            nombre='Ley bajo seguimiento',
            fecha_publicacion=timezone.now().date(),
            estado_procesamiento=LeyFederal.EstadoProcesamiento.PENDIENTE,
        )

        response = self.client.get(
            reverse('gestor_dashboard') + f'?seguimiento={ley.pk}'
        )

        self.assertContains(response, reverse('gestor_estados_indexacion'))
        self.assertContains(response, f'data-indexacion-ley="{ley.pk}"')
        self.assertContains(response, f'data-seguimiento="{ley.pk}"')

    def test_endpoint_de_estados_solo_expone_leyes_del_gestor(self):
        self.client.force_login(self.usuario_gestor)
        propia = LeyFederal.objects.create(
            usuario=self.usuario_gestor,
            nombre='Ley propia',
            fecha_publicacion=timezone.now().date(),
            estado_procesamiento=LeyFederal.EstadoProcesamiento.PROCESANDO,
        )
        ajena = LeyFederal.objects.create(
            usuario=self.usuario_admin,
            nombre='Ley ajena',
            fecha_publicacion=timezone.now().date(),
            estado_procesamiento=LeyFederal.EstadoProcesamiento.COMPLETADO,
        )

        response = self.client.get(
            reverse('gestor_estados_indexacion'),
            {'ids': f'{propia.pk},{ajena.pk}'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['leyes'], [{
            'id': propia.pk,
            'nombre': 'Ley propia',
            'estado_procesamiento': LeyFederal.EstadoProcesamiento.PROCESANDO,
            'error_procesamiento': '',
        }])

    def test_endpoint_de_estados_valida_ids_y_requiere_rol_gestor(self):
        self.client.force_login(self.usuario_gestor)
        response_invalida = self.client.get(
            reverse('gestor_estados_indexacion'),
            {'ids': '1,no-es-numero'},
        )
        self.assertEqual(response_invalida.status_code, 400)

        self.client.force_login(self.usuario_registrado)
        response_sin_rol = self.client.get(
            reverse('gestor_estados_indexacion'),
            {'ids': '1'},
        )
        self.assertEqual(response_sin_rol.status_code, 302)

    def test_cambiar_estado_y_eliminar_usuario_requieren_post(self):
        self.client.force_login(self.usuario_admin)

        response_estado = self.client.get(
            reverse('admin_cambiar_estado', args=[self.usuario_gestor.id])
        )
        response_eliminar = self.client.get(
            reverse('admin_eliminar_usuario', args=[self.usuario_gestor.id])
        )

        self.assertEqual(response_estado.status_code, 405)
        self.assertEqual(response_eliminar.status_code, 405)
        self.usuario_gestor.refresh_from_db()
        self.assertTrue(self.usuario_gestor.activo)

    def test_cambiar_estado_y_eliminar_usuario_aceptan_post(self):
        self.client.force_login(self.usuario_admin)

        response_estado = self.client.post(
            reverse('admin_cambiar_estado', args=[self.usuario_gestor.id])
        )
        self.assertRedirects(response_estado, reverse('admin_dashboard'), fetch_redirect_response=False)
        self.usuario_gestor.refresh_from_db()
        self.assertFalse(self.usuario_gestor.activo)

        response_eliminar = self.client.post(
            reverse('admin_eliminar_usuario', args=[self.usuario_gestor.id])
        )
        self.assertRedirects(response_eliminar, reverse('admin_dashboard'), fetch_redirect_response=False)
        self.assertFalse(Usuario.objects.filter(pk=self.usuario_gestor.pk).exists())

    def test_post_de_acciones_de_usuario_requiere_token_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.usuario_admin)

        response = client.post(
            reverse('admin_cambiar_estado', args=[self.usuario_gestor.id])
        )

        self.assertEqual(response.status_code, 403)
        self.usuario_gestor.refresh_from_db()
        self.assertTrue(self.usuario_gestor.activo)

    def test_paginacion_en_los_tres_listados_administrativos(self):
        self.client.force_login(self.usuario_admin)
        Usuario.objects.bulk_create([
            Usuario(
                correo_electronico=f'pagina-{i}@test.com',
                nombre='Usuario',
                apellido=str(i),
            )
            for i in range(24)
        ])
        LeyFederal.objects.bulk_create([
            LeyFederal(
                nombre=f'Ley paginada {i}',
                fecha_publicacion=timezone.now().date(),
                hash_documento=f'{i:064}',
            )
            for i in range(26)
        ])
        RegistroFalloAPI.objects.bulk_create([
            RegistroFalloAPI(
                proveedor_solicitado=RegistroFalloAPI.ProveedorIA.GROQ,
                codigo_error=f'error-{i}',
            )
            for i in range(26)
        ])

        usuarios = self.client.get(reverse('admin_dashboard') + '?page=2')
        self.client.force_login(self.usuario_gestor)
        leyes = self.client.get(reverse('gestor_dashboard') + '?page=2')
        self.client.force_login(self.usuario_admin)
        fallos = self.client.get(reverse('admin_fallos_api') + '?page=2&proveedor=GROQ')

        self.assertEqual(usuarios.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(usuarios.context['usuarios']), 1)
        self.assertEqual(leyes.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(leyes.context['leyes']), 1)
        self.assertEqual(fallos.context['page_obj'].paginator.count, 26)
        self.assertEqual(len(fallos.context['fallos']), 1)

        usuarios_tamano = self.client.get(reverse('admin_dashboard') + '?page=2&page_size=10')
        self.client.force_login(self.usuario_gestor)
        leyes_tamano = self.client.get(reverse('gestor_dashboard') + '?page=2&page_size=10')
        self.client.force_login(self.usuario_admin)
        fallos_tamano = self.client.get(
            reverse('admin_fallos_api') + '?page=2&page_size=10&proveedor=GROQ'
        )

        for respuesta in (usuarios_tamano, leyes_tamano, fallos_tamano):
            self.assertEqual(respuesta.context['page_obj'].paginator.per_page, 10)
            self.assertEqual(len(respuesta.context['page_obj'].object_list), 10)
            self.assertContains(respuesta, 'Filas por página')
            self.assertContains(respuesta, 'page_size=10')

    def test_ordenamiento_en_los_tres_listados_se_aplica_antes_de_paginar(self):
        Usuario.objects.bulk_create([
            Usuario(
                correo_electronico=f'orden-usuario-{indice:02}@test.com',
                nombre='Usuario ordenado',
                apellido=str(indice),
            )
            for indice in range(26)
        ])
        LeyFederal.objects.bulk_create([
            LeyFederal(
                nombre=f'Orden ley {indice:02}',
                fecha_publicacion=timezone.now().date(),
            )
            for indice in range(26)
        ])
        RegistroFalloAPI.objects.bulk_create([
            RegistroFalloAPI(
                proveedor_solicitado=RegistroFalloAPI.ProveedorIA.GROQ,
                codigo_error=f'orden-{indice:02}',
            )
            for indice in range(26)
        ])
        for indice in range(26):
            RegistroFalloAPI.objects.filter(codigo_error=f'orden-{indice:02}').update(
                fecha_evento=timezone.now() - timedelta(days=indice)
            )

        self.client.force_login(self.usuario_admin)
        usuarios = self.client.get(
            reverse('admin_dashboard'),
            {'page': 2, 'page_size': 10, 'ordering': 'correo'},
        )
        self.client.force_login(self.usuario_gestor)
        leyes = self.client.get(
            reverse('gestor_dashboard'),
            {'page': 2, 'page_size': 10, 'ordering': '-nombre'},
        )
        self.client.force_login(self.usuario_admin)
        fallos = self.client.get(
            reverse('admin_fallos_api'),
            {'page': 2, 'page_size': 10, 'ordering': '-fecha'},
        )

        self.assertEqual(
            usuarios.context['usuarios'][0].correo_electronico,
            'orden-usuario-09@test.com',
        )
        self.assertEqual(usuarios.context['ordering'], 'correo')
        self.assertEqual(leyes.context['leyes'][0].nombre, 'Orden ley 15')
        self.assertEqual(leyes.context['ordering'], '-nombre')
        self.assertEqual(fallos.context['fallos'][0].codigo_error, 'orden-10')
        self.assertEqual(fallos.context['ordering'], '-fecha')

    def test_busqueda_de_usuarios_y_leyes_filtra_todos_los_registros_antes_de_paginar(self):
        self.client.force_login(self.usuario_admin)
        usuarios = [
            Usuario(
                correo_electronico=f'buscar-{indice}@test.com',
                nombre='Usuario de prueba',
                apellido=f'{indice}',
            )
            for indice in range(30)
        ]
        usuarios[0].nombre = 'Coincidencia remota'
        Usuario.objects.bulk_create(usuarios)
        LeyFederal.objects.bulk_create([
            LeyFederal(
                nombre='Ley de prueba remota' if indice == 0 else f'Ley de prueba {indice}',
                siglas='LPR' if indice == 0 else f'LP{indice}',
                fecha_publicacion=timezone.now().date(),
                hash_documento=f'{indice:064}',
            )
            for indice in range(30)
        ])

        respuesta_usuarios = self.client.get(
            reverse('admin_dashboard'),
            {'q': 'Coincidencia remota'},
        )
        self.client.force_login(self.usuario_gestor)
        respuesta_leyes = self.client.get(
            reverse('gestor_dashboard'),
            {'q': 'LPR'},
        )

        self.assertEqual(respuesta_usuarios.context['page_obj'].paginator.count, 1)
        self.assertEqual(respuesta_usuarios.context['usuarios'][0].nombre, 'Coincidencia remota')
        self.assertContains(respuesta_usuarios, 'value="Coincidencia remota"')
        self.assertContains(respuesta_usuarios, 'data-live-search')
        self.assertEqual(respuesta_leyes.context['page_obj'].paginator.count, 1)
        self.assertEqual(respuesta_leyes.context['leyes'][0].nombre, 'Ley de prueba remota')
        self.assertContains(respuesta_leyes, 'value="LPR"')
        self.assertContains(respuesta_leyes, 'data-live-search')

    def test_dashboard_muestra_mensaje_visible_cuando_falla_la_indexacion(self):
        self.client.force_login(self.usuario_gestor)
        LeyFederal.objects.create(
            nombre='Ley con error de indexación',
            fecha_publicacion=timezone.now().date(),
            estado_procesamiento=LeyFederal.EstadoProcesamiento.ERROR,
            error_procesamiento='El PDF no contiene texto legible.',
        )

        response = self.client.get(reverse('gestor_dashboard'))

        self.assertContains(response, 'Falló la indexación')
        self.assertContains(response, 'El PDF no contiene texto legible.')

    @patch('apps.core.tasks.default_storage.delete')
    @patch('apps.core.tasks.default_storage.open', return_value=io.BytesIO(b'%PDF'))
    @patch(
        'apps.core.tasks.procesar_pdf_en_memoria',
        return_value=('d' * 64, 'b' * 64, ['Artículo 1. Texto vigente.']),
    )
    def test_tarea_de_pdf_actualiza_hashes_y_preserva_fragmentos_si_no_cambia_contenido(
        self,
        mock_procesar,
        mock_abrir,
        mock_eliminar,
    ):
        from .tasks import procesar_pdf_ley

        ley = LeyFederal.objects.create(
            nombre='Ley en reproceso',
            fecha_publicacion=timezone.now().date(),
            hash_documento='a' * 64,
            hash_contenido='b' * 64,
            archivo_proceso='pdf_ingesta/reemplazo.pdf',
            estado_procesamiento=LeyFederal.EstadoProcesamiento.PENDIENTE,
        )
        FragmentoNormativo.objects.create(
            ley=ley,
            contenido_fragmento='Artículo 1. Texto vigente.',
            vector_embedding=[0.0] * 768,
            hash_fragmento='c' * 64,
        )

        procesar_pdf_ley.run(ley.pk)

        ley.refresh_from_db()
        self.assertEqual(ley.hash_documento, 'd' * 64)
        self.assertEqual(ley.estado_procesamiento, LeyFederal.EstadoProcesamiento.COMPLETADO)
        self.assertEqual(ley.archivo_proceso, '')
        self.assertEqual(ley.fragmentos.count(), 1)
        mock_procesar.assert_called_once()
        mock_eliminar.assert_called_once_with('pdf_ingesta/reemplazo.pdf')

    @patch('apps.core.views.gestor.gestor_views.default_storage.save', return_value='pdf_ingesta/nueva.pdf')
    @patch('apps.core.views.gestor.gestor_views.procesar_pdf_ley.delay')
    def test_carga_encola_el_procesamiento_sin_generar_vectores_en_la_peticion(self, mock_delay, mock_save):
        self.client.force_login(self.usuario_gestor)

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

        self.assertRedirects(
            response,
            f"{reverse('gestor_dashboard')}?seguimiento={LeyFederal.objects.get(nombre='Copia de la ley').pk}",
            fetch_redirect_response=False,
        )
        ley = LeyFederal.objects.get(nombre='Copia de la ley')
        self.assertEqual(ley.estado_procesamiento, LeyFederal.EstadoProcesamiento.PENDIENTE)
        self.assertEqual(ley.archivo_proceso, 'pdf_ingesta/nueva.pdf')
        mock_save.assert_called_once()
        mock_delay.assert_called_once_with(ley.pk)

    @patch('apps.core.views.gestor.gestor_views.default_storage.save', return_value='pdf_ingesta/reemplazo.pdf')
    @patch('apps.core.views.gestor.gestor_views.procesar_pdf_ley.delay')
    def test_edicion_de_pdf_encola_reproceso_y_conserva_fragmentos_hasta_completar(self, mock_delay, mock_save):
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

        response = self.client.post(
            reverse('gestor_editar_ley', args=[ley.id]),
            {
                'nombre': ley.nombre,
                'siglas': '',
                'fecha_publicacion': ley.fecha_publicacion.isoformat(),
                'fecha_ultima_reforma': '2025-01-01',
                'estado': LeyFederal.Estado.VIGENTE,
                'archivo_pdf': SimpleUploadedFile('ley.pdf', b'contenido pdf', content_type='application/pdf'),
            },
        )

        self.assertRedirects(
            response,
            f"{reverse('gestor_dashboard')}?seguimiento={ley.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(ley.fragmentos.count(), 1)
        ley.refresh_from_db()
        self.assertEqual(ley.estado_procesamiento, LeyFederal.EstadoProcesamiento.PENDIENTE)
        self.assertEqual(ley.archivo_proceso, 'pdf_ingesta/reemplazo.pdf')
        mock_save.assert_called_once()
        mock_delay.assert_called_once_with(ley.pk)

    @patch(
        'apps.core.services.document_ingestion.generar_vectores_en_lote',
        side_effect=lambda chunks, batch_size: [[0.0] * 768 for _ in chunks],
    )
    def test_fragmento_repetido_se_deduplica_en_una_ley_y_se_acepta_en_otra(self, mock_embedding):
        from .services.document_ingestion import crear_fragmentos_normativos

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
            crear_fragmentos_normativos(ley_a, [repetido, repetido])
        )
        FragmentoNormativo.objects.bulk_create(
            crear_fragmentos_normativos(ley_b, [repetido])
        )

        self.assertEqual(FragmentoNormativo.objects.count(), 2)
        self.assertEqual(ley_a.fragmentos.count(), 1)
        self.assertEqual(ley_b.fragmentos.count(), 1)
        self.assertEqual(ley_a.fragmentos.get().articulo, '1')
        self.assertEqual(mock_embedding.call_count, 2)

    @patch(
        'apps.core.services.document_ingestion.generar_vectores_en_lote',
        side_effect=lambda chunks, batch_size: [[0.0] * 768 for _ in chunks],
    )
    def test_articulos_despues_de_transitorios_se_identifican(self, mock_embedding):
        from .services.document_ingestion import crear_fragmentos_normativos

        ley = LeyFederal.objects.create(
            nombre='Ley con transitorios',
            fecha_publicacion=timezone.now().date(),
            hash_documento='3' * 64,
        )
        chunks = [
            'Artículo 1. Texto principal.',
            'TRANSITORIOS',
            'Artículo 1. Entrada en vigor.',
            'Artículo 2. Derogaciones.',
        ]

        fragmentos = crear_fragmentos_normativos(ley, chunks)

        self.assertEqual(
            [fragmento.articulo for fragmento in fragmentos],
            ['1', '', '1 Transitorio', '2 Transitorio'],
        )
        mock_embedding.assert_called_once()

    @patch(
        'apps.core.services.document_ingestion.generar_vectores_en_lote',
        side_effect=lambda chunks, batch_size: [[0.0] * 768 for _ in chunks],
    )
    def test_articulo_anterior_a_transitorios_mismo_fragmento_no_es_transitorio(self, mock_embedding):
        from .services.document_ingestion import crear_fragmentos_normativos

        ley = LeyFederal.objects.create(
            nombre='Ley con título y transitorios juntos',
            fecha_publicacion=timezone.now().date(),
            hash_documento='4' * 64,
        )
        chunks = [
            'Artículo 123. Última disposición ordinaria.\nTRANSITORIOS',
            'Artículo Primero.- Entrada en vigor.',
        ]

        fragmentos = crear_fragmentos_normativos(ley, chunks)

        self.assertEqual(
            [fragmento.articulo for fragmento in fragmentos],
            ['123', 'Primero Transitorio'],
        )


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

    def test_extrae_encabezados_transitorios_con_nombre(self):
        self.assertEqual(extraer_articulo_fragmento("Artículo Primero.- Entrada en vigor."), "Primero")
        self.assertEqual(extraer_articulo_fragmento("PRIMERO.- Vigencia."), "Primero")
        self.assertEqual(extraer_articulo_fragmento("Artículo Único.- Derogaciones."), "Único")

    def test_extrae_articulos_ordinales_y_sufijos(self):
        self.assertEqual(
            extraer_articulo_fragmento("Artículo 1o.- La presente Ley…"),
            "1o",
        )
        self.assertEqual(extraer_articulo_fragmento("Artículo 2º. Texto."), "2º")
        self.assertEqual(extraer_articulo_fragmento("Artículo 3°. Texto."), "3°")
        self.assertEqual(extraer_articulo_fragmento("Artículo 4 Ter. Texto."), "4 Ter")
        self.assertEqual(extraer_articulo_fragmento("Artículo 5 Quáter. Texto."), "5 Quáter")

        chunks = _fragmentar_texto_estructurado(
            "Artículo 1o.- Inicio.\n\nArtículo 2 Quáter. Continuación.",
            chunk_size=100,
        )
        self.assertEqual(len(chunks), 2)
        self.assertEqual(extraer_articulo_fragmento(chunks[0]), "1o")
        self.assertEqual(extraer_articulo_fragmento(chunks[1]), "2 Quáter")

    def test_mencion_de_articulo_en_oracion_no_se_toma_como_encabezado(self):
        texto = (
            "Artículo 1o.- La presente Ley establece las disposiciones aplicables.\n"
            "En términos del artículo 27 de esta Ley, se determinarán los requisitos."
        )

        chunks = _fragmentar_texto_estructurado(texto, chunk_size=250)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(extraer_articulo_fragmento(chunks[0]), "1o")

    def test_articulo_en_minusculas_al_inicio_de_linea_no_es_encabezado(self):
        texto = (
            "Artículo 1. Disposición inicial.\n"
            "artículo 27 de esta Ley establece una referencia."
        )

        chunks = _fragmentar_texto_estructurado(texto, chunk_size=250)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(extraer_articulo_fragmento(chunks[0]), "1")
        self.assertEqual(extraer_articulo_fragmento("Artículo 27 de esta Ley"), "")

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

    @patch('apps.core.services.pdf_processor.pypdf.PdfReader')
    def test_rechaza_vigencia_imss_que_no_es_ley(self, mock_pdf_reader):
        pagina = MagicMock()
        pagina.extract_text.return_value = (
            "Instituto Mexicano del Seguro Social\n"
            "VIGENCIA DE ACUERDOS ADMINISTRATIVOS\n"
            "Documento informativo de vigencia y trámites internos."
        )
        mock_pdf_reader.return_value.pages = [pagina]

        with self.assertRaises(ValidationError):
            procesar_pdf_en_memoria(io.BytesIO(b"documento vigencia IMSS"))

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
        mock_page.extract_text.return_value = (
            "CÁMARA DE DIPUTADOS DEL H. CONGRESO DE LA UNIÓN\n"
            "Secretaría de Servicios Parlamentarios\n"
            "CÓDIGO CIVIL FEDERAL\nTEXTO VIGENTE\n"
            "Artículo 1. Texto vigente de la ley federal.\n"
            "Artículo 2. Disposición normativa federal.\n"
            "Artículo 3. Fracción aplicable a esta ley.\n"
            + ("Disposición normativa y fracción aplicable. " * 8)
        )
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
    def test_saludos_y_cortesias_no_consultan_leyes_ni_consumen_cuota(self, mock_buscar, mock_generar):
        self.client.force_login(self.usuario)
        for mensaje, respuesta_esperada in (
            ('Hola, buenas tardes', '¡Hola!'),
            ('Gracias', '¡Con gusto!'),
        ):
            with self.subTest(mensaje=mensaje):
                response = self.client.post(
                    reverse('chat_api_enviar'),
                    data=json.dumps({'mensaje': mensaje}),
                    content_type='application/json',
                )

                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()['respuesta']['proveedor'])
                self.assertEqual(response.json()['respuesta']['citas'], [])
                self.assertIn(respuesta_esperada, response.json()['respuesta']['contenido'])
                self.assertEqual(response.json()['mensajes_restantes'], self.usuario.cuota_diaria_mensajes)

        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.mensajes_usados_hoy, 0)
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

    @patch('apps.core.services.chat_service.buscar_contexto_normativo')
    @patch('apps.core.services.chat_service.get_groq_client')
    def test_endpoint_stream_transmite_respuesta_y_persiste_intercambio(self, mock_get_groq, mock_buscar):
        mock_buscar.return_value = ('Artículo 1. Contexto legal.', [])
        fragmentos = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content='Respuesta '), finish_reason=None)]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content='transmitida.'), finish_reason='stop')]),
        ]
        mock_get_groq.return_value.chat.completions.create.return_value = iter(fragmentos)
        self.client.force_login(self.usuario)

        response = self.client.post(
            reverse('chat_api_enviar'),
            data=json.dumps({'mensaje': '¿Qué dice el artículo 1?', 'stream': True}),
            content_type='application/json',
        )

        contenido = b''.join(response.streaming_content).decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Type'].startswith('text/event-stream'))
        self.assertIn('event: token', contenido)
        self.assertIn('Respuesta ', contenido)
        self.assertIn('transmitida.', contenido)
        self.assertIn('event: complete', contenido)
        self.assertEqual(Mensaje.objects.filter(conversacion__usuario=self.usuario).count(), 2)
        mensaje_respuesta = Mensaje.objects.get(
            conversacion__usuario=self.usuario,
            emisor=Mensaje.Emisor.SISTEMA,
        )
        self.assertEqual(mensaje_respuesta.contenido_texto, 'Respuesta transmitida.')
        self.assertEqual(mensaje_respuesta.proveedor_ia, Mensaje.ProveedorIA.GROQ)

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
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.mensajes_usados_hoy, 1)

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

    @patch('apps.core.services.chat_service.get_gemini_client')
    @patch('apps.core.services.chat_service.get_groq_client')
    def test_stream_fallback_reinicia_fragmentos_del_proveedor_anterior(self, mock_get_groq, mock_get_gemini):
        def fragmentos_groq():
            yield MagicMock(choices=[MagicMock(delta=MagicMock(content='Parcial Groq'), finish_reason=None)])
            raise Exception('Groq timeout')

        mock_get_groq.return_value.chat.completions.create.return_value = fragmentos_groq()
        mock_get_gemini.return_value.models.generate_content_stream.return_value = [
            MagicMock(text='Respuesta Gemini')
        ]

        eventos = list(generar_respuesta_ia_stream(
            pregunta_usuario='¿Qué dice el artículo 1?',
            contexto_normativo='Artículo 1. Contexto.',
            usuario=self.usuario,
        ))

        self.assertEqual(
            [(evento['event'], evento.get('text')) for evento in eventos if evento['event'] in ('token', 'reset')],
            [('token', 'Parcial Groq'), ('reset', None), ('token', 'Respuesta Gemini')],
        )
        self.assertEqual(eventos[-1], {
            'event': 'meta',
            'proveedor': Mensaje.ProveedorIA.GEMINI,
            'conmutado': True,
        })

    def test_historial_conserva_resumen_y_ultimos_mensajes(self):
        historial = [
            {'emisor': 'USUARIO', 'texto': 'Primera pregunta antigua'},
            {'emisor': 'SISTEMA', 'texto': 'Primera respuesta antigua'},
            {'emisor': 'USUARIO', 'texto': 'Segunda pregunta antigua'},
            {'emisor': 'SISTEMA', 'texto': 'Segunda respuesta antigua'},
            {'emisor': 'USUARIO', 'texto': 'Pregunta reciente'},
            {'emisor': 'SISTEMA', 'texto': 'Respuesta reciente'},
        ]

        resumen, recientes = preparar_contexto_conversacion(historial)

        self.assertIn('Primera pregunta antigua', resumen)
        self.assertEqual(len(recientes), 4)
        self.assertTrue(len(resumir_historial_mensajes(historial)) <= 1200)

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
        system_prompt = mock_client.chat.completions.create.call_args.kwargs['messages'][0]['content']
        self.assertIn("Menciona explícitamente el artículo", system_prompt)
        self.assertNotIn("Evita copiar texto literal", system_prompt)

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

    @patch('apps.core.services.chat_service.time.sleep')
    @patch('apps.core.services.chat_service.get_gemini_client')
    @patch('apps.core.services.chat_service.get_groq_client')
    def test_gemini_reintenta_error_503_temporal(self, mock_get_groq, mock_get_gemini, mock_sleep):
        mock_groq = MagicMock()
        mock_groq.chat.completions.create.side_effect = Exception("Groq Timeout")
        mock_get_groq.return_value = mock_groq

        mock_gemini = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Respuesta recuperada tras el reintento."
        mock_gemini.models.generate_content.side_effect = [
            Exception("503 UNAVAILABLE: high demand"),
            mock_response,
        ]
        mock_get_gemini.return_value = mock_gemini

        respuesta, proveedor, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario="¿Qué dice el artículo 1?",
            contexto_normativo="Artículo 1. Disposición federal.",
            usuario=self.usuario,
        )

        self.assertEqual(respuesta, "Respuesta recuperada tras el reintento.")
        self.assertEqual(proveedor, Mensaje.ProveedorIA.GEMINI)
        self.assertTrue(conmutado)
        self.assertEqual(mock_gemini.models.generate_content.call_count, 2)
        mock_sleep.assert_called_once_with(1)

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

    def test_identifica_cualquier_ley_cargada_por_nombre_siglas_o_alias_articulo(self):
        LeyFederal.objects.create(
            nombre="Código Civil Federal",
            siglas="CCF",
            fecha_publicacion=timezone.now().date(),
            hash_documento="dummyhashccf1234567890abcdef1234567890abcdef1234567890abcdef1234",
            estado=LeyFederal.Estado.VIGENTE,
        )

        from .services.chat_service import obtener_leyes_mencionadas

        nombres = {
            ley['nombre']
            for ley in obtener_leyes_mencionadas("¿Qué protege el Código Civil Federal?")
        }
        siglas = {
            ley['nombre']
            for ley in obtener_leyes_mencionadas("¿Qué establece el artículo 123 de la LFT?")
        }

        self.assertIn("Código Civil Federal", nombres)
        self.assertIn("Ley Federal del Trabajo", siglas)

    def test_catalogo_excluye_leyes_vigentes_pendientes_de_procesamiento(self):
        LeyFederal.objects.create(
            nombre="Ley Federal en procesamiento",
            siglas="LFP",
            fecha_publicacion=timezone.now().date(),
            hash_documento="dummyhashpendiente1234567890abcdef1234567890abcdef1234567890abcd",
            estado=LeyFederal.Estado.VIGENTE,
            estado_procesamiento=LeyFederal.EstadoProcesamiento.PENDIENTE,
        )

        from .services.chat_service import obtener_leyes_mencionadas

        mencionadas = obtener_leyes_mencionadas("Consulta sobre la LFP")

        self.assertNotIn("Ley Federal en procesamiento", {ley['nombre'] for ley in mencionadas})
