from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .views import favicon

urlpatterns = [
    path('favicon.ico', favicon, name='favicon'),
    path('django-admin/', admin.site.urls),  # built-in Django admin (superusers)

    path('', include('apps.dashboard.urls_public', namespace='public')),
    path('accounts/', include('apps.accounts.urls', namespace='accounts')),
    path('dashboard/', include('apps.dashboard.urls', namespace='dashboard')),
    path('learning/', include('apps.learning.urls', namespace='learning')),
    path('quizzes/', include('apps.quizzes.urls', namespace='quizzes')),
    path('analytics/', include('apps.analytics.urls', namespace='analytics')),
    path('admin-panel/', include('apps.accounts.admin_urls', namespace='admin_panel')),

    # optional teacher ecosystem
    path('teachers/', include('apps.teachers.urls', namespace='teachers')),
    path('classes/', include('apps.classrooms.urls', namespace='classrooms')),
    path('class-quizzes/', include('apps.class_quizzes.urls', namespace='class_quizzes')),
    path('market/', include('apps.marketplace.urls', namespace='market')),

    path('api/', include('api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
