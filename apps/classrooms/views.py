from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import verified_teacher_required, student_required
from . import services
from .forms import ClassroomForm, InviteForm, JoinCodeForm, AnnouncementForm
from .models import Classroom, Enrollment, Announcement
from .services import EnrollmentError


# ======================= Teacher side =======================

@verified_teacher_required
def teacher_classes(request):
    form = ClassroomForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        c = form.save(commit=False)
        c.teacher = request.user
        c.save()
        messages.success(request, f'Class "{c.name}" created.')
        return redirect('classrooms:teacher_detail', class_id=c.id)
    classes = Classroom.objects.filter(teacher=request.user)
    return render(request, 'teacher/classes.html', {'classes': classes, 'form': form})


def _own_class(request, class_id):
    return get_object_or_404(Classroom, pk=class_id, teacher=request.user)


@verified_teacher_required
def teacher_detail(request, class_id):
    c = _own_class(request, class_id)
    enrollments = c.enrollments.select_related('student')
    ctx = {
        'c': c,
        'active': [e for e in enrollments if e.status == Enrollment.Status.ACTIVE],
        'requests': [e for e in enrollments if e.status == Enrollment.Status.REQUESTED],
        'invited': [e for e in enrollments if e.status == Enrollment.Status.INVITED],
        'invite_form': InviteForm(),
        'announce_form': AnnouncementForm(),
        'announcements': c.announcements.all()[:10],
        'class_quizzes': c.class_quizzes.all() if hasattr(c, 'class_quizzes') else [],
        'edit_form': ClassroomForm(instance=c),
    }
    return render(request, 'teacher/class_detail.html', ctx)


@verified_teacher_required
@require_POST
def teacher_edit(request, class_id):
    c = _own_class(request, class_id)
    form = ClassroomForm(request.POST, instance=c)
    if form.is_valid():
        form.save()
        messages.success(request, 'Class updated.')
    else:
        messages.error(request, 'Could not update the class: ' + '; '.join(
            f'{k}: {" ".join(v)}' for k, v in form.errors.items()))
    return redirect('classrooms:teacher_detail', class_id=c.id)


@verified_teacher_required
@require_POST
def teacher_regenerate_code(request, class_id):
    c = _own_class(request, class_id)
    c.regenerate_code()
    messages.success(request, 'New join code generated; the old code no longer works.')
    return redirect('classrooms:teacher_detail', class_id=c.id)


@verified_teacher_required
@require_POST
def teacher_invite(request, class_id):
    c = _own_class(request, class_id)
    form = InviteForm(request.POST)
    if form.is_valid():
        try:
            enr = services.invite_student(c, form.cleaned_data['identifier'])
            if enr.status == Enrollment.Status.ACTIVE:
                messages.success(request, 'Student had already requested to join and is now enrolled.')
            else:
                messages.success(request, 'Invitation sent. The student decides whether to accept.')
        except EnrollmentError as exc:
            messages.error(request, str(exc))
    return redirect('classrooms:teacher_detail', class_id=c.id)


@verified_teacher_required
@require_POST
def teacher_decide(request, enrollment_id):
    try:
        enr = services.teacher_decide_request(request.user, enrollment_id, request.POST.get('decision') == 'approve')
        messages.success(request, f'Request {"approved" if enr.status == Enrollment.Status.ACTIVE else "declined"}.')
        return redirect('classrooms:teacher_detail', class_id=enr.classroom_id)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect('classrooms:teacher_classes')


@verified_teacher_required
@require_POST
def teacher_remove(request, enrollment_id):
    try:
        enr = services.remove_student(request.user, enrollment_id)
        messages.info(request, 'Student removed from the class.')
        return redirect('classrooms:teacher_detail', class_id=enr.classroom_id)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect('classrooms:teacher_classes')


@verified_teacher_required
@require_POST
def teacher_announce(request, class_id):
    c = _own_class(request, class_id)
    form = AnnouncementForm(request.POST)
    if form.is_valid():
        a = form.save(commit=False)
        a.classroom = c
        a.save()
        messages.success(request, 'Announcement posted.')
    return redirect('classrooms:teacher_detail', class_id=c.id)


@verified_teacher_required
def teacher_student_progress(request, class_id, student_id):
    """What a teacher may see about one student: ONLY class-assessed work, plus an
    independent-practice summary if (and only if) the student has consented."""
    c = _own_class(request, class_id)
    enr = get_object_or_404(Enrollment, classroom=c, student_id=student_id, status=Enrollment.Status.ACTIVE)
    from apps.class_quizzes.reporting import student_class_summary
    ctx = {'c': c, 'enr': enr, 'student': enr.student, 'class_summary': student_class_summary(c, enr.student)}
    if enr.share_progress:
        from apps.analytics import performance
        ctx['shared_overview'] = performance.student_overview(enr.student)
    return render(request, 'teacher/student_progress.html', ctx)


# ======================= Student side (all optional) =======================

@student_required
def my_classes(request):
    enrollments = (Enrollment.objects.filter(student=request.user)
                   .exclude(status__in=[Enrollment.Status.REMOVED, Enrollment.Status.DECLINED])
                   .select_related('classroom', 'classroom__teacher'))
    return render(request, 'student/my_classes.html', {
        'active': [e for e in enrollments if e.status == Enrollment.Status.ACTIVE],
        'invites': [e for e in enrollments if e.status == Enrollment.Status.INVITED],
        'pending': [e for e in enrollments if e.status == Enrollment.Status.REQUESTED],
        'join_form': JoinCodeForm(),
    })


@student_required
@require_POST
def join_class(request):
    form = JoinCodeForm(request.POST)
    if form.is_valid():
        try:
            enr = services.join_by_code(request.user, form.cleaned_data['code'])
            if enr.status == Enrollment.Status.ACTIVE:
                messages.success(request, f'You joined {enr.classroom.name}.')
            else:
                messages.info(request, 'Request sent. The teacher will approve or decline it.')
        except EnrollmentError as exc:
            messages.error(request, str(exc))
    return redirect('classrooms:my_classes')


@student_required
@require_POST
def respond_invite(request, enrollment_id):
    try:
        enr = services.respond_to_invite(request.user, enrollment_id, request.POST.get('decision') == 'accept')
        messages.success(request, 'Invitation accepted.' if enr.status == Enrollment.Status.ACTIVE else 'Invitation declined.')
    except EnrollmentError as exc:
        messages.error(request, str(exc))
    return redirect('classrooms:my_classes')


@student_required
@require_POST
def leave(request, enrollment_id):
    try:
        services.leave_class(request.user, enrollment_id)
        messages.info(request, 'You left the class. Your personal learning data is unchanged.')
    except EnrollmentError as exc:
        messages.error(request, str(exc))
    return redirect('classrooms:my_classes')


@student_required
@require_POST
def toggle_share(request, enrollment_id):
    enr = get_object_or_404(Enrollment, pk=enrollment_id, student=request.user, status=Enrollment.Status.ACTIVE)
    enr.share_progress = request.POST.get('share') == '1'
    enr.save(update_fields=['share_progress'])
    messages.success(request, 'Sharing enabled: this teacher can see a summary of your independent practice.'
                     if enr.share_progress else 'Sharing turned off.')
    return redirect('classrooms:student_class', class_id=enr.classroom_id)


@student_required
def student_class(request, class_id):
    c = get_object_or_404(Classroom, pk=class_id)
    if not services.is_active_member(request.user, c):
        raise PermissionDenied
    enr = Enrollment.objects.get(classroom=c, student=request.user)
    return render(request, 'student/class_detail.html', {
        'c': c, 'enr': enr,
        'announcements': c.announcements.all()[:20],
        'class_quizzes': c.class_quizzes.filter(status='published').order_by('-created_at'),
        'resources': c.resources.filter(visibility='classes'),
    })
