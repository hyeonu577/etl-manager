from canvasapi import Canvas
import datetime
import os
from dotenv import load_dotenv


load_dotenv()



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


if __name__ == '__main__':
    courses = get_courses()
    for course in courses:
        if '고전' not in course.name:
            continue
        # print(course.name)
        # print(list(get_announcements(course)))
        for ann in get_announcements(course):
            print(ann.title)