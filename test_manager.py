import contextlib
import datetime
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import xxhash

import manager
from trash_etl import LearningXError


KST = ZoneInfo('Asia/Seoul')
UTC = datetime.timezone.utc
COURSE = SimpleNamespace(id=305087, name='2026-2 응용전산물리 (001)')
OTHER_COURSE = SimpleNamespace(id=305127, name='2026-2 기본물리수학 (001)')
OPEN_DEADLINE = datetime.datetime(2099, 10, 16, 4, 59, 59, tzinfo=UTC)
PASSED_DEADLINE = datetime.datetime(2020, 10, 16, 4, 59, 59, tzinfo=UTC)
LECTURE_URL = 'https://etl.example/courses/305087/modules/items/1666105'
ASSIGNMENT_URL = 'https://etl.example/courses/305087/assignments/374664'


def lecture(**overrides):
    """trash_etl.get_lectures가 돌려주는 강의영상"""
    values = {
        'module_item_id': 1666105,
        'title': 'ch06',
        'due_at': OPEN_DEADLINE,
        'completed': False,
        'html_url': LECTURE_URL,
    }
    values.update(overrides)
    return values


def assignment(**overrides):
    values = {
        'name': 'homework05',
        'due_at': '2099-10-09T04:59:59Z',
        'html_url': ASSIGNMENT_URL,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def lecture_module_item(**overrides):
    """Canvas 모듈 항목으로 보이는 강의영상"""
    values = {
        'id': 1666105,
        'title': 'ch06',
        'type': 'ExternalTool',
        'html_url': LECTURE_URL,
        'content_details': {'locked_for_user': False},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.enter(contextlib.redirect_stdout(io.StringIO()))
        tmp = self.enter(tempfile.TemporaryDirectory())
        self.patch(manager, 'get_db_path', return_value=Path(tmp) / 'checked_items.db')
        manager.init_db()
        self.self_email = self.patch(manager.true_email, 'self_email')
        self.add_event = self.patch(manager.true_calendar, 'add_event')
        self.send_text = self.patch(manager.true_line, 'send_text')
        self.add_todolist = self.patch(manager, 'add_todolist')

    def enter(self, context_manager):
        value = context_manager.__enter__()
        self.addCleanup(context_manager.__exit__, None, None, None)
        return value

    def patch(self, target, name, **kwargs):
        return self.enter(mock.patch.object(target, name, **kwargs))


class ProcessAssignmentsTest(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.get_assignments = self.patch(manager, 'get_assignments', return_value=[assignment()])

    def test_new_assignment_is_registered_in_calendar_at_its_due_date(self):
        manager.process_assignments(COURSE, KST)

        self.add_event.assert_called_once_with(
            title='2026-2 응용전산물리 (001) homework05',
            description=f'링크: {ASSIGNMENT_URL}',
            calendar_name='과제, 강의',
            start_time=datetime.datetime(2099, 10, 9, 13, 59, 59, tzinfo=KST),
            duration=datetime.timedelta(hours=0),
            notify_times=[datetime.timedelta(days=-2), datetime.timedelta(days=-1)],
        )

    def test_assignment_announced_by_an_earlier_version_is_not_announced_again(self):
        # 이전 버전이 기록해 둔 본문 형식. 형식이 달라지면 기존 과제가 전부 다시 알림된다.
        old_body = ('2026-2 응용전산물리 (001) homework05\n'
                    '\n타입: 과제\n'
                    '\n마감: 2099-10-09 13:59:59+09:00\n'
                    f'\n링크: {ASSIGNMENT_URL}')
        old_hash = xxhash.xxh3_128(old_body.encode('utf-8')).hexdigest()
        manager.update_checked_item_list(old_hash, '2026-2 응용전산물리 (001) homework05')

        manager.process_assignments(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_event.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_due_date_without_utc_marker_is_read_as_korean_time(self):
        self.get_assignments.return_value = [assignment(due_at='2099-10-09T13:59:59')]

        manager.process_assignments(COURSE, KST)

        self.assertEqual(self.add_event.call_args.kwargs['start_time'],
                         datetime.datetime(2099, 10, 9, 13, 59, 59, tzinfo=KST))

    def test_assignment_past_its_due_date_is_ignored(self):
        self.get_assignments.return_value = [assignment(due_at='2020-10-09T04:59:59Z')]

        manager.process_assignments(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_event.assert_not_called()

    def test_assignment_without_due_date_is_registered_at_the_current_time(self):
        self.get_assignments.return_value = [assignment(due_at=None)]
        before = datetime.datetime.now(KST)

        manager.process_assignments(COURSE, KST)

        after = datetime.datetime.now(KST)
        self.assertLessEqual(before, self.add_event.call_args.kwargs['start_time'])
        self.assertLessEqual(self.add_event.call_args.kwargs['start_time'], after)
        self.assertIn('\n마감: None\n', self.self_email.call_args.args[1])


class ProcessLecturesTest(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.get_lectures = self.patch(manager, 'get_lectures', return_value=[lecture()])

    def test_open_lecture_is_registered_in_calendar_at_its_attendance_deadline(self):
        manager.process_lectures(COURSE, KST)

        self.add_event.assert_called_once_with(
            title='2026-2 응용전산물리 (001) ch06',
            description=f'링크: {LECTURE_URL}',
            calendar_name='과제, 강의',
            start_time=datetime.datetime(2099, 10, 16, 13, 59, 59, tzinfo=KST),
            duration=datetime.timedelta(hours=0),
            notify_times=[datetime.timedelta(days=-2), datetime.timedelta(days=-1)],
        )

    def test_open_lecture_is_emailed_with_its_deadline(self):
        manager.process_lectures(COURSE, KST)

        self.self_email.assert_called_once_with(
            '[etl] 2026-2 응용전산물리 (001) ch06',
            '2026-2 응용전산물리 (001) ch06\n'
            '\n타입: 강의영상\n'
            '\n마감: 2099-10-16 13:59:59+09:00\n'
            f'\n링크: {LECTURE_URL}',
        )

    def test_open_lecture_adds_a_calendar_review_task(self):
        manager.process_lectures(COURSE, KST)

        self.add_todolist.assert_called_once_with(
            name='2026-2 응용전산물리 (001) ch06 일정 검토',
            description='2099-10-16 13:59:59+09:00에 시작하는 2026-2 응용전산물리 (001) ch06 '
                        f'일정을 캘린더에 추가했습니다.\n링크: {LECTURE_URL}',
            due_date='today',
            priority=3,
        )

    def test_lecture_past_its_deadline_is_ignored(self):
        self.get_lectures.return_value = [lecture(due_at=PASSED_DEADLINE)]

        manager.process_lectures(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_event.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_watched_lecture_is_ignored(self):
        self.get_lectures.return_value = [lecture(completed=True)]

        manager.process_lectures(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_event.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_without_deadline_is_ignored(self):
        self.get_lectures.return_value = [lecture(due_at=None)]

        manager.process_lectures(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_event.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_is_announced_only_once(self):
        manager.process_lectures(COURSE, KST)
        manager.process_lectures(COURSE, KST)

        self.assertEqual(self.self_email.call_count, 1)
        self.assertEqual(self.add_event.call_count, 1)
        self.assertEqual(self.add_todolist.call_count, 1)

    def test_changed_deadline_is_registered_again(self):
        manager.process_lectures(COURSE, KST)
        self.get_lectures.return_value = [lecture(due_at=datetime.datetime(2099, 10, 23, 4, 59, 59, tzinfo=UTC))]

        manager.process_lectures(COURSE, KST)

        self.assertEqual(
            [call.kwargs['start_time'] for call in self.add_event.call_args_list],
            [datetime.datetime(2099, 10, 16, 13, 59, 59, tzinfo=KST),
             datetime.datetime(2099, 10, 23, 13, 59, 59, tzinfo=KST)],
        )


class ProcessItemsTest(ManagerTestCase):
    MODULE_ITEM_EMAIL = (
        '[etl] 2026-2 응용전산물리 (001) ch06',
        f'2026-2 응용전산물리 (001) ch06\n\n타입: 줌/녹강\n\n링크: {LECTURE_URL}',
    )

    def setUp(self):
        super().setUp()
        self.patch(manager, 'get_items', return_value=[lecture_module_item()])
        self.get_lectures = self.patch(manager, 'get_lectures', return_value=[lecture()])

    def test_open_lecture_is_not_announced_as_a_module_item(self):
        manager.process_items(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_is_not_announced_as_a_module_item_after_its_deadline_passes(self):
        manager.process_items(COURSE, KST)
        self.get_lectures.return_value = [lecture(due_at=PASSED_DEADLINE)]

        manager.process_items(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_is_not_announced_as_a_module_item_after_it_is_watched(self):
        manager.process_items(COURSE, KST)
        self.get_lectures.return_value = [lecture(completed=True)]

        manager.process_items(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_seen_while_open_is_not_announced_when_learningx_fails_later(self):
        manager.process_items(COURSE, KST)
        self.get_lectures.side_effect = LearningXError('LearningX 응답 오류: 500')

        manager.process_items(COURSE, KST)

        self.self_email.assert_not_called()
        self.add_todolist.assert_not_called()

    def test_lecture_first_seen_after_its_deadline_is_announced_as_a_module_item(self):
        self.get_lectures.return_value = [lecture(due_at=PASSED_DEADLINE)]

        manager.process_items(COURSE, KST)

        self.self_email.assert_called_once_with(*self.MODULE_ITEM_EMAIL)

    def test_lecture_without_deadline_is_announced_as_a_module_item(self):
        self.get_lectures.return_value = [lecture(due_at=None)]

        manager.process_items(COURSE, KST)

        self.self_email.assert_called_once_with(*self.MODULE_ITEM_EMAIL)
        self.add_todolist.assert_called_once_with(
            name='2026-2 응용전산물리 (001) ch06',
            description=LECTURE_URL,
            due_date='today',
            priority=3,
        )

    def test_lecture_never_seen_before_is_announced_as_a_module_item_when_learningx_fails(self):
        self.get_lectures.side_effect = LearningXError('LearningX 응답 오류: 500')

        manager.process_items(COURSE, KST)

        self.self_email.assert_called_once_with(*self.MODULE_ITEM_EMAIL)


class MainTest(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.enter(mock.patch.dict(os.environ, {
            'HEALTHCHECK_ETL_MANAGER': 'https://hc.example/abc',
        }))
        self.ping_test = self.patch(manager, 'ping_test')
        self.get_courses = self.patch(manager, 'get_courses', return_value=[COURSE])
        self.patch(manager, 'get_assignments', return_value=[])
        self.patch(manager, 'get_announcements', return_value=[])
        self.patch(manager, 'get_files', return_value=[])
        self.get_items = self.patch(manager, 'get_items', return_value=[])
        self.get_lectures = self.patch(manager, 'get_lectures', return_value=[lecture()])

    def test_run_registers_open_lectures_and_reports_success(self):
        manager.main()

        self.assertEqual(self.add_event.call_args.kwargs['title'], '2026-2 응용전산물리 (001) ch06')
        self.assertEqual(self.ping_test.call_args_list, [
            mock.call('https://hc.example/abc/start'),
            mock.call('https://hc.example/abc'),
        ])

    def test_learningx_failure_is_reported_as_a_failed_run(self):
        self.get_lectures.side_effect = LearningXError('LearningX 응답 오류: 500')

        manager.main()

        pinged_urls = [call.args[0] for call in self.ping_test.call_args_list]
        self.assertEqual(pinged_urls, ['https://hc.example/abc/start', 'https://hc.example/abc/fail'])
        failure_message = self.ping_test.call_args.args[1]
        self.assertIn('2026-2 응용전산물리 (001)', failure_message)
        self.assertIn('LearningX 응답 오류: 500', failure_message)

    def test_later_courses_are_still_processed_after_a_learningx_failure(self):
        self.get_courses.return_value = [COURSE, OTHER_COURSE]
        self.get_lectures.side_effect = self.lectures_or_error({
            COURSE.id: LearningXError('LearningX 응답 오류: 500'),
            OTHER_COURSE.id: [lecture(title='Lecture6-2')],
        })

        manager.main()

        self.assertEqual([call.kwargs['title'] for call in self.add_event.call_args_list],
                         ['2026-2 기본물리수학 (001) Lecture6-2'])
        failure_message = self.ping_test.call_args.args[1]
        self.assertIn('2026-2 응용전산물리 (001)', failure_message)
        self.assertNotIn('2026-2 기본물리수학 (001)', failure_message)

    @staticmethod
    def lectures_or_error(outcomes):
        def get_lectures(course):
            outcome = outcomes[course.id]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return get_lectures

    def test_other_items_are_still_announced_when_learningx_fails(self):
        self.get_lectures.side_effect = LearningXError('LearningX 응답 오류: 500')
        self.get_items.return_value = [lecture_module_item()]

        manager.main()

        self.self_email.assert_called_once_with(
            '[etl] 2026-2 응용전산물리 (001) ch06',
            f'2026-2 응용전산물리 (001) ch06\n\n타입: 줌/녹강\n\n링크: {LECTURE_URL}',
        )


if __name__ == '__main__':
    unittest.main()
