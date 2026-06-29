import os
import sys
import time
import socket
import urllib.request
import subprocess
import unittest

class TestServerStartup(unittest.TestCase):
    def test_startup(self):
        # Resolve path to tagpup_gui.py
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script_path = os.path.join(project_root, "tagpup_gui.py")
        
        # Run tagpup_gui.py with the child environment variable set directly
        # so it runs the server in the foreground, and we capture its output.
        env = os.environ.copy()
        env["TAGPUP_RELOADED_CHILD"] = "1"
        env["TAGPUP_PORT"] = "8095"  # Use a separate test port to avoid conflicts
        
        # Start the server as a subprocess
        proc = subprocess.Popen(
            [sys.executable, script_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=project_root
        )
        
        try:
            # Poll the port to see when the server starts responding
            server_ready = False
            for _ in range(50):
                if proc.poll() is not None:
                    # Server process died early
                    break
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.connect(("127.0.0.1", 8095))
                        server_ready = True
                        break
                except (ConnectionRefusedError, OSError):
                    time.sleep(0.1)
            
            self.assertTrue(server_ready, "Server failed to start and bind to port 8095")
            
            # Send an HTTP request to verify the server is responding correctly
            response = urllib.request.urlopen("http://127.0.0.1:8095/", timeout=5.0)
            self.assertEqual(response.status, 200, "Server responded with non-200 status code")
            html = response.read().decode("utf-8")
            self.assertIn("TagPup", html, "Server did not return the TagPup GUI page")
            
        finally:
            # Clean up the process
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

    def test_tag_embedding_cache(self):
        # Add scripts directory to path to import index
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(project_root, "scripts"))
        from index import PhotoIndex
        
        test_db = os.path.join(project_root, "data", "test_tag_emb_cache.db")
        if os.path.exists(test_db):
            os.remove(test_db)
            
        try:
            photo_index = PhotoIndex(db_path=test_db)
            self.assertTrue(photo_index.load())
            
            # Initially, get_tag_embedding should return None
            cached = photo_index.get_tag_embedding("test_tag", "prompt", "model", "pretrained")
            self.assertIsNone(cached)
            
            # Save embedding
            test_emb = [0.1, 0.2, 0.3, 0.4]
            photo_index.save_tag_embedding("test_tag", "prompt", "model", "pretrained", test_emb)
            
            # Retrieve embedding
            retrieved = photo_index.get_tag_embedding("test_tag", "prompt", "model", "pretrained")
            self.assertIsNotNone(retrieved)
            self.assertEqual(len(retrieved), len(test_emb))
            for a, b in zip(retrieved, test_emb):
                self.assertAlmostEqual(a, b, places=5)
                
            # Querying with different params should return None
            self.assertIsNone(photo_index.get_tag_embedding("test_tag", "diff_prompt", "model", "pretrained"))
            self.assertIsNone(photo_index.get_tag_embedding("test_tag", "prompt", "diff_model", "pretrained"))
            self.assertIsNone(photo_index.get_tag_embedding("test_tag", "prompt", "model", "diff_pretrained"))
            
        finally:
            if os.path.exists(test_db):
                try:
                    os.remove(test_db)
                except Exception:
                    pass

if __name__ == "__main__":
    unittest.main()
