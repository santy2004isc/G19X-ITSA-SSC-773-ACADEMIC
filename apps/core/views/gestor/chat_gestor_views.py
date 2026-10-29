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
from ...decorators import roles_requeridos
from ...services.chat_service import (
    MAX_LONGITUD_MENSAJE,
    procesar_mensaje_chat,
)
from ..common_chat import ejecutar_envio_mensaje

logger = logging.getLogger(__name__)


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
def chat_gestor_interfaz_view(request, conversacion_id=None):
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

    context = {
        'conversaciones': conversaciones,
        'conversacion_actual': conversacion_actual,
        'mensajes': mensajes,
        'mensajes_restantes': "Ilimitado"
    }
    return render(request, 'gestor/chat.html', context)


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def crear_nueva_conversacion_gestor_view(request):
    return redirect(f"{reverse('gestor_chat_interfaz')}?nueva=1")


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def eliminar_conversacion_gestor_view(request, conversacion_id):
    conv = get_object_or_404(Conversacion, id=conversacion_id, usuario=request.user)
    conv.delete()
    messages.success(request, "La conversación fue eliminada.")
    return redirect('gestor_chat_interfaz')


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def enviar_mensaje_gestor_api_view(request):
    return ejecutar_envio_mensaje(request, es_gestor=True)
