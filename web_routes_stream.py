"""WebSocket and streaming route handlers extracted from web_server.py."""

import base64
import hashlib
import socket
import threading as _thr
import queue as _q_mod


def handle_ws_audio(handler, parent):
    """GET /ws_audio -- Low-latency PCM WebSocket.

    Upgrades the HTTP connection to a WebSocket, then streams PCM audio
    frames from the gateway to the browser via a dedicated send thread.
    Blocks the handler thread for the lifetime of the connection.
    """
    # WebSocket upgrade for low-latency PCM audio streaming
    handler._upgrading_ws = True
    ws_key = handler.headers.get('Sec-WebSocket-Key', '')
    if not ws_key or handler.headers.get('Upgrade', '').lower() != 'websocket':
        handler._upgrading_ws = False
        handler.send_response(400)
        handler.end_headers()
        return
    # WebSocket handshake -- write raw bytes to bypass
    # BaseHTTPRequestHandler's send_response which adds
    # Server/Date headers that can confuse strict WS clients
    _WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
    accept = base64.b64encode(
        hashlib.sha1((ws_key + _WS_MAGIC).encode()).digest()
    ).decode()
    # Flush any buffered wfile data, then write handshake
    # directly to raw socket to avoid BufferedWriter issues
    handler.wfile.flush()
    handshake = (
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    )
    handler.request.sendall(handshake.encode('ascii'))
    handler.close_connection = True  # prevent handler loop after do_GET returns
    _sock = handler.request  # raw TCP socket for binary frames
    _client_ip = handler.client_address[0]
    print(f"\n[WS-Audio] Low-latency client connected from {_client_ip}")
    _sock.settimeout(30)  # 30s recv timeout for keepalive
    _sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    _send_q = _q_mod.Queue(maxsize=6)  # ~300ms buffer at 50ms chunks
    _ws_entry = (_sock, _send_q)

    def _ws_sender(_s, _q):
        """Dedicated send thread -- drains queue, never blocks audio loop."""
        while True:
            try:
                frame = _q.get(timeout=5)
                if frame is None:
                    break
                _s.sendall(frame)
            except (_q_mod.Empty):
                continue
            except (BrokenPipeError, ConnectionResetError, OSError):
                break

    _send_thread = _thr.Thread(target=_ws_sender, args=(_sock, _send_q), daemon=True)
    _send_thread.start()

    with parent._ws_lock:
        parent._ws_clients.append(_ws_entry)
    try:
        # Keep connection alive -- read and discard client frames
        # (we only send, but must handle pings/close)
        while True:
            try:
                hdr = _sock.recv(2)
                if not hdr or len(hdr) < 2:
                    break
                opcode = hdr[0] & 0x0F
                masked = (hdr[1] & 0x80) != 0
                payload_len = hdr[1] & 0x7F
                if payload_len == 126:
                    ext = _sock.recv(2)
                    payload_len = int.from_bytes(ext, 'big')
                elif payload_len == 127:
                    ext = _sock.recv(8)
                    payload_len = int.from_bytes(ext, 'big')
                mask_key = _sock.recv(4) if masked else b''
                payload = b''
                while len(payload) < payload_len:
                    chunk = _sock.recv(payload_len - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                if opcode == 0x8:  # Close
                    # Send close frame back
                    _sock.sendall(b'\x88\x00')
                    break
                elif opcode == 0x9:  # Ping -> Pong
                    if masked and mask_key:
                        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
                    pong = bytearray()
                    pong.append(0x8A)  # FIN + Pong
                    if len(payload) < 126:
                        pong.append(len(payload))
                    pong.extend(payload)
                    _sock.sendall(bytes(pong))
            except socket.timeout:
                continue  # recv timeout is normal, keep waiting
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
    finally:
        _send_q.put(None)  # signal sender thread to exit
        with parent._ws_lock:
            try:
                parent._ws_clients.remove(_ws_entry)
            except ValueError:
                pass
        print(f"[WS-Audio] Disconnected {_client_ip}")
    return


def handle_ws_mic(handler, parent):
    """GET /ws_mic -- Browser microphone WebSocket.

    Upgrades the HTTP connection to a WebSocket that receives PCM audio
    from the browser microphone and pushes it into the WebMicSource for
    radio TX.  Blocks the handler thread for the lifetime of the connection.
    """
    # WebSocket endpoint for browser microphone -> radio TX
    handler._upgrading_ws = True
    ws_key = handler.headers.get('Sec-WebSocket-Key', '')
    if not ws_key or handler.headers.get('Upgrade', '').lower() != 'websocket':
        handler._upgrading_ws = False
        handler.send_response(400)
        handler.end_headers()
        return
    # Check if web mic source is available
    _mic_src = parent.gateway.web_mic_source if parent.gateway else None
    if not _mic_src:
        handler._upgrading_ws = False
        handler.send_response(503)
        handler.end_headers()
        return
    # Reject if another mic client is already connected
    if _mic_src.client_connected:
        handler._upgrading_ws = False
        handler.send_response(409)  # Conflict
        handler.end_headers()
        return
    # WebSocket handshake
    _WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
    accept = base64.b64encode(
        hashlib.sha1((ws_key + _WS_MAGIC).encode()).digest()
    ).decode()
    handler.wfile.flush()
    handshake = (
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    )
    handler.request.sendall(handshake.encode('ascii'))
    handler.close_connection = True
    _sock = handler.request
    _client_ip = handler.client_address[0]
    _sock.settimeout(30)
    _sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    _mic_src.client_connected = True
    print(f"\n[WS-Mic] Browser mic connected from {_client_ip}")
    # PTT is handled by the bus system -- WebMicSource has ptt_control=True,
    # so any SoloBus with webmic as a TX source will auto-key its radio.
    try:
        while True:
            try:
                hdr = _sock.recv(2)
                if not hdr or len(hdr) < 2:
                    break
                opcode = hdr[0] & 0x0F
                masked = (hdr[1] & 0x80) != 0
                payload_len = hdr[1] & 0x7F
                if payload_len == 126:
                    ext = _sock.recv(2)
                    payload_len = int.from_bytes(ext, 'big')
                elif payload_len == 127:
                    ext = _sock.recv(8)
                    payload_len = int.from_bytes(ext, 'big')
                mask_key = _sock.recv(4) if masked else b''
                payload = b''
                while len(payload) < payload_len:
                    chunk = _sock.recv(payload_len - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                if masked and mask_key:
                    payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:  # Close
                    _sock.sendall(b'\x88\x00')
                    break
                elif opcode == 0x9:  # Ping -> Pong
                    pong = bytearray([0x8A, len(payload) if len(payload) < 126 else 0])
                    if len(payload) < 126:
                        pong[1] = len(payload)
                    pong.extend(payload)
                    _sock.sendall(bytes(pong))
                elif opcode == 0x2:  # Binary -- PCM audio data
                    _mic_src.push_audio(payload)
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
    finally:
        _mic_src.client_connected = False
        _mic_src._sub_buffer = b''
        # PTT release handled by bus system -- SoloBus releases PTT
        # after ptt_release_delay when WebMicSource stops producing audio.
        print(f"[WS-Mic] Disconnected {_client_ip}")
    return


def handle_ws_monitor(handler, parent):
    """GET /ws_monitor -- Room monitor WebSocket.

    Upgrades the HTTP connection to a WebSocket that receives PCM audio
    from the browser room-monitor mic and pushes it into the
    WebMonitorSource.  Blocks the handler thread for the lifetime of the
    connection.
    """
    # WebSocket endpoint for room monitor -- audio into mixer, NO PTT
    handler._upgrading_ws = True
    ws_key = handler.headers.get('Sec-WebSocket-Key', '')
    if not ws_key or handler.headers.get('Upgrade', '').lower() != 'websocket':
        handler._upgrading_ws = False
        handler.send_response(400)
        handler.end_headers()
        return
    _mon_src = parent.gateway.web_monitor_source if parent.gateway else None
    if not _mon_src:
        handler._upgrading_ws = False
        handler.send_response(503)
        handler.end_headers()
        return
    if _mon_src.client_connected:
        handler._upgrading_ws = False
        handler.send_response(409)
        handler.end_headers()
        return
    _WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
    accept = base64.b64encode(
        hashlib.sha1((ws_key + _WS_MAGIC).encode()).digest()
    ).decode()
    handler.wfile.flush()
    handshake = (
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    )
    handler.request.sendall(handshake.encode('ascii'))
    handler.close_connection = True
    _sock = handler.request
    _client_ip = handler.client_address[0]
    _sock.settimeout(30)
    _sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    _mon_src.client_connected = True
    print(f"\n[WS-Monitor] Room monitor connected from {_client_ip}")
    try:
        while True:
            try:
                hdr = _sock.recv(2)
                if not hdr or len(hdr) < 2:
                    break
                opcode = hdr[0] & 0x0F
                masked = (hdr[1] & 0x80) != 0
                payload_len = hdr[1] & 0x7F
                if payload_len == 126:
                    ext = _sock.recv(2)
                    payload_len = int.from_bytes(ext, 'big')
                elif payload_len == 127:
                    ext = _sock.recv(8)
                    payload_len = int.from_bytes(ext, 'big')
                mask_key = _sock.recv(4) if masked else b''
                payload = b''
                while len(payload) < payload_len:
                    chunk = _sock.recv(payload_len - len(payload))
                    if not chunk:
                        break
                    payload += chunk
                if masked and mask_key:
                    payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:  # Close
                    _sock.sendall(b'\x88\x00')
                    break
                elif opcode == 0x9:  # Ping -> Pong
                    pong = bytearray([0x8A, len(payload) if len(payload) < 126 else 0])
                    if len(payload) < 126:
                        pong[1] = len(payload)
                    pong.extend(payload)
                    _sock.sendall(bytes(pong))
                elif opcode == 0x2:  # Binary -- PCM audio data
                    _mon_src.push_audio(payload)
            except socket.timeout:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                break
    finally:
        _mon_src.client_connected = False
        _mon_src._sub_buffer = b''
        print(f"[WS-Monitor] Disconnected {_client_ip}")
    return


def handle_stream(handler, parent):
    """GET /stream -- MP3 audio stream.

    Subscribes to the shared MP3 encoder and streams chunks to the HTTP
    client as ``audio/mpeg``.  Blocks the handler thread for the lifetime
    of the connection.
    """
    import os
    # MP3 audio stream from shared encoder
    _client_ip = handler.client_address[0]
    print(f"\n[Stream] Connection from {_client_ip}")
    ev, seq = parent._subscribe_stream()
    _bytes_sent = 0
    try:
        # Wait for encoder to produce initial MP3 data
        for _wait in range(50):  # up to 5 seconds
            ev.wait(timeout=0.1)
            ev.clear()
            with parent._stream_lock:
                if parent._mp3_seq > seq:
                    break
        with parent._stream_lock:
            if parent._mp3_seq <= seq:
                print(f"[Stream] No encoder data for {_client_ip} -- aborting")
                handler.send_response(503)
                handler.end_headers()
                return

        handler.send_response(200)
        handler.send_header('Content-Type', 'audio/mpeg')
        handler.send_header('Cache-Control', 'no-cache, no-store')
        handler.send_header('Connection', 'close')
        handler.send_header('Access-Control-Allow-Origin', '*')
        handler.send_header('icy-name', 'Radio Gateway')
        handler.end_headers()
        print(f"[Stream] Streaming to {_client_ip}")

        while True:
            ev.wait(timeout=5)
            ev.clear()
            with parent._stream_lock:
                buf = parent._mp3_buffer
                cur_seq = parent._mp3_seq
                # How many new chunks since our last read
                available = cur_seq - seq
                if available > 0:
                    # Clamp to buffer size (in case we fell behind)
                    available = min(available, len(buf))
                    chunks = buf[-available:] if available < len(buf) else list(buf)
                    seq = cur_seq
                else:
                    chunks = []
            for chunk in chunks:
                handler.wfile.write(chunk)
                _bytes_sent += len(chunk)
            if chunks:
                handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as e:
        print(f"\n[Stream] Error for {_client_ip}: {e}")
    finally:
        _kb = _bytes_sent // 1024
        print(f"[Stream] Disconnected {_client_ip} ({_kb}KB sent)")
        parent._unsubscribe_stream(ev)
    return


# ---------------------------------------------------------------------------
# WebSocket Link Bridge — proxies endpoint connections through CF tunnel
# ---------------------------------------------------------------------------

def handle_ws_link(handler, parent):
    """GET /ws/link -- WebSocket bridge to the Gateway Link server (TCP 9700).

    Allows remote endpoints to connect through the Cloudflare tunnel.
    Each WS binary message = one complete link protocol frame.
    The bridge opens a local TCP connection to the link server and
    bidirectionally forwards frames between WS and TCP.
    """
    handler._upgrading_ws = True
    ws_key = handler.headers.get('Sec-WebSocket-Key', '')
    if not ws_key or handler.headers.get('Upgrade', '').lower() != 'websocket':
        handler._upgrading_ws = False
        handler.send_response(400)
        handler.end_headers()
        return

    gw = parent.gateway if parent else None
    link_port = int(getattr(gw.config, 'LINK_PORT', 9700)) if gw else 9700

    # WebSocket handshake
    _WS_MAGIC = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
    accept = base64.b64encode(
        hashlib.sha1((ws_key + _WS_MAGIC).encode()).digest()
    ).decode()
    handler.wfile.flush()
    handshake = (
        'HTTP/1.1 101 Switching Protocols\r\n'
        'Upgrade: websocket\r\n'
        'Connection: Upgrade\r\n'
        f'Sec-WebSocket-Accept: {accept}\r\n'
        '\r\n'
    )
    handler.request.sendall(handshake.encode('ascii'))
    handler.close_connection = True

    ws_sock = handler.request
    _client_ip = handler.client_address[0]
    ws_sock.settimeout(15)
    ws_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    # Open local TCP connection to link server
    try:
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.settimeout(5)
        tcp_sock.connect(('127.0.0.1', link_port))
        tcp_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        tcp_sock.settimeout(15)
    except Exception as e:
        print(f"[WS-Link] Failed to connect to link server: {e}")
        try:
            ws_sock.sendall(b'\x88\x00')  # WS close
        except Exception:
            pass
        return

    print(f"[WS-Link] Bridge connected from {_client_ip}")
    _stop = _thr.Event()

    def _tcp_to_ws():
        """Read link frames from TCP, send as WS binary messages."""
        import struct as _st
        _hdr_struct = _st.Struct('>BH')
        try:
            while not _stop.is_set():
                # Read 3-byte link frame header
                hdr = _recv_exact(tcp_sock, 3)
                if hdr is None:
                    break
                _type, _length = _hdr_struct.unpack(hdr)
                payload = _recv_exact(tcp_sock, _length) if _length else b''
                if payload is None and _length > 0:
                    break
                # Send complete frame as one WS binary message
                frame_data = hdr + (payload or b'')
                _ws_send_binary(ws_sock, frame_data)
        except (OSError, ConnectionError):
            pass
        finally:
            _stop.set()

    def _ws_to_tcp():
        """Read WS binary messages, write raw bytes to TCP."""
        try:
            while not _stop.is_set():
                data = _ws_recv_message(ws_sock)
                if data is None:
                    break
                tcp_sock.sendall(data)
        except (OSError, ConnectionError):
            pass
        finally:
            _stop.set()

    tcp_thread = _thr.Thread(target=_tcp_to_ws, daemon=True,
                              name="ws-link-tcp2ws")
    tcp_thread.start()

    try:
        _ws_to_tcp()  # runs in handler thread
    finally:
        _stop.set()
        try:
            tcp_sock.close()
        except Exception:
            pass
        try:
            ws_sock.sendall(b'\x88\x00')
        except Exception:
            pass
        tcp_thread.join(timeout=5)
        print(f"[WS-Link] Bridge disconnected {_client_ip}")


# -- WS frame helpers for link bridge --------------------------------------

def _recv_exact(sock, n):
    """Read exactly n bytes from a socket.  Returns bytes or None."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (OSError, ConnectionError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _ws_send_binary(sock, data):
    """Send a binary WS frame."""
    frame = bytearray()
    frame.append(0x82)  # FIN + binary opcode
    length = len(data)
    if length < 126:
        frame.append(length)
    elif length < 65536:
        frame.append(126)
        frame.extend(length.to_bytes(2, 'big'))
    else:
        frame.append(127)
        frame.extend(length.to_bytes(8, 'big'))
    frame.extend(data)
    sock.sendall(bytes(frame))


def _ws_recv_message(sock):
    """Read one complete WS message.  Returns payload bytes or None.

    Handles masked frames (from clients), ping/pong, and close.
    """
    hdr = _recv_exact(sock, 2)
    if not hdr:
        return None
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    payload_len = hdr[1] & 0x7F
    if payload_len == 126:
        ext = _recv_exact(sock, 2)
        if not ext:
            return None
        payload_len = int.from_bytes(ext, 'big')
    elif payload_len == 127:
        ext = _recv_exact(sock, 8)
        if not ext:
            return None
        payload_len = int.from_bytes(ext, 'big')
    mask_key = _recv_exact(sock, 4) if masked else None
    if masked and mask_key is None:
        return None
    payload = _recv_exact(sock, payload_len) if payload_len else b''
    if payload is None and payload_len > 0:
        return None
    if masked and mask_key and payload:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    if opcode == 0x08:  # Close
        try:
            sock.sendall(b'\x88\x00')
        except Exception:
            pass
        return None
    if opcode == 0x09:  # Ping → Pong
        pong = bytearray([0x8A, len(payload) if len(payload) < 126 else 0])
        pong.extend(payload)
        try:
            sock.sendall(bytes(pong))
        except Exception:
            pass
        return _ws_recv_message(sock)  # recurse for next real message
    return payload
