from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Safely check Gemini configuration without sending an API request.'

    def handle(self, *args, **options):
        self.stdout.write(
            f'Environment file: {settings.ENV_FILE.name} '
            f'(found: {settings.ENV_FILE.is_file()})'
        )
        self.stdout.write(f'GEMINI_API_KEY configured: {bool(settings.GEMINI_API_KEY)}')
        self.stdout.write(f'GEMINI_API_KEY source: {settings.GEMINI_API_KEY_SOURCE}')
        self.stdout.write(f'Gemini model: {settings.GEMINI_MODEL}')
        self.stdout.write(
            self.style.WARNING('This check does not validate the key with Google.')
        )
