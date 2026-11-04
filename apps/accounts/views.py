from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import get_user_model

from .forms import RegisterForm, LoginForm, ProfileForm, AdminPasswordChangeForm

User = get_user_model()


def register_view(request):
    if request.user.is_authenticated:
        return redirect('teachers:dashboard' if request.user.is_teacher else 'dashboard:home')
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Welcome to CerebroX AI! Now add the courses you want to study.')
            return redirect('learning:courses')
    else:
        form = RegisterForm()
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('teachers:dashboard' if request.user.is_teacher else 'dashboard:home')
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
            if next_url and url_has_allowed_host_and_scheme(
                    next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                return redirect(next_url)
            if user.is_admin_role:
                return redirect('admin_panel:dashboard')
            if user.is_teacher:
                return redirect('teachers:dashboard')
            return redirect('dashboard:home')
        messages.error(request, 'Invalid credentials. Please try again.')
    return render(request, 'accounts/login.html', {'form': form, 'next': next_url})


@login_required
def logout_view(request):
    logout(request)
    messages.info(request, "You've been signed out.")
    return redirect('public:landing')


@login_required
def profile_view(request):
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Profile updated.')
            return redirect('accounts:profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'accounts/profile.html', {'form': form})


@login_required
def password_change_view(request):
    if request.method == 'POST':
        form = AdminPasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Password changed successfully.')
            return redirect('accounts:profile')
    else:
        form = AdminPasswordChangeForm(request.user)
    return render(request, 'accounts/password_change.html', {'form': form})
