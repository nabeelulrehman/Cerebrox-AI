"""All enrollment state changes go through here so rules are enforced in one place."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from .models import Classroom, Enrollment


class EnrollmentError(Exception):
    pass


def is_active_member(user, classroom) -> bool:
    """Server-side membership check used before ANY private class resource is exposed."""
    if not (user and user.is_authenticated):
        return False
    return Enrollment.objects.filter(
        classroom=classroom, student=user, status=Enrollment.Status.ACTIVE, classroom__is_active=True,
    ).exists()


def find_student(identifier: str):
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    return User.objects.filter(role=User.Role.STUDENT, is_active=True).filter(
        Q(username__iexact=identifier) | Q(email__iexact=identifier)).first()


@transaction.atomic
def invite_student(classroom, identifier):
    student = find_student(identifier)
    if student is None:
        raise EnrollmentError('No registered student found with that username or email.')
    enr, created = Enrollment.objects.select_for_update().get_or_create(
        classroom=classroom, student=student, defaults={'status': Enrollment.Status.INVITED})
    if not created:
        if enr.status == Enrollment.Status.ACTIVE:
            raise EnrollmentError('That student is already in this class.')
        if enr.status == Enrollment.Status.REQUESTED:
            # They already asked to join: inviting them is the same as approving.
            enr.status = Enrollment.Status.ACTIVE
        else:
            enr.status = Enrollment.Status.INVITED
        enr.responded_at = None if enr.status == Enrollment.Status.INVITED else timezone.now()
        enr.save(update_fields=['status', 'responded_at'])
    return enr


@transaction.atomic
def join_by_code(student, code):
    if not student.is_student:
        raise EnrollmentError('Only student accounts can join classes.')
    code = (code or '').strip().upper()
    classroom = Classroom.objects.filter(join_code=code, is_active=True).first()
    if classroom is None or classroom.enrollment_policy == Classroom.Policy.INVITE_ONLY:
        # same message for both so codes of invite-only classes can't be probed
        raise EnrollmentError('That join code is not valid.')
    enr, created = Enrollment.objects.select_for_update().get_or_create(
        classroom=classroom, student=student, defaults={'status': Enrollment.Status.REQUESTED})
    target = (Enrollment.Status.ACTIVE if classroom.enrollment_policy == Classroom.Policy.OPEN_CODE
              else Enrollment.Status.REQUESTED)
    if not created:
        if enr.status == Enrollment.Status.ACTIVE:
            raise EnrollmentError('You are already in this class.')
        if enr.status == Enrollment.Status.INVITED:
            target = Enrollment.Status.ACTIVE        # they were invited; joining accepts it
        if enr.status == Enrollment.Status.REMOVED:
            raise EnrollmentError('The teacher has removed you from this class.')
    enr.status = target
    enr.responded_at = timezone.now() if target == Enrollment.Status.ACTIVE else None
    enr.save(update_fields=['status', 'responded_at'])
    return enr


@transaction.atomic
def respond_to_invite(student, enrollment_id, accept: bool):
    enr = Enrollment.objects.select_for_update().filter(
        pk=enrollment_id, student=student, status=Enrollment.Status.INVITED).first()
    if enr is None:
        raise EnrollmentError('Invitation not found.')
    enr.status = Enrollment.Status.ACTIVE if accept else Enrollment.Status.DECLINED
    enr.responded_at = timezone.now()
    enr.save(update_fields=['status', 'responded_at'])
    return enr


@transaction.atomic
def teacher_decide_request(teacher, enrollment_id, approve: bool):
    enr = Enrollment.objects.select_for_update().filter(
        pk=enrollment_id, classroom__teacher=teacher, status=Enrollment.Status.REQUESTED).first()
    if enr is None:
        raise EnrollmentError('Request not found.')
    enr.status = Enrollment.Status.ACTIVE if approve else Enrollment.Status.DECLINED
    enr.responded_at = timezone.now()
    enr.save(update_fields=['status', 'responded_at'])
    return enr


@transaction.atomic
def remove_student(teacher, enrollment_id):
    enr = Enrollment.objects.select_for_update().filter(pk=enrollment_id, classroom__teacher=teacher).first()
    if enr is None:
        raise EnrollmentError('Enrollment not found.')
    enr.status = Enrollment.Status.REMOVED
    enr.share_progress = False       # consent ends with membership
    enr.responded_at = timezone.now()
    enr.save(update_fields=['status', 'share_progress', 'responded_at'])
    return enr


def leave_class(student, enrollment_id):
    enr = Enrollment.objects.filter(pk=enrollment_id, student=student, status=Enrollment.Status.ACTIVE).first()
    if enr is None:
        raise EnrollmentError('Enrollment not found.')
    enr.status = Enrollment.Status.DECLINED
    enr.share_progress = False
    enr.responded_at = timezone.now()
    enr.save(update_fields=['status', 'share_progress', 'responded_at'])
    return enr
