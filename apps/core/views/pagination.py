PAGE_SIZE_OPTIONS = (10, 20, 25, 30, 40, 50)
DEFAULT_PAGE_SIZE = 25


def get_page_size(request):
    try:
        page_size = int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return page_size if page_size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


def apply_table_ordering(queryset, request, ordering_options, default_ordering):
    requested_ordering = request.GET.get('ordering', '')
    descending = requested_ordering.startswith('-')
    ordering_key = requested_ordering[1:] if descending else requested_ordering
    fields = ordering_options.get(ordering_key)

    if not fields:
        return queryset.order_by(*default_ordering), ''

    ordering = [f'-{field}' if descending else field for field in fields]
    if 'pk' not in fields:
        ordering.append('pk')
    return queryset.order_by(*ordering), requested_ordering
