from pathlib import Path
import secrets
import sqlite3
import string

from flask import Flask, jsonify, request, send_from_directory
from database import get_connection, init_db

WEB_DIR = Path(__file__).resolve().parent.parent / 'web'
app = Flask(__name__)
init_db()


def body():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}


def error(message, status=400):
    return jsonify({'error': message}), status


def validate_question(data):
    question = data.get('question')
    options = data.get('options')
    answer = data.get('answer')
    if not isinstance(question, str) or not question.strip():
        return None, '문제를 입력하세요.'
    if not isinstance(options, list) or len(options) != 4 or any(
        not isinstance(x, str) or not x.strip() for x in options
    ):
        return None, '보기 4개를 모두 입력하세요.'
    if type(answer) is not int or answer not in (1, 2, 3, 4):
        return None, '정답은 1~4 중 하나여야 합니다.'
    return (question.strip(), *[x.strip() for x in options], answer), None


def room_for(conn, code):
    return conn.execute('SELECT * FROM rooms WHERE code=?', (code,)).fetchone()


def questions_for(conn, quiz_id):
    return conn.execute('SELECT * FROM questions WHERE quiz_id=? ORDER BY id', (quiz_id,)).fetchall()


def host_ok(room, data):
    return room and isinstance(data.get('host_token'), str) and secrets.compare_digest(
        data['host_token'], room['host_token']
    )


@app.get('/')
def home():
    return send_from_directory(WEB_DIR, 'index.html')


@app.get('/api/health')
def health():
    return jsonify({'status': 'ok'})


@app.get('/api/quizzes')
def list_quizzes():
    with get_connection() as conn:
        rows = conn.execute('SELECT id, title FROM quizzes ORDER BY id DESC').fetchall()
        return jsonify([dict(row) for row in rows])


@app.post('/api/quizzes')
def create_quiz():
    title = body().get('title')
    if not isinstance(title, str) or not title.strip():
        return error('퀴즈 제목을 입력하세요.')
    with get_connection() as conn:
        cursor = conn.execute('INSERT INTO quizzes(title) VALUES (?)', (title.strip(),))
        return jsonify({'id': cursor.lastrowid, 'title': title.strip()}), 201


@app.put('/api/quizzes/<int:quiz_id>')
def rename_quiz(quiz_id):
    title = body().get('title')
    if not isinstance(title, str) or not title.strip():
        return error('퀴즈 제목을 입력하세요.')
    with get_connection() as conn:
        cursor = conn.execute('UPDATE quizzes SET title=? WHERE id=?', (title.strip(), quiz_id))
        if not cursor.rowcount:
            return error('퀴즈가 없습니다.', 404)
    return jsonify({'message': '퀴즈 제목 수정 완료'})


@app.delete('/api/quizzes/<int:quiz_id>')
def delete_quiz(quiz_id):
    with get_connection() as conn:
        if conn.execute('SELECT 1 FROM rooms WHERE quiz_id=?', (quiz_id,)).fetchone():
            return error('생성된 퀴즈방이 있어 삭제할 수 없습니다.', 409)
        cursor = conn.execute('DELETE FROM quizzes WHERE id=?', (quiz_id,))
        if not cursor.rowcount:
            return error('퀴즈가 없습니다.', 404)
    return jsonify({'message': '퀴즈 삭제 완료'})


@app.get('/api/quizzes/<int:quiz_id>/questions')
def list_questions(quiz_id):
    with get_connection() as conn:
        if not conn.execute('SELECT 1 FROM quizzes WHERE id=?', (quiz_id,)).fetchone():
            return error('퀴즈가 없습니다.', 404)
        return jsonify([dict(row) for row in questions_for(conn, quiz_id)])


@app.post('/api/quizzes/<int:quiz_id>/questions')
def create_question(quiz_id):
    values, problem = validate_question(body())
    if problem:
        return error(problem)
    with get_connection() as conn:
        if not conn.execute('SELECT 1 FROM quizzes WHERE id=?', (quiz_id,)).fetchone():
            return error('퀴즈가 없습니다.', 404)
        if conn.execute('SELECT 1 FROM rooms WHERE quiz_id=? AND state != ?', (quiz_id, 'finished')).fetchone():
            return error('대기 또는 진행 중인 퀴즈방이 있어 수정할 수 없습니다.', 409)
        cursor = conn.execute('''INSERT INTO questions
            (quiz_id, question, option1, option2, option3, option4, answer)
            VALUES (?, ?, ?, ?, ?, ?, ?)''', (quiz_id, *values))
    return jsonify({'id': cursor.lastrowid, 'message': '문제 등록 완료'}), 201


@app.put('/api/questions/<int:question_id>')
def update_question(question_id):
    values, problem = validate_question(body())
    if problem:
        return error(problem)
    with get_connection() as conn:
        row = conn.execute('SELECT quiz_id FROM questions WHERE id=?', (question_id,)).fetchone()
        if not row:
            return error('문제가 없습니다.', 404)
        if conn.execute('SELECT 1 FROM rooms WHERE quiz_id=? AND state != ?', (row['quiz_id'], 'finished')).fetchone():
            return error('대기 또는 진행 중인 퀴즈방이 있어 수정할 수 없습니다.', 409)
        conn.execute('''UPDATE questions SET question=?, option1=?, option2=?,
            option3=?, option4=?, answer=? WHERE id=?''', (*values, question_id))
    return jsonify({'message': '문제 수정 완료'})


@app.delete('/api/questions/<int:question_id>')
def delete_question(question_id):
    with get_connection() as conn:
        row = conn.execute('SELECT quiz_id FROM questions WHERE id=?', (question_id,)).fetchone()
        if not row:
            return error('문제가 없습니다.', 404)
        if conn.execute('SELECT 1 FROM rooms WHERE quiz_id=?', (row['quiz_id'],)).fetchone():
            return error('퀴즈방에 사용된 문제는 삭제할 수 없습니다.', 409)
        conn.execute('DELETE FROM questions WHERE id=?', (question_id,))
    return jsonify({'message': '문제 삭제 완료'})


@app.post('/api/rooms')
def create_room():
    quiz_id = body().get('quiz_id')
    if type(quiz_id) is not int:
        return error('퀴즈를 선택하세요.')
    alphabet = string.ascii_uppercase + string.digits
    with get_connection() as conn:
        if not conn.execute('SELECT 1 FROM quizzes WHERE id=?', (quiz_id,)).fetchone():
            return error('퀴즈가 없습니다.', 404)
        if not questions_for(conn, quiz_id):
            return error('문제를 먼저 등록하세요.')
        for _ in range(20):
            code = ''.join(secrets.choice(alphabet) for _ in range(6))
            token = secrets.token_urlsafe(32)
            try:
                conn.execute('INSERT INTO rooms(code, quiz_id, host_token) VALUES (?, ?, ?)', (code, quiz_id, token))
                return jsonify({'code': code, 'host_token': token}), 201
            except sqlite3.IntegrityError:
                continue
    return error('방 코드 생성에 실패했습니다.', 500)


@app.post('/api/rooms/<code>/join')
def join_room(code):
    code = code.upper()
    nickname = body().get('nickname')
    if not isinstance(nickname, str) or not 1 <= len(nickname.strip()) <= 20:
        return error('닉네임은 1~20자로 입력하세요.')
    nickname = nickname.strip()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if room['state'] != 'waiting':
            return error('이미 시작하거나 종료된 방입니다.', 409)
        token = secrets.token_urlsafe(32)
        try:
            conn.execute('INSERT INTO participants(room_code, nickname, token) VALUES (?, ?, ?)', (code, nickname, token))
        except sqlite3.IntegrityError:
            return error('이미 사용 중인 닉네임입니다.', 409)
    return jsonify({'code': code, 'nickname': nickname, 'token': token}), 201


@app.post('/api/rooms/<code>/leave')
def leave_room(code):
    code = code.upper()
    token = body().get('token')
    if not isinstance(token, str) or not token:
        return error('참가자 인증이 필요합니다.', 403)
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if room['state'] == 'playing':
            return error('진행 중에는 나갈 수 없습니다.', 409)
        cursor = conn.execute('DELETE FROM participants WHERE room_code=? AND token=?', (code, token))
        if not cursor.rowcount:
            return error('참가 기록이 없습니다.', 404)
    return jsonify({'message': '퇴장 완료'})


@app.get('/api/rooms/<code>')
def room_info(code):
    code = code.upper()
    with get_connection() as conn:
        room = conn.execute('''SELECT r.*, q.title FROM rooms r
            JOIN quizzes q ON q.id=r.quiz_id WHERE r.code=?''', (code,)).fetchone()
        if not room:
            return error('방이 없습니다.', 404)
        players = conn.execute('SELECT nickname FROM participants WHERE room_code=? ORDER BY id', (code,)).fetchall()
    return jsonify({'code': code, 'quiz_title': room['title'], 'state': room['state'],
                    'participants': [p['nickname'] for p in players]})


@app.post('/api/rooms/<code>/start')
def start_game(code):
    code = code.upper()
    data = body()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if not host_ok(room, data):
            return error('방장 권한이 없습니다.', 403)
        if room['state'] != 'waiting':
            return error('이미 시작하거나 종료된 방입니다.', 409)
        if not conn.execute('SELECT 1 FROM participants WHERE room_code=?', (code,)).fetchone():
            return error('참가자가 한 명 이상 필요합니다.')
        conn.execute("UPDATE rooms SET state='playing', question_index=0 WHERE code=?", (code,))
    return jsonify({'message': '퀴즈 시작'})


@app.get('/api/rooms/<code>/game')
def game_status(code):
    code = code.upper()
    with get_connection() as conn:
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        questions = questions_for(conn, room['quiz_id'])
        q = questions[room['question_index']] if room['state'] == 'playing' and room['question_index'] < len(questions) else None
        current = None if q is None else {
            'id': q['id'], 'number': room['question_index'] + 1, 'total': len(questions),
            'question': q['question'], 'options': [q[f'option{i}'] for i in range(1, 5)]
        }
        players = conn.execute('''SELECT p.nickname, p.id,
            COALESCE(SUM(s.is_correct * 10), 0) AS score
            FROM participants p LEFT JOIN submissions s
            ON s.participant_id=p.id AND s.room_code=p.room_code
            WHERE p.room_code=? GROUP BY p.id, p.nickname
            ORDER BY score DESC, p.id ASC''', (code,)).fetchall()
        answered = 0 if q is None else conn.execute('''SELECT COUNT(*) FROM submissions
            WHERE room_code=? AND question_id=?''', (code, q['id'])).fetchone()[0]
    return jsonify({'state': room['state'], 'question': current, 'answered': answered,
                    'player_count': len(players), 'ranking': [
                        {'nickname': p['nickname'], 'score': p['score']} for p in players
                    ]})


@app.post('/api/rooms/<code>/answer')
def submit_answer(code):
    code = code.upper()
    data = body()
    selected = data.get('answer')
    token = data.get('token')
    if type(selected) is not int or selected not in (1, 2, 3, 4):
        return error('1~4번 중 선택하세요.')
    if not isinstance(token, str) or not token:
        return error('참가자 인증이 필요합니다.', 403)
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if room['state'] != 'playing':
            return error('진행 중인 퀴즈가 아닙니다.', 409)
        player = conn.execute('SELECT id FROM participants WHERE room_code=? AND token=?', (code, token)).fetchone()
        if not player:
            return error('참가자 인증 실패', 403)
        questions = questions_for(conn, room['quiz_id'])
        if room['question_index'] >= len(questions):
            return error('문제가 종료되었습니다.', 409)
        q = questions[room['question_index']]
        if conn.execute('''SELECT 1 FROM submissions WHERE room_code=?
            AND question_id=? AND participant_id=?''', (code, q['id'], player['id'])).fetchone():
            return error('이미 제출한 문제입니다.', 409)
        correct = int(selected == q['answer'])
        conn.execute('''INSERT INTO submissions(room_code, question_id, participant_id,
            selected_answer, is_correct) VALUES (?, ?, ?, ?, ?)''',
            (code, q['id'], player['id'], selected, correct))
    return jsonify({'message': '정답입니다!' if correct else '오답입니다.',
                    'correct': bool(correct), 'score_earned': 10 if correct else 0})


@app.post('/api/rooms/<code>/next')
def next_question(code):
    code = code.upper()
    data = body()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if not host_ok(room, data):
            return error('방장 권한이 없습니다.', 403)
        if room['state'] != 'playing':
            return error('진행 중인 퀴즈가 아닙니다.', 409)
        count = len(questions_for(conn, room['quiz_id']))
        index = room['question_index'] + 1
        if index >= count:
            conn.execute("UPDATE rooms SET state='finished' WHERE code=?", (code,))
            return jsonify({'message': '마지막 문제 종료. 최종 순위를 확인하세요.'})
        conn.execute('UPDATE rooms SET question_index=? WHERE code=?', (index, code))
    return jsonify({'message': '다음 문제로 이동'})


@app.post('/api/rooms/<code>/finish')
def finish_game(code):
    code = code.upper()
    data = body()
    with get_connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        room = room_for(conn, code)
        if not room:
            return error('방이 없습니다.', 404)
        if not host_ok(room, data):
            return error('방장 권한이 없습니다.', 403)
        if room['state'] == 'finished':
            return error('이미 종료된 방입니다.', 409)
        conn.execute("UPDATE rooms SET state='finished' WHERE code=?", (code,))
    return jsonify({'message': '퀴즈 종료'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
