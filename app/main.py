"""Quiz ON — classroom quiz demo. Run behind nginx using Gunicorn."""
import os
import json
import secrets
import sqlite3
import string
from flask import Flask, jsonify, request

app = Flask(__name__)
app.json.ensure_ascii = False
DB = os.environ.get('DATABASE_PATH', '/data/quiz.db')
QUESTIONS = [
    ('HTTP에서 데이터를 조회할 때 주로 사용하는 메서드는?', ['GET', 'POST', 'PUT', 'DELETE'], 0),
    ('여러 Docker 컨테이너를 한 설정으로 실행하는 도구는?', ['Git', 'Docker Compose', 'SSH', 'npm'], 1),
    ('웹 브라우저에서 실행되는 프로그래밍 언어는?', ['SQL', 'Bash', 'JavaScript', 'YAML'], 2),
    ('관계형 데이터베이스에서 조회할 때 사용하는 SQL 명령은?', ['INSERT', 'UPDATE', 'SELECT', 'DROP'], 2),
    ('AWS에서 가상 서버를 제공하는 서비스는?', ['EC2', 'S3', 'Route 53', 'CloudFront'], 0),
]

def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB)), exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db():
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS quiz_sets (
            id TEXT PRIMARY KEY, edit_token TEXT NOT NULL, title TEXT NOT NULL,
            questions_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id TEXT PRIMARY KEY, host_token TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'waiting',
            question_index INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY, room_id TEXT NOT NULL, name TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(room_id) REFERENCES rooms(id), UNIQUE(room_id,name)
        );
        CREATE TABLE IF NOT EXISTS answers (
            room_id TEXT NOT NULL, player_id TEXT NOT NULL,
            question_index INTEGER NOT NULL, choice INTEGER NOT NULL,
            PRIMARY KEY(room_id,player_id,question_index),
            FOREIGN KEY(player_id) REFERENCES players(id)
        );
        ''')
        columns = [row['name'] for row in db.execute('PRAGMA table_info(rooms)').fetchall()]
        if 'quiz_set_id' not in columns:
            db.execute('ALTER TABLE rooms ADD COLUMN quiz_set_id TEXT')

init_db()

def error(message, code=400):
    return jsonify(error=message), code

def data():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else {}

def room(db, code):
    return db.execute('SELECT * FROM rooms WHERE id=?', (code,)).fetchone()

def is_host(payload, r):
    return secrets.compare_digest(str(payload.get('host_token', '')), r['host_token'])

def validate_questions(value):
    if not isinstance(value, list) or not 2 <= len(value) <= 10:
        raise ValueError('문제는 2~10개를 등록해 주세요.')
    normalized = []
    for idx, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f'{idx}번 문제 형식이 올바르지 않습니다.')
        title = item.get('text')
        choices = item.get('choices')
        answer = item.get('answer')
        if not isinstance(title, str) or not 5 <= len(title.strip()) <= 200:
            raise ValueError(f'{idx}번 문제는 5~200자로 입력해 주세요.')
        if not isinstance(choices, list) or len(choices) != 4 or any(not isinstance(c, str) or not 1 <= len(c.strip()) <= 100 for c in choices):
            raise ValueError(f'{idx}번 선택지는 4개 모두 입력해 주세요.')
        if len({c.strip().casefold() for c in choices}) != 4:
            raise ValueError(f'{idx}번 선택지는 서로 달라야 합니다.')
        if type(answer) is not int or not 0 <= answer < 4:
            raise ValueError(f'{idx}번 정답을 1~4번 중 선택해 주세요.')
        normalized.append({'text': title.strip(), 'choices': [c.strip() for c in choices], 'answer': answer})
    return normalized

def question_bank(db, r):
    if r['quiz_set_id'] is None:
        return [{'text': t, 'choices': c, 'answer': a} for t, c, a in QUESTIONS]
    row = db.execute('SELECT questions_json FROM quiz_sets WHERE id=?', (r['quiz_set_id'],)).fetchone()
    return json.loads(row['questions_json']) if row else []

@app.get('/api/quiz-sets')
def sets_list():
    with connect() as db:
        rows = db.execute('SELECT id, title, questions_json FROM quiz_sets ORDER BY rowid DESC').fetchall()
        return {'sets': [{'id':r['id'], 'title':r['title'], 'count':len(json.loads(r['questions_json']))} for r in rows]}

@app.get('/api/quiz-sets/<sid>')
def sets_get(sid):
    with connect() as db:
        r = db.execute('SELECT id,title,questions_json FROM quiz_sets WHERE id=?',(sid,)).fetchone()
        if not r: return error('퀴즈 세트를 찾을 수 없습니다.',404)
        # Answer keys are only available in edit mode with the owner token.
        token = request.headers.get('X-Edit-Token','')
        owner = db.execute('SELECT edit_token FROM quiz_sets WHERE id=?',(sid,)).fetchone()
        questions = json.loads(r['questions_json'])
        if not secrets.compare_digest(token, owner['edit_token']):
            questions = [{'text':q['text'],'choices':q['choices']} for q in questions]
        return {'id':sid, 'title':r['title'], 'questions':questions, 'can_edit':secrets.compare_digest(token,owner['edit_token'])}

@app.post('/api/quiz-sets')
def sets_create():
    d=data(); title=d.get('title');
    if not isinstance(title,str) or not 2 <= len(title.strip()) <= 60:
        return error('퀴즈 제목은 2~60자로 입력해 주세요.')
    try: questions=validate_questions(d.get('questions'))
    except ValueError as e: return error(str(e))
    sid=secrets.token_hex(5).upper(); token=secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('INSERT INTO quiz_sets VALUES (?,?,?,?)',(sid,token,title.strip(),json.dumps(questions,ensure_ascii=False)))
    return {'id':sid,'edit_token':token,'title':title.strip()},201

@app.put('/api/quiz-sets/<sid>')
def sets_update(sid):
    d=data();title=d.get('title')
    if not isinstance(title,str) or not 2 <= len(title.strip()) <= 60:
        return error('퀴즈 제목은 2~60자로 입력해 주세요.')
    try: questions=validate_questions(d.get('questions'))
    except ValueError as e: return error(str(e))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute('SELECT edit_token FROM quiz_sets WHERE id=?',(sid,)).fetchone()
        if not r:return error('퀴즈 세트를 찾을 수 없습니다.',404)
        if not secrets.compare_digest(str(d.get('edit_token','')),r['edit_token']):return error('수정 권한이 없습니다.',403)
        active=db.execute("SELECT COUNT(*) FROM rooms WHERE quiz_set_id=? AND state!='finished'",(sid,)).fetchone()[0]
        if active:return error('사용 중인 퀴즈는 수정할 수 없습니다. 퀴즈 종료 후 수정해 주세요.',409)
        db.execute('UPDATE quiz_sets SET title=?,questions_json=? WHERE id=?',(title.strip(),json.dumps(questions,ensure_ascii=False),sid))
    return {'message':'퀴즈가 수정되었습니다.'}

@app.delete('/api/quiz-sets/<sid>')
def sets_delete(sid):
    d=data()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r=db.execute('SELECT edit_token FROM quiz_sets WHERE id=?',(sid,)).fetchone()
        if not r:return error('퀴즈 세트를 찾을 수 없습니다.',404)
        if not secrets.compare_digest(str(d.get('edit_token','')),r['edit_token']):return error('삭제 권한이 없습니다.',403)
        # Prevent deleting a set currently in use; protects an active game.
        count=db.execute("SELECT COUNT(*) FROM rooms WHERE quiz_set_id=? AND state!='finished'",(sid,)).fetchone()[0]
        if count:return error('진행 또는 대기 중인 방에서 사용 중인 퀴즈는 삭제할 수 없습니다.',409)
        db.execute('DELETE FROM quiz_sets WHERE id=?',(sid,))
    return {'message':'퀴즈가 삭제되었습니다.'}

def list_players(db, code):
    rows = db.execute('SELECT name, score FROM players WHERE room_id=? ORDER BY score DESC,name COLLATE NOCASE', (code,)).fetchall()
    return [dict(x) for x in rows]

@app.get('/api/health')
def health():
    try:
        with connect() as db:
            db.execute('SELECT 1')
        return {'status': 'ok'}
    except sqlite3.Error:
        return error('데이터베이스 연결 오류', 503)

@app.post('/api/rooms')
def create():
    payload=data()
    set_id=payload.get('quiz_set_id') or None
    if set_id is not None:
        if not isinstance(set_id, str):return error('퀴즈 선택 정보가 올바르지 않습니다.')
        with connect() as db:
            if not db.execute('SELECT 1 FROM quiz_sets WHERE id=?',(set_id,)).fetchone():
                return error('선택한 퀴즈를 찾을 수 없습니다.',404)
    token = secrets.token_urlsafe(32)
    alphabet = string.ascii_uppercase + string.digits
    with connect() as db:
        for _ in range(12):
            code = ''.join(secrets.choice(alphabet) for _ in range(6))
            try:
                db.execute('INSERT INTO rooms(id,host_token,quiz_set_id) VALUES (?,?,?)', (code,token,set_id))
                return {'room_code': code, 'host_token': token}, 201
            except sqlite3.IntegrityError:
                continue
    return error('방 코드를 생성하지 못했습니다. 다시 시도해 주세요.', 503)

@app.post('/api/rooms/<code>/join')
def join(code):
    payload = data()
    raw = payload.get('name', '')
    if not isinstance(raw, str):
        return error('닉네임은 문자열이어야 합니다.')
    name = raw.strip()
    if not 1 <= len(name) <= 24:
        return error('닉네임은 1~24자로 입력해 주세요.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('존재하지 않는 방 코드입니다.', 404)
        if r['state'] != 'waiting':
            return error('진행 또는 종료된 방에는 새로 참가할 수 없습니다.', 409)
        pid = secrets.token_urlsafe(24)
        try:
            db.execute('INSERT INTO players(id,room_id,name) VALUES (?,?,?)', (pid,code,name))
        except sqlite3.IntegrityError:
            return error('이미 사용 중인 닉네임입니다.', 409)
    return {'player_id':pid,'name':name}, 201

@app.post('/api/rooms/<code>/leave')
def leave(code):
    """Remove an explicitly departing participant and their score/answers."""
    payload = data()
    pid = payload.get('player_id', '')
    if not isinstance(pid, str) or not pid:
        return error('참가자 정보가 필요합니다.', 400)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.', 404)
        player = db.execute('SELECT id FROM players WHERE room_id=? AND id=?', (code, pid)).fetchone()
        if not player:
            return error('이미 나갔거나 존재하지 않는 참가자입니다.', 404)
        db.execute('DELETE FROM answers WHERE room_id=? AND player_id=?', (code, pid))
        db.execute('DELETE FROM players WHERE room_id=? AND id=?', (code, pid))
    return {'message': '방에서 나갔습니다. 참가자 목록과 순위에서 제외되었습니다.'}

@app.post('/api/rooms/<code>/start')
def start(code):
    payload = data()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.', 404)
        if not is_host(payload,r):
            return error('방장만 시작할 수 있습니다.', 403)
        if r['state'] != 'waiting':
            return error('대기 중인 방만 시작할 수 있습니다.', 409)
        count = db.execute('SELECT COUNT(*) FROM players WHERE room_id=?',(code,)).fetchone()[0]
        if count < 1:
            return error('참가자가 최소 1명 필요합니다.', 409)
        db.execute("UPDATE rooms SET state='playing' WHERE id=?", (code,))
    return {'message':'퀴즈 시작!'}

@app.post('/api/rooms/<code>/answer')
def answer(code):
    payload = data()
    pid = payload.get('player_id')
    choice = payload.get('choice')
    if type(choice) is not int or not 0 <= choice <= 3:
        return error('정답 선택은 1~4번 중 하나여야 합니다.')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.', 404)
        if r['state'] != 'playing':
            return error('진행 중인 퀴즈에서만 답안을 제출할 수 있습니다.', 409)
        player = db.execute('SELECT id FROM players WHERE room_id=? AND id=?', (code,pid)).fetchone()
        if not player:
            return error('참가자 정보를 찾을 수 없습니다.', 403)
        index = r['question_index']
        bank=question_bank(db,r)
        if index >= len(bank):return error('현재 문제가 없습니다.',409)
        try:
            db.execute('INSERT INTO answers(room_id,player_id,question_index,choice) VALUES (?,?,?,?)', (code,pid,index,choice))
        except sqlite3.IntegrityError:
            return error('이 문제에는 이미 답안을 제출했습니다.', 409)
        correct = choice == bank[index]['answer']
        if correct:
            db.execute('UPDATE players SET score=score+10 WHERE id=?', (pid,))
    return {'correct':correct, 'earned':10 if correct else 0, 'correct_choice':bank[index]['answer']}

@app.post('/api/rooms/<code>/next')
def next_question(code):
    payload = data()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.', 404)
        if not is_host(payload,r):
            return error('방장만 문제를 진행할 수 있습니다.',403)
        if r['state'] != 'playing':
            return error('진행 중인 퀴즈가 아닙니다.',409)
        index = r['question_index'] + 1
        if index >= len(question_bank(db,r)):
            db.execute("UPDATE rooms SET state='finished' WHERE id=?", (code,))
            return {'state':'finished','message':'마지막 문제를 마쳤습니다.'}
        db.execute('UPDATE rooms SET question_index=? WHERE id=?', (index,code))
    return {'state':'playing','message':'다음 문제로 이동했습니다.'}

@app.post('/api/rooms/<code>/finish')
def finish(code):
    payload = data()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.',404)
        if not is_host(payload,r):
            return error('방장만 종료할 수 있습니다.',403)
        if r['state'] == 'finished':
            return error('이미 종료된 퀴즈입니다.',409)
        db.execute("UPDATE rooms SET state='finished' WHERE id=?", (code,))
    return {'state':'finished','message':'퀴즈가 종료되었습니다.'}

@app.get('/api/rooms/<code>')
def get_room(code):
    pid = request.args.get('player_id','')
    with connect() as db:
        r = room(db, code)
        if not r:
            return error('방을 찾을 수 없습니다.',404)
        index = r['question_index']
        bank=question_bank(db,r)
        q = None
        if r['state'] == 'playing':
            if index < len(bank):
                item=bank[index]
                q = {'index':index+1, 'total':len(bank),'text':item['text'],'choices':item['choices']}
        players = list_players(db, code)
        count = db.execute('SELECT COUNT(*) FROM answers WHERE room_id=? AND question_index=?', (code,index)).fetchone()[0] if r['state']=='playing' else 0
        mine = None
        if pid:
            player = db.execute('SELECT id,name,score FROM players WHERE id=? AND room_id=?',(pid,code)).fetchone()
            if player:
                a = db.execute('SELECT choice FROM answers WHERE room_id=? AND player_id=? AND question_index=?',(code,pid,index)).fetchone()
                mine = {'name':player['name'],'score':player['score'],'answered':a is not None}
        return {'code':code,'state':r['state'],'question':q,'quiz_set_id':r['quiz_set_id'],'players':players,
                'answer_count':count,'player_count':len(players),'mine':mine}

if __name__ == '__main__':
    app.run(host='0.0.0.0',port=5000)
