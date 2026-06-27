import os
import sys
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
import numpy as np
from PIL import Image

# Ensure workspace and scripts directories are in search path
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup_cli import cli, get_config, get_exiftool_path
import exiftool

def create_dummy_jpeg(path):
    """Create a minimal 100x100 pixel valid JPEG image."""
    img = Image.new("RGB", (100, 100), color="blue")
    img.save(path, "JPEG")

class TestFunctionalCLI(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary workspace directory
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_path = self.temp_dir.name

        # Define isolated database and taxonomy paths
        self.db_path = os.path.join(self.workspace_path, "functional_photo_index.db")
        self.tax_path = os.path.join(self.workspace_path, "functional_photo_taxonomy.json")

        # Inject environment variable overrides to get_db_paths
        os.environ["TAGPUP_DB_PATH"] = self.db_path
        os.environ["TAGPUP_TAXONOMY_PATH"] = self.tax_path

        # Create isolated dummy photos library folder
        self.library_dir = os.path.join(self.workspace_path, "library")
        os.makedirs(self.library_dir, exist_ok=True)

        self.photo1_path = os.path.join(self.library_dir, "photo1.jpg")
        self.photo2_path = os.path.join(self.library_dir, "photo2.jpg")
        self.photo3_path = os.path.join(self.library_dir, "photo3.jpg")
        create_dummy_jpeg(self.photo1_path)
        create_dummy_jpeg(self.photo2_path)
        create_dummy_jpeg(self.photo3_path)

        # Retrieve ExifTool path and write metadata to photo1 and photo2.
        # photo3 remains untagged and should not be indexed.
        config = get_config()
        self.exiftool_path = get_exiftool_path(config)

        try:
            with exiftool.ExifToolHelper(executable=self.exiftool_path) as et:
                et.set_tags(
                    [self.photo1_path],
                    tags={"IPTC:Keywords": ["Nature", "Forest"], "XMP:Subject": ["Nature", "Forest"]}
                )
                et.set_tags(
                    [self.photo2_path],
                    tags={"IPTC:Keywords": ["Ocean"], "XMP:Subject": ["Ocean"], "XMP:PersonInImage": ["Alice"]}
                )
        except Exception as e:
            # Skip testing metadata write during setup if ExifTool is missing or fails, 
            # but log warning so developers know.
            sys.stderr.write(f"\n[WARNING] ExifTool setup write failed: {e}\n")

    def tearDown(self):
        # Clean up temporary directory
        try:
            self.temp_dir.cleanup()
        except Exception:
            pass
        # Clear environment variables
        os.environ.pop("TAGPUP_DB_PATH", None)
        os.environ.pop("TAGPUP_TAXONOMY_PATH", None)

    @patch('embedder.ClipEmbedder._init_model')
    @patch('embedder.ClipEmbedder.embed_image')
    @patch('embedder.ClipEmbedder.embed_text')
    @patch('faces.FaceProcessor._init_models')
    @patch('faces.FaceProcessor.detect_and_embed_faces')
    def test_cli_end_to_end_workflow(self, mock_detect, mock_init_faces, mock_embed_text, mock_embed_image, mock_init_clip):
        # Mock ML models behavior to match database expected dimensionality
        config = get_config()
        model_name = config.get("model", "name", fallback="ViT-B-32")
        expected_dim = 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)
        mock_embed_image.return_value = [0.1] * expected_dim
        mock_embed_text.return_value = [0.1] * expected_dim
        
        # Detect mock returns a face in photo2
        mock_detect.return_value = [
            {
                "box": [10, 10, 50, 50],
                "embedding": [0.15] * 512,
                "crop_image": b"mock_crop_bytes",
                "prob": 0.95
            }
        ]

        runner = CliRunner()

        # --- 1. INDEX COMMAND ---
        result = runner.invoke(cli, ["index", self.library_dir])
        self.assertEqual(result.exit_code, 0, f"index command failed: {result.output}")
        self.assertIn("Indexing successfully completed!", result.output)

        # Verify SQLite DB has been created and populated
        self.assertTrue(os.path.exists(self.db_path), "Database was not created")
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT path, tags, people FROM photos")
        rows = c.fetchall()
        paths = [r[0] for r in rows]
        
        # Only tagged photos (photo1, photo2) should be indexed; photo3 (untagged) must be skipped.
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("photo1.jpg" in p for p in paths))
        self.assertTrue(any("photo2.jpg" in p for p in paths))
        
        # Check faces are indexed
        c.execute("SELECT photo_path, name FROM faces")
        face_rows = c.fetchall()
        self.assertEqual(len(face_rows), 2) # Both photos got a mock face
        conn.close()

        # --- 2. INCREMENTAL SKIP INDEXING COMMAND ---
        result = runner.invoke(cli, ["index", self.library_dir])
        self.assertEqual(result.exit_code, 0, f"incremental index failed: {result.output}")
        self.assertIn("No new tagged images found. Index remains current.", result.output)

        # --- 3. STATS COMMAND ---
        result = runner.invoke(cli, ["stats"])
        self.assertEqual(result.exit_code, 0, f"stats command failed: {result.output}")
        self.assertIn("Total Indexed Photos: 2", result.output)
        self.assertIn("Nature", result.output)
        self.assertIn("Alice", result.output)

        # --- 4. SEARCH COMMAND ---
        result = runner.invoke(cli, ["search", "forest scene"])
        self.assertEqual(result.exit_code, 0, f"search command failed: {result.output}")
        self.assertIn("Search Results", result.output)
        self.assertIn("Nature, Forest", result.output)

        # --- 5. SUGGEST COMMAND ---
        # Create an untagged target folder for suggestions
        target_dir = os.path.join(self.workspace_path, "target")
        os.makedirs(target_dir, exist_ok=True)
        target_photo = os.path.join(target_dir, "new_photo.jpg")
        create_dummy_jpeg(target_photo)

        suggestions_file = os.path.join(self.workspace_path, "test_suggestions.json")
        result = runner.invoke(cli, ["suggest", target_dir, "--output", suggestions_file])
        self.assertEqual(result.exit_code, 0, f"suggest command failed: {result.output}")
        self.assertTrue(os.path.exists(suggestions_file), "Suggestions file was not generated")

        # Verify suggestions.json structure
        with open(suggestions_file, 'r', encoding='utf-8') as f:
            sugg_data = json.load(f)
        self.assertEqual(len(sugg_data), 1)
        self.assertEqual(os.path.abspath(target_photo), os.path.abspath(sugg_data[0]["path"]))
        
        # --- 6. WRITE COMMAND ---
        # Run a live write to apply tags and verify ExifTool modifies target
        result = runner.invoke(cli, ["write", suggestions_file, "-Live", "--nobackup"], input="YES\n")
        self.assertEqual(result.exit_code, 0, f"write command failed: {result.output}")
        self.assertIn("Finished writing metadata. Success: 1", result.output)

if __name__ == "__main__":
    unittest.main()
