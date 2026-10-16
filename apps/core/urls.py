from django.urls import path
from .views.auth import autenticacion, router
from .views.admin import api_log, profile, user
from .views.gestor import gestor_views, chat_gestor_views
from .views.usuario import chat_views

urlpatterns = [
    path('', router.home_router_view, name='router'),
    path('login/', autenticacion.login_view, name='login'),
    path('logout/', autenticacion.logout_view, name='logout'),
    path('registro/', autenticacion.registro_view, name='registro'),

    # Administrador
    path('admin-panel/', user.admin_dashboard_view, name='admin_dashboard'),
    path('admin-panel/usuarios/crear/', user.crear_usuario_view, name='admin_crear_usuario'),
    path('admin-panel/usuarios/<int:user_id>/editar/', user.editar_usuario_view, name='admin_editar_usuario'),
    path('admin-panel/usuarios/<int:user_id>/estado/', user.cambiar_estado_usuario_view, name='admin_cambiar_estado'),
    path('admin-panel/usuarios/<int:user_id>/eliminar/', user.eliminar_usuario_view, name='admin_eliminar_usuario'),
    path('admin-panel/perfil/', profile.perfil_admin_view, name='admin_perfil'),
    path('admin-panel/fallos-api/', api_log.registros_fallos_api_view, name='admin_fallos_api'),

    # Gestor
    path('gestor-panel/', gestor_views.gestor_dashboard_view, name='gestor_dashboard'),
    path('gestor-panel/documentos/previsualizar/', gestor_views.previsualizar_documento_view, name='gestor_previsualizar_documento'),
    path('gestor-panel/documentos/cargar/', gestor_views.cargar_documento_view, name='gestor_cargar_documento'),
    path('gestor-panel/documentos/<int:ley_id>/editar/', gestor_views.editar_ley_view, name='gestor_editar_ley'),
    path('gestor-panel/perfil/', gestor_views.perfil_gestor_view, name='gestor_perfil'),
    path('gestor/chat/', chat_gestor_views.chat_gestor_interfaz_view, name='gestor_chat_interfaz'),
    path('gestor/chat/c/<uuid:conversacion_id>/', chat_gestor_views.chat_gestor_interfaz_view, name='gestor_chat_conversacion'),
    path('gestor/chat/nueva/', chat_gestor_views.crear_nueva_conversacion_gestor_view, name='gestor_crear_conversacion'),
    path('gestor/chat/eliminar/<uuid:conversacion_id>/', chat_gestor_views.eliminar_conversacion_gestor_view, name='gestor_eliminar_conversacion'),
    path('gestor/chat/api/enviar/', chat_gestor_views.enviar_mensaje_gestor_api_view, name='gestor_enviar_mensaje_api'),

    # Usuario
    path('chat/', chat_views.chat_interfaz_view, name='chat_interfaz'),
    path('chat/c/<uuid:conversacion_id>/', chat_views.chat_interfaz_view, name='chat_conversacion'),
    path('chat/nueva/', chat_views.crear_nueva_conversacion_view, name='chat_nueva_conversacion'),
    path('chat/eliminar/<uuid:conversacion_id>/', chat_views.eliminar_conversacion_view, name='chat_eliminar_conversacion'),
    path('chat/api/enviar/', chat_views.enviar_mensaje_api_view, name='chat_api_enviar'),
    path('perfil/', chat_views.perfil_usuario_view, name='usuario_perfil'),
]