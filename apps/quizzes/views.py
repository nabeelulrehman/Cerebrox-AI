from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.generics import RetrieveAPIView
from django.shortcuts import get_object_or_404

from .models import Quiz
from .serializers import QuizSerializer, QuestionSerializer
from .services import QuizGenerationInProgress, create_ai_quiz
from .evaluation import submit_quiz
from apps.learning.models import Topic
from apps.accounts.permissions import IsStudentRole


class GenerateQuizAPIView(APIView):
    permission_classes = [IsStudentRole]

    def post(self, request):
        try:
            topic_id = int(request.data['topic_id'])
            num_questions = int(request.data.get('num_questions', 10))
        except (KeyError, TypeError, ValueError):
            return Response({'detail': 'topic_id and num_questions must be integers.'}, status=400)
        if not 3 <= num_questions <= 25:
            return Response({'detail': 'num_questions must be between 3 and 25.'}, status=400)
        difficulty = request.data.get('difficulty', 'Medium')
        if difficulty not in ('Easy', 'Medium', 'Hard'):
            return Response({'detail': 'difficulty must be Easy, Medium or Hard.'}, status=400)
        topic = get_object_or_404(Topic.objects.select_related('course'), pk=topic_id, course__student=request.user)
        try:
            quiz = create_ai_quiz(request.user, topic, difficulty, num_questions)
        except QuizGenerationInProgress:
            return Response(
                {'detail': 'This quiz is already being generated. Please wait before retrying.'},
                status=409,
            )
        questions = [QuestionSerializer(a.question).data for a in quiz.answers.select_related('question').all()]
        return Response({'quiz': QuizSerializer(quiz).data, 'questions': questions})


class SubmitQuizAPIView(APIView):
    permission_classes = [IsStudentRole]

    def post(self, request, pk):
        quiz = get_object_or_404(Quiz, pk=pk, student=request.user)
        if quiz.completed:
            return Response({'detail': 'This quiz was already submitted.'}, status=409)
        answers = request.data.get('answers', {})
        if not isinstance(answers, dict):
            return Response({'detail': 'answers must be an object of question_id -> option.'}, status=400)
        submit_quiz(quiz, answers)
        return Response(QuizSerializer(quiz).data)


class QuizDetailAPIView(RetrieveAPIView):
    permission_classes = [IsStudentRole]
    serializer_class = QuizSerializer

    def get_queryset(self):
        return Quiz.objects.filter(student=self.request.user)
