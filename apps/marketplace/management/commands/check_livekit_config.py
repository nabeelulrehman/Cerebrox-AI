from django.conf import settings
from django.core.management.base import BaseCommand

from apps.marketplace.services import (
    livekit_configuration_issues,
    livekit_recording_configuration_issues,
    livekit_recording_ready,
)


class Command(BaseCommand):
    help = 'Safely report effective LiveKit and recording configuration without displaying secrets.'

    def handle(self, *args, **options):
        names = ('LIVEKIT_URL', 'LIVEKIT_API_KEY', 'LIVEKIT_API_SECRET')
        self.stdout.write('LiveKit settings detected (values are never shown):')
        for name in names:
            self.stdout.write(f'{name}: {bool(getattr(settings, name, ""))}')

        issues = livekit_configuration_issues()
        if issues:
            self.stdout.write(self.style.WARNING('Configuration issues: ' + '; '.join(issues)))
        else:
            self.stdout.write(self.style.SUCCESS('LiveKit configuration is ready.'))

        self.stdout.write('LiveKit recording settings (secret values are never shown):')
        for name in (
            'LIVEKIT_RECORDING_S3_BUCKET',
            'LIVEKIT_RECORDING_S3_REGION',
            'LIVEKIT_RECORDING_S3_ACCESS_KEY',
            'LIVEKIT_RECORDING_S3_SECRET',
            'LIVEKIT_RECORDING_S3_ENDPOINT',
        ):
            self.stdout.write(f'{name}: {bool(getattr(settings, name, ""))}')
        self.stdout.write(f'LIVEKIT_RECORDING_ENABLED: {settings.LIVEKIT_RECORDING_ENABLED}')
        self.stdout.write(f'LIVEKIT_RECORDING_VERIFIED: {settings.LIVEKIT_RECORDING_VERIFIED}')
        self.stdout.write(f'Recording available: {livekit_recording_ready()}')
        recording_issues = livekit_recording_configuration_issues()
        if recording_issues:
            self.stdout.write(self.style.WARNING('Recording issues: ' + '; '.join(recording_issues)))
