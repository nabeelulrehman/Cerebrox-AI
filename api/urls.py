"""
Central REST API aggregator.
- Session-auth based (same login as the web app) so the API and the
  server-rendered pages share one auth system.
- quizzes/analytics already expose their own API sub-paths under
  /quizzes/api/... and /analytics/api/... (see config/urls.py) since
  those apps are primarily server-rendered; they're not duplicated here.
"""
from django.urls import path, include
from apps.dashboard.api_views import DashboardSummaryAPIView

urlpatterns = [
    path('auth/', include('apps.accounts.api_urls')),
    path('learning/', include('apps.learning.api_urls')),
    path('ai/', include('apps.ai_engine.urls')),
    path('dashboard/summary/', DashboardSummaryAPIView.as_view(), name='api_dashboard_summary'),
]
