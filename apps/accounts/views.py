from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib.auth import get_user_model
from django.conf import settings
import os
import uuid
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db import transaction

from .forms import RegisterForm, LoginForm, ProfileForm, AdminPasswordChangeForm
from .login_throttle import consume_login_attempt, normalize_login_identifier

User = get_user_model()


def register_view(request):
    if request.user.is_authenticated:
        return redirect('teachers:dashboard' if request.user.is_teacher else 'dashboard:home')
    registration_type = request.POST.get('registration_type', 'student') if request.method == 'POST' else 'student'
    if registration_type not in {'student', 'teacher'}:
        registration_type = 'student'

    from apps.teachers.forms import TeacherRegisterForm
    if request.method == 'POST':
        student_form = RegisterForm(request.POST if registration_type == 'student' else None)
        teacher_form = TeacherRegisterForm(request.POST if registration_type == 'teacher' else None)
        form = teacher_form if registration_type == 'teacher' else student_form
        if form.is_valid():
            user = form.save()
            login(request, user)
            if registration_type == 'teacher':
                messages.success(
                    request,
                    'Account created. An administrator must verify your profile before you can use teacher tools.',
                )
                return redirect('teachers:dashboard')
            messages.success(request, 'Welcome to CerebroX AI! Now add the courses you want to study.')
            return redirect('learning:courses')
    else:
        student_form = RegisterForm()
        teacher_form = TeacherRegisterForm()
        form = teacher_form if registration_type == 'teacher' else student_form
    return render(request, 'accounts/register.html', {
        'form': form, 'student_form': student_form, 'teacher_form': teacher_form,
        'registration_type': registration_type,
    })


def login_view(request):
    if request.user.is_authenticated:
        return redirect('teachers:dashboard' if request.user.is_teacher else 'dashboard:home')
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    form = LoginForm(request, data=request.POST or None)
    if request.method == 'POST':
        blocked, retry_after = consume_login_attempt(
            request, normalize_login_identifier(request.POST.get('username', '')),
        )
        if blocked:
            response = render(request, 'accounts/login.html', {
                'form': form, 'next': next_url,
                'login_error': 'Too many attempts. Please try again later.',
            }, status=429)
            response['Retry-After'] = str(retry_after)
            return response
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
@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You've been signed out.")
    return redirect('public:landing')


@login_required
def profile_view(request):
    from apps.marketplace.models import PendingObjectUpload
    from apps.marketplace.uploads import promote_uploaded_object, verify_avatar_image

    pending_avatar = None
    if request.method == 'POST':
        previous_avatar_key = request.user.avatar.name
        form = ProfileForm(request.POST, request.FILES, instance=request.user)
        upload_id = request.POST.get('avatar_upload_id', '').strip()
        if upload_id:
            try:
                pending_avatar = PendingObjectUpload.objects.get(
                    pk=uuid.UUID(upload_id), owner=request.user,
                    purpose=PendingObjectUpload.Purpose.AVATAR,
                    expires_at__gt=timezone.now(),
                )
                verify_avatar_image(pending_avatar)
            except (ValueError, TypeError, PendingObjectUpload.DoesNotExist):
                form.add_error(None, 'The uploaded profile image expired or could not be verified. Please upload it again.')
                pending_avatar = None
            except Exception:
                form.add_error(None, 'The uploaded profile image could not be verified. Please try again.')
                pending_avatar = None
            if pending_avatar and request.FILES.get('avatar'):
                form.add_error('avatar', 'Choose either the direct-uploaded image or a form upload, not both.')
                pending_avatar = None
        if form.is_valid():
            final_avatar_key = None
            if pending_avatar:
                try:
                    final_avatar_key = promote_uploaded_object(pending_avatar)
                except Exception:
                    form.add_error(None, 'The profile image could not be saved to object storage. Please try again.')
                    return render(request, 'accounts/profile.html', {
                        'form': form,
                        'direct_avatar_upload': settings.USE_OBJECT_STORAGE,
                        'avatar_upload_id': upload_id,
                    })
            user = form.save(commit=False)
            if final_avatar_key:
                user.avatar.name = final_avatar_key
            with transaction.atomic():
                user.save()
                if previous_avatar_key and previous_avatar_key != user.avatar.name:
                    from apps.marketplace.models import OrphanedAvatar
                    OrphanedAvatar.objects.get_or_create(storage_key=previous_avatar_key)
            if pending_avatar:
                from apps.marketplace.uploads import delete_staged_upload
                try:
                    delete_staged_upload(pending_avatar)
                except Exception:
                    # Keep its authorization row so the expiry cleanup command can retry.
                    pass
                else:
                    pending_avatar.delete()
            messages.success(request, 'Profile updated.')
            return redirect('accounts:profile')
    else:
        form = ProfileForm(instance=request.user)
    return render(request, 'accounts/profile.html', {
        'form': form,
        'direct_avatar_upload': settings.USE_OBJECT_STORAGE,
        'avatar_upload_id': request.POST.get('avatar_upload_id', '') if request.method == 'POST' else '',
    })


@login_required
@require_POST
def avatar_upload_start(request):
    from apps.marketplace.models import PendingObjectUpload
    from apps.marketplace.uploads import issue_upload_url

    if not settings.USE_OBJECT_STORAGE:
        return JsonResponse({'error': 'Persistent object storage is not configured.'}, status=503)
    filename = os.path.basename(request.POST.get('file_name', '').replace('\\', '/')).strip()
    try:
        size = int(request.POST.get('file_size', ''))
    except (TypeError, ValueError):
        size = 0
    extensions = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.webp': 'image/webp'}
    extension = os.path.splitext(filename)[1].lower()
    if not filename or len(filename) > 255 or extension not in extensions:
        return JsonResponse({'error': 'Choose a JPEG, PNG, or WebP image.'}, status=400)
    if size <= 0 or size > settings.AVATAR_UPLOAD_MAX_BYTES:
        return JsonResponse({'error': 'Profile images must be 5 MB or smaller.'}, status=400)
    active = PendingObjectUpload.objects.filter(
        owner=request.user, purpose=PendingObjectUpload.Purpose.AVATAR,
        expires_at__gt=timezone.now(),
    )
    if active.count() >= 5:
        return JsonResponse({'error': 'Finish or wait for an existing upload to expire before starting another.'}, status=429)
    upload = PendingObjectUpload.objects.create(
        owner=request.user,
        purpose=PendingObjectUpload.Purpose.AVATAR,
        object_key=f'upload-staging/avatars/{uuid.uuid4().hex}{extension}',
        final_key=f'profiles/{uuid.uuid4().hex}{extension}',
        original_name=filename,
        content_type=extensions[extension],
        size=size,
    )
    try:
        response = issue_upload_url(upload)
    except Exception:
        upload.delete()
        return JsonResponse({'error': 'Could not prepare secure storage upload. Try again later.'}, status=503)
    return JsonResponse(response)


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
