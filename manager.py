from trash_etl import *
from true_email import true_email
from html2text import html2text
import datetime
from zoneinfo import ZoneInfo
import xxhash
import openai
from true_line import true_line
import os
from todoist_api_python.api import TodoistAPI
import sqlite3
from true_calendar import true_calendar
from dotenv import load_dotenv


load_dotenv()


def get_current_path():
    folder_path = '/python/etl_manager/'
    folder_exists = os.path.exists(folder_path)
    if folder_exists:
        return folder_path
    else:
        return ''


def add_todolist(name, description, due_date, priority):
    api_token = os.getenv('TODOIST_API_TOKEN')
    api = TodoistAPI(api_token)
    task = api.add_task(
        content=name,
        description=description,
        due_string=due_date,
        priority=priority,
        labels=['eTL']
    )
    print(f"작업 생성 성공: {task.content} (ID: {task.id})")


def get_db_path():
    DB_FILENAME = 'checked_items.db'
    return f"{get_current_path()}{DB_FILENAME}"


def init_db():
    db_path = get_db_path()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS checked_items (
                hash_value TEXT PRIMARY KEY,
                title TEXT,
                created_at TEXT
            )
        ''')
        conn.commit()


def update_checked_item_list(hash_value, title):
    init_db()
    
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db_path = get_db_path()
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT OR IGNORE INTO checked_items (hash_value, title, created_at)
                VALUES (?, ?, ?)
            ''', (hash_value, title, current_time))
            conn.commit()
        except sqlite3.Error as e:
            print(f"데이터베이스 에러 발생: {e}")
            raise


def is_checked(hash_value):
    init_db()
    db_path = get_db_path()
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM checked_items WHERE hash_value = ?', (hash_value,))
        result = cursor.fetchone()
        
    return result is not None


def get_checked_item_list():
    """
    저장된 모든 아이템 리스트를 반환합니다 (디버깅/확인용).
    """
    init_db()
    db_path = get_db_path()
    
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT hash_value, title, created_at FROM checked_items')
        rows = cursor.fetchall()
        
    return rows


def convert_item_type_into_korean(item_):
    type_dict = {
        'File': '파일',
        'Assignment': '과제',
        'ExternalTool': '줌/녹강',
        'Quiz': '퀴즈'
                 }
    try:
        korean_type = type_dict[item_.type]
        return korean_type
    except KeyError:
        return item_.type


def summarize_announcement(announcement_body):
    summarize_api_key = os.getenv('OPENAI_API_KEY_ETL')
    system_message = '''당신은 주어진 공지사항을 3문장 이하로 요약하는 전문가입니다. 각 문장을 번호를 붙여서 요약하십시오.
요약은 한국어로 작성해야 합니다. 단, 전문용어는 영어로 작성할 수 있습니다.

# Steps

1. 공지사항의 핵심 내용을 파악하십시오.
2. 불필요한 세부사항을 제거하고 중요한 정보만을 남기십시오.
3. 요약된 정보를 3문장 이하로 구성하십시오.
4. 각 문장에 번호를 붙여 나열하십시오.

# Output Format

- 최대 3문장으로 요약된 내용
- 각 문장의 앞에 넘버링 (1, 2, 3) 추가
- 한국어로 작성

# Examples

**Input**: 
"[공지사항 내용]"

**Output**: 
1. [첫 번째 문장]
2. [두 번째 문장]
3. [세 번째 문장]

# Notes

- 문장 수가 3 이하로 적절히 필수적인 정보를 함축해야 합니다.
- 모든 문장에 넘버링이 있어야 합니다.
- 전문 용어는 영어를 사용할 수 있으니 적절히 활용하세요.
    '''
    message = f"공지사항: {announcement_body}\n요약:"
    try:
        client = openai.OpenAI(api_key=summarize_api_key)
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": message}
            ],
            max_tokens=500,
            temperature=0.3,
            model="gpt-4o-mini",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"요약 중 에러 발생: {e}")
        return f"요약 중 에러 발생: {e}"


def get_xxh3_128(string):
    """
    문자열을 입력받아 XXH3 128비트 해시값(Hex)을 반환하는 함수
    """
    byte_string = string.encode('utf-8')
    hash_object = xxhash.xxh3_128(byte_string)
    hash_value = hash_object.hexdigest()

    return hash_value


if __name__ == '__main__':
    kst = ZoneInfo('Asia/Seoul')
    courses = get_courses()
    for course in courses:
        try:
            if course.access_restricted_by_date:
                continue
        except AttributeError:
            pass

        # 과제
        assignments = get_assignments(course)
        for assignment in assignments:
            text = f'{course.name} {assignment.name}'
            try:
                if 'Z' in assignment.due_at:
                    due_date = datetime.datetime.strptime(assignment.due_at, '%Y-%m-%dT%H:%M:%SZ')
                    due_date = due_date.replace(tzinfo=datetime.timezone.utc).astimezone(kst)
                else:
                    due_date = datetime.datetime.strptime(assignment.due_at, '%Y-%m-%dT%H:%M:%S')
                    due_date = due_date.replace(tzinfo=kst)
                if due_date < datetime.datetime.now(kst):
                    continue
            except TypeError:
                due_date = None
            title = f'[etl] {text}'
            body = f'{text}\n'
            body += f'\n타입: 과제\n'
            body += f'\n마감: {due_date}\n'
            body += f'\n링크: {assignment.html_url}'
            body_hash = get_xxh3_128(body)
            print(text)
            if is_checked(body_hash):
                print('pass')
                continue
            true_email.self_email(title, body)
            start_time = due_date if due_date is not None else datetime.datetime.now(kst)
            true_calendar.add_event(
                title=text,
                description=f'링크: {assignment.html_url}',
                calendar_name='과제, 강의',
                start_time=start_time,
                duration=datetime.timedelta(hours=0),
                notify_times=[datetime.timedelta(days=-2), datetime.timedelta(days=-1)]
            )
            add_todolist(
                name=f'{text} 일정 검토',
                description=f'{start_time}에 시작하는 {text} 일정을 캘린더에 추가했습니다.\n링크: {assignment.html_url}',
                due_date='today',
                priority=3
            )
            update_checked_item_list(body_hash, text)

        # 공지
        announcements = get_announcements(course)
        for announcement in announcements:
            body_hash = f'{announcement.title} separate {html2text(announcement.message)}'
            body_hash = get_xxh3_128(body_hash)
            print(announcement.title)
            if is_checked(body_hash):
                print('pass')
                continue
            text = f'{course.name} {announcement.title}'
            title = f'[etl] {text}'
            body = f'{text}\n'
            body += f'\n타입: 공지\n'
            summary = summarize_announcement(html2text(announcement.message))
            body += f'\n요약\n{summary}\n'
            body += f'\n본문\n{html2text(announcement.message)}\n'
            body += f'\n링크: {announcement.html_url}'
            true_email.self_email(title, body)
            short_body = f'{text}\n\n{summary}'
            true_line.send_text(short_body)
            add_todolist(
                name=text,
                description=f'{summary}\n\n링크: {announcement.html_url}',
                due_date='today',
                priority=3
            )
            update_checked_item_list(body_hash, text)

        # 파일
        files = get_files(course)
        for file in files:
            if file.locked_for_user:
                continue
            text = f'{course.name} {file.display_name}'
            title = f'[etl] {text}'
            body = f'{text}\n\n타입: 파일\n\n파일명: {file.filename}\n\n링크: {file.url}'
            body_hash = get_xxh3_128(body)
            print(text)
            if is_checked(body_hash):
                print('pass')
                continue
            true_email.self_email(title, body)
            add_todolist(
                name=text,
                description=f'{file.url}',
                due_date='this Saturday',
                priority=3
            )
            update_checked_item_list(body_hash, text)

        # 기타
        items = get_items(course)
        for item in items:
            if not is_available(item):
                continue
            text = f'{course.name} {item.title}'
            item_type = convert_item_type_into_korean(item)
            if item_type in ['과제', '파일']:
                continue
            title = f'[etl] {text}'
            try:
                body = f'{text}\n\n타입: {item_type}\n\n링크: {item.html_url}'
            except AttributeError:
                body = f'{text}\n\n타입: {item_type}'
            body_hash = get_xxh3_128(body)
            print(text)
            if is_checked(body_hash):
                print('pass')
                continue
            true_email.self_email(title, body)
            add_todolist(
                name=text,
                description=f'{item.html_url}' if hasattr(item, 'html_url') else '',
                due_date='today',
                priority=3
            )
            update_checked_item_list(body_hash, text)

