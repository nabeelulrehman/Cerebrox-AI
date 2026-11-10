import asyncio
import time
import urllib.request
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from livekit import api
from livekit.protocol import egress

from apps.marketplace.models import TeachingSession
from apps.marketplace.services import (
    _configured_secret,
    _recording_storage_client,
    _start_egress,
    _verify_private_recording_object,
    livekit_api_url,
    livekit_ready,
    livekit_room_name,
)


class Command(BaseCommand):
    help = 'Record an active class through LiveKit Egress and verify private S3 playback before enabling recording.'

    def add_arguments(self, parser):
        parser.add_argument('--session-id', type=int, required=True)
        parser.add_argument('--duration', type=int, default=10, help='Capture duration in seconds (10–60).')
        parser.add_argument(
            '--consent-confirmed',
            action='store_true',
            help='Confirm all participants were told about and consented to this short verification recording.',
        )

    def handle(self, *args, **options):
        if not 10 <= options['duration'] <= 60:
            raise CommandError('--duration must be between 10 and 60 seconds.')
        if not options['consent_confirmed']:
            raise CommandError('Notify all participants and obtain consent, then rerun with --consent-confirmed.')
        if not settings.LIVEKIT_RECORDING_ENABLED:
            raise CommandError('Set LIVEKIT_RECORDING_ENABLED=True, then restart Django before running this test.')
        if not livekit_ready():
            raise CommandError('LiveKit URL or API credentials are missing or invalid.')
        storage_values = (
            settings.LIVEKIT_RECORDING_S3_BUCKET,
            settings.LIVEKIT_RECORDING_S3_REGION,
            settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
            settings.LIVEKIT_RECORDING_S3_SECRET,
        )
        if not all(_configured_secret(value) for value in storage_values):
            raise CommandError('One or more private S3 storage settings are missing or placeholders.')

        session = TeachingSession.objects.filter(pk=options['session_id']).first()
        if session is None or session.status != TeachingSession.Status.ONGOING:
            raise CommandError('Choose a class that is currently ongoing; the verification records that actual room.')
        if session.recording_status in (
            TeachingSession.RecordingStatus.STARTING,
            TeachingSession.RecordingStatus.RECORDING,
            TeachingSession.RecordingStatus.STOPPING,
        ):
            raise CommandError('This class already has an active or stopping recording; choose another test session.')

        recording_key = f'recordings/session-{session.pk}/verification-{uuid.uuid4().hex}.mp4'
        egress_id = ''
        completed = None
        try:
            info = asyncio.run(_start_egress(session, recording_key))
            egress_id = info.egress_id
            self.stdout.write('LiveKit Egress started; recording the active class for the requested duration…')
            time.sleep(options['duration'])
            asyncio.run(self._stop(egress_id))
            completed = self._wait_for_completion(egress_id)
            if not completed.file_results or not completed.file_results[0].size:
                raise CommandError('Egress completed without a non-empty MP4 file.')
            _verify_private_recording_object(recording_key)
            self._verify_signed_playback(recording_key)
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError(f'LiveKit Egress or private playback verification failed ({type(exc).__name__}).') from exc
        finally:
            if egress_id and completed is None:
                try:
                    asyncio.run(self._stop(egress_id))
                except Exception:
                    pass
            if completed is not None:
                try:
                    _recording_storage_client().delete_object(
                        Bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
                        Key=recording_key,
                    )
                except Exception:
                    self.stderr.write(self.style.WARNING(
                        'The private verification file could not be removed; delete its verification-* object from the recordings/session folder.'
                    ))
        self.stdout.write(self.style.SUCCESS(
            'LiveKit Egress and private signed playback were verified. '
            'No settings were changed. Set LIVEKIT_RECORDING_VERIFIED=True yourself only if this test is accepted, then restart Django.'
        ))

    async def _stop(self, egress_id):
        client = api.LiveKitAPI(livekit_api_url(), settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        try:
            await client.egress.stop_egress(egress.StopEgressRequest(egress_id=egress_id))
        finally:
            await client.aclose()

    def _wait_for_completion(self, egress_id):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            response = asyncio.run(self._list_egress(egress_id))
            info = next((item for item in response.items if item.egress_id == egress_id), None)
            if info is None:
                time.sleep(2)
                continue
            if info.status == egress.EgressStatus.EGRESS_COMPLETE:
                return info
            if info.status in (
                egress.EgressStatus.EGRESS_FAILED,
                egress.EgressStatus.EGRESS_ABORTED,
                egress.EgressStatus.EGRESS_LIMIT_REACHED,
            ):
                raise CommandError('LiveKit Egress reported a failed or aborted recording.')
            time.sleep(2)
        raise CommandError('Timed out waiting for LiveKit Egress to finish the test recording.')

    async def _list_egress(self, egress_id):
        client = api.LiveKitAPI(livekit_api_url(), settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        try:
            return await client.egress.list_egress(egress.ListEgressRequest(egress_id=egress_id))
        finally:
            await client.aclose()

    def _verify_signed_playback(self, key):
        client = _recording_storage_client()
        url = client.generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.LIVEKIT_RECORDING_S3_BUCKET, 'Key': key},
            ExpiresIn=60,
        )
        request = urllib.request.Request(url, headers={'Range': 'bytes=0-0'})
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status not in (200, 206) or not response.read(1):
                raise CommandError('The signed recording playback URL did not return video data.')

