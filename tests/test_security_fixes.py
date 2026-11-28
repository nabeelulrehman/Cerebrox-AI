import os
import subprocess
import sys

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from apps.accounts.forms import AdminPasswordChangeForm
from apps.accounts.models import LoginRateLimitBucket

User = get_user_model()


class LoginThrottleTests(TestCase):
    def test_web_login_is_throttled_with_generic_response_and_retry_after(self):
        client = Client()
        for _ in range(5):
            response = client.post('/accounts/login/', {
                'username': 'unknown-user', 'password': 'incorrect',
            })
            self.assertEqual(response.status_code, 200)

        with self.assertLogs('apps.accounts.login_throttle', level='WARNING') as captured:
            response = client.post('/accounts/login/', {
                'username': 'unknown-user', 'password': 'incorrect',
            })
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('unknown-user', '\n'.join(captured.output))
        self.assertIn('Retry-After', response)
        self.assertContains(response, 'Too many attempts', status_code=429)
        self.assertNotContains(response, 'unknown-user', status_code=429)

    def test_account_bucket_is_shared_across_source_ips(self):
        for index in range(5):
            response = Client(REMOTE_ADDR=f'192.0.2.{index + 1}').post(
                '/api/auth/login/',
                {'username': 'shared-login', 'password': 'incorrect'},
                content_type='application/json',
            )
            self.assertEqual(response.status_code, 400)

        response = Client(REMOTE_ADDR='198.51.100.20').post(
            '/api/auth/login/',
            {'username': 'shared-login', 'password': 'incorrect'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(LoginRateLimitBucket.objects.filter(attempts__gt=0).count(), 7)

    def test_ip_bucket_is_shared_between_web_and_api_login(self):
        client = Client(REMOTE_ADDR='203.0.113.20')
        for _ in range(5):
            self.assertEqual(client.post('/accounts/login/', {
                'username': 'different-account', 'password': 'incorrect',
            }).status_code, 200)
        response = client.post(
            '/api/auth/login/',
            {'username': 'another-account', 'password': 'incorrect'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 429)

    @override_settings(IS_VERCEL=True)
    def test_vercel_uses_platform_overwritten_client_ip_header(self):
        client = Client(REMOTE_ADDR='10.0.0.8')
        for index in range(5):
            response = client.post(
                '/api/auth/login/',
                {'username': f'vercel-user-{index}', 'password': 'incorrect'},
                content_type='application/json',
                HTTP_X_VERCEL_FORWARDED_FOR='198.51.100.10',
            )
            self.assertEqual(response.status_code, 400)
        response = client.post(
            '/api/auth/login/',
            {'username': 'different-user', 'password': 'incorrect'},
            content_type='application/json',
            HTTP_X_VERCEL_FORWARDED_FOR='198.51.100.11',
        )
        self.assertEqual(response.status_code, 400)


class ApiLoginCsrfTests(TestCase):
    def test_api_login_and_registration_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(
            '/api/auth/login/',
            {'username': 'someone', 'password': 'anything'},
            content_type='application/json',
        ).status_code, 403)
        self.assertEqual(client.post(
            '/api/auth/register/',
            {'username': 'csrf-register', 'password': 'StrongPass123'},
            content_type='application/json',
        ).status_code, 403)
        self.assertFalse(User.objects.filter(username='csrf-register').exists())

    def test_api_login_succeeds_with_csrf_and_shared_rate_limit(self):
        user = User.objects.create_user('api-csrf-user', password='StrongPass123')
        client = Client(enforce_csrf_checks=True)
        client.get('/accounts/login/')
        token = client.cookies['csrftoken'].value
        response = client.post(
            '/api/auth/login/',
            {'username': user.username, 'password': 'StrongPass123'},
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(client.session['_auth_user_id']), str(user.pk))

    def test_api_registration_succeeds_with_csrf_and_creates_student_session(self):
        client = Client(enforce_csrf_checks=True)
        client.get('/accounts/register/')
        token = client.cookies['csrftoken'].value
        response = client.post(
            '/api/auth/register/',
            {
                'username': 'csrf-api-student',
                'first_name': 'CSRF',
                'last_name': 'Student',
                'email': 'csrf-api-student@example.com',
                'password': 'StrongPass123',
            },
            content_type='application/json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)
        student = User.objects.get(username='csrf-api-student')
        self.assertTrue(student.is_student)
        self.assertEqual(str(client.session['_auth_user_id']), str(student.pk))


class PasswordChangeValidationTests(TestCase):
    def test_password_change_uses_configured_validators(self):
        user = User.objects.create_user('password-user', password='CurrentPass123')
        weak = AdminPasswordChangeForm(user, {
            'old_password': 'CurrentPass123',
            'new_password1': 'password',
            'new_password2': 'password',
        })
        self.assertFalse(weak.is_valid())

        strong_password = 'Distinct phrase! 947'
        strong = AdminPasswordChangeForm(user, {
            'old_password': 'CurrentPass123',
            'new_password1': strong_password,
            'new_password2': strong_password,
        })
        self.assertTrue(strong.is_valid(), strong.errors)
        strong.save()
        user.refresh_from_db()
        self.assertTrue(user.check_password(strong_password))

    def test_password_change_view_rejects_weak_and_accepts_valid_password(self):
        user = User.objects.create_user('password-view-user', password='CurrentPass123')
        client = Client()
        client.force_login(user)
        weak = client.post('/accounts/password/', {
            'old_password': 'CurrentPass123',
            'new_password1': 'password',
            'new_password2': 'password',
        })
        self.assertEqual(weak.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('CurrentPass123'))

        strong_password = 'A different strong phrase! 681'
        changed = client.post('/accounts/password/', {
            'old_password': 'CurrentPass123',
            'new_password1': strong_password,
            'new_password2': strong_password,
        })
        self.assertEqual(changed.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password(strong_password))
        self.assertEqual(str(client.session['_auth_user_id']), str(user.pk))


class ProductionSecuritySettingsTests(TestCase):
    def _run_settings_probe(self, extra_env, code):
        env = os.environ.copy()
        for name in (
            'VERCEL', 'DJANGO_PRODUCTION_SECURITY_CHECKS', 'DEBUG', 'SECRET_KEY',
            'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS', 'DATABASE_URL',
            'OBJECT_STORAGE_BUCKET', 'OBJECT_STORAGE_ACCESS_KEY', 'OBJECT_STORAGE_SECRET_KEY',
            'GEMINI_API_KEY',
        ):
            env.pop(name, None)
        env.update(extra_env)
        return subprocess.run(
            [sys.executable, '-c', code],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_production_security_mode_isolated_from_local_debug_and_database(self):
        env = {
            'DJANGO_PRODUCTION_SECURITY_CHECKS': '1',
            'DEBUG': 'False',
            'SECRET_KEY': 'x' * 60,
            'ALLOWED_HOSTS': 'app.example.com',
            'CSRF_TRUSTED_ORIGINS': 'https://app.example.com',
        }
        code = (
            'from django.conf import settings; '
            'assert settings.DEBUG is False; '
            'assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"; '
            'assert settings.CSRF_COOKIE_SECURE and settings.SESSION_COOKIE_SECURE; '
            'assert settings.SECURE_SSL_REDIRECT and settings.SECURE_HSTS_SECONDS > 0; '
            'print("production settings verified")'
        )
        result = self._run_settings_probe(env, code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('production settings verified', result.stdout)

    def test_production_mode_rejects_http_and_unapproved_csrf_origins(self):
        for origin in ('http://app.example.com', 'https://attacker.example'):
            result = self._run_settings_probe({
                'DJANGO_PRODUCTION_SECURITY_CHECKS': '1',
                'DEBUG': 'False',
                'SECRET_KEY': 'x' * 60,
                'ALLOWED_HOSTS': 'app.example.com',
                'CSRF_TRUSTED_ORIGINS': origin,
            }, 'import config.settings as s; print(s.DEBUG, s.IS_PRODUCTION_CONFIGURATION, s.CSRF_TRUSTED_ORIGINS)')
            self.assertNotEqual(result.returncode, 0, f'{origin}: {result.stdout} {result.stderr}')
            self.assertIn('CSRF_TRUSTED_ORIGINS', result.stderr)

    def test_vercel_gemini_key_never_falls_back_to_dotenv(self):
        result = self._run_settings_probe({
            'VERCEL': '1',
            'DEBUG': 'False',
            'SECRET_KEY': 'x' * 60,
            'ALLOWED_HOSTS': 'app.example.com',
            'CSRF_TRUSTED_ORIGINS': 'https://app.example.com',
            'DATABASE_URL': 'postgresql://not-used.invalid/db?sslmode=require',
            'OBJECT_STORAGE_BUCKET': 'private-bucket',
            'OBJECT_STORAGE_ACCESS_KEY': 'placeholder-access',
            'OBJECT_STORAGE_SECRET_KEY': 'placeholder-secret',
        }, (
            'import dotenv; from unittest.mock import patch; '
            'patcher=patch("dotenv.dotenv_values", return_value={"GEMINI_API_KEY":"dotenv-only-key"}); '
            'patcher.start(); import config.settings as s; '
            'assert s._DOTENV_VALUES == {}; '
            'assert s.GEMINI_API_KEY == ""; '
            'assert s.GEMINI_API_KEY_SOURCE == "not configured"'
        ))
        self.assertEqual(result.returncode, 0, result.stderr)
