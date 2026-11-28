from .models import Notification


def create_notification(*, recipient, title, message, notification_type, target_url,
                        related_object_id=None, dedupe_key=None):
    if recipient is None:
        return None
    if dedupe_key:
        notification, _ = Notification.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                'recipient': recipient,
                'title': title[:160],
                'message': message[:255],
                'notification_type': notification_type,
                'target_url': target_url[:500],
                'related_object_id': related_object_id,
            },
        )
        return notification
    return Notification.objects.create(
        recipient=recipient, title=title[:160], message=message[:255],
        notification_type=notification_type, target_url=target_url[:500],
        related_object_id=related_object_id,
    )


def recent_notifications(user, notification_type, limit=4):
    return Notification.objects.filter(
        recipient=user, notification_type=notification_type,
    )[:limit]
