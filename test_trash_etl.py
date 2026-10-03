import contextlib
import datetime
import io
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import requests

import trash_etl
from trash_etl import LearningXError, get_lectures


BASE = 'https://etl.example'
COURSE = SimpleNamespace(id=305087)
OTHER_COURSE = SimpleNamespace(id=305172)

# Canvas가 sessionless launch 페이지에 내려주는 LTI 자동 제출 폼
LAUNCH_PAGE = '''<!DOCTYPE html>
<html>
<body>
<form action="/search" method="GET" id="search_form">
  <input type="text" name="q" value="ignored" />
</form>
<form action="https://etl.example/learningx/lti/lecture_attendance/course_menu"
        class="hide"
        method="POST"
        target="tool_content"
        id="tool_form"
        data-tool-launch-type="self"
        data-tool-id="xinics_lecture_attendance"
        data-tool-path="/learningx/lti/lecture_attendance/course_menu"
        data-message-type="tool_launch">
        <input type="hidden" name="oauth_consumer_key" id="oauth_consumer_key" value="key123" />
        <input type="hidden" name="launch_presentation_return_url" id="launch_presentation_return_url" value="https://etl.example/courses/305087/external_content/success/external_tool_redirect?a=1&amp;b=2" />
        <input type="hidden" name="lis_person_name_full" id="lis_person_name_full" value="Kim &quot;Tester&quot;" />
        <input type="hidden" name="custom_masquerading_user_id" id="custom_masquerading_user_id" value="" />
        <input type="hidden" name="oauth_signature" id="oauth_signature" value="abc+/=" />
        <div class="load_tab">
          <button class="btn" type="submit">Load in a new tab</button>
        </div>
</form>
<input type="hidden" name="outside_form" value="ignored" />
</body>
</html>'''

LAUNCH_ACTION = 'https://etl.example/learningx/lti/lecture_attendance/course_menu'
LAUNCH_FIELDS = {
    'oauth_consumer_key': 'key123',
    'launch_presentation_return_url':
        'https://etl.example/courses/305087/external_content/success/external_tool_redirect?a=1&b=2',
    'lis_person_name_full': 'Kim "Tester"',
    'custom_masquerading_user_id': '',
    'oauth_signature': 'abc+/=',
}


ABSENT = object()


def lecture_item(module_item_id, title, due_at, late_at=None, lock_at=None, unlock_at=None,
                 attendance_status='none', completed=False, use_attendance=True):
    """
    LearningX 모듈 목록의 강의영상(attendance_item) 항목.
    attendance_status, completed는 실제 응답에서 키 자체가 빠지는 경우가 많다(ABSENT).
    """
    item = {
        'module_item_id': module_item_id,
        'title': title,
        'content_type': 'attendance_item',
        'content_id': 305541,
        'position': 2,
        'published': None,
        'indent': 0,
        'url': 'https://etl.example/learningx/lti/lecture_attendance/items/view/305541',
        'is_child_content': False,
        'completion_determinable': True,
        'content_data': {
            'item_id': 305541,
            'course_id': 305087,
            'original_item_id': 0,
            'use_attendance': use_attendance,
            'omit_progress': False,
            'item_content_type': 'commons',
            'item_content_id': '6abc8da808841f340236bc0f',
            'use_week_and_lesson': True,
            'week_position': 6,
            'lesson_position': 1,
            'title': title,
            'description': '',
            'created_at': '2026-09-30T04:18:48Z',
            'updated_at': '2026-09-30T04:19:00Z',
            'published': True,
            'course_sctn_ids': [],
            'lecture_period_status': 'open',
            'unlock_at': unlock_at,
            'late_at': late_at,
            'due_at': due_at,
            'lock_at': lock_at,
            'item_content_data': {
                '_id': '6abc8da808841f340236bc0f',
                'content_id': '6abc7dd340b4a',
                'content_type': 'movie',
                'view_url': 'https://lcms.example/em/6abc7dd340b4a',
                'thumbnail_url': 'https://lcms.example/thumb/6abc7dd340b4a',
                'progress_support': True,
                'duration': 6422.6,
                'title': title,
                'content': '',
                'total_file_size': 5188242285,
                'registered_date': '2026-09-30T03:11:15+00:00',
                'last_updated_date': '2026-09-30T03:56:22+00:00',
                'updated_at': '2026-09-30T04:18:48+00:00',
                'created_at': '2026-09-30T04:18:48+00:00',
            },
            'opened': True,
        },
    }
    if attendance_status is not ABSENT:
        item['attendance_status'] = attendance_status
    if completed is not ABSENT:
        item['completed'] = completed
    return item


def module(module_id, title, module_items):
    return {
        'module_id': module_id,
        'title': title,
        'position': 6,
        'published': None,
        'unlock_at': None,
        'is_child_content': False,
        'week_position': 6,
        'module_items': module_items,
    }


ASSIGNMENT_ITEM = {
    'module_item_id': 1624912,
    'title': 'homework05',
    'content_type': 'assignment',
    'content_id': 374664,
    'position': 1,
    'published': None,
    'indent': 0,
    'url': None,
    'is_child_content': False,
    'completion_determinable': True,
    'completed': False,
    'content_data': {
        'unlock_at': None,
        'due_at': '2099-10-09T04:59:59Z',
        'lock_at': None,
        'points_possible': 10,
        'use_week_and_lesson': False,
        'week_position': None,
        'lesson_position': None,
    },
}

ATTACHMENT_ITEM = {
    'module_item_id': 1666121,
    'title': 'Ch06.pdf',
    'content_type': 'attachment',
    'content_id': 9328686,
    'position': 3,
    'published': None,
    'indent': 0,
    'url': None,
    'is_child_content': False,
    'completion_determinable': False,
    'content_data': {},
}


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=''):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise requests.exceptions.JSONDecodeError('Expecting value', self.text, 0)
        return self._json_data


class FakeSession:
    def __init__(self, lms):
        self.lms = lms
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, **kwargs):
        if self.lms.launch_page_error is not None:
            raise self.lms.launch_page_error
        if url != self.lms.launch_url or 'Authorization' in (kwargs.get('headers') or {}):
            return FakeResponse(404)
        return FakeResponse(text=self.lms.launch_page)

    def post(self, url, data=None, **kwargs):
        accepted = (url == LAUNCH_ACTION and data == LAUNCH_FIELDS
                    and 'Authorization' not in (kwargs.get('headers') or {}))
        if accepted and self.lms.issues_token:
            self.cookies.set('xn_api_token', 'lx-token', domain='etl.example', path='/')
            if self.lms.issues_duplicate_cookie:
                self.cookies.set('xn_api_token', 'lx-token', domain='etl.example', path='/learningx')
        return FakeResponse(text='<html></html>')


class FakeLms:
    """Canvas와 LearningX의 HTTP 응답을 흉내낸다."""

    def __init__(self):
        self.launch_url = f'{BASE}/courses/305087/external_tools/sessionless_launch?verifier=v1'
        self.launch_page = LAUNCH_PAGE
        self.launch_page_error = None
        self.launch_address_response = None
        self.issues_token = True
        self.issues_duplicate_cookie = False
        self.modules = {}
        # 다음 모듈 요청들에 한 번씩만 돌려줄 응답(또는 발생시킬 예외)
        self.transient_modules_outcomes = []
        self.permanent_modules_response = None
        self.network_down = False
        self.request_count = 0
        self.launch_count = 0
        self.modules_request_count = 0

    def session(self):
        return FakeSession(self)

    def get(self, url, headers=None, params=None, **kwargs):
        self.request_count += 1
        if self.network_down:
            raise requests.ConnectionError('network is unreachable')
        authorization = (headers or {}).get('Authorization')
        if '/external_tools/sessionless_launch' in url:
            self.launch_count += 1
            tool_url = f'{BASE}/learningx/lti/lecture_attendance/course_menu'
            if authorization != 'Bearer canvas-key' or (params or {}).get('url') != tool_url:
                return FakeResponse(401, {'errors': [{'message': 'Invalid access token.'}]})
            if self.launch_address_response is not None:
                return self.launch_address_response
            return FakeResponse(json_data={'id': 48, 'name': '강의콘텐츠', 'url': self.launch_url})
        for course_id, modules in self.modules.items():
            if url == f'{BASE}/learningx/api/v1/courses/{course_id}/modules':
                self.modules_request_count += 1
                if authorization != 'Bearer lx-token' or (params or {}).get('include_detail') != 'true':
                    return FakeResponse(401, {'message': 'Unauthorized'})
                if self.transient_modules_outcomes:
                    outcome = self.transient_modules_outcomes.pop(0)
                    if isinstance(outcome, Exception):
                        raise outcome
                    return outcome
                if self.permanent_modules_response is not None:
                    return self.permanent_modules_response
                return FakeResponse(json_data=modules)
        return FakeResponse(404, {'message': 'Not Found'})


def reset_learningx_state():
    trash_etl._learningx_token = None
    trash_etl._lectures_cache.clear()
    trash_etl._lecture_failures.clear()


class GetLecturesTest(unittest.TestCase):
    def setUp(self):
        reset_learningx_state()
        self.lms = FakeLms()
        self.lms.modules[305087] = [
            module(1657325, '6주차', [
                ASSIGNMENT_ITEM,
                lecture_item(1666105, 'ch06', '2099-10-16T04:59:59Z',
                             late_at='2099-10-18T04:59:59Z', lock_at='2099-10-23T04:59:59Z'),
                ATTACHMENT_ITEM,
            ]),
            module(1657326, '7주차', []),
        ]
        self.lms.modules[305172] = []
        self.enter(mock.patch.dict(os.environ, {
            'CANVAS_API_URL': f'{BASE}/',
            'CANVAS_API_KEY': 'canvas-key',
        }))
        self.enter(mock.patch.object(trash_etl.requests, 'get', self.lms.get))
        self.enter(mock.patch.object(trash_etl.requests, 'Session', self.lms.session))
        self.sleep = self.enter(mock.patch.object(trash_etl.time, 'sleep'))
        self.enter(contextlib.redirect_stdout(io.StringIO()))

    def enter(self, context_manager):
        value = context_manager.__enter__()
        self.addCleanup(context_manager.__exit__, None, None, None)
        return value

    def test_only_lecture_videos_are_returned_with_their_attendance_deadline(self):
        self.assertEqual(get_lectures(COURSE), [{
            'module_item_id': 1666105,
            'title': 'ch06',
            'due_at': datetime.datetime(2099, 10, 16, 4, 59, 59, tzinfo=datetime.timezone.utc),
            'completed': False,
            'html_url': 'https://etl.example/courses/305087/modules/items/1666105',
        }])

    def test_lecture_that_is_not_open_yet_is_returned_with_its_deadline(self):
        self.lms.modules[305087] = [module(1657325, '6주차', [
            lecture_item(1668375, 'Lecture6-2', '2099-10-12T03:30:59Z',
                         unlock_at='2099-10-03T13:00:00Z', lock_at='2099-10-12T03:30:59Z',
                         attendance_status=ABSENT, completed=ABSENT),
        ])]

        self.assertEqual(get_lectures(COURSE)[0]['due_at'],
                         datetime.datetime(2099, 10, 12, 3, 30, 59, tzinfo=datetime.timezone.utc))

    def test_unexpected_deadline_format_is_a_learningx_error(self):
        for due_at in ('2099-10-16T04:59:59.000Z', '2099-10-16T13:59:59+09:00', '', 20991016):
            with self.subTest(due_at=due_at):
                reset_learningx_state()
                self.lms.modules[305087] = [module(1657325, '6주차', [
                    lecture_item(1666105, 'ch06', due_at),
                ])]

                with self.assertRaises(LearningXError):
                    get_lectures(COURSE)

    def test_lecture_without_attendance_period_has_no_deadline(self):
        self.lms.modules[305087] = [module(1657325, '6주차', [
            lecture_item(1666200, '보강', None, attendance_status=ABSENT,
                         completed=ABSENT, use_attendance=False),
        ])]

        self.assertEqual(get_lectures(COURSE), [{
            'module_item_id': 1666200,
            'title': '보강',
            'due_at': None,
            'completed': False,
            'html_url': 'https://etl.example/courses/305087/modules/items/1666200',
        }])

    def test_fully_watched_lecture_is_completed(self):
        self.lms.modules[305087] = [module(1657325, '6주차', [
            lecture_item(1666105, 'ch06', '2099-10-16T04:59:59Z',
                         attendance_status='none', completed=True),
        ])]

        self.assertIs(get_lectures(COURSE)[0]['completed'], True)

    def test_lecture_already_credited_as_attended_is_completed(self):
        self.lms.modules[305087] = [module(1657325, '6주차', [
            lecture_item(1666105, 'ch06', '2099-10-16T04:59:59Z',
                         attendance_status='attendance', completed=False),
        ])]

        self.assertIs(get_lectures(COURSE)[0]['completed'], True)

    def test_token_is_issued_once_for_all_courses(self):
        get_lectures(COURSE)
        get_lectures(OTHER_COURSE)

        self.assertEqual(self.lms.launch_count, 1)

    def test_lectures_are_requested_once_per_course(self):
        get_lectures(COURSE)
        get_lectures(COURSE)

        self.assertEqual(self.lms.modules_request_count, 1)

    def test_rejected_canvas_key_is_a_learningx_error(self):
        with mock.patch.dict(os.environ, {'CANVAS_API_KEY': 'wrong-key'}):
            with self.assertRaises(LearningXError):
                get_lectures(COURSE)

    def test_unexpected_launch_address_response_is_a_learningx_error(self):
        for body in ([], {'id': 48, 'name': '강의콘텐츠'}):
            with self.subTest(body=body):
                reset_learningx_state()
                self.lms.launch_address_response = FakeResponse(json_data=body)

                with self.assertRaises(LearningXError):
                    get_lectures(COURSE)

    def test_launch_page_without_tool_form_is_a_learningx_error(self):
        self.lms.launch_page = '<html><body>로그인이 필요합니다</body></html>'

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_launch_that_issues_no_token_is_a_learningx_error(self):
        self.lms.issues_token = False

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_token_is_read_even_when_the_cookie_is_set_for_two_paths(self):
        self.lms.issues_duplicate_cookie = True

        self.assertEqual([lecture['title'] for lecture in get_lectures(COURSE)], ['ch06'])

    def test_error_does_not_expose_the_one_time_launch_address(self):
        self.lms.launch_page_error = requests.ConnectionError(
            "HTTPSConnectionPool(host='etl.example', port=443): Max retries exceeded with url: "
            '/courses/305087/external_tools/sessionless_launch?verifier=v1')

        with self.assertRaises(LearningXError) as raised:
            get_lectures(COURSE)

        self.assertNotIn('verifier', str(raised.exception))

    def test_non_json_response_is_a_learningx_error(self):
        self.lms.permanent_modules_response = FakeResponse(200, text='<html>점검 중</html>')

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_unexpected_response_shape_is_a_learningx_error(self):
        self.lms.permanent_modules_response = FakeResponse(200, {'message': 'error'})

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_malformed_lecture_item_is_a_learningx_error(self):
        malformed = lecture_item(1666105, 'ch06', '2099-10-16T04:59:59Z')
        del malformed['content_data']
        self.lms.modules[305087] = [module(1657325, '6주차', [malformed])]

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_server_error_is_retried(self):
        self.lms.transient_modules_outcomes = [FakeResponse(500, text='Whoops, looks like something went wrong.')]

        self.assertEqual([lecture['title'] for lecture in get_lectures(COURSE)], ['ch06'])

    def test_network_error_is_retried(self):
        self.lms.transient_modules_outcomes = [requests.ConnectionError('connection reset')]

        self.assertEqual([lecture['title'] for lecture in get_lectures(COURSE)], ['ch06'])

    def test_persistent_server_error_is_a_learningx_error_after_three_attempts(self):
        self.lms.permanent_modules_response = FakeResponse(500, text='Whoops, looks like something went wrong.')

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

        self.assertEqual(self.lms.modules_request_count, 3)

    def test_client_error_is_a_learningx_error_without_retry(self):
        self.lms.permanent_modules_response = FakeResponse(403, {'message': 'Forbidden'})

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

        self.assertEqual(self.lms.modules_request_count, 1)

    def test_network_failure_is_a_learningx_error(self):
        self.lms.network_down = True

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

    def test_failed_course_is_not_requested_again_in_the_same_run(self):
        self.lms.network_down = True
        with self.assertRaises(LearningXError):
            get_lectures(COURSE)
        self.lms.network_down = False
        requests_so_far = self.lms.request_count

        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

        self.assertEqual(self.lms.request_count, requests_so_far)

    def test_failure_of_one_course_does_not_affect_other_courses(self):
        del self.lms.modules[305087]
        with self.assertRaises(LearningXError):
            get_lectures(COURSE)

        self.assertEqual(get_lectures(OTHER_COURSE), [])


if __name__ == '__main__':
    unittest.main()
