from django.shortcuts import render, redirect
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from apps.core.forms.auth_forms import LoginForm, RegistroForm

def login_view(request):
    """
    Vista de Inicio de Sesión.
    """
    if request.user.is_authenticated:
        return redirect('router')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            user = form.cleaned_data.get('user')
            auth_login(request, user)
            messages.success(request, f"¡Bienvenido de nuevo, {user.nombre}!")
            next_url = request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect('router')
    else:
        form = LoginForm()

    return render(request, 'auth/login.html', {'form': form})


def logout_view(request):
    """
    Cierre de sesión.
    """
    auth_logout(request)
    messages.info(request, "Has cerrado sesión correctamente.")
    return redirect('login')


def registro_view(request):
    """
    Vista de Registro Público.
    """
    if request.user.is_authenticated:
        return redirect('router')

    if request.method == 'POST':
        form = RegistroForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Cuenta registrada exitosamente.")
            return redirect('login')
    else:
        form = RegistroForm()

    return render(request, 'auth/registro.html', {'form': form})