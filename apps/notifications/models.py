from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Type(models.TextChoices):
        MESSAGE = 'message', 'Message'
        PAYMENT = 'payment', 'Payment'

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                  related_name='notifications')
    title = models.CharField(max_length=160)
    message = models.CharField(max_length=255)
    notification_type = models.CharField(max_length=12, choices=Type.choices, db_index=True)
    related_object_id = models.PositiveBigIntegerField(null=True, blank=True)
    target_url = models.CharField(max_length=500)
    dedupe_key = models.CharField(max_length=180, unique=True, null=True, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    def __str__(self):
        return f'{self.title} for {self.recipient}'
