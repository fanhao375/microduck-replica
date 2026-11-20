import base64,io,tempfile,unittest
from unittest.mock import patch
from studio import camera_capture
from studio.ai_connections import AiError
from studio.ai_tools import RobotTools
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAOYd/h0AAAAASUVORK5CYII=')
class CameraTests(unittest.TestCase):
 def capture(self,active,data):
  response=io.BytesIO(data);response.status=200
  with patch.object(camera_capture,'CameraSession') as session,patch('pathlib.Path.read_text',return_value='{"active":'+str(active).lower()+'}'),patch.object(camera_capture.time,'sleep') as sleep,patch.object(camera_capture.http.client,'HTTPConnection') as http:
   http.return_value.getresponse.return_value=response
   result=camera_capture.capture()
   self.assertTrue(session.return_value.__exit__.called)
   return result,sleep
 def test_cold_camera_keeps_session_for_warmup(self):
  data=PNG;result,sleep=self.capture(False,data)
  sleep.assert_called_once_with(12)
  self.assertEqual(base64.b64decode(result.split(',')[1]),data)
 def test_active_preview_does_not_cold_start(self):
  _,sleep=self.capture(True,PNG);sleep.assert_called_once_with(3)
 def test_invalid_image_is_not_forwarded(self):
  with self.assertRaises(AiError):self.capture(True,b'not an image')
class LoopbackCameraTests(unittest.TestCase):
 def test_configured_signal_and_snapshot_ports_share_only_the_selected_frame(self):
  import json,socketserver,threading
  from http.server import BaseHTTPRequestHandler,HTTPServer
  from wsproto import WSConnection,ConnectionType
  from wsproto.events import AcceptConnection,Request,TextMessage
  requested=[]
  png=PNG
  class Signal(socketserver.BaseRequestHandler):
   def handle(self):
    protocol=WSConnection(ConnectionType.SERVER)
    while chunk:=self.request.recv(8192):
     protocol.receive_data(chunk)
     for event in protocol.events():
      if isinstance(event,Request):
       self.request.sendall(protocol.send(AcceptConnection()))
       reply={'type':'welcome'}
      elif isinstance(event,TextMessage):
       msg=json.loads(event.data);requested.append(msg)
       reply=({'type':'list','producers':[{'id':'duck','meta':{'release':'fixture','api_version':1}}]}
              if msg['type']=='list' else {'type':'sessionStarted','sessionId':'fixture'})
      else:continue
      self.request.sendall(protocol.send(TextMessage(data=json.dumps(reply))))
  class Frame(BaseHTTPRequestHandler):
   def do_GET(self):
    requested.append(self.path);self.send_response(200);self.end_headers();self.wfile.write(png)
   def log_message(self,*args):pass
  with socketserver.TCPServer(('127.0.0.1',0),Signal) as signal, HTTPServer(('127.0.0.1',0),Frame) as frames:
   threads=[threading.Thread(target=srv.handle_request,daemon=True) for srv in (signal,frames)]
   for thread in threads:thread.start()
   with patch.object(camera_capture.time,'sleep'):
    image=camera_capture.capture(signal.server_address[1],frames.server_port)
   for thread in threads:thread.join(timeout=1)
  self.assertEqual(requested,[{'type':'list'},{'type':'startSession','peerId':'duck'},'/frame'])
  self.assertEqual(base64.b64decode(image.split(',')[1]),png)
class CameraSchemaTests(unittest.IsolatedAsyncioTestCase):
 async def test_camera_available_when_motion_disabled(self):
  with tempfile.TemporaryDirectory() as d:
   schemas,_=await RobotTools(d).schemas()
   self.assertEqual(schemas,[])
   schemas,_=await RobotTools(d).schemas(allow_camera=True)
   self.assertEqual([s['function']['name'] for s in schemas],['duck_camera'])
