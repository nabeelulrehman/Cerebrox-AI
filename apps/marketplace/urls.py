from django.urls import path
from . import views as v

app_name = 'market'

urlpatterns = [
    path('resources/', v.public_resources, name='resources'),
    path('resources/<int:resource_id>/download/', v.resource_download, name='download'),
    path('resources/<int:resource_id>/buy/', v.resource_buy, name='buy'),
    path('teach/resources/', v.teacher_resources, name='teacher_resources'),
    path('teach/resources/<int:resource_id>/delete/', v.resource_delete, name='resource_delete'),
    path('teach/inbox/', v.teacher_inbox, name='teacher_inbox'),
    path('teach/inbox/<int:request_id>/reply/', v.guidance_reply, name='guidance_reply'),
    path('teach/sessions/', v.teacher_sessions, name='teacher_sessions'),
    path('guidance/<int:teacher_id>/', v.guidance_request, name='guidance_request'),
    path('guidance/send/', v.guidance_send, name='guidance_send'),
    path('guidance/conversations/<int:request_id>/reply/', v.guidance_conversation_reply,
         name='guidance_conversation_reply'),
    path('guidance/', v.my_guidance, name='my_guidance'),
    path('sessions/', v.sessions_list, name='sessions'),
    path('sessions/<int:session_id>/book/', v.session_book, name='session_book'),
    path('reviews/<int:teacher_id>/', v.review_create, name='review_create'),
    path('admin/queue/', v.admin_queue, name='admin_queue'),
    path('admin/payments/<int:payment_id>/', v.admin_payment, name='admin_payment'),
    path('admin/reviews/<int:review_id>/', v.admin_review, name='admin_review'),
]
