PAGE_SIZE_OPTIONS = (10, 20, 25, 30, 40, 50)
DEFAULT_PAGE_SIZE = 25


def get_page_size(request):
    try:
        page_size = int(request.GET.get('page_size', DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return page_size if page_size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE
