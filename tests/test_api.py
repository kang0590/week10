"""Run: python -m unittest discover -s tests -v (Flask installed)."""
import os,sys,tempfile,unittest
sys.path.insert(0,os.path.join(os.path.dirname(__file__),'..','app'))
os.environ['DATABASE_PATH']=os.path.join(tempfile.mkdtemp(prefix='quiz-on-test-'),'test.db')
from main import app

class QuizAPITest(unittest.TestCase):
    def setUp(self):
        self.c=app.test_client()
        self.room=self.c.post('/api/rooms').json
        self.code=self.room['room_code'];self.token=self.room['host_token'];self.url=f'/api/rooms/{self.code}'
    def join(self,name):
        return self.c.post(self.url+'/join',json={'name':name})
    def cmd(self,path,token=None):
        return self.c.post(self.url+path,json={'host_token':token if token is not None else self.token})
    def test_health_and_missing(self):
        self.assertEqual(self.c.get('/api/health').json['status'],'ok')
        self.assertEqual(self.c.get('/api/rooms/XXXXXX').status_code,404)
    def test_empty_start_and_participants(self):
        self.assertEqual(self.cmd('/start').status_code,409)
        self.assertEqual(self.join('').status_code,400)
        self.assertEqual(self.join('a'*25).status_code,400)
        self.assertEqual(self.join('학생A').status_code,201)
        self.assertEqual(self.join('학생A').status_code,409)
        self.assertEqual(self.cmd('/start','bad').status_code,403)
        self.assertEqual(self.cmd('/start').status_code,200)
        self.assertEqual(self.join('학생B').status_code,409)
    def test_scoring_and_normal_finish(self):
        p=self.join('정답자').json['player_id']
        self.join('오답자')
        self.cmd('/start')
        self.assertFalse(self.c.get(self.url+'?player_id='+p).json['mine']['answered'])
        result=self.c.post(self.url+'/answer',json={'player_id':p,'choice':0})
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['earned'],10)
        self.assertTrue(self.c.get(self.url+'?player_id='+p).json['mine']['answered'])
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':p,'choice':0}).status_code,409)
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':'wrong','choice':0}).status_code,403)
        self.assertEqual(self.c.get(self.url).json['players'][0]['score'],10)
        for _ in range(5): self.assertEqual(self.cmd('/next').status_code,200)
        self.assertEqual(self.c.get(self.url).json['state'],'finished')
        self.assertEqual(self.cmd('/next').status_code,409)
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':p,'choice':0}).status_code,409)
    def test_early_finish_and_access_control(self):
        p=self.join('학생B').json['player_id']
        self.assertEqual(self.cmd('/finish','bad').status_code,403)
        self.cmd('/start')
        self.assertEqual(self.cmd('/finish').status_code,200)
        self.assertEqual(self.c.get(self.url).json['state'],'finished')
        self.assertEqual(self.cmd('/finish').status_code,409)
        self.assertEqual(self.cmd('/start').status_code,409)
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':p,'choice':0}).status_code,409)
    def test_leave_removes_rank_and_allows_same_name_to_rejoin(self):
        pid=self.join('만두').json['player_id']
        self.join('만수')
        self.cmd('/start')
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':pid,'choice':0}).status_code,200)
        self.assertEqual(self.c.get(self.url).json['player_count'],2)
        self.assertEqual(self.c.post(self.url+'/leave',json={'player_id':pid}).status_code,200)
        data=self.c.get(self.url).json
        self.assertEqual(data['player_count'],1)
        self.assertNotIn('만두', [x['name'] for x in data['players']])
        self.assertEqual(data['answer_count'],0)
        self.assertEqual(self.c.post(self.url+'/leave',json={'player_id':pid}).status_code,404)
        self.assertEqual(self.c.post(self.url+'/answer',json={'player_id':pid,'choice':0}).status_code,403)
    def test_leave_waiting_and_rejoin(self):
        pid=self.join('민수').json['player_id']
        self.assertEqual(self.join('민수').status_code,409)
        self.assertEqual(self.c.post(self.url+'/leave',json={'player_id':pid}).status_code,200)
        self.assertEqual(self.join('민수').status_code,201)
        self.assertEqual(self.c.post(self.url+'/leave',json={'player_id':'invalid'}).status_code,404)
    def test_invalid_choice(self):
        self.assertEqual(self.c.post(self.url+'/answer',json={'choice':True}).status_code,400)
        self.assertEqual(self.c.post(self.url+'/answer',json={'choice':7}).status_code,400)

if __name__=='__main__':unittest.main()

class QuizSetTest(unittest.TestCase):
    def setUp(self):self.c=app.test_client()
    def sample(self):
        return {'title':'직접 만든 CS 퀴즈','questions':[
            {'text':'파이썬의 출력 함수는?','choices':['print','input','open','len'],'answer':0},
            {'text':'브라우저의 스크립트 언어는?','choices':['JavaScript','Python','SQL','YAML'],'answer':0}]}
    def test_create_update_play_and_delete(self):
        payload=self.sample()
        created=self.c.post('/api/quiz-sets',json=payload)
        self.assertEqual(created.status_code,201)
        sid=created.json['id'];token=created.json['edit_token']
        listing=self.c.get('/api/quiz-sets').json['sets']
        self.assertIn(sid,[x['id'] for x in listing])
        self.assertNotIn('answer',self.c.get('/api/quiz-sets/'+sid).json['questions'][0])
        self.assertIn('answer',self.c.get('/api/quiz-sets/'+sid,headers={'X-Edit-Token':token}).json['questions'][0])
        self.assertEqual(self.c.put('/api/quiz-sets/'+sid,json={**payload,'edit_token':'invalid'}).status_code,403)
        room=self.c.post('/api/rooms',json={'quiz_set_id':sid}).json
        code=room['room_code'];host=room['host_token'];url='/api/rooms/'+code
        self.assertEqual(self.c.delete('/api/quiz-sets/'+sid,json={'edit_token':token}).status_code,409)
        player=self.c.post(url+'/join',json={'name':'학생'}).json['player_id']
        self.assertEqual(self.c.post(url+'/start',json={'host_token':host}).status_code,200)
        self.assertEqual(self.c.get(url).json['question']['total'],2)
        self.assertEqual(self.c.post(url+'/answer',json={'player_id':player,'choice':0}).json['earned'],10)
        self.c.post(url+'/next',json={'host_token':host})
        self.assertEqual(self.c.post(url+'/answer',json={'player_id':player,'choice':0}).json['earned'],10)
        self.c.post(url+'/next',json={'host_token':host})
        self.assertEqual(self.c.get(url).json['state'],'finished')
        payload['title']='수정된 퀴즈'
        self.assertEqual(self.c.put('/api/quiz-sets/'+sid,json={**payload,'edit_token':token}).status_code,200)
        self.assertEqual(self.c.delete('/api/quiz-sets/'+sid,json={'edit_token':token}).status_code,200)
        self.assertEqual(self.c.get('/api/quiz-sets/'+sid).status_code,404)
    def test_invalid_questions_and_missing_set(self):
        payload=self.sample()
        payload['questions'][0]['choices']=['같음']*4
        self.assertEqual(self.c.post('/api/quiz-sets',json=payload).status_code,400)
        self.assertEqual(self.c.post('/api/rooms',json={'quiz_set_id':'NOTFOUND'}).status_code,404)
