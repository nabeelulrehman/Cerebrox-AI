from django.contrib import admin
from .models import (
    Resource, Payment, PaymentMethodConfiguration, GuidanceRequest, TeachingSession, SessionBooking,
    SessionAttendance, Review,
)

@admin.register(PaymentMethodConfiguration)
class PaymentMethodConfigurationAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Availability', {'fields': ('jazzcash_enabled', 'easypaisa_enabled', 'bank_transfer_enabled', 'stripe_enabled')}),
        ('Bank transfer destination', {'fields': ('bank_account_title', 'bank_name', 'bank_iban', 'bank_raast_id', 'bank_instructions')}),
        ('Provider credentials', {'fields': (), 'description': 'Gateway secrets and sandbox/production keys are configured only through server environment variables.'}),
    )

    def has_add_permission(self, request):
        return not PaymentMethodConfiguration.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('reference', 'student', 'teacher', 'method', 'amount', 'status', 'created_at')
    list_filter = ('method', 'status')
    search_fields = ('reference', 'transaction_reference', 'student__username', 'teacher__username')
    readonly_fields = ('reference', 'created_at', 'confirmed_at', 'confirmed_by', 'provider_reference',
                       'provider_session_id', 'checkout_url', 'receipt')


for m in (Resource, GuidanceRequest, TeachingSession, SessionBooking, SessionAttendance, Review):
    admin.site.register(m)
