from django.db.models import Count, Q

from .models import Notification


def notification_context(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0, 'unread_message_notification_count': 0,
                'unread_payment_notification_count': 0}
    unread = Notification.objects.filter(recipient=request.user, is_read=False)
    counts = unread.aggregate(
        total=Count('pk'),
        messages=Count('pk', filter=Q(notification_type=Notification.Type.MESSAGE)),
        payments=Count('pk', filter=Q(notification_type=Notification.Type.PAYMENT)),
    )
    return {
        'unread_notification_count': counts['total'],
        'unread_message_notification_count': counts['messages'],
        'unread_payment_notification_count': counts['payments'],
    }
