---
name: TagPup Development Guidelines
description: Best practices for workspace code analysis, precise editing, Windows command execution, and database verification.
---

## Core Guidelines for TagPup Development

To optimize execution speed, avoid redundant work, and prevent common tool failures (such as terminal escaping bugs, file mismatch edits, and path casing issues), adhere strictly to the following instructions:

### 1. Windows Command Executions & Python Scripts
- **Avoid Complex Inline Python Commands**: Python one-liners with complex single/double quote nesting are highly prone to parsing and termination errors in Windows/PowerShell.
- **Use Scratch Scripts**: For any non-trivial database query, batch update, or test code, write a temporary Python script to the conversation's scratch directory (`C:\Users\kidzi\.gemini\antigravity-ide\brain\<conversation-id>\scratch\`) and execute it via `run_command` instead of trying to escape quotes.
- **PyTorch/CUDA Load Times**: Loading PyTorch and CUDA models takes up to 20 seconds. Ensure terminal runs are set with a generous `WaitMsBeforeAsync` (e.g. 5000-10000ms) to allow standard imports to finish before sending them to the background.

### 2. Precise File Edits with `replace_file_content`
- **Verify Line Slices First**: Before replacing content, call `view_file` to inspect the exact line numbers and leading whitespace. Whitespace mismatches are the primary cause of block matching failures.
- **Avoid Multi-turn Refits**: Make edits in self-contained chunks. If making multiple non-contiguous edits in a single file, use `multi_replace_file_content` immediately instead of running multiple sequential `replace_file_content` calls.
- **Indentation Mismatches**: Python requires exact indentation. An extra or missing space in a replacement block causes `IndentationError`. Always visually align replacements to match surrounding code block indentation.

### 3. Ripgrep & Windows Paths
- **Ripgrep Slash Casing**: In `grep_search`, use standardized forward slashes `/` in paths or search directories. Ripgrep on Windows can fail silently or return empty results when mixed slashes are used.
- **Case-Insensitive Database String Comparison**: When querying or comparing database path keys on Windows, use `pathsEqual` or L2 normalization (`replace(/\\/g, '/').toLowerCase()`) because different components write backslashes or forward slashes depending on where they run.

### 4. Database Taxonomy and Person Extraction
- **Leaf Name Matching**: The `name` column in `tag_taxonomy` must always store the bare leaf name (e.g., `'Clara Idzi'`), not the hierarchical tag path (e.g., `'People/Clara Idzi'`).
- **Face matching propagation (`has_face`)**: Subcategories created under face-matching root categories (`People/`, `Pets/`, `Family/`, `Friends/`) must have their `has_face` column set to `1` in the database to be correctly recognized as people.

### 5. Type and Structure Safety
- **Type-Safe Comparisons**: Sanitize database results (such as `mtime`, metadata values, or parsed dates) before making comparisons (e.g., checking sliding windows). If a value can be a string placeholder like `"Unknown"`, cast it safely using `try-except` blocks.
- **Bounding Box Array Slices**: Bounding boxes retrieved from database faces (e.g., `box`) can be empty (`[]`). Validate array length (`len(box) >= 4`) before performing arithmetic on slices to avoid `IndexError`.
- **Reusing Active SQLite Connections**: Functions that query database tables (like `get_people_roots` or `extract_people`) and are called inside transaction blocks must accept an optional `conn` parameter to reuse the active connection. Opening a new connection (`sqlite3.connect`) while an exclusive write transaction is uncommitted will result in a `database is locked` OperationalError.

### 6. Tool-Call Parameter Constraints
- **ArtifactMetadata Usage**: Only supply `ArtifactMetadata` when editing files inside the active conversation's brain folder (`C:\Users\kidzi\.gemini\antigravity-ide\brain\<conversation-id>/`). Providing it for repository files causes validation failures.
- **View File Bounds**: Always verify `StartLine <= EndLine` when calling `view_file` on text documents.
