"""One function per user action. The only code that writes, and each returns a Result.

The servers and the CLI parse a request, call one of these, and reply with what it
returned (ARCHITECTURE.md, "Layers").
"""
