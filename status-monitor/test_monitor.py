from monitor import decode_chunked, public_status


def test_decode_chunked():
    assert decode_chunked(b"4\r\ntest\r\n3\r\ning\r\n0\r\n\r\n") == b"testing"


def test_public_status():
    assert public_status("running") == "UP"
    assert public_status("restarting") == "RESTARTING"
    assert public_status("exited") == "DOWN"
