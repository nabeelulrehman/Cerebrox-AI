from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.marketplace.scanning import scan_batch


class Command(BaseCommand):
    help = 'Scan private teacher resources through a private ClamAV daemon.'

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=10)
        parser.add_argument(
            '--retry-failed', action='store_true',
            help='Retry resources whose earlier scan attempt failed.',
        )

    def handle(self, *args, **options):
        if not settings.USE_OBJECT_STORAGE:
            raise CommandError('The scanning worker requires configured private object storage.')
        if not settings.CLAMAV_HOST:
            raise CommandError('Set CLAMAV_HOST in the isolated worker environment.')
        if options['batch_size'] < 1 or options['batch_size'] > 100:
            raise CommandError('--batch-size must be between 1 and 100.')
        results = scan_batch(
            batch_size=options['batch_size'], retry_failed=options['retry_failed'],
        )
        self.stdout.write(self.style.SUCCESS(
            'Scan batch complete: '
            + ', '.join(f'{state}={count}' for state, count in results.items())
        ))
