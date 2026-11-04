from rest_framework.views import APIView
from rest_framework.response import Response
from apps.accounts.permissions import IsStudentRole

from . import performance, weak_topics, recommendations


class PerformanceAPIView(APIView):
    permission_classes = [IsStudentRole]

    def get(self, request):
        overview = performance.student_overview(request.user)
        overview['recent_quizzes'] = [
            {'topic': q.topic.name, 'percentage': q.percentage, 'created_at': q.created_at}
            for q in overview['recent_quizzes']
        ]
        overview['topics'] = performance.topic_wise_performance(request.user)
        overview['courses'] = performance.course_wise_performance(request.user)
        return Response(overview)


class WeakTopicsAPIView(APIView):
    permission_classes = [IsStudentRole]

    def get(self, request):
        return Response(weak_topics.strongest_and_weakest(request.user))


class RecommendationsAPIView(APIView):
    permission_classes = [IsStudentRole]

    def get(self, request):
        return Response(recommendations.recommendations_for_student(request.user))
