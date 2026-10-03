from canvasapi import Canvas
import datetime
import os
import time
from html.parser import HTMLParser

import requests
from dotenv import load_dotenv


load_dotenv()

LECTURE_TOOL_PATH = '/learningx/lti/lecture_attendance/course_menu'
REQUEST_TIMEOUT = 20

# 실행(프로세스) 한 번 동안만 유지되는 LearningX 조회 결과
_learningx_token = None
_lectures_cache = {}
_lecture_failures = {}


class LearningXError(Exception):
    """강의영상 도구(LearningX)에서 정보를 가져오지 못했을 때 발생한다."""


def get_canvas():
    api_url = os.getenv('CANVAS_API_URL')
    api_key = os.getenv('CANVAS_API_KEY')
    canvas = Canvas(api_url, api_key)
    return canvas


def get_courses():
    canvas = get_canvas()
    courses_ = canvas.get_courses()
    return courses_


def get_items(course_):
    items_list = []
    modules = course_.get_modules()
    for module in modules:
        items_ = module.get_module_items(include='content_details')
        for item_ in items_:
            items_list.append(item_)
    return items_list


def is_available(item_):
    content_details = item_.content_details
    not_available = content_details['locked_for_user']
    not_available = bool(not_available)
    return not not_available


def get_assignments(course_):
    assignments_ = course_.get_assignments()
    return assignments_


def get_announcements(course_):
    canvas = get_canvas()
    current_date = datetime.datetime.today()
    one_year_ago = current_date - datetime.timedelta(days=365)
    next_week = current_date + datetime.timedelta(days=7)
    start_date = one_year_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_date = next_week.strftime("%Y-%m-%dT%H:%M:%SZ")
    announcements_ = canvas.get_announcements([course_.id],
                                              start_date=start_date, end_date=end_date)
    return announcements_


def get_files(course_):
    files_ = course_.get_files()
    return files_


class ToolFormParser(HTMLParser):
    """Canvas의 도구 실행 페이지에서 LTI 자동 제출 폼(tool_form)의 주소와 입력값을 읽는다."""

    def __init__(self):
        super().__init__()
        self.action = None
        self.fields = {}
        self.in_tool_form = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.in_tool_form = attrs.get('id') == 'tool_form'
            if self.in_tool_form:
                self.action = attrs.get('action')
        elif tag == 'input' and self.in_tool_form and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value') or ''

    def handle_endtag(self, tag):
        if tag == 'form':
            self.in_tool_form = False


def get_base_url():
    return os.getenv('CANVAS_API_URL').rstrip('/')


def issue_learningx_token(course_id):
    """
    Canvas API 키로 강의영상 도구(LearningX)를 실행해 LearningX API 토큰을 발급받는다.
    토큰은 강좌와 무관하게 계정 단위로 발급된다.
    네트워크 오류(requests.RequestException)는 호출한 쪽에서 재시도하도록 그대로 전파한다.
    """
    base_url = get_base_url()
    response = requests.get(
        f'{base_url}/api/v1/courses/{course_id}/external_tools/sessionless_launch',
        headers={'Authorization': f'Bearer {os.getenv("CANVAS_API_KEY")}'},
        params={'url': base_url + LECTURE_TOOL_PATH},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        raise LearningXError(f'강의영상 도구 실행 주소 요청 실패: {response.status_code}')
    try:
        launch_url = response.json()['url']
    except (KeyError, TypeError) as e:
        raise LearningXError('강의영상 도구 실행 주소 응답 형식이 예상과 다릅니다.') from e
    session = requests.Session()
    launch_page = session.get(launch_url, timeout=REQUEST_TIMEOUT)
    form = ToolFormParser()
    form.feed(launch_page.text)
    if form.action is None:
        raise LearningXError('강의영상 도구 실행 폼을 찾을 수 없습니다.')
    session.post(form.action, data=form.fields, timeout=REQUEST_TIMEOUT)
    # 같은 이름의 쿠키가 여러 경로로 내려와도 읽을 수 있게 직접 찾는다.
    token = next((cookie.value for cookie in session.cookies if cookie.name == 'xn_api_token'), None)
    if not token:
        raise LearningXError('LearningX 토큰이 발급되지 않았습니다.')
    return token


def get_lecture_modules(course_id):
    global _learningx_token
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            if _learningx_token is None:
                _learningx_token = issue_learningx_token(course_id)
            response = requests.get(
                f'{get_base_url()}/learningx/api/v1/courses/{course_id}/modules',
                headers={'Authorization': f'Bearer {_learningx_token}'},
                params={'include_detail': 'true'},
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code == 200:
                return response.json()
            # 5xx(서버 일시 오류)와 네트워크 오류만 재시도하고, 4xx는 즉시 전파한다.
            if response.status_code < 500:
                raise LearningXError(f'LearningX 응답 오류: {response.status_code}')
            error = f'LearningX 응답 오류: {response.status_code}'
        except requests.RequestException as e:
            # 예외 메시지에는 일회용 실행 주소(verifier)가 들어갈 수 있어 예외 종류만 남긴다.
            error = f'LearningX 요청 실패: {type(e).__name__}'
        print(f'{error} (시도 {attempt}/{max_retries})')
        if attempt < max_retries:
            time.sleep(attempt)
    raise LearningXError(error)


def parse_lectures(modules, course_id):
    lectures = []
    try:
        for module in modules:
            for item_ in module['module_items']:
                if item_['content_type'] != 'attendance_item':
                    continue
                due_at = item_['content_data']['due_at']
                if due_at is not None:
                    due_at = datetime.datetime.strptime(due_at, '%Y-%m-%dT%H:%M:%SZ')
                    due_at = due_at.replace(tzinfo=datetime.timezone.utc)
                # completed, attendance_status는 출석 대상이 아니거나 시청 전이면 응답에서 빠진다.
                completed = bool(item_.get('completed')) or item_.get('attendance_status') == 'attendance'
                lectures.append({
                    'module_item_id': item_['module_item_id'],
                    'title': item_['title'],
                    'due_at': due_at,
                    'completed': completed,
                    'html_url': f'{get_base_url()}/courses/{course_id}/modules/items/{item_["module_item_id"]}',
                })
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise LearningXError(f'LearningX 응답 형식이 예상과 다릅니다: {e!r}') from e
    return lectures


def get_lectures(course_):
    """
    강의영상 목록을 반환합니다. due_at은 학습인정기간(출석 인정) 마감(UTC)이며 없으면 None입니다.
    아직 열리지 않은 강의영상도 포함합니다.
    조회에 실패한 강좌는 같은 실행에서 다시 요청하지 않고 같은 LearningXError를 발생시킵니다.
    """
    if course_.id in _lecture_failures:
        raise _lecture_failures[course_.id]
    if course_.id not in _lectures_cache:
        try:
            _lectures_cache[course_.id] = parse_lectures(get_lecture_modules(course_.id), course_.id)
        except LearningXError as e:
            _lecture_failures[course_.id] = e
            raise
    return _lectures_cache[course_.id]


if __name__ == '__main__':
    courses = get_courses()
    for course in courses:
        if '고전' not in course.name:
            continue
        # print(course.name)
        # print(list(get_announcements(course)))
        for ann in get_announcements(course):
            print(ann.title)