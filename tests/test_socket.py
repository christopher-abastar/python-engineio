import unittest

import eventlet
import six
if six.PY3:
    from unittest import mock
else:
    import mock

from engineio import packet
from engineio import payload
from engineio import socket


class TestSocket(unittest.TestCase):
    def test_create(self):
        s = socket.Socket('server', 'sid')
        self.assertEqual(s.server, 'server')
        self.assertEqual(s.sid, 'sid')
        self.assertFalse(s.upgraded)
        self.assertFalse(s.closed)
        self.assertTrue(hasattr(s.queue, 'get'))
        self.assertTrue(hasattr(s.queue, 'put'))
        self.assertTrue(hasattr(s.queue, 'task_done'))
        self.assertTrue(hasattr(s.queue, 'join'))

    def test_empty_poll(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'sid')
        self.assertRaises(IOError, s.poll)

    def test_poll(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'sid')
        pkt1 = packet.Packet(packet.MESSAGE, data='hello')
        pkt2 = packet.Packet(packet.MESSAGE, data='bye')
        s.send(pkt1)
        s.send(pkt2)
        self.assertEqual(s.poll(), [pkt1, pkt2])

    def test_ping_pong(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'sid')
        s.receive(packet.Packet(packet.PING, data='abc'))
        r = s.poll()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0].encode(), b'3abc')

    def test_invalid_packet(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'sid')
        self.assertRaises(ValueError, s.receive, packet.Packet(packet.OPEN))

    def test_polling_read(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'foo')
        pkt1 = packet.Packet(packet.MESSAGE, data='hello')
        pkt2 = packet.Packet(packet.MESSAGE, data='bye')
        s.send(pkt1)
        s.send(pkt2)
        environ = {'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'sid=foo'}
        start_response = mock.MagicMock()
        packets = s.handle_get_request(environ, start_response)
        self.assertEqual(packets, [pkt1, pkt2])

    def test_polling_read_error(self):
        mock_server = mock.Mock()
        mock_server.ping_timeout = 0.1
        s = socket.Socket(mock_server, 'foo')
        environ = {'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'sid=foo'}
        start_response = mock.MagicMock()
        self.assertRaises(IOError, s.handle_get_request, environ,
                          start_response)

    def test_polling_write(self):
        mock_server = mock.Mock()
        mock_server.max_http_buffer_size = 1000
        pkt1 = packet.Packet(packet.MESSAGE, data='hello')
        pkt2 = packet.Packet(packet.MESSAGE, data='bye')
        p = payload.Payload(packets=[pkt1, pkt2]).encode()
        s = socket.Socket(mock_server, 'foo')
        s.receive = mock.MagicMock()
        environ = {'REQUEST_METHOD': 'POST', 'QUERY_STRING': 'sid=foo',
                   'CONTENT_LENGTH': len(p), 'wsgi.input': six.BytesIO(p)}
        s.handle_post_request(environ)
        self.assertEqual(s.receive.call_count, 2)

    def test_polling_write_too_large(self):
        mock_server = mock.Mock()
        pkt1 = packet.Packet(packet.MESSAGE, data='hello')
        pkt2 = packet.Packet(packet.MESSAGE, data='bye')
        p = payload.Payload(packets=[pkt1, pkt2]).encode()
        mock_server.max_http_buffer_size = len(p) - 1
        s = socket.Socket(mock_server, 'foo')
        s.receive = mock.MagicMock()
        environ = {'REQUEST_METHOD': 'POST', 'QUERY_STRING': 'sid=foo',
                   'CONTENT_LENGTH': len(p), 'wsgi.input': six.BytesIO(p)}
        self.assertRaises(ValueError, s.handle_post_request, environ)

    def test_upgrade_handshake(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'foo')
        s._upgrade_websocket = mock.MagicMock()
        environ = {'REQUEST_METHOD': 'GET', 'QUERY_STRING': 'sid=foo',
                   'HTTP_CONNECTION': 'Foo,Upgrade,Bar',
                   'HTTP_UPGRADE': 'websocket'}
        start_response = mock.MagicMock()
        s.handle_get_request(environ, start_response)
        s._upgrade_websocket.assert_called_once_with(environ, start_response)

    @mock.patch('eventlet.websocket.WebSocketWSGI')
    def test_upgrade(self, WebSocketWSGI):
        mock_ws = mock.MagicMock()
        WebSocketWSGI.configure_mock(return_value=mock_ws)
        s = socket.Socket('server', 'sid')
        environ = "foo"
        start_response = "bar"
        s._upgrade_websocket(environ, start_response)
        WebSocketWSGI.assert_called_once_with(s._websocket_handler)
        mock_ws.assert_called_once_with(environ, start_response)

    @mock.patch('eventlet.websocket.WebSocketWSGI')
    def test_upgrade_twice(self, WebSocketWSGI):
        s = socket.Socket('server', 'sid')
        s.upgraded = True
        environ = "foo"
        start_response = "bar"
        self.assertRaises(IOError, s._upgrade_websocket,
                          environ, start_response)

    def test_upgrade_packet(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.receive(packet.Packet(packet.UPGRADE))
        r = s.poll()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0].encode(), b'6')

    def test_upgrade_no_probe(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        ws = mock.MagicMock()
        ws.wait.return_value = packet.Packet(packet.NOOP).encode(
            always_bytes=False)
        s._websocket_handler(ws)
        self.assertFalse(s.upgraded)

    def test_upgrade_no_upgrade_packet(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.queue.join = mock.MagicMock(return_value=None)
        ws = mock.MagicMock()
        probe = six.text_type('probe')
        ws.wait.side_effect = [
            packet.Packet(packet.PING, data=probe).encode(
                always_bytes=False),
            packet.Packet(packet.NOOP).encode(always_bytes=False)]
        s._websocket_handler(ws)
        ws.send.assert_called_once_with(packet.Packet(
            packet.PONG, data=probe).encode(always_bytes=False))
        self.assertEqual(s.queue.get().packet_type, packet.NOOP)
        self.assertFalse(s.upgraded)

    def test_websocket_read_write(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.queue.join = mock.MagicMock(return_value=None)
        foo = six.text_type('foo')
        bar = six.text_type('bar')
        probe = six.text_type('probe')
        s.poll = mock.MagicMock(side_effect=[
            [packet.Packet(packet.MESSAGE, data=bar)], IOError])
        ws = mock.MagicMock()
        ws.wait.side_effect = [
            packet.Packet(packet.PING, data=probe).encode(
                always_bytes=False),
            packet.Packet(packet.UPGRADE).encode(always_bytes=False),
            packet.Packet(packet.MESSAGE, data=foo).encode(
                always_bytes=False),
            None]
        s._websocket_handler(ws)
        eventlet.sleep()
        self.assertTrue(s.upgraded)
        self.assertEqual(mock_server._trigger_event.call_count, 2)
        mock_server._trigger_event.assert_has_calls([
            mock.call('message', 'sid', 'foo'),
            mock.call('disconnect', 'sid')])
        ws.send.assert_called_with('4bar')

    def test_websocket_ignore_invalid_packet(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.queue.join = mock.MagicMock(return_value=None)
        foo = six.text_type('foo')
        bar = six.text_type('bar')
        probe = six.text_type('probe')
        s.poll = mock.MagicMock(side_effect=[
            [packet.Packet(packet.MESSAGE, data=bar)], IOError])
        ws = mock.MagicMock()
        ws.wait.side_effect = [
            packet.Packet(packet.PING, data=probe).encode(
                always_bytes=False),
            packet.Packet(packet.UPGRADE).encode(always_bytes=False),
            packet.Packet(packet.OPEN).encode(always_bytes=False),
            packet.Packet(packet.MESSAGE, data=foo).encode(
                always_bytes=False),
            None]
        s._websocket_handler(ws)
        eventlet.sleep()
        self.assertTrue(s.upgraded)
        self.assertEqual(mock_server._trigger_event.call_count, 2)
        mock_server._trigger_event.assert_has_calls([
            mock.call('message', 'sid', foo),
            mock.call('disconnect', 'sid')])
        ws.send.assert_called_with('4bar')

    def test_send_after_close(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.close(wait=False)
        self.assertRaises(IOError, s.send, packet.Packet(packet.NOOP))

    def test_close_and_wait(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.queue = mock.MagicMock()
        s.close(wait=True)
        s.queue.join.assert_called_once_with()

    def test_close_without_wait(self):
        mock_server = mock.Mock()
        s = socket.Socket(mock_server, 'sid')
        s.queue = mock.MagicMock()
        s.close(wait=False)
        self.assertEqual(s.queue.join.call_count, 0)