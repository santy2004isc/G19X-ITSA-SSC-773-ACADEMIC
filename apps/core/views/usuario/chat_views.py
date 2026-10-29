import json
import logging
from uuid import UUID
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse

from ...models import Usuario, Conversacion
from ...forms.usuario_forms import EditarPerfilUsuarioForm
from ...decorators import roles_requeridos
from ...services.chat_service import (
    MAX_LONGITUD_MENSAJE,
    procesar_mensaje_chat,
)
from ..common_chat import ejecutar_envio_mensaje

logger = logging.getLogger(__name__)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
def chat_interfaz_view(request, conversacion_id=None):
    """
    Interfaz de chat optimizada para Usuario Registrado.
    """
    usuario = request.user
    conversaciones = Conversacion.objects.filter(
        usuario=usuario,
        mensajes__isnull=False,
    ).distinct().order_by('-fecha_creacion')
    
    conversacion_actual = None
    mensajes = []

    if conversacion_id:
        conversacion_actual = get_object_or_404(Conversacion, id=conversacion_id, usuario=usuario)
    elif request.GET.get('nueva') != '1' and conversaciones.exists():
        conversacion_actual = conversaciones.first()

    if conversacion_actual:
        mensajes = conversacion_actual.mensajes.all().order_by('fecha_envio')

    usuario.verificar_y_reiniciar_cuota_diaria()
    mensajes_restantes = max(0, usuario.cuota_diaria_mensajes - usuario.mensajes_usados_hoy)

    context = {
        'conversaciones': conversaciones,
        'conversacion_actual': conversacion_actual,
        'mensajes': mensajes,
        'mensajes_restantes': mensajes_restantes,
    }
    return render(request, 'usuario/chat.html', context)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def crear_nueva_conversacion_view(request):
    return redirect(f"{reverse('chat_interfaz')}?nueva=1")


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def eliminar_conversacion_view(request, conversacion_id):
    conv = get_object_or_404(Conversacion, id=conversacion_id, usuario=request.user)
    conv.delete()
    messages.success(request, "La conversación fue eliminada.")
    return redirect('chat_interfaz')


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def enviar_mensaje_api_view(request):
    """
    Endpoint AJAX con control estricto de cuota diaria.
    """
    return ejecutar_envio_mensaje(request, es_gestor=False)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
def perfil_usuario_view(request):
    user = request.user
    if request.method == 'POST':
        form = EditarPerfilUsuarioForm(request.POST, instance=user, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tu perfil ha sido actualizado.")
            return redirect('usuario_perfil')
    else:
        form = EditarPerfilUsuarioForm(instance=user, user=user)

    return render(request, 'usuario/perfil.html', {'form': form})
