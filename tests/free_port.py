"""A port nothing is listening on, for a test server.

Test servers used fixed ports (9089, 9955, ...), so two runs at once -- two worktrees,
two agents, a run and a re-run -- bound the same port. On Windows both binds
succeeded and each run's requests could reach the other's server, where a mocked
folder picker was the real one and opened on the desktop. tagpup.web.app.bind now refuses a
port in use; this makes sure a test never asks for one.
"""
import socket


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
