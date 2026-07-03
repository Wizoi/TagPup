# Workspace Rules: TagPup Development Guidelines

To prevent common errors, redundant operations, and improve execution speed, please adhere to these behavioral rules:

1. **Avoid Complex Terminal Escaping**: Never attempt to run inline python commands with complex nested quotes in Windows/PowerShell. Write a helper script in the brain's `scratch/` directory and execute it instead.
2. **Double-Check Line Ranges**: Always view exact line slices before editing with `replace_file_content` to match leading whitespaces perfectly.
3. **Windows File Paths**: Standardize path strings with forward slashes `/` in ripgrep searches, and handle casing and backslash normalization (`replace(/\\/g, '/').toLowerCase()`) when comparing database path keys on Windows.
4. **Taxonomy & Face Propagation**: Subnodes created under face-matching root categories (`People/`, `Pets/`, etc.) must have their `has_face` set to `1` in the database to be correctly processed by face clustering.
