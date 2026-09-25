"""The web layer: one Flask app per page, both served by Waitress from one process
(docs/ARCHITECTURE.md, phase 5).

A route is thin. It reads the request, asks a service, and answers; what a request
means to a library lives in tagpup.services, and what runs on after the answer in
tagpup.jobs. The two standard-library servers this replaces held 4,000 lines of both.
"""
