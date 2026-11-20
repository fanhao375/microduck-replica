import base64,io,tempfile,unittest
from unittest.mock import patch
from studio import camera_capture
from studio.ai_connections import AiError
from studio.ai_tools import RobotTools
class CameraTests(unittest.TestCase):
 def capture(self,active,data):
  response=io.BytesIO(data)
  with patch.object(camera_capture,'CameraSession') as session,patch('pathlib.Path.read_text',return_value='{"active":'+str(active).lower()+'}'),patch.object(camera_capture.time,'sleep') as sleep,patch.object(camera_capture.urllib.request,'urlopen',return_value=response):
   result=camera_capture.capture()
   self.assertTrue(session.return_value.__exit__.called)
   return result,sleep
 def test_cold_camera_keeps_session_for_warmup(self):
  data=b'\x89PNG\r\n\x1a\nframe';result,sleep=self.capture(False,data)
  sleep.assert_called_once_with(12)
  self.assertEqual(base64.b64decode(result.split(',')[1]),data)
 def test_active_preview_does_not_cold_start(self):
  _,sleep=self.capture(True,b'\x89PNG\r\n\x1a\nframe');sleep.assert_called_once_with(3)
 def test_invalid_image_is_not_forwarded(self):
  with self.assertRaises(AiError):self.capture(True,b'not an image')
class CameraSchemaTests(unittest.IsolatedAsyncioTestCase):
 async def test_camera_available_when_motion_disabled(self):
  with tempfile.TemporaryDirectory() as d:
   schemas,_=await RobotTools(d).schemas()
   self.assertEqual([s['function']['name'] for s in schemas],['duck_camera'])
