from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from ...models import RegistroFalloAPI
from .user import admin_required


@login_required
@admin_required
def registros_fallos_api_view(request):
    """
    Auditoría y consulta de registros de fallos de API y conmutación fallback (RF-3.3, RNF-04).
    """
    fallos = RegistroFalloAPI.objects.select_related('usuario', 'mensaje').order_by('-fecha_evento')
    
    # Filtro opcional por proveedor
    proveedor_filtro = request.GET.get('proveedor')
    if proveedor_filtro in ['GROQ', 'GEMINI']:
        fallos = fallos.filter(proveedor_solicitado=proveedor_filtro)

    page_obj = Paginator(fallos, 25).get_page(request.GET.get('page'))
    context = {
        'fallos': page_obj.object_list,
        'page_obj': page_obj,
        'proveedor_filtro': proveedor_filtro,
        'total_fallos': page_obj.paginator.count,
    }
    return render(request, 'admin/fallos_api.html', context)