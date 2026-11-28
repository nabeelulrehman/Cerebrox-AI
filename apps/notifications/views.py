from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .models import Notification


def _own_notifications(user):
    return Notification.objects.filter(recipient=user)


@login_required
@require_GET
def notification_summary(request):
    rows = list(_own_notifications(request.user).values(
        'id', 'title', 'message', 'notification_type', 'target_url', 'is_read', 'created_at',
    )[:8])
    for row in rows:
        row['created_at'] = row['created_at'].isoformat()
    return JsonResponse({
        'unread_count': _own_notifications(request.user).filter(is_read=False).count(),
        'notifications': rows,
    })


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(_own_notifications(request.user), pk=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def mark_all_read(request):
    _own_notifications(request.user).filter(is_read=False).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required
@require_GET
def open_notification(request, notification_id):
    notification = get_object_or_404(_own_notifications(request.user), pk=notification_id)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=['is_read'])
    target = notification.target_url
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                           require_https=request.is_secure()):
        target = reverse('dashboard:home')
    return redirect(target)
