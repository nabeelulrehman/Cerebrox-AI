from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.learning.models import Topic, AINote
from .serializers import NotesGenerateSerializer, MCQGenerateSerializer
from .notes_generator import generate_notes
from .mcq_generator import generate_questions
from .gemini_client import AIUnavailableError


class GenerateNotesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = NotesGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = Topic.objects.select_related('course').get(
            pk=serializer.validated_data['topic_id'], course__student=request.user,
        )
        try:
            content = generate_notes(
                topic.course.name, topic.name,
                request.user.effective_study_level, serializer.validated_data['length'],
            )
        except AIUnavailableError as exc:
            return Response({'error': str(exc)}, status=503)

        note = AINote.objects.create(
            student=request.user, topic=topic,
            study_level=request.user.effective_study_level, content=content,
        )
        return Response({'id': note.id, 'content': content})


class GenerateMCQAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MCQGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = Topic.objects.select_related('course').get(
            pk=serializer.validated_data['topic_id'], course__student=request.user,
        )
        questions = generate_questions(
            topic.course.name, topic.name, request.user.effective_study_level,
            serializer.validated_data['difficulty'], serializer.validated_data['count'],
        )
        return Response({'questions': questions})
