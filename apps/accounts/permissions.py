from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from rest_framework.permissions import BasePermission

admin_required = user_passes_test(lambda u: u.is_authenticated and u.is_admin_role, login_url='accounts:login')


def teacher_required(view):
    """Require a teacher role, returning 403 for authenticated non-teachers."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url='accounts:login')
        if not request.user.is_teacher:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def student_required(view):
    """Require a student role while preserving login redirects for anonymous users."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url='accounts:login')
        if not request.user.is_student:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def verified_teacher_required(view):
    """Logged-in + teacher role + admin-approved. Unapproved teachers are sent to their
    dashboard (which explains the pending state); everyone else gets 403."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), login_url='accounts:login')
        if not request.user.is_verified_teacher:
            if request.user.is_teacher:
                messages.warning(request, 'Your teacher account has not been approved yet.')
                return redirect('teachers:dashboard')
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


class IsAdminRole(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_admin_role)


class IsVerifiedTeacher(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_verified_teacher)


class IsStudentRole(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_student)
