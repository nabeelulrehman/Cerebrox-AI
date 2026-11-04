from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from apps.accounts.permissions import IsStudentRole
from apps.learning.models import Topic, AINote
from .serializers import NotesGenerateSerializer, MCQGenerateSerializer
from .notes_generator import NotesGenerationInProgress, generate_notes, notes_generation_lock
from .mcq_generator import generate_questions
from .gemini_client import AIUnavailableError


class GenerateNotesAPIView(APIView):
    permission_classes = [IsStudentRole]

    def post(self, request):
        serializer = NotesGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = get_object_or_404(
            Topic.objects.select_related('course'),
            pk=serializer.validated_data['topic_id'], course__student=request.user,
        )
        try:
            with notes_generation_lock(
                request.user.pk, topic.pk, serializer.validated_data['length'],
            ):
                content = generate_notes(
                    topic.course.name, topic.name,
                    request.user.effective_study_level, serializer.validated_data['length'],
                )
                note = AINote.objects.create(
                    student=request.user, topic=topic,
                    study_level=request.user.effective_study_level, content=content,
                )
        except NotesGenerationInProgress as exc:
            return Response({'error': str(exc)}, status=409)
        except AIUnavailableError as exc:
            return Response({'error': str(exc)}, status=503)

        return Response({'id': note.id, 'content': content})


class GenerateMCQAPIView(APIView):
    permission_classes = [IsStudentRole]

    def post(self, request):
        serializer = MCQGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = get_object_or_404(
            Topic.objects.select_related('course'),
            pk=serializer.validated_data['topic_id'], course__student=request.user,
        )
        questions = generate_questions(
            topic.course.name, topic.name, request.user.effective_study_level,
            serializer.validated_data['difficulty'], serializer.validated_data['count'],
        )
        return Response({'questions': questions})
