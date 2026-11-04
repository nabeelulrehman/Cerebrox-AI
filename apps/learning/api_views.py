from rest_framework import viewsets, mixins
from apps.accounts.permissions import IsStudentRole

from .models import Course, Topic, AINote
from .serializers import CourseSerializer, TopicSerializer, AINoteSerializer


class CourseViewSet(viewsets.ModelViewSet):
    """Each student only ever sees/manages their own courses."""
    serializer_class = CourseSerializer
    permission_classes = [IsStudentRole]

    def get_queryset(self):
        return Course.objects.filter(student=self.request.user)

    def perform_create(self, serializer):
        serializer.save(student=self.request.user)


class TopicViewSet(viewsets.ModelViewSet):
    serializer_class = TopicSerializer
    permission_classes = [IsStudentRole]

    def get_queryset(self):
        qs = Topic.objects.filter(course__student=self.request.user).select_related('course')
        course_id = self.request.query_params.get('course')
        return qs.filter(course_id=course_id) if course_id else qs

    def perform_create(self, serializer):
        course_id = self.request.data.get('course')
        if course_id:
            course = Course.objects.filter(pk=course_id, student=self.request.user).first()
            if course is not None:
                serializer.save(course=course)
                return
        serializer.save()

    def perform_update(self, serializer):
        course_id = self.request.data.get('course')
        if course_id:
            course = Course.objects.filter(pk=course_id, student=self.request.user).first()
            if course is not None:
                serializer.save(course=course)
                return
        serializer.save()


class AINoteViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AINoteSerializer
    permission_classes = [IsStudentRole]

    def get_queryset(self):
        return AINote.objects.filter(student=self.request.user)
