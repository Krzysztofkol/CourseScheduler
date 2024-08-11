import os
import json
from datetime import datetime
import logging
from flask import Flask, render_template, jsonify, request
import networkx as nx
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, template_folder='.')

SUBJECTS_FOLDER = 'subjects'
DATETIME_FORMAT = '%Y-%m-%d_%H:%M:%S'
SUBJECT_DURATION_DAYS = 4/3 # previously in {1, 1.5}
SUBJECT_DURATION_MINUTES = int(SUBJECT_DURATION_DAYS*1440)
COURSE_DURATION_DAYS = 2

def get_available_subjects():
    return [d for d in os.listdir(SUBJECTS_FOLDER) if os.path.isdir(os.path.join(SUBJECTS_FOLDER, d))]

def get_subject_path(subject):
    return os.path.join(SUBJECTS_FOLDER, subject)

def get_graph_file(subject):
    return os.path.join(get_subject_path(subject), 'graph.txt')

def get_progress_file(subject):
    return os.path.join(get_subject_path(subject), 'progress.json')

def clean_course_name(name):
    return name.replace(';', '').strip()

def parse_graph_file(subject):
    graph = nx.DiGraph()
    seen_nodes = set()
    try:
        with open(get_graph_file(subject), 'r') as file:
            for line in file:
                if '->' in line:
                    source, target = line.strip().split('->')
                    source = clean_course_name(source)
                    target = clean_course_name(target)
                    
                    if source not in seen_nodes:
                        graph.add_node(source)
                        seen_nodes.add(source)
                    if target not in seen_nodes:
                        graph.add_node(target)
                        seen_nodes.add(target)
                    
                    graph.add_edge(source, target)
        return graph
    except FileNotFoundError:
        app.logger.error(f"Graph file not found for subject: {subject}")
        return graph
    except Exception as e:
        app.logger.error(f"Error parsing graph file for subject {subject}: {str(e)}")
        return graph

def load_progress(subject):
    try:
        with open(get_progress_file(subject), 'r') as f:
            progress = json.load(f)
        
        cleaned_progress = {}
        for course, data in progress.items():
            cleaned_course = clean_course_name(course)
            if cleaned_course not in cleaned_progress:
                cleaned_progress[cleaned_course] = data
        
        logging.debug(f"Loaded progress for subject '{subject}': {cleaned_progress}")
        return cleaned_progress
    except FileNotFoundError:
        logging.warning(f"Progress file not found for subject: {subject}. Creating new progress file.")
        return {}
    except json.JSONDecodeError:
        logging.error(f"Error decoding progress file for subject: {subject}. Creating new progress file.")
        return {}

def save_progress(subject, progress):
    try:
        with open(get_progress_file(subject), 'w') as f:
            json.dump(progress, f)
    except Exception as e:
        app.logger.error(f"Error saving progress for subject {subject}: {str(e)}")

def get_available_courses(graph, progress):
    available_courses = []
    for node in graph.nodes():
        node_progress = progress.get(node, {})
        if isinstance(node_progress, bool):
            completed = node_progress
        else:
            completed = node_progress.get('completed', False)
        
        if not completed:
            predecessors = list(graph.predecessors(node))
            if all(progress.get(pred, {}).get('completed', False) if isinstance(progress.get(pred, {}), dict) else progress.get(pred, False) for pred in predecessors):
                available_courses.append(node)
    return available_courses

def calculate_progress(last_datetime, duration_days):
    now = datetime.now()
    time_passed = now - last_datetime
    total_minutes = SUBJECT_DURATION_MINUTES
    remaining_minutes = max(0, total_minutes - time_passed.total_seconds() / 60)
    progress = remaining_minutes / total_minutes
    return progress, int(remaining_minutes), int(total_minutes)

def calculate_subject_progress(subject):
    progress = load_progress(subject)
    logging.debug(f"Calculating progress for subject '{subject}'")
    logging.debug(f"Raw progress data: {progress}")

    if not progress:
        logging.debug(f"No progress data for subject '{subject}'. Returning no time remaining.")
        return 0, 0, SUBJECT_DURATION_MINUTES

    total_courses = len(progress)
    logging.debug(f"Total courses: {total_courses}")

    interacted_courses = [
        datetime.strptime(data['last_completed'], DATETIME_FORMAT)
        for data in progress.values()
        if isinstance(data, dict) and 'last_completed' in data
    ]

    if not interacted_courses:
        logging.debug(f"No interacted courses for subject '{subject}'. Returning no time rremaining.")
        return 0, 0, SUBJECT_DURATION_MINUTES

    most_recent_interaction = max(interacted_courses)
    progress_ratio, remaining_minutes, total_minutes = calculate_progress(most_recent_interaction, SUBJECT_DURATION_DAYS)

    logging.debug(f"Final progress calculation for subject '{subject}': {progress_ratio}, {remaining_minutes}, {total_minutes}")
    return progress_ratio, remaining_minutes, total_minutes

def format_time(minutes):
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}min"

class GraphFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith('graph.txt'):
            subject = os.path.basename(os.path.dirname(event.src_path))
            app.logger.info(f"Graph file for {subject} has been modified. Updating...")
            try:
                update_subject_graph(subject)
            except Exception as e:
                app.logger.error(f"Error updating subject graph for {subject}: {str(e)}")

def update_subject_graph(subject):
    graph = parse_graph_file(subject)
    progress = load_progress(subject)
    available_courses = get_available_courses(graph, progress)
    app.logger.info(f"Updated available courses for {subject}: {available_courses}")

observer = Observer()
event_handler = GraphFileHandler()
observer.schedule(event_handler, SUBJECTS_FOLDER, recursive=True)
observer.start()

@app.route('/')
def index():
    subjects = get_available_subjects()
    return render_template('frontend.html', subjects=subjects)

@app.route('/subjects')
def get_subjects():
    subjects = get_available_subjects()
    subject_data = []
    for subject in subjects:
        try:
            progress_ratio, current_minutes, total_minutes = calculate_subject_progress(subject)
            subject_data.append({
                'name': subject,
                'progress': progress_ratio,
                'courses': get_available_courses(parse_graph_file(subject), load_progress(subject)),
                'timeRemaining': format_time(current_minutes),
                'totalTime': format_time(total_minutes)
            })
        except Exception as e:
            app.logger.error(f"Error processing subject {subject}: {str(e)}")
    subject_data.sort(key=lambda x: x['progress'])
    return jsonify(subject_data)

@app.route('/courses/<subject>')
def get_courses(subject):
    if subject not in get_available_subjects():
        return jsonify({'error': 'Subject not found'}), 404
    
    try:
        progress = load_progress(subject)
        graph = parse_graph_file(subject)
        available_courses = get_available_courses(graph, progress)
        course_data = []
        for course in graph.nodes():
            course_progress = progress.get(course, {})
            if isinstance(course_progress, bool):
                last_completed = datetime.min
                completed = course_progress
            else:
                last_completed = datetime.strptime(course_progress.get('last_completed', '1970-01-01_00:00:00'), DATETIME_FORMAT)
                completed = course_progress.get('completed', False)
            
            course_progress_value, current_minutes, total_minutes = calculate_progress(last_completed, COURSE_DURATION_DAYS)
            course_data.append({
                'name': course,
                'progress': course_progress_value,
                'completed': completed,
                'available': course in available_courses,
                'timeRemaining': format_time(current_minutes),
                'totalTime': format_time(total_minutes),
                'last_completed': last_completed.strftime(DATETIME_FORMAT) if completed else None
            })
        
        # Sort courses: available first, then unavailable, then completed
        # Within each group, sort by progress (least to most) for non-completed courses
        # Completed courses are sorted by completion time (most recent last)
        course_data.sort(key=lambda x: (
            2 if x['completed'] else (0 if x['available'] else 1),
            x['progress'] if not x['completed'] else datetime.strptime(x['last_completed'], DATETIME_FORMAT) if x['last_completed'] else datetime.min
        ))
        
        return jsonify(course_data)
    except Exception as e:
        app.logger.error(f"Error getting courses for subject {subject}: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/update_course', methods=['POST'])
def update_course_route():
    data = request.json
    if not data or 'subject' not in data or 'course' not in data or 'action' not in data:
        return jsonify({'error': 'Invalid request data'}), 400
    
    subject = data['subject']
    course = data['course']
    action = data['action']
    
    try:
        result = update_course(subject, course, action)
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error updating course: {str(e)}")
        return jsonify({'error': 'Internal server error'}), 500
    
def update_course(subject, course, action):
    logging.debug(f"Updating course '{course}' in subject '{subject}' with action '{action}'")
    progress = load_progress(subject)
    if course not in progress:
        progress[course] = {'completed': False, 'last_completed': '1970-01-01_00:00:00'}
    elif isinstance(progress[course], bool):
        progress[course] = {'completed': progress[course], 'last_completed': '1970-01-01_00:00:00'}
    
    progress[course]['last_completed'] = datetime.now().strftime(DATETIME_FORMAT)
    if action == 'finished':
        progress[course]['completed'] = True
    
    logging.debug(f"Updated progress for course '{course}': {progress[course]}")
    save_progress(subject, progress)
    
    subject_progress, subject_remaining_minutes, subject_total_minutes = calculate_subject_progress(subject)
    course_progress, course_remaining_minutes, course_total_minutes = calculate_progress(
        datetime.strptime(progress[course]['last_completed'], DATETIME_FORMAT), COURSE_DURATION_DAYS)
    
    result = {
        'success': True,
        'subject_progress': subject_progress,
        'subject_time_remaining': format_time(subject_remaining_minutes),
        'subject_total_time': format_time(subject_total_minutes),
        'course_progress': course_progress,
        'course_time_remaining': format_time(course_remaining_minutes),
        'course_total_time': format_time(course_total_minutes),
        'course_completed': progress[course]['completed']
    }
    logging.debug(f"Update result: {result}")
    return result

@app.errorhandler(404)
def not_found_error(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    app.logger.error(f'Server Error: {str(error)}')
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    try:
        app.run(debug=True, port=7474)
    finally:
        observer.stop()
        observer.join()