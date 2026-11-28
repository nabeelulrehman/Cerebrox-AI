from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse

from .models import Notification
from .services import create_notification


def notify_guidance_message(conversation, sender, message=None):
    recipient = conversation.teacher if sender.pk == conversation.student_id else conversation.student
    target_name = 'market:teacher_inbox' if recipient.pk == conversation.teacher_id else 'market:my_guidance'
    recipient_name = sender.get_full_name() or sender.username
    return create_notification(
        recipient=recipient,
        title=f'New message from {recipient_name}',
        message=message.body if message else conversation.message,
        notification_type=Notification.Type.MESSAGE,
        target_url=f"{reverse(target_name)}#conversation-{conversation.pk}",
        related_object_id=conversation.pk,
        dedupe_key=f'guidance-message-{message.pk if message else "initial-" + str(conversation.pk)}-user-{recipient.pk}',
    )


def _payment_target(payment, recipient):
    if getattr(recipient, 'is_admin_role', False):
        return f"{reverse('admin_panel:payment_reports')}#payment-{payment.pk}"
    if recipient.pk == payment.student_id:
        return reverse('market:payment_result', kwargs={'payment_reference': payment.reference})
    return f"{reverse('market:payment_history')}#payment-{payment.pk}"


def notify_payment_submitted(payment, audit):
    """Alert platform reviewers and a teacher when a student submits a direct transfer."""
    User = get_user_model()
    recipients = [payment.student]
    recipients.extend(User.objects.filter(Q(role='admin') | Q(is_superuser=True)).distinct())
    if payment.is_manual_teacher_payment and payment.teacher_id != payment.student_id:
        recipients.append(payment.teacher)
    label = payment.reference
    for recipient in recipients:
        create_notification(
            recipient=recipient,
            title=('Payment awaiting review' if recipient.pk == payment.student_id else 'New payment submitted'),
            message=(
                f'Your payment {label} was submitted and is awaiting review.'
                if recipient.pk == payment.student_id else
                f'{payment.student.get_full_name() or payment.student.username} submitted payment {label} for PKR {payment.amount}.'
            ),
            notification_type=Notification.Type.PAYMENT,
            target_url=_payment_target(payment, recipient),
            related_object_id=payment.pk,
            dedupe_key=f'payment-submitted-audit-{audit.pk}-user-{recipient.pk}',
        )


def notify_payment_status(payment, audit, actor=None):
    """Notify the student and, when useful, the teacher after a real status transition."""
    status_text = payment.get_status_display()
    title = ('New earning received' if payment.status == 'confirmed' and not payment.is_platform_payment
             else f'Payment {status_text.lower()}')
    message = (f'You received an earning of PKR {payment.teacher_earning} from payment {payment.reference}.'
               if title == 'New earning received' else f'Payment {payment.reference} is {status_text.lower()}.')
    recipients = [] if actor and actor.pk == payment.student_id else [payment.student]
    if payment.teacher_id != payment.student_id and (
        actor is None or payment.teacher_id != actor.pk
        or payment.status == 'confirmed' and not payment.is_platform_payment
    ):
        recipients.append(payment.teacher)
    for recipient in recipients:
        create_notification(
            recipient=recipient,
            title=title,
            message=message,
            notification_type=Notification.Type.PAYMENT,
            target_url=_payment_target(payment, recipient),
            related_object_id=payment.pk,
            dedupe_key=f'payment-status-audit-{audit.pk}-user-{recipient.pk}',
        )
