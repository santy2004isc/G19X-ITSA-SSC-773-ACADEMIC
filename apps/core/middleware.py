from django.shortcuts import redirect
from django.urls import resolve
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
import time

class AutenticacionRequeridaMiddleware:
    """
    Middleware que asegura que todas las rutas del sistema requieran autenticación,
    excepto aquellas explícitamente autorizadas como públicas (RF-1.4, RNF-02).
    """
    RUTAS_EXENTAS = [
        'login',
        'registro',
        'logout',
    ]

    PREFIXES_EXENTOS = [
        '/static/',
        '/media/',
        '/favicon.ico',
        '/admin/login/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            ultima_actividad = request.session.get('ultima_actividad')
            ahora = time.time()

            if ultima_actividad is not None:
                try:
                    sesion_expirada = ahora - float(ultima_actividad) >= settings.SESSION_IDLE_TIMEOUT_SECONDS
                except (TypeError, ValueError):
                    sesion_expirada = True

                if sesion_expirada:
                    logout(request)
                    messages.warning(request, "Tu sesión expiró por inactividad. Inicia sesión nuevamente.")
                    return redirect(settings.LOGIN_URL)

            request.session['ultima_actividad'] = ahora
            return self.get_response(request)

        # Permitir rutas estáticas o favicon
        path = request.path_info
        for prefix in self.PREFIXES_EXENTOS:
            if path.startswith(prefix):
                return self.get_response(request)

        # Resolver nombre de la vista
        try:
            match = resolve(path)
            if match.url_name in self.RUTAS_EXENTAS:
                response = self.get_response(request)
                if request.user.is_authenticated and 'ultima_actividad' not in request.session:
                    request.session['ultima_actividad'] = time.time()
                return response
        except Exception:
            pass

        # Redirigir a inicio de sesión con el parámetro next
        login_url = settings.LOGIN_URL
        if not login_url.startswith('/'):
            from django.urls import reverse
            login_url = reverse(login_url)
        return redirect(f"{login_url}?next={path}")
