from rest_framework import serializers
from .models import Question, Quiz, QuizAnswer


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = ('id', 'question', 'option_a', 'option_b', 'option_c', 'option_d', 'difficulty')
        # correct_answer intentionally excluded from the student-facing serializer


class QuizAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuizAnswer
        fields = ('id', 'question', 'selected_answer', 'correct')


class QuizSerializer(serializers.ModelSerializer):
    topic_name = serializers.CharField(source='topic.name', read_only=True)
    performance_label = serializers.ReadOnlyField()

    class Meta:
        model = Quiz
        fields = ('id', 'topic', 'topic_name', 'difficulty', 'total_questions', 'score',
                   'percentage', 'performance_label', 'completed', 'created_at',
                   'preparation_plan', 'preparation_topic', 'preparation_session')
        read_only_fields = ('score', 'percentage', 'completed', 'preparation_plan',
                            'preparation_topic', 'preparation_session')
