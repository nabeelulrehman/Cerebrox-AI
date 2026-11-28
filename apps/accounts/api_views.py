from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth import login, logout, authenticate
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from .serializers import RegisterSerializer, UserSerializer
from .login_throttle import consume_login_attempt, normalize_login_identifier


@method_decorator(csrf_protect, name='dispatch')
class RegisterAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        login(request, user)
        return Response(UserSerializer(user).data, status=201)


@method_decorator(csrf_protect, name='dispatch')
class LoginAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        blocked, retry_after = consume_login_attempt(
            request, normalize_login_identifier(request.data.get('username', '')),
        )
        if blocked:
            response = JsonResponse({'detail': 'Too many attempts. Please try again later.'}, status=429)
            response['Retry-After'] = str(retry_after)
            return response
        user = authenticate(request, username=request.data.get('username'), password=request.data.get('password'))
        if user is None:
            return Response({'detail': 'Invalid credentials.'}, status=400)
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=204)


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
