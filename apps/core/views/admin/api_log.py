from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from ...models import RegistroFalloAPI
from .user import admin_required
from ..pagination import PAGE_SIZE_OPTIONS, apply_table_ordering, get_page_size


@login_required
@admin_required
def registros_fallos_api_view(request):
    """
    Auditoría y consulta de registros de fallos de API y conmutación fallback (RF-3.3, RNF-04).
    """
    fallos = RegistroFalloAPI.objects.select_related('usuario', 'mensaje')
    
    # Filtro opcional por proveedor
    proveedor_filtro = request.GET.get('proveedor')
    if proveedor_filtro in ['GROQ', 'GEMINI']:
        fallos = fallos.filter(proveedor_solicitado=proveedor_filtro)

    fallos, ordering = apply_table_ordering(
        fallos,
        request,
        {'fecha': ('fecha_evento',)},
        ('-fecha_evento', 'pk'),
    )
    page_size = get_page_size(request)
    page_obj = Paginator(fallos, page_size).get_page(request.GET.get('page'))
    context = {
        'fallos': page_obj.object_list,
        'page_obj': page_obj,
        'proveedor_filtro': proveedor_filtro,
        'total_fallos': page_obj.paginator.count,
        'page_size': page_size,
        'page_size_options': PAGE_SIZE_OPTIONS,
        'ordering': ordering,
    }
    return render(request, 'admin/fallos_api.html', context)