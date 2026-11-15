import socket
from pathlib import Path

import pytest

from edgecar.nlu.slm import SlmServer


def test_refuses_a_port_another_server_holds():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            SlmServer(Path("llama-server"), Path("model.gguf"), port=port).start()
