#!/usr/bin/env python3
"""
Mumble to Radio Gateway via AIOC
Reads configuration from gateway_config.txt
Optimized for low latency and high quality audio
"""

import sys
import os
import time
import signal
import threading
import collections
from struct import Struct
import select  # For non-blocking keyboard input
import array as _array_mod
import math as _math_mod
import numpy as np

# Check for required libraries
try:
    import hid
except ImportError:
    print("ERROR: hidapi library not found!")
    print("Install it with: pip3 install hidapi --break-system-packages")
    sys.exit(1)

# SSL compatibility shim — pymumble uses ssl.wrap_socket() (removed in Python
# 3.12) and ssl.PROTOCOL_TLSv1_2 (deprecated). Patch before importing pymumble.
import ssl as _ssl
if not hasattr(_ssl, 'wrap_socket'):
    def _ssl_wrap_compat(sock, keyfile=None, certfile=None, server_side=False,
                         cert_reqs=None, ssl_version=None, ca_certs=None,
                         do_handshake_on_connect=True, suppress_ragged_eofs=True,
                         ciphers=None, **_):
        ctx = _ssl.SSLContext(
            _ssl.PROTOCOL_TLS_SERVER if server_side else _ssl.PROTOCOL_TLS_CLIENT
        )
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
        if certfile:
            ctx.load_cert_chain(certfile, keyfile)
        if ca_certs:
            ctx.load_verify_locations(ca_certs)
        if ciphers:
            ctx.set_ciphers(ciphers)
        return ctx.wrap_socket(sock, server_side=server_side,
                               do_handshake_on_connect=do_handshake_on_connect,
                               suppress_ragged_eofs=suppress_ragged_eofs)
    _ssl.wrap_socket = _ssl_wrap_compat
if not hasattr(_ssl, 'PROTOCOL_TLSv1_2'):
    _ssl.PROTOCOL_TLSv1_2 = _ssl.PROTOCOL_TLS_CLIENT

try:
    from pymumble_py3 import Mumble
    from pymumble_py3.callbacks import PYMUMBLE_CLBK_SOUNDRECEIVED, PYMUMBLE_CLBK_TEXTMESSAGERECEIVED
    import pymumble_py3.constants as mumble_constants
except ImportError:
    try:
        from pymumble import Mumble
        from pymumble.callbacks import PYMUMBLE_CLBK_SOUNDRECEIVED, PYMUMBLE_CLBK_TEXTMESSAGERECEIVED
        import pymumble.constants as mumble_constants
    except ImportError:
        print("ERROR: pymumble library not found!")
        print("Install with: pip3 install pymumble --break-system-packages")
        print("          or: pip3 install pymumble-py3 --break-system-packages")
        sys.exit(1)

try:
    import pyaudio
except ImportError:
    print("ERROR: pyaudio library not found!")
    print("Install it with: sudo apt-get install python3-pyaudio")
    sys.exit(1)

class Config:
    """Configuration loaded from gateway_config.txt"""
    def __init__(self, config_file="gateway_config.txt"):
        self.config_file = config_file
        self.load_config()
    
    def load_config(self):
        """Load configuration from file"""
        # Default values
        defaults = {
            'MUMBLE_SERVER': '192.168.2.126',
            'MUMBLE_PORT': 64738,
            'MUMBLE_USERNAME': 'RadioGateway',
            'MUMBLE_PASSWORD': '',
            'MUMBLE_CHANNEL': '',
            'AUDIO_RATE': 48000,
            'AUDIO_CHUNK_SIZE': 9600,
            'AUDIO_CHANNELS': 1,
            'AUDIO_BITS': 16,
            'MUMBLE_BITRATE': 96000,
            'MUMBLE_VBR': True,
            'MUMBLE_JITTER_BUFFER': 10,
            'AIOC_PTT_CHANNEL': 3,
            'PTT_RELEASE_DELAY': 0.5,
            'PTT_ACTIVATION_DELAY': 0.1,
            'AIOC_VID': 0x1209,
            'AIOC_PID': 0x7388,
            'AIOC_INPUT_DEVICE': -1,
            'AIOC_OUTPUT_DEVICE': -1,
            'ENABLE_AGC': False,
            'ENABLE_NOISE_SUPPRESSION': False,
            'NOISE_SUPPRESSION_METHOD': 'none',
            'NOISE_SUPPRESSION_STRENGTH': 0.5,
            'ENABLE_NOISE_GATE': False,
            'NOISE_GATE_THRESHOLD': -40,
            'NOISE_GATE_ATTACK': 0.01,  # float (seconds)
            'NOISE_GATE_RELEASE': 0.1,  # float (seconds)
            'ENABLE_HIGHPASS_FILTER': False,
            'HIGHPASS_CUTOFF_FREQ': 300,
            'ENABLE_ECHO_CANCELLATION': False,
            'INPUT_VOLUME': 1.0,
            'OUTPUT_VOLUME': 1.0,
            'MUMBLE_LOOP_RATE': 0.01,
            'MUMBLE_STEREO': False,
            'MUMBLE_RECONNECT': True,
            'MUMBLE_DEBUG': False,
            'NETWORK_TIMEOUT': 10,
            'TCP_NODELAY': True,
            'VERBOSE_LOGGING': False,
            'STATUS_UPDATE_INTERVAL': 1,  # seconds
            'MAX_MUMBLE_BUFFER_SECONDS': 1.0,
            'BUFFER_MANAGEMENT_VERBOSE': False,
            'ENABLE_VAD': True,
            'VAD_THRESHOLD': -45,
            'VAD_ATTACK': 0.05,  # float (seconds)
            'VAD_RELEASE': 1,    # float (seconds)
            'VAD_MIN_DURATION': 0.25,  # float (seconds)
            'ENABLE_STREAM_HEALTH': False,
            'STREAM_RESTART_INTERVAL': 60,
            'STREAM_RESTART_IDLE_TIME': 3,
            'ENABLE_VOX': False,
            'VOX_THRESHOLD': -30,
            'VOX_ATTACK_TIME': 0.05,  # float (seconds)
            'VOX_RELEASE_TIME': 0.5,  # float (seconds)
            # File Playback
            'ENABLE_PLAYBACK': False,
            'PLAYBACK_DIRECTORY': './audio/',
            'PLAYBACK_ANNOUNCEMENT_FILE': '',
            'PLAYBACK_ANNOUNCEMENT_INTERVAL': 0,  # seconds, 0 = disabled
            'PLAYBACK_VOLUME': 2.0,               # float (multiplier; >1.0 boosts, audio is clipped to int16 range)
            # Text-to-Speech and Text Commands (Phase 4)
            'ENABLE_TTS': True,
            'ENABLE_TEXT_COMMANDS': True,
            'TTS_VOLUME': 1.0,  # Volume multiplier for TTS audio (1.0 = normal, 2.0 = double, 3.0 = triple)
            'PTT_TTS_DELAY': 1.0,   # Silence padding before TTS (seconds) to prevent cutoff
            'PTT_ANNOUNCEMENT_DELAY': 0.5,  # Seconds after PTT key-up before announcement audio starts
            # SDR Integration
            'ENABLE_SDR': True,
            'SDR_DEVICE_NAME': 'hw:6,1',  # ALSA device name (e.g., 'Loopback', 'hw:5,1')
            'SDR_DUCK': True,             # Duck SDR: silence SDR when higher priority source is active
            'SDR_MIX_RATIO': 1.0,        # Volume/mix ratio when ducking is disabled (1.0 = full volume)
            'SDR_DISPLAY_GAIN': 1.0,     # Display sensitivity multiplier (1.0 = normal, higher = more sensitive bar)
            'SDR_AUDIO_BOOST': 1.0,      # Actual audio volume boost (1.0 = no change, 2.0 = 2x louder)
            'SDR_BUFFER_MULTIPLIER': 8,  # Buffer size multiplier (8 = 8x normal buffer for smoother playback)
            'SDR_PRIORITY': 1,           # SDR priority for ducking (1 = higher priority, 2 = lower priority)
            # SDR2 Integration (second SDR receiver)
            'ENABLE_SDR2': True,
            'SDR2_DEVICE_NAME': 'hw:4,1',
            'SDR2_DUCK': True,
            'SDR2_MIX_RATIO': 1.0,
            'SDR2_DISPLAY_GAIN': 1.0,
            'SDR2_AUDIO_BOOST': 1.0,
            'SDR2_BUFFER_MULTIPLIER': 8,
            'SDR2_PRIORITY': 2,          # SDR2 priority for ducking (1 = higher, 2 = lower)
            # Signal Detection Hysteresis (prevents stuttering from rapid on/off)
            'SIGNAL_ATTACK_TIME': 0.5,   # Seconds of CONTINUOUS signal required before a source switch is allowed
            'SIGNAL_RELEASE_TIME': 1.0,  # Seconds of continuous silence required before switching back
            'SWITCH_PADDING_TIME': 1.0,  # Seconds of silence inserted at each transition (duck-out and duck-in)
            # EchoLink Integration (Phase 3B)
            'ENABLE_ECHOLINK': False,
            'ECHOLINK_RX_PIPE': '/tmp/echolink_rx',
            'ECHOLINK_TX_PIPE': '/tmp/echolink_tx',
            'ECHOLINK_TO_MUMBLE': True,
            'ECHOLINK_TO_RADIO': False,
            'RADIO_TO_ECHOLINK': True,
            'MUMBLE_TO_ECHOLINK': False,
            # Streaming Output (Phase 3A)
            'ENABLE_STREAM_OUTPUT': False,
            'STREAM_SERVER': 'localhost',
            'STREAM_PORT': 8000,
            'STREAM_PASSWORD': 'hackme',
            'STREAM_MOUNT': '/radio',
            'STREAM_NAME': 'Radio Gateway',
            'STREAM_DESCRIPTION': 'Radio to Mumble Gateway',
            'STREAM_BITRATE': 64,
            'STREAM_FORMAT': 'mp3',
        }
        
        # Set defaults
        for key, value in defaults.items():
            setattr(self, key, value)
        
        # Try to load from file
        if not os.path.exists(self.config_file):
            print(f"WARNING: Config file '{self.config_file}' not found, using defaults")
            return
        
        try:
            with open(self.config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse key = value
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Strip inline comments (everything after #)
                        if '#' in value:
                            value = value.split('#')[0].strip()
                        
                        # Skip if value is empty after stripping comments
                        if not value:
                            continue
                        
                        # Convert to appropriate type
                        if key in defaults:
                            default_type = type(defaults[key])
                            
                            if default_type == bool:
                                value = value.lower() in ('true', 'yes', '1', 'on')
                            elif default_type == int:
                                # Handle hex values for VID/PID
                                if value.startswith('0x'):
                                    value = int(value, 16)
                                else:
                                    value = int(value)
                            elif default_type == float:
                                value = float(value)
                            # else keep as string
                            
                            setattr(self, key, value)
                        else:
                            # Key not in defaults - try to infer type
                            # Try float first (works for both int and float strings)
                            try:
                                if '.' in value:
                                    value = float(value)
                                else:
                                    value = int(value)
                            except ValueError:
                                # Not a number, check for boolean
                                if value.lower() in ('true', 'false', 'yes', 'no', 'on', 'off'):
                                    value = value.lower() in ('true', 'yes', 'on')
                                # else keep as string
                            
                            setattr(self, key, value)
            
            print(f"✓ Configuration loaded from '{self.config_file}'")
            
        except Exception as e:
            print(f"WARNING: Error loading config file: {e}")
            print("Using default values")

# ============================================================================
# AUDIO SOURCE SYSTEM - Multi-Source Support
# ============================================================================

class AudioSource:
    """Base class for all audio sources"""
    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.enabled = True
        self.priority = 0  # Lower = higher priority
        self.volume = 1.0
        self.ptt_control = False  # Can this source trigger PTT?
        
    def initialize(self):
        """Initialize the audio source. Return True on success."""
        return True
    
    def cleanup(self):
        """Clean up resources"""
        pass
    
    def get_audio(self, chunk_size):
        """
        Get audio chunk from this source.
        Returns: (audio_bytes, should_trigger_ptt)
        audio_bytes: PCM audio data or None
        should_trigger_ptt: True if this audio should key PTT
        """
        return None, False
    
    def is_active(self):
        """Return True if source currently has audio to transmit"""
        return False
    
    def get_status(self):
        """Return status string for display"""
        return f"{self.name}: {'ON' if self.enabled else 'OFF'}"


class AIOCRadioSource(AudioSource):
    """Radio audio source via AIOC device"""
    def __init__(self, config, gateway):
        super().__init__("Radio", config)
        self.gateway = gateway  # Reference to main gateway for shared resources
        self.priority = 1  # Lower priority than file playback
        self.ptt_control = False  # Radio RX doesn't control PTT
        self.volume = config.INPUT_VOLUME
        
    def get_audio(self, chunk_size):
        """Get audio from radio via AIOC input stream"""
        # Reset the full-duplex cache every call so stale data is never forwarded
        self._rx_cache = None

        if not self.gateway.input_stream or self.gateway.restarting_stream:
            return None, False

        try:
            # Always read from the AIOC stream, even when muted.
            # If we skip the read while muted the PyAudio buffer fills up,
            # input_stream.is_active() returns False, the audio_transmit_loop
            # raises "Stream inactive", and the mixer never runs — which means
            # SDRSource.get_audio() is never called and the SDR bar freezes.
            try:
                data = self.gateway.input_stream.read(chunk_size, exception_on_overflow=False)
            except IOError as io_err:
                # Handle buffer overflow gracefully; absorb all other AIOC errors
                # so they never propagate through the mixer and interrupt SDR audio.
                if io_err.errno == -9981:  # Input overflow
                    try:
                        self.gateway.input_stream.read(chunk_size * 2, exception_on_overflow=False)
                    except:
                        pass
                return None, False
            except Exception:
                return None, False

            # Update capture time so stream-health checks stay happy
            self.gateway.last_audio_capture_time = time.time()
            self.gateway.last_successful_read = time.time()
            self.gateway.audio_capture_active = True

            # When muted: buffer drained (stream stays alive) but return nothing.
            # _rx_cache stays None so full-duplex forwarding also respects mute.
            if self.gateway.rx_muted:
                return None, False

            # Calculate audio level (for status display)
            current_level = self.gateway.calculate_audio_level(data)
            if current_level > self.gateway.tx_audio_level:
                self.gateway.tx_audio_level = current_level
            else:
                self.gateway.tx_audio_level = int(self.gateway.tx_audio_level * 0.7 + current_level * 0.3)

            # Apply volume if needed
            if self.volume != 1.0 and data:
                arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                data = np.clip(arr * self.volume, -32768, 32767).astype(np.int16).tobytes()

            # Apply audio processing
            data = self.gateway.process_audio_for_mumble(data)

            # Cache the processed audio for full-duplex forwarding during PTT.
            # The transmit loop reads this directly so RX → Mumble works even if
            # VAD is blocking and regardless of ptt_active timing in the mixer.
            self._rx_cache = data

            # Check VAD - always call to keep the envelope/state current.
            should_transmit = self.gateway.check_vad(data)

            # Full-duplex: when the gateway is transmitting (PTT active), bypass
            # the VAD gate so radio RX still flows to Mumble via the normal path.
            if self.gateway.ptt_active:
                should_transmit = True

            if should_transmit:
                return data, False  # Don't trigger PTT (radio RX)
            else:
                return None, False
                
        except Exception as e:
            # Log the error so we can see what's wrong
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[RadioSource] Error reading audio: {type(e).__name__}: {e}")
            return None, False
    
    def is_active(self):
        """Radio is active if VAD is detecting signal"""
        return self.gateway.vad_active


class FilePlaybackSource(AudioSource):
    """Audio file playback source"""
    def __init__(self, config, gateway):
        super().__init__("FilePlayback", config)
        self.gateway = gateway
        self.priority = 0  # HIGHEST priority - announcements interrupt radio
        self.ptt_control = True  # File playback triggers PTT
        self.volume = getattr(config, 'PLAYBACK_VOLUME', 1.0)
        
        # Playback state
        self.current_file = None
        self.file_data = None
        self.file_position = 0
        self.playlist = []  # Queue of files to play
        
        # Periodic announcement - auto-detect station_id file
        self.last_announcement_time = 0
        self.announcement_interval = config.PLAYBACK_ANNOUNCEMENT_INTERVAL if hasattr(config, 'PLAYBACK_ANNOUNCEMENT_INTERVAL') else 0
        self.announcement_directory = config.PLAYBACK_DIRECTORY if hasattr(config, 'PLAYBACK_DIRECTORY') else './audio/'
        
        # File status tracking for status line indicators (0-9 = 10 files)
        self.file_status = {
            '0': {'exists': False, 'playing': False, 'path': None},  # station_id
            '1': {'exists': False, 'playing': False, 'path': None},
            '2': {'exists': False, 'playing': False, 'path': None},
            '3': {'exists': False, 'playing': False, 'path': None},
            '4': {'exists': False, 'playing': False, 'path': None},
            '5': {'exists': False, 'playing': False, 'path': None},
            '6': {'exists': False, 'playing': False, 'path': None},
            '7': {'exists': False, 'playing': False, 'path': None},
            '8': {'exists': False, 'playing': False, 'path': None},
            '9': {'exists': False, 'playing': False, 'path': None}
        }
        self.check_file_availability()
    
    def check_file_availability(self):
        """Scan audio directory and intelligently load files"""
        import os
        import glob
        
        if not os.path.exists(self.announcement_directory):
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Playback] Audio directory not found: {self.announcement_directory}")
            return
        
        # Storage for found files
        file_map = {}  # key -> (filepath, filename)
        
        # Step 1: Look for station_id (key 0)
        # Priority: station_id.mp3 > station_id.wav > station_id.*
        station_id_found = False
        for ext in ['.mp3', '.wav', '.ogg', '.flac', '.m4a']:
            path = os.path.join(self.announcement_directory, f'station_id{ext}')
            if os.path.exists(path):
                file_map['0'] = (path, os.path.basename(path))
                station_id_found = True
                break
        
        # Step 2: Look for numbered files (1_ through 9_)
        # Example: 1_welcome.mp3, 2_emergency.wav, etc.
        all_files = []
        for ext in ['*.mp3', '*.wav', '*.ogg', '*.flac', '*.m4a']:
            all_files.extend(glob.glob(os.path.join(self.announcement_directory, ext)))
        
        # Sort files alphabetically for consistent loading
        all_files.sort()
        
        # First pass: Look for files with number prefixes (1_ through 9_)
        for filepath in all_files:
            filename = os.path.basename(filepath)
            
            # Skip station_id files
            if filename.startswith('station_id'):
                continue
            
            # Check for number prefix (1_ through 9_)
            if len(filename) >= 2 and filename[0].isdigit() and filename[1] == '_':
                key = filename[0]
                if key in '123456789' and key not in file_map:
                    file_map[key] = (filepath, filename)
        
        # Second pass: If slots still empty, fill with any remaining files
        unassigned_files = [f for f in all_files 
                           if os.path.basename(f) not in [v[1] for v in file_map.values()]
                           and not os.path.basename(f).startswith('station_id')]
        
        # Fill empty slots in order (1-9)
        for filepath in unassigned_files:
            # Find next empty slot
            assigned = False
            for slot in range(1, 10):
                key = str(slot)
                if key not in file_map:
                    file_map[key] = (filepath, os.path.basename(filepath))
                    assigned = True
                    break
            
            if not assigned:
                # All slots 1-9 are full
                break
        
        # Step 3: Update file_status with found files
        for key in '0123456789':
            if key in file_map:
                filepath, filename = file_map[key]
                self.file_status[key]['exists'] = True
                self.file_status[key]['path'] = filepath
                self.file_status[key]['filename'] = filename
        
        # Step 4: Print file mapping (will be displayed before status bar)
        self.file_mapping_display = self._generate_file_mapping_display(file_map, station_id_found)
    
    def _generate_file_mapping_display(self, file_map, station_id_found):
        """Generate the file mapping display string"""
        lines = []
        lines.append("=" * 60)
        lines.append("FILE PLAYBACK MAPPING")
        lines.append("=" * 60)
        
        if not file_map:
            lines.append("No audio files found in: " + self.announcement_directory)
            lines.append("Supported formats: .mp3, .wav, .ogg, .flac, .m4a")
            lines.append("")
            lines.append("Naming conventions:")
            lines.append("  station_id.mp3 or station_id.wav  → Key [0]")
            lines.append("  1_filename.mp3                    → Key [1]")
            lines.append("  2_filename.wav                    → Key [2]")
            lines.append("  Or place any audio files and they'll auto-assign to keys 1-9")
            lines.append("=" * 60)
            return "\n".join(lines)
        
        # Show all keys 1-9 then 0 (matching status bar order)
        # Format: "Key [N]: filename.mp3" or "Key [N]: <none>"
        
        # Keys 1-9 - Announcements
        for key in '123456789':
            if key in file_map:
                lines.append(f"Key [{key}]: {file_map[key][1]}")
            else:
                lines.append(f"Key [{key}]: <none>")
        
        # Key 0 - Station ID (at end, matching status bar)
        if '0' in file_map:
            lines.append(f"Key [0]: {file_map['0'][1]}")
        else:
            lines.append(f"Key [0]: <none>")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def print_file_mapping(self):
        """Print the file mapping (call this just before status bar starts)"""
        if hasattr(self, 'file_mapping_display'):
            print(self.file_mapping_display)
    
    def get_file_status_string(self):
        """Get status indicator string for display"""
        # ANSI color codes
        WHITE = '\033[97m'
        GREEN = '\033[92m'
        RED = '\033[91m'
        RESET = '\033[0m'
        
        status_str = ""
        # Show all 10 slots: 1-9 then 0 (station_id at end) - no brackets to save space
        for key in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']:
            if self.file_status[key]['playing']:
                # Red when playing
                status_str += f"{RED}{key}{RESET}"
            elif self.file_status[key]['exists']:
                # Green when file exists
                status_str += f"{GREEN}{key}{RESET}"
            else:
                # White when no file
                status_str += f"{WHITE}{key}{RESET}"
        
        return status_str
        
    def queue_file(self, filepath):
        """Add a file to the playback queue. Returns True if file exists, False otherwise."""
        import os
        
        # Check if file exists
        full_path = filepath
        if not os.path.exists(filepath):
            # Try with announcement directory prefix
            alt_path = os.path.join(self.announcement_directory, filepath)
            if os.path.exists(alt_path):
                full_path = alt_path
            else:
                # File not found
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"\n[Playback] File not found: {filepath}")
                    print(f"  Looked in: {os.path.abspath(filepath)}")
                    print(f"  Looked in: {os.path.abspath(alt_path)}")
                return False
        
        # File exists, queue it
        self.playlist.append(full_path)
        if self.gateway.config.VERBOSE_LOGGING:
            print(f"\n[Playback] ✓ Queued: {os.path.basename(full_path)} ({len(self.playlist)} in queue)")
        return True
    
    def load_next_file(self):
        """Load the next file from the queue"""
        if not self.playlist:
            return False
        
        filepath = self.playlist.pop(0)
        return self.load_file(filepath)
    
    def stop_playback(self):
        """Stop current playback and clear queue"""
        # Mark current file as not playing
        if self.current_file:
            # Find which key this file belongs to
            for key, info in self.file_status.items():
                if info['path'] == self.current_file:
                    self.file_status[key]['playing'] = False
                    break
        
        # Clear current playback
        self.current_file = None
        self.file_data = None
        self.file_position = 0
        
        # Clear queue
        self.playlist.clear()
        
        if self.gateway.config.VERBOSE_LOGGING:
            print("\n[Playback] ✓ Stopped playback and cleared queue")
    
    def load_file(self, filepath):
        """Load an audio file for playback (supports WAV, MP3, OGG, FLAC, M4A via soundfile)"""
        try:
            import os
            
            # Check if file exists
            if not os.path.exists(filepath):
                # Try with announcement directory prefix
                alt_path = os.path.join(self.announcement_directory, filepath)
                if os.path.exists(alt_path):
                    filepath = alt_path
                else:
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"\n[Playback] File not found: {filepath}")
                    return False
            
            # Get file extension
            file_ext = os.path.splitext(filepath)[1].lower()
            
            # Determine which file number this is for status tracking
            filename = os.path.basename(filepath)
            file_key = None
            
            # Check against all stored paths to find the key
            for key, info in self.file_status.items():
                if info['path'] == filepath:
                    file_key = key
                    break
            
            # Try soundfile first (best option for Python 3.13)
            try:
                import soundfile as sf
                import numpy as np
                
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"\n[Playback] Loading {os.path.basename(filepath)} (using soundfile)...")
                
                # Read audio file - soundfile handles MP3 via libsndfile + ffmpeg
                audio_data, sample_rate = sf.read(filepath, dtype='int16')
                
                # Get file info
                channels = 1 if len(audio_data.shape) == 1 else audio_data.shape[1]
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"  Format: {sample_rate}Hz, {channels}ch, 16-bit")
                
                # Convert stereo to mono if needed
                if channels == 2:
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"  Converting stereo to mono...")
                    audio_data = audio_data.mean(axis=1).astype('int16')
                elif channels > 2:
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"  Converting {channels} channels to mono...")
                    audio_data = audio_data.mean(axis=1).astype('int16')
                
                # Resample if needed
                if sample_rate != self.config.AUDIO_RATE:
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"  Resampling: {sample_rate}Hz → {self.config.AUDIO_RATE}Hz")
                    try:
                        import resampy
                        # resampy works with float data
                        audio_float = audio_data.astype('float32') / 32768.0
                        audio_resampled = resampy.resample(audio_float, sample_rate, self.config.AUDIO_RATE)
                        audio_data = (audio_resampled * 32768.0).astype('int16')
                    except ImportError:
                        # Fallback: simple linear interpolation
                        if self.gateway.config.VERBOSE_LOGGING:
                            print(f"    (using basic resampling - install resampy for better quality)")
                        ratio = self.config.AUDIO_RATE / sample_rate
                        new_length = int(len(audio_data) * ratio)
                        indices = (np.arange(new_length) / ratio).astype(int)
                        audio_data = audio_data[indices]
                
                # Convert to bytes
                self.file_data = audio_data.tobytes()
                self.file_position = 0
                self.current_file = filepath
                
                # Mark file as playing
                if file_key:
                    self.file_status[file_key]['playing'] = True
                
                duration_sec = len(audio_data) / self.config.AUDIO_RATE
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"  ✓ Loaded {duration_sec:.1f}s of audio")
                
                return True
                
            except ImportError:
                # soundfile not available, try wave module (WAV only)
                if file_ext != '.wav':
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"\n[Playback] Error: {file_ext.upper()} not supported without soundfile")
                        print(f"  Install soundfile for multi-format support:")
                        print(f"    pip install soundfile resampy --break-system-packages")
                        print(f"  Also install system library:")
                        print(f"    sudo apt-get install libsndfile1")
                        print(f"\n  Or convert to WAV:")
                        print(f"    ffmpeg -i {os.path.basename(filepath)} -ar 48000 -ac 1 output.wav")
                    return False
                
                # Fall back to wave module for WAV files
                import wave
                
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"\n[Playback] Loading {os.path.basename(filepath)} (WAV only)...")
                
                with wave.open(filepath, 'rb') as wf:
                    # Get file info
                    channels = wf.getnchannels()
                    rate = wf.getframerate()
                    width = wf.getsampwidth()
                    frames = wf.getnframes()
                    
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"  Format: {rate}Hz, {channels}ch, {width*8}-bit")
                    
                    # Check format compatibility
                    needs_conversion = False
                    
                    if channels != self.config.AUDIO_CHANNELS:
                        if self.gateway.config.VERBOSE_LOGGING:
                            print(f"  ⚠ Warning: {channels} channel(s), expected {self.config.AUDIO_CHANNELS}")
                            print(f"    File may not play correctly")
                        needs_conversion = True
                    
                    if rate != self.config.AUDIO_RATE:
                        if self.gateway.config.VERBOSE_LOGGING:
                            print(f"  ⚠ Warning: {rate}Hz, expected {self.config.AUDIO_RATE}Hz")
                            print(f"    Audio will play at wrong speed!")
                        needs_conversion = True
                    
                    if width != 2:  # 16-bit = 2 bytes
                        if self.gateway.config.VERBOSE_LOGGING:
                            print(f"  ⚠ Warning: {width*8}-bit, expected 16-bit")
                        needs_conversion = True
                    
                    if needs_conversion and self.gateway.config.VERBOSE_LOGGING:
                        print(f"  Convert with: ffmpeg -i {os.path.basename(filepath)} -ar 48000 -ac 1 -sample_fmt s16 output.wav")
                        print(f"  Or install soundfile for automatic conversion")
                    
                    # Read entire file into memory
                    self.file_data = wf.readframes(frames)
                    self.file_position = 0
                    self.current_file = filepath
                    
                    # Mark file as playing
                    if file_key:
                        self.file_status[file_key]['playing'] = True
                    
                    duration_sec = frames / rate
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"  ✓ Loaded {duration_sec:.1f}s of audio")
                    
                    return True
                
        except Exception as e:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Playback] Error loading {filepath}: {e}")
            return False
    
    def check_periodic_announcement(self):
        """Check if it's time for a periodic announcement"""
        # Use auto-detected station_id file (key 0)
        if self.announcement_interval <= 0 or not self.file_status['0']['exists']:
            return
        
        current_time = time.time()
        if self.last_announcement_time == 0:
            self.last_announcement_time = current_time
            return
        
        # Check if enough time has passed
        elapsed = current_time - self.last_announcement_time
        if elapsed >= self.announcement_interval:
            # Check if radio is idle
            if not self.gateway.vad_active:
                # Queue the station_id file
                station_id_path = self.file_status['0']['path']
                if station_id_path:
                    self.queue_file(station_id_path)
                    self.last_announcement_time = current_time
                    if self.gateway.config.VERBOSE_LOGGING:
                        print(f"\n[Playback] Periodic station ID triggered (every {self.announcement_interval}s)")
    
    def get_audio(self, chunk_size):
        """Get audio chunk from file playback"""
        import os
        
        # Check for periodic announcements
        self.check_periodic_announcement()
        
        # If no file is playing, try to load next from queue
        if not self.current_file and self.playlist:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[FilePlayback] Loading file from queue (queue length: {len(self.playlist)})")
            if not self.load_next_file():
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"[FilePlayback] Failed to load file from queue")
                return None, False
            else:
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"[FilePlayback] Successfully loaded: {os.path.basename(self.current_file)}")
        
        # No file playing
        if not self.file_data:
            return None, False

        # Calculate chunk size in bytes (16-bit = 2 bytes per sample)
        chunk_bytes = chunk_size * self.config.AUDIO_CHANNELS * 2

        # During the PTT announcement delay the radio is keying up.  Return silence
        # without advancing the file position so no audio is lost.
        if getattr(self.gateway, 'announcement_delay_active', False):
            return b'\x00' * chunk_bytes, True
        
        # Check if we have enough data left
        if self.file_position >= len(self.file_data):
            # File finished
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Playback] Finished: {os.path.basename(self.current_file) if self.current_file else 'unknown'}")
            
            # Reset volume to configured level (in case TTS boosted it)
            self.volume = getattr(self.gateway.config, 'PLAYBACK_VOLUME', 1.0)
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"[Playback] Volume reset to {self.volume}x")
            
            # Mark file as not playing by matching path
            if self.current_file:
                for key, info in self.file_status.items():
                    if info['path'] == self.current_file:
                        self.file_status[key]['playing'] = False
                        break
            
            self.current_file = None
            self.file_data = None
            self.file_position = 0
            
            # Try to load next file
            if self.playlist:
                if not self.load_next_file():
                    return None, False
                # Continue with the new file
            else:
                return None, False
        
        # Get chunk from file
        end_pos = min(self.file_position + chunk_bytes, len(self.file_data))
        chunk = self.file_data[self.file_position:end_pos]
        self.file_position = end_pos
        
        # Pad with silence if chunk is too short
        if len(chunk) < chunk_bytes:
            chunk += b'\x00' * (chunk_bytes - len(chunk))
        
        # Apply volume
        if self.volume != 1.0:
            arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            chunk = np.clip(arr * self.volume, -32768, 32767).astype(np.int16).tobytes()

        # Small yield to prevent file playback from overwhelming other threads
        # (especially important now that we removed priority scheduling)
        import time
        time.sleep(0.001)  # 1ms - negligible latency but helps system balance
        
        # File playback triggers PTT - ALWAYS
        return chunk, True
    
    def is_active(self):
        """Playback is active if file is currently playing"""
        return self.current_file is not None
    
    def get_status(self):
        """Return status string for display"""
        if self.current_file:
            import os
            filename = os.path.basename(self.current_file)
            progress = (self.file_position / len(self.file_data)) * 100 if self.file_data else 0
            return f"{self.name}: Playing {filename} ({progress:.0f}%)"
        elif self.playlist:
            return f"{self.name}: {len(self.playlist)} queued"
        else:
            return f"{self.name}: Idle"


class EchoLinkSource(AudioSource):
    """EchoLink audio input via TheLinkBox IPC"""
    def __init__(self, config, gateway):
        super().__init__("EchoLink", config)
        self.gateway = gateway
        self.priority = 2  # After Radio (1), before Files (0)
        self.ptt_control = False  # EchoLink doesn't trigger radio PTT
        self.volume = 1.0
        
        # IPC state
        self.rx_pipe = None
        self.tx_pipe = None
        self.connected = False
        self.last_audio_time = 0
        
        # Try to setup IPC
        if config.ENABLE_ECHOLINK:
            self.setup_ipc()
    
    def setup_ipc(self):
        """Setup named pipes for TheLinkBox IPC"""
        import os
        import errno
        
        try:
            rx_path = self.config.ECHOLINK_RX_PIPE
            tx_path = self.config.ECHOLINK_TX_PIPE
            
            # Create named pipes if they don't exist
            for pipe_path in [rx_path, tx_path]:
                if not os.path.exists(pipe_path):
                    try:
                        os.mkfifo(pipe_path)
                        if self.gateway.config.VERBOSE_LOGGING:
                            print(f"  Created FIFO: {pipe_path}")
                    except OSError as e:
                        if e.errno != errno.EEXIST:
                            raise
            
            # Open pipes (non-blocking mode)
            import fcntl
            
            # RX pipe (read from TheLinkBox)
            self.rx_pipe = open(rx_path, 'rb', buffering=0)
            flags = fcntl.fcntl(self.rx_pipe, fcntl.F_GETFL)
            fcntl.fcntl(self.rx_pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            # TX pipe (write to TheLinkBox)
            self.tx_pipe = open(tx_path, 'wb', buffering=0)
            flags = fcntl.fcntl(self.tx_pipe, fcntl.F_GETFL)
            fcntl.fcntl(self.tx_pipe, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            self.connected = True
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"  ✓ EchoLink IPC connected via named pipes")
                print(f"    RX: {rx_path}")
                print(f"    TX: {tx_path}")
            
        except Exception as e:
            print(f"  ⚠ EchoLink IPC setup failed: {e}")
            print(f"    Make sure TheLinkBox is running and configured")
            self.connected = False
    
    def get_audio(self, chunk_size):
        """Get audio from EchoLink via named pipe"""
        if not self.connected or not self.rx_pipe:
            return None, False
        
        try:
            chunk_bytes = chunk_size * self.config.AUDIO_CHANNELS * 2  # 16-bit
            data = self.rx_pipe.read(chunk_bytes)
            
            if data and len(data) == chunk_bytes:
                self.last_audio_time = time.time()
                
                # Apply volume
                if self.volume != 1.0:
                    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                    data = np.clip(arr * self.volume, -32768, 32767).astype(np.int16).tobytes()

                return data, False  # No PTT control
            else:
                return None, False
                
        except BlockingIOError:
            # No data available (non-blocking read)
            return None, False
        except Exception as e:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[EchoLink] Read error: {e}")
            return None, False
    
    def send_audio(self, audio_data):
        """Send audio to EchoLink via named pipe"""
        if not self.connected or not self.tx_pipe:
            return
        
        try:
            self.tx_pipe.write(audio_data)
            self.tx_pipe.flush()
        except BlockingIOError:
            # Pipe full, skip this chunk
            pass
        except Exception as e:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[EchoLink] Write error: {e}")
    
    def is_active(self):
        """EchoLink is active if we've received audio recently"""
        if not self.connected:
            return False
        return (time.time() - self.last_audio_time) < 2.0
    
    def cleanup(self):
        """Close IPC connections"""
        if self.rx_pipe:
            try:
                self.rx_pipe.close()
            except:
                pass
        if self.tx_pipe:
            try:
                self.tx_pipe.close()
            except:
                pass


class SDRSource(AudioSource):
    """SDR receiver audio input via ALSA loopback"""
    def __init__(self, config, gateway, name="SDR1", sdr_priority=1):
        super().__init__(name, config)
        self.gateway = gateway
        self.priority = 2  # Audio mixer priority (lower than radio/files)
        self.sdr_priority = sdr_priority  # Priority for SDR-to-SDR ducking (1=higher, 2=lower)
        self.ptt_control = False  # SDR doesn't trigger PTT
        self.volume = 1.0
        self.mix_ratio = 1.0  # Volume applied when ducking is disabled
        self.duck = True      # When True: silence SDR if higher priority source is active
        self.enabled = True   # Start enabled by default
        self.muted = False    # Can be muted independently
        
        # Audio stream
        self.input_stream = None
        self.pyaudio = None
        self.audio_level = 0
        self.last_read_time = 0
        
        # Dropout tracking
        self.dropout_count = 0
        self.overflow_count = 0
        self.total_reads = 0
        self.last_stats_time = time.time()

        # Background reader thread state
        self._chunk_queue = collections.deque(maxlen=4)  # ~800ms of headroom
        self._reader_running = False
        self._reader_thread = None

        if self.config.VERBOSE_LOGGING:
            print(f"[{self.name}] Initializing SDR audio source...")
    
    def setup_audio(self):
        """Initialize SDR audio input from ALSA loopback"""
        try:
            import pyaudio
            self.pyaudio = pyaudio.PyAudio()
            
            # Find the SDR loopback device
            device_index = None
            device_name = None
            
            # Determine which config parameter to use based on SDR name
            if self.name == "SDR2":
                config_device_attr = 'SDR2_DEVICE_NAME'
                config_buffer_attr = 'SDR2_BUFFER_MULTIPLIER'
            else:  # SDR1 or legacy "SDR"
                config_device_attr = 'SDR_DEVICE_NAME'
                config_buffer_attr = 'SDR_BUFFER_MULTIPLIER'
            
            if hasattr(self.config, config_device_attr) and getattr(self.config, config_device_attr):
                # User specified a device name
                target_name = getattr(self.config, config_device_attr)
                
                if self.config.VERBOSE_LOGGING:
                    print(f"[{self.name}] Searching for device matching: {target_name}")
                    print(f"[{self.name}] Available input devices:")
                
                # Search for matching device
                for i in range(self.pyaudio.get_device_count()):
                    info = self.pyaudio.get_device_info_by_index(i)
                    if info['maxInputChannels'] > 0:
                        if self.config.VERBOSE_LOGGING:
                            print(f"[{self.name}]   [{i}] {info['name']} (in:{info['maxInputChannels']})")
                        
                        # Match by name substring OR by hw device number
                        # Examples:
                        #   "Loopback" matches "Loopback: PCM (hw:2,0)"
                        #   "hw:2,0" matches "Loopback: PCM (hw:2,0)"
                        #   "hw:Loopback,2,0" extracts "hw:2,0" and matches
                        name_lower = info['name'].lower()
                        
                        # Extract hw device from target if format is hw:Name,X,Y
                        if target_name.startswith('hw:') and ',' in target_name:
                            # Extract just the hw:X,Y part (skip the name)
                            parts = target_name.split(',')
                            if len(parts) >= 2:
                                # hw:Loopback,2,0 -> look for hw:2,0
                                hw_device = f"hw:{parts[-2]},{parts[-1]}"
                                if hw_device in name_lower:
                                    device_index = i
                                    device_name = info['name']
                                    break
                        
                        # Simple substring match
                        if target_name.lower() in name_lower:
                            device_index = i
                            device_name = info['name']
                            break
            
            if device_index is None:
                print(f"[{self.name}] ✗ SDR device not found")
                if hasattr(self.config, config_device_attr):
                    print(f"[{self.name}]   Looked for: {getattr(self.config, config_device_attr)}")
                    print(f"[{self.name}]   Try one of these formats:")
                    print(f"[{self.name}]     {config_device_attr} = Loopback")
                    print(f"[{self.name}]     {config_device_attr} = hw:2,0")
                    print(f"[{self.name}]   Or enable VERBOSE_LOGGING to see all devices")
                return False
            
            # Open input stream with larger buffer to prevent stuttering
            buffer_multiplier = getattr(self.config, config_buffer_attr, 4)
            buffer_size = self.config.AUDIO_CHUNK_SIZE * buffer_multiplier
            
            # Note: Priority scheduling removed - let system manage all threads equally
            # Manual priority tweaking was causing TTS glitches and display freezing
            
            # Auto-detect supported channel count
            # Try stereo first, fall back to mono
            device_info = self.pyaudio.get_device_info_by_index(device_index)
            max_channels = device_info['maxInputChannels']
            
            # Use 2 channels if supported (stereo), otherwise use 1 (mono)
            sdr_channels = min(2, max_channels)
            
            try:
                self.input_stream = self.pyaudio.open(
                    format=pyaudio.paInt16,
                    channels=sdr_channels,
                    rate=self.config.AUDIO_RATE,
                    input=True,
                    input_device_index=device_index,
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE,
                    stream_callback=None
                )
                self.sdr_channels = sdr_channels  # Store for later use
            except Exception as e:
                # If 2 channels failed, try 1 channel
                if sdr_channels == 2:
                    if self.config.VERBOSE_LOGGING:
                        print(f"[{self.name}] Stereo failed, trying mono...")
                    sdr_channels = 1
                    self.input_stream = self.pyaudio.open(
                        format=pyaudio.paInt16,
                        channels=sdr_channels,
                        rate=self.config.AUDIO_RATE,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=self.config.AUDIO_CHUNK_SIZE,
                        stream_callback=None
                    )
                    self.sdr_channels = sdr_channels
                else:
                    raise
            
            if self.config.VERBOSE_LOGGING:
                print(f"[{self.name}] ✓ Audio input configured: {device_name}")
                print(f"[{self.name}]   Channels: {sdr_channels} ({'stereo' if sdr_channels == 2 else 'mono'})")
                chunk = self.config.AUDIO_CHUNK_SIZE
                print(f"[{self.name}]   Buffer: {chunk} samples ({chunk/self.config.AUDIO_RATE*1000:.1f}ms per period)")

            # Start background reader thread so SDR ALSA reads run independently
            # of the main loop, preventing sequential double-blocking (AIOC + SDR).
            self._chunk_queue.clear()
            self._reader_running = True
            self._reader_thread = threading.Thread(
                target=self._reader_thread_func,
                name=f"{self.name}-reader",
                daemon=True
            )
            self._reader_thread.start()

            if self.config.VERBOSE_LOGGING:
                print(f"[{self.name}] ✓ Reader thread started")

            return True
            
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"[{self.name}] ✗ Failed to setup audio: {e}")
            return False
    
    def _reader_thread_func(self):
        """Background thread: reads raw PCM from SDR ALSA stream and enqueues it.

        Intentionally does NO processing between reads — no numpy, no level
        calculation, no mute checks. The less work done between ALSA reads, the
        less chance the OS scheduler or Python GIL delays the next read and causes
        an ALSA overrun. All processing happens in get_audio() in the main thread.
        """
        chunk_size = self.config.AUDIO_CHUNK_SIZE
        while self._reader_running:
            if not self.input_stream:
                time.sleep(0.01)
                continue
            try:
                raw = self.input_stream.read(chunk_size, exception_on_overflow=False)
                self._chunk_queue.append(raw)
            except IOError:
                self.overflow_count += 1
            except Exception:
                time.sleep(0.002)

    def get_audio(self, chunk_size):
        """Get processed audio from SDR receiver.

        Pops raw bytes from the reader thread's queue, then does all processing
        (stereo-to-mono, level metering, audio boost) here in the main thread.
        Returns None immediately if the queue is empty — the main loop sends
        silence to keep the Mumble encoder continuously fed.
        """
        if not self.enabled:
            return None, False

        if not self.input_stream:
            return None, False

        # Pop raw chunk — non-blocking, return None if reader hasn't produced yet.
        # The main loop substitutes silence so the Mumble encoder stays fed.
        try:
            raw = self._chunk_queue.popleft()
        except IndexError:
            return None, False

        # Muted: chunk was popped (keeps queue fresh/current), discard it.
        should_discard = self.muted or (self.gateway.tx_muted and self.gateway.rx_muted)
        if should_discard:
            self.audio_level = max(0, int(self.audio_level * 0.7))
            return None, False

        self.total_reads += 1
        self.last_read_time = time.time()

        # Stereo→mono (all numpy processing happens here, not in reader thread)
        arr = np.frombuffer(raw, dtype=np.int16)
        if hasattr(self, 'sdr_channels') and self.sdr_channels == 2 and len(arr) >= 2:
            stereo = arr.reshape(-1, 2).astype(np.int32)
            arr = ((stereo[:, 0] + stereo[:, 1]) >> 1).astype(np.int16)
            raw = arr.tobytes()

        # Level metering and audio boost
        if len(arr) > 0:
            farr = arr.astype(np.float32)
            rms = float(np.sqrt(np.mean(farr * farr)))
            if rms > 0:
                db = 20 * _math_mod.log10(rms / 32767.0)
                raw_level = max(0, min(100, (db + 60) * (100 / 60)))
            else:
                raw_level = 0
            display_gain = getattr(self.gateway.config, 'SDR_DISPLAY_GAIN', 1.0)
            display_level = min(100, int(raw_level * display_gain))
            if display_level > self.audio_level:
                self.audio_level = display_level
            else:
                self.audio_level = int(self.audio_level * 0.7 + display_level * 0.3)

            audio_boost = getattr(self.gateway.config, 'SDR_AUDIO_BOOST', 1.0)
            if audio_boost != 1.0:
                arr = np.clip(farr * audio_boost, -32768, 32767).astype(np.int16)
                raw = arr.tobytes()

        return raw, False  # SDR never triggers PTT
    
    def is_active(self):
        """SDR is active if enabled and receiving audio"""
        return self.enabled and not self.muted and self.input_stream is not None
    
    def get_status(self):
        """Return status string"""
        if not self.enabled:
            return "SDR: Disabled"
        elif self.muted:
            return "SDR: Muted"
        else:
            return f"SDR: Active ({self.audio_level}%)"
    
    def cleanup(self):
        """Close SDR audio stream"""
        # Stop reader thread before closing the stream it reads from
        self._reader_running = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
        self._chunk_queue.clear()

        if self.input_stream:
            try:
                # Stop stream first to prevent ALSA errors
                if self.input_stream.is_active():
                    self.input_stream.stop_stream()
                time.sleep(0.05)  # Give ALSA time to clean up buffers
                self.input_stream.close()
            except Exception:
                pass  # Suppress ALSA errors during shutdown
        if self.pyaudio:
            try:
                self.pyaudio.terminate()
            except Exception:
                pass  # Suppress errors


class StreamOutputSource:
    """Stream audio output to named pipe for Darkice"""
    def __init__(self, config, gateway):
        self.config = config
        self.gateway = gateway
        self.connected = False
        self.pipe = None
        
        # Try to open pipe if enabled
        if config.ENABLE_STREAM_OUTPUT:
            self.setup_stream()
    
    def setup_stream(self):
        """Open named pipe for Darkice"""
        import os
        
        try:
            pipe_path = '/tmp/darkice_audio'
            
            # Create pipe if it doesn't exist
            if not os.path.exists(pipe_path):
                os.mkfifo(pipe_path)
                os.chmod(pipe_path, 0o666)
                if self.gateway.config.VERBOSE_LOGGING:
                    print(f"  Created pipe: {pipe_path}")
            
            # Open pipe for writing (non-blocking)
            import fcntl
            self.pipe = open(pipe_path, 'wb', buffering=0)
            
            # Make non-blocking
            fd = self.pipe.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            
            self.connected = True
            
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"  ✓ Streaming via Darkice pipe")
                print(f"    Pipe: {pipe_path}")
                print(f"    Format: PCM 48kHz mono 16-bit")
                print(f"    Make sure Darkice is running:")
                print(f"      darkice -c /etc/darkice.cfg")
                
        except Exception as e:
            print(f"  ⚠ Darkice pipe setup failed: {e}")
            print(f"    Install: sudo apt-get install darkice")
            print(f"    Configure: /etc/darkice.cfg")
            print(f"    Start: darkice -c /etc/darkice.cfg")
            self.connected = False
    
    def send_audio(self, audio_data):
        """Send raw PCM audio to Darkice via pipe"""
        if not self.connected or not self.pipe:
            return
        
        try:
            self.pipe.write(audio_data)
                
        except BlockingIOError:
            # Pipe full - skip this chunk
            pass
        except BrokenPipeError:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Stream] Darkice pipe broken - Darkice may have stopped")
            self.connected = False
        except Exception as e:
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Stream] Pipe error: {e}")
            self.connected = False
    
    def cleanup(self):
        """Close pipe"""
        if self.pipe:
            try:
                self.pipe.close()
            except:
                pass


class AudioMixer:
    """Mix audio from multiple sources with priority handling"""
    def __init__(self, config):
        self.config = config
        self.sources = []
        self.mixing_mode = 'simultaneous'  # Mix all sources together
        self.call_count = 0  # Debug counter
        
        # Per-source signal state for attack/release hysteresis
        self.signal_state = {}

        # Hysteresis + transition timing
        self.SIGNAL_ATTACK_TIME  = config.SIGNAL_ATTACK_TIME
        self.SIGNAL_RELEASE_TIME = config.SIGNAL_RELEASE_TIME
        self.SWITCH_PADDING_TIME = getattr(config, 'SWITCH_PADDING_TIME', 1.0)

        # Duck state machines — one entry per duck-group (e.g. 'aioc_vs_sdrs')
        # Tracks current duck state and active padding windows
        self.duck_state = {}

        # Per-SDR hold timers: instant attack, held release for smooth audio
        self.sdr_hold_until = {}      # {sdr_name: float timestamp}
        self.sdr_prev_included = {}   # {sdr_name: bool} - for fade-in detection
        
    def add_source(self, source):
        """Add an audio source to the mixer"""
        self.sources.append(source)
        # Sort by priority (lower number = higher priority)
        self.sources.sort(key=lambda s: s.priority)
        
    def remove_source(self, name):
        """Remove a source by name"""
        self.sources = [s for s in self.sources if s.name != name]
    
    def get_source(self, name):
        """Get a source by name"""
        for source in self.sources:
            if source.name == name:
                return source
        return None
    
    def get_mixed_audio(self, chunk_size):
        """
        Get mixed audio from all enabled sources.
        Returns: (mixed_audio, ptt_required, active_sources, sdr1_was_ducked, sdr2_was_ducked)
        """
        self.call_count += 1
        
        # Debug output every 100 calls
        if self.call_count % 100 == 0 and self.config.VERBOSE_LOGGING:
            print(f"\n[Mixer Debug] Called {self.call_count} times, {len(self.sources)} sources")
            for src in self.sources:
                print(f"  Source: {src.name}, enabled={src.enabled}, priority={src.priority}")
        
        if not self.sources:
            return None, False, [], False, False, None

        # Priority mode: only use highest priority active source
        if self.mixing_mode == 'priority':
            for source in self.sources:
                if not source.enabled:
                    if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"  [Mixer] Skipping {source.name} (disabled)")
                    continue

                # Try to get audio from this source
                audio, ptt = source.get_audio(chunk_size)

                # Debug what each source returns
                if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                    if audio is not None:
                        print(f"  [Mixer] {source.name} returned audio ({len(audio)} bytes), PTT={ptt}")
                    else:
                        print(f"  [Mixer] {source.name} returned None (no audio)")

                if audio is not None:
                    return audio, ptt and source.ptt_control, [source.name], False, False, None

            # No sources had audio
            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                print(f"  [Mixer] No sources returned audio")
            return None, False, [], False, False, None

        # Simultaneous mode: mix all active sources
        elif self.mixing_mode == 'simultaneous':
            return self._mix_simultaneous(chunk_size)

        # Duck mode: reduce volume of lower priority when higher priority active
        elif self.mixing_mode == 'duck':
            return self._mix_with_ducking(chunk_size)

        return None, False, [], False, False, None
    
    def _mix_simultaneous(self, chunk_size):
        """Mix all active sources together with SDR priority-based ducking"""
        mixed_audio = None
        ptt_required = False
        active_sources = []
        ptt_audio = None      # Separate PTT audio
        non_ptt_audio = None  # Non-PTT, non-SDR audio (Radio RX etc)
        sdr_sources = {}      # Dictionary of SDR sources: name -> (audio, source_obj)
        
        for source in self.sources:
            if not source.enabled:
                if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                    print(f"  [Mixer-Simultaneous] Skipping {source.name} (disabled)")
                continue
            
            audio, ptt = source.get_audio(chunk_size)
            
            # Debug what each source returns
            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                if audio is not None:
                    print(f"  [Mixer-Simultaneous] {source.name} returned audio ({len(audio)} bytes), PTT={ptt}")
                else:
                    print(f"  [Mixer-Simultaneous] {source.name} returned None")
            
            if audio is None:
                continue
            
            active_sources.append(source.name)
            
            # SDR audio is held separately so priority-based ducking can be applied
            if source.name.startswith("SDR"):
                # Apply mix_ratio to SDR audio when not ducking
                if hasattr(source, 'mix_ratio') and source.mix_ratio != 1.0:
                    arr_s = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
                    audio = np.clip(arr_s * source.mix_ratio, -32768, 32767).astype(np.int16).tobytes()
                sdr_sources[source.name] = (audio, source)
                continue  # Don't add to other buckets yet
            
            # Separate PTT and non-PTT sources
            if ptt and source.ptt_control:
                ptt_required = True
                if ptt_audio is None:
                    ptt_audio = audio
                else:
                    ptt_audio = self._mix_audio_streams(ptt_audio, audio, 0.5)
            else:
                if non_ptt_audio is None:
                    non_ptt_audio = audio
                else:
                    non_ptt_audio = self._mix_audio_streams(non_ptt_audio, audio, 0.5)
        
        # --- SDR priority-based ducking decision ---
        # AIOC audio (Radio RX) and PTT audio always take priority over all SDRs
        # Between SDRs: lower sdr_priority number = higher priority (ducks others)
        # BUT: Only duck if there's actual audio signal (not just silence/zeros)
        # Uses hysteresis to prevent rapid on/off switching (stuttering)
        
        import time
        current_time = time.time()
        
        # Helper function to check if audio has actual signal (instantaneous)
        def check_signal_instant(audio_data):
            """Check if audio contains actual signal above noise floor (instant check, no hysteresis)"""
            if not audio_data:
                return False
            try:
                arr = np.frombuffer(audio_data, dtype=np.int16).astype(np.float32)
                if len(arr) == 0:
                    return False
                rms = float(np.sqrt(np.mean(arr * arr)))
                if rms > 0:
                    db = 20 * _math_mod.log10(rms / 32767.0)
                    return db > -50.0
                return False
            except:
                return False
        
        # Helper function with hysteresis for stable signal detection
        def has_actual_audio(audio_data, source_name):
            """
            Check if audio has actual signal with attack/release hysteresis.

            Attack: signal must be CONTINUOUSLY present for SIGNAL_ATTACK_TIME before
                    a switch is allowed.  Any chunk of silence resets the attack timer,
                    so brief transients never trigger a source switch.

            Release: once active, the source must be continuously silent for
                     SIGNAL_RELEASE_TIME before it is declared inactive again.
            """
            if source_name not in self.signal_state:
                self.signal_state[source_name] = {
                    'has_signal': False,
                    'signal_continuous_start': 0.0,  # start of current unbroken signal run
                    'last_signal_time': 0.0,
                    'last_silence_time': current_time,
                }

            state = self.signal_state[source_name]
            signal_present_now = check_signal_instant(audio_data)

            if signal_present_now:
                state['last_signal_time'] = current_time
                if state['signal_continuous_start'] == 0.0:
                    # First chunk of a new continuous signal run — start the attack timer
                    state['signal_continuous_start'] = current_time
            else:
                state['last_silence_time'] = current_time
                # Any silence breaks continuity — reset the attack timer
                state['signal_continuous_start'] = 0.0

            if not state['has_signal']:
                # Inactive — fire attack only when signal has been unbroken for ATTACK_TIME
                if state['signal_continuous_start'] > 0.0:
                    continuous_duration = current_time - state['signal_continuous_start']
                    if continuous_duration >= self.SIGNAL_ATTACK_TIME:
                        state['has_signal'] = True
                        if self.config.VERBOSE_LOGGING:
                            print(f"  [Mixer] {source_name} ACTIVATED "
                                  f"(continuous signal for {continuous_duration:.2f}s)")
            else:
                # Active — release only after RELEASE_TIME of continuous silence
                time_since_signal = current_time - state['last_signal_time']
                if time_since_signal >= self.SIGNAL_RELEASE_TIME:
                    state['has_signal'] = False
                    if self.config.VERBOSE_LOGGING:
                        print(f"  [Mixer] {source_name} RELEASED "
                              f"(silent for {time_since_signal:.2f}s)")

            return state['has_signal']
        
        other_audio_active = (ptt_audio is not None) or (non_ptt_audio is not None)
        
        # Check if other_audio actually has signal (not just zeros) with hysteresis.
        # PTT audio (file playback) is deterministic — when FilePlaybackSource returns data
        # it IS playing.  Applying attack hysteresis to it would delay SDR ducking AND would
        # trigger a duck-out transition that inserts SWITCH_PADDING_TIME of silence, cutting
        # the start of every announcement and dropping PTT.
        # Only apply hysteresis to non-PTT radio RX to suppress noise/squelch-tail transients.
        if other_audio_active:
            ptt_is_active = ptt_audio is not None  # Deterministic: treat as active immediately
            non_ptt_has_signal = has_actual_audio(non_ptt_audio, "Radio") if non_ptt_audio else False
            other_audio_active = ptt_is_active or non_ptt_has_signal

            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                if non_ptt_audio and not non_ptt_has_signal:
                    print(f"  [Mixer] Non-PTT audio present but only silence - not ducking SDRs")
        
        # --- Duck state machine with transition padding ---
        # Manages the AIOC/Radio/PTT vs SDR duck relationship.
        # When a transition occurs (ducking starts or stops), SWITCH_PADDING_TIME
        # seconds of silence are inserted so the changeover is never abrupt:
        #   duck-out: both SDR and radio are silenced → then radio takes over
        #   duck-in:  SDR stays silent for padding → then SDR resumes
        ds = self.duck_state.setdefault('aioc_vs_sdrs', {
            'is_ducked': False,
            'prev_signal': False,
            'padding_end_time': 0.0,
            'transition_type': None,   # 'out' = duck starting, 'in' = duck ending
        })

        prev_signal = ds['prev_signal']
        ds['prev_signal'] = other_audio_active

        if not ds['is_ducked'] and other_audio_active and not prev_signal:
            # Transition: other audio just became active → start ducking SDRs.
            # Record whether SDR had actual signal now so we know whether the
            # transition-silence is needed (SDR→radio handoff) or not (radio-only).
            ds['is_ducked'] = True
            ds['padding_end_time'] = current_time + self.SWITCH_PADDING_TIME
            ds['transition_type'] = 'out'
            ds['sdr_active_at_transition'] = any(
                self.sdr_prev_included.get(name, False)
                for name in sdr_sources.keys()
            )
            if self.config.VERBOSE_LOGGING:
                print(f"  [Mixer] SDR duck-OUT: {self.SWITCH_PADDING_TIME:.2f}s transition silence "
                      f"(SDR active: {ds['sdr_active_at_transition']})")
        elif ds['is_ducked'] and not other_audio_active and prev_signal:
            # Transition: other audio just went inactive → stop ducking SDRs
            ds['is_ducked'] = False
            ds['padding_end_time'] = current_time + self.SWITCH_PADDING_TIME
            ds['transition_type'] = 'in'
            if self.config.VERBOSE_LOGGING:
                print(f"  [Mixer] SDR duck-IN:  {self.SWITCH_PADDING_TIME:.2f}s transition silence")

        in_padding = current_time < ds['padding_end_time']
        # Effective duck: still silencing SDRs either because a source is active
        # OR because we're inside a padding window after a transition
        aioc_ducks_sdrs = ds['is_ducked'] or in_padding
        # During duck-out padding: silence ALL output so the switch is a clean break
        in_transition_out = in_padding and ds['transition_type'] == 'out'

        sdr1_was_ducked = False
        sdr2_was_ducked = False

        # First pass: determine which SDRs should be ducked
        sdrs_to_include = {}  # SDRs that will actually be mixed

        # Sort SDR sources by priority (lower number = higher priority)
        sorted_sdrs = sorted(
            sdr_sources.items(),
            key=lambda x: getattr(x[1][1], 'sdr_priority', 99)
        )

        for sdr_name, (sdr_audio, sdr_source) in sorted_sdrs:
            sdr_duck = sdr_source.duck if hasattr(sdr_source, 'duck') else True
            sdr_priority = getattr(sdr_source, 'sdr_priority', 99)

            should_duck = False

            if sdr_duck:
                # Rule 1: AIOC/PTT/Radio audio ducks ALL SDRs (with padding on transitions)
                if aioc_ducks_sdrs:
                    should_duck = True
                    if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"  [Mixer] {sdr_name} ducked by AIOC/Radio/PTT audio")
                else:
                    # Rule 2: Higher priority SDR (lower number) ducks lower priority SDRs
                    # Only check SDRs that have actual signal (not just silence)
                    for other_name, other_tuple in sdrs_to_include.items():
                        other_source = other_tuple[1]
                        other_priority = getattr(other_source, 'sdr_priority', 99)
                        if other_priority < sdr_priority:
                            should_duck = True
                            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                                print(f"  [Mixer] {sdr_name} (priority {sdr_priority}) ducked by {other_name} (priority {other_priority})")
                            break
            
            # Track ducking state for status bar
            if should_duck:
                if sdr_name == "SDR1":
                    sdr1_was_ducked = True
                elif sdr_name == "SDR2":
                    sdr2_was_ducked = True
            else:
                # Instant attack + held release for SDR inclusion.
                #
                # The old has_actual_audio() approach used a 0.1s attack timer which
                # dropped the first 200ms chunk (one full AUDIO_CHUNK_SIZE period) and
                # then switched to full volume abruptly → missing audio + pop/click.
                #
                # New approach:
                #   - Include immediately on any detectable signal (no attack delay)
                #   - Hold inclusion for SIGNAL_RELEASE_TIME after signal stops so brief
                #     pauses don't cause dropouts and the tail fades away naturally
                #   - Apply a short linear fade-in at the moment of first inclusion to
                #     prevent the onset click when SDR activates after silence
                has_instant = check_signal_instant(sdr_audio)
                if has_instant:
                    self.sdr_hold_until[sdr_name] = current_time + self.SIGNAL_RELEASE_TIME
                hold_active = current_time < self.sdr_hold_until.get(sdr_name, 0.0)
                include_sdr = has_instant or hold_active
                prev_included = self.sdr_prev_included.get(sdr_name, False)

                if include_sdr:
                    audio_to_include = sdr_audio
                    if not prev_included:
                        # Onset: fade-in from 0→1 over first 10ms (480 samples)
                        arr = np.frombuffer(sdr_audio, dtype=np.int16).astype(np.float32)
                        fade_len = min(480, len(arr))
                        arr[:fade_len] *= np.linspace(0.0, 1.0, fade_len)
                        audio_to_include = arr.astype(np.int16).tobytes()
                    self.sdr_prev_included[sdr_name] = True
                    sdrs_to_include[sdr_name] = (audio_to_include, sdr_source)
                    if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"  [Mixer] {sdr_name} included (instant={'yes' if has_instant else 'hold'})")
                elif prev_included:
                    # Transition frame: was included last chunk, not now.
                    # Apply fade-out so the cutoff is always smooth regardless of
                    # how much time elapsed since the last iteration (avoids the
                    # timing-window bug where a slow AIOC read skips the fade).
                    arr = np.frombuffer(sdr_audio, dtype=np.int16).astype(np.float32)
                    arr *= np.linspace(1.0, 0.0, len(arr))
                    audio_to_include = arr.astype(np.int16).tobytes()
                    self.sdr_prev_included[sdr_name] = False
                    sdrs_to_include[sdr_name] = (audio_to_include, sdr_source)
                    if self.config.VERBOSE_LOGGING:
                        print(f"  [Mixer] {sdr_name} fade-out (hold expired)")
                else:
                    self.sdr_prev_included[sdr_name] = False
        
        # Second pass: actually mix the non-ducked SDRs
        for sdr_name, (sdr_audio, sdr_source) in sdrs_to_include.items():
            if non_ptt_audio is None:
                non_ptt_audio = sdr_audio
            else:
                non_ptt_audio = self._mix_audio_streams(non_ptt_audio, sdr_audio, 0.5)
        
        # Priority: PTT audio always wins (full volume, no mixing with radio)
        if ptt_audio is not None:
            mixed_audio = ptt_audio
            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                print(f"  [Mixer-Simultaneous] Using PTT audio at FULL VOLUME (not mixing with radio)")
        elif non_ptt_audio is not None:
            mixed_audio = non_ptt_audio

        # Duck-out transition padding: silence non-PTT output for SWITCH_PADDING_TIME
        # ONLY when SDR was actually playing at the time of the transition.
        # This creates a clean break at the SDR→radio handoff point.
        #
        # CRITICAL: Do NOT silence if no SDR was active — non_ptt_audio at this point
        # IS the radio RX audio going to Mumble.  Silencing it unconditionally caused
        # every new radio transmission to lose its first 1.0 s of audio to Mumble.
        # SDRs are already muted via aioc_ducks_sdrs regardless of this flag.
        if in_transition_out and not ptt_required and ds.get('sdr_active_at_transition', False):
            mixed_audio = None

        # When PTT (file playback) wins the mix, non_ptt_audio (radio RX) is not
        # included in mixed_audio.  Carry it out separately so the transmit loop
        # can still forward it to Mumble — listeners hear the radio channel even
        # while an announcement is being transmitted.
        rx_audio = non_ptt_audio if ptt_required else None

        if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
            print(f"  [Mixer-Simultaneous] Result: {len(active_sources)} active sources, PTT={ptt_required}")

        return mixed_audio, ptt_required, active_sources, sdr1_was_ducked, sdr2_was_ducked, rx_audio
    
    def _mix_with_ducking(self, chunk_size):
        """Mix with ducking: reduce lower priority sources"""
        # Find highest priority active source
        high_priority_active = False
        for source in self.sources:
            if source.enabled:
                audio, _ = source.get_audio(chunk_size)
                if audio is not None:
                    high_priority_active = True
                    break
        
        # If high priority is active, duck the others
        mixed_audio = None
        ptt_required = False
        active_sources = []
        
        for i, source in enumerate(self.sources):
            if not source.enabled:
                continue
            
            audio, ptt = source.get_audio(chunk_size)
            if audio is None:
                continue
            
            active_sources.append(source.name)
            
            # Duck lower priority sources
            if i > 0 and high_priority_active:
                audio = self._apply_volume(audio, 0.3)  # 30% volume
            
            if ptt and source.ptt_control:
                ptt_required = True
            
            if mixed_audio is None:
                mixed_audio = audio
            else:
                mixed_audio = self._mix_audio_streams(mixed_audio, audio, 0.5)
        
        return mixed_audio, ptt_required, active_sources, False, False, None

    def _mix_audio_streams(self, audio1, audio2, ratio=0.5):
        """Mix two audio streams together"""
        import array
        
        # Convert to samples
        samples1 = array.array('h', audio1)
        samples2 = array.array('h', audio2)
        
        # Ensure same length
        min_len = min(len(samples1), len(samples2))
        samples1 = samples1[:min_len]
        samples2 = samples2[:min_len]
        
        # Mix with ratio
        mixed = array.array('h', [
            int((s1 * ratio + s2 * (1 - ratio)))
            for s1, s2 in zip(samples1, samples2)
        ])
        
        return mixed.tobytes()
    
    def _apply_volume(self, audio, volume):
        """Apply volume multiplier to audio"""
        arr = np.frombuffer(audio, dtype=np.int16).astype(np.float32)
        return np.clip(arr * volume, -32768, 32767).astype(np.int16).tobytes()
    
    def get_status(self):
        """Get status of all sources"""
        status = []
        for source in self.sources:
            status.append(source.get_status())
        return status


class MumbleRadioGateway:
    def __init__(self, config):
        self.config = config
        self.start_time = time.time()  # Track gateway start time for uptime
        self.aioc_device = None
        self.mumble = None
        self.pyaudio_instance = None
        self.input_stream = None
        self.output_stream = None
        self.ptt_active = False
        self.running = True
        self.last_sound_time = 0
        self.last_audio_capture_time = 0
        self.audio_capture_active = False
        self.last_status_print = 0
        self.rx_audio_level = 0  # Received audio level (Mumble → Radio)
        self.tx_audio_level = 0  # Transmitted audio level (Radio → Mumble)
        self.last_rx_audio_time = 0  # When we last received audio
        self.stream_restart_count = 0
        self.last_stream_error = "None"
        self.restarting_stream = False  # Flag to prevent read during restart
        self.mumble_buffer_full_count = 0  # Track buffer full warnings
        self.last_buffer_clear = 0  # Last time we cleared the buffer
        
        # VOX (Voice Operated Switch) state for Radio → Mumble
        self.vox_active = False
        self.vox_level = 0.0
        self.last_vox_active_time = 0
        
        # VAD (Voice Activity Detection) state
        self.vad_active = False
        self.vad_envelope = 0.0
        self.vad_open_time = 0  # When VAD opened
        self.vad_close_time = 0  # When VAD closed
        self.vad_transmissions = 0  # Count of transmissions
        
        # Stream health monitoring
        self.last_successful_read = time.time()
        self.stream_age = 0  # How long current stream has been alive
        
        # Mute controls (keyboard toggle)
        self.tx_muted = False  # Mute Mumble → Radio (press 't')
        self.rx_muted = False  # Mute Radio → Mumble (press 'r')
        
        # Manual PTT control (keyboard toggle)
        self.manual_ptt_mode = False  # Manual PTT control (press 'p')

        # Restart flag (set by !restart command, checked in main() after run() exits)
        self.restart_requested = False
        
        # Audio processing state
        self.noise_profile = None  # For spectral subtraction
        self.gate_envelope = 0.0  # For noise gate smoothing
        self.highpass_state = None  # For high-pass filter state
        
        # Initialize audio mixer and sources
        self.mixer = AudioMixer(config)
        self.radio_source = None  # Will be initialized after AIOC setup
        self.sdr_source = None  # SDR1 receiver audio source
        self.sdr_muted = False  # SDR1-specific mute
        self.sdr_ducked = False  # Is SDR1 currently being ducked (status display)
        self.sdr_audio_level = 0  # SDR1 audio level for status bar
        self.sdr2_source = None  # SDR2 receiver audio source
        self.sdr2_muted = False  # SDR2-specific mute
        self.sdr2_ducked = False  # Is SDR2 currently being ducked (status display)
        self.sdr2_audio_level = 0  # SDR2 audio level for status bar
        self.aioc_available = False  # Track if AIOC is connected
    
    def calculate_audio_level(self, pcm_data):
        """Calculate RMS audio level from PCM data (0-100 scale)"""
        try:
            if not pcm_data:
                return 0
            arr = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            if len(arr) == 0:
                return 0
            rms = float(np.sqrt(np.mean(arr * arr)))
            if rms > 0:
                db = 20 * _math_mod.log10(rms / 32767.0)
                level = max(0, min(100, (db + 60) * (100/60)))
                return int(level)
            return 0
        except Exception:
            return 0
    
    def format_level_bar(self, level, muted=False, ducked=False, color='green'):
        """Format audio level as a visual bar (0-100 scale) with optional color
        
        Args:
            level: Audio level 0-100
            muted: Whether this channel is muted
            ducked: Whether this channel is being ducked (SDR only)
            color: 'green' for RX, 'red' for TX, 'cyan' for SDR
        
        Returns a fixed-width string (same width regardless of muted/ducked/normal state)
        """
        # ANSI color codes
        YELLOW = '\033[93m'
        GREEN = '\033[92m'
        RED = '\033[91m'
        CYAN = '\033[96m'
        MAGENTA = '\033[95m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
        
        # Choose bar color
        if color == 'red':
            bar_color = RED
        elif color == 'cyan':
            bar_color = CYAN
        elif color == 'magenta':
            bar_color = MAGENTA
        else:
            bar_color = GREEN
        
        # All return paths have EXACTLY the same visible character width:
        # [12 chars] + space + 4 chars = 17 visible characters total
        
        # Show MUTE if muted (fixed width, colored)
        if muted:
            # [---MUTE---] + 2 spaces + "M  " (M with 2 trailing spaces for alignment)
            return f"{WHITE}[{bar_color}---MUTE---{WHITE}]{RESET}  {bar_color}M  {RESET}"
        
        # Show DUCK if ducked (fixed width, colored) - for SDR only
        if ducked:
            # [---DUCK---] + 2 spaces + "D  " (D with 2 trailing spaces for alignment)
            return f"{WHITE}[{bar_color}---DUCK---{WHITE}]{RESET}  {bar_color}D  {RESET}"
        
        # Create a 10-character bar graph
        bar_length = 10
        filled = int((level / 100.0) * bar_length)
        
        # [bar graph] + space + "XXX%" (always 4 chars: 3 digits right-aligned + %)
        bar = '█' * filled + '-' * (bar_length - filled)
        return f"{WHITE}[{bar_color}{bar}{WHITE}]{RESET} {YELLOW}{level:3d}%{RESET}"
    
    def apply_highpass_filter(self, pcm_data):
        """Apply high-pass filter to remove low-frequency rumble"""
        try:
            import array
            import math
            
            samples = array.array('h', pcm_data)
            if len(samples) == 0:
                return pcm_data
            
            # Simple first-order IIR high-pass filter
            cutoff = self.config.HIGHPASS_CUTOFF_FREQ
            sample_rate = self.config.AUDIO_RATE
            
            # Calculate filter coefficient
            rc = 1.0 / (2.0 * math.pi * cutoff)
            dt = 1.0 / sample_rate
            alpha = rc / (rc + dt)
            
            # Initialize state if needed
            if self.highpass_state is None:
                self.highpass_state = 0.0
            
            # Apply filter
            filtered = []
            prev_input = self.highpass_state
            prev_output = 0.0
            
            for sample in samples:
                output = alpha * (prev_output + sample - prev_input)
                filtered.append(int(output))
                prev_input = sample
                prev_output = output
            
            self.highpass_state = prev_input
            return array.array('h', filtered).tobytes()
            
        except Exception:
            return pcm_data
    
    def apply_noise_gate(self, pcm_data):
        """Apply noise gate with attack/release to reduce background hiss"""
        try:
            import array
            import math
            
            samples = array.array('h', pcm_data)
            if len(samples) == 0:
                return pcm_data
            
            # Convert threshold from dB to linear
            threshold_db = self.config.NOISE_GATE_THRESHOLD
            threshold = 32767.0 * pow(10.0, threshold_db / 20.0)
            
            # Attack and release times in samples
            attack_samples = (self.config.NOISE_GATE_ATTACK / 1000.0) * self.config.AUDIO_RATE
            release_samples = (self.config.NOISE_GATE_RELEASE / 1000.0) * self.config.AUDIO_RATE
            
            # Attack and release coefficients
            attack_coef = 1.0 / attack_samples if attack_samples > 0 else 1.0
            release_coef = 1.0 / release_samples if release_samples > 0 else 0.1
            
            # Apply gate with envelope follower
            gated = []
            for sample in samples:
                # Calculate signal level (absolute value)
                level = abs(sample)
                
                # Update envelope with attack/release
                if level > self.gate_envelope:
                    self.gate_envelope += (level - self.gate_envelope) * attack_coef
                else:
                    self.gate_envelope += (level - self.gate_envelope) * release_coef
                
                # Calculate gain based on envelope vs threshold
                if self.gate_envelope > threshold:
                    gain = 1.0
                else:
                    # Smooth transition below threshold
                    ratio = self.gate_envelope / threshold if threshold > 0 else 0
                    gain = ratio * ratio  # Quadratic for smooth fade
                
                gated.append(int(sample * gain))
            
            return array.array('h', gated).tobytes()
            
        except Exception:
            return pcm_data
    
    def apply_spectral_noise_suppression(self, pcm_data):
        """Apply spectral subtraction to reduce constant background noise"""
        try:
            import array
            import math
            
            samples = array.array('h', pcm_data)
            if len(samples) == 0:
                return pcm_data
            
            # Convert to float
            float_samples = [s / 32767.0 for s in samples]
            
            # Simple spectral subtraction using moving average
            # (Proper implementation would use FFT, but this is lighter)
            window_size = 32
            strength = self.config.NOISE_SUPPRESSION_STRENGTH
            
            processed = []
            for i, sample in enumerate(float_samples):
                # Calculate local noise estimate (moving average of absolute values)
                start = max(0, i - window_size // 2)
                end = min(len(float_samples), i + window_size // 2)
                window = float_samples[start:end]
                
                # Noise estimate
                noise_estimate = sum(abs(s) for s in window) / len(window)
                
                # Subtract noise with strength control
                if abs(sample) > noise_estimate * (1.0 + strength):
                    # Signal above noise: keep it
                    processed_sample = sample
                else:
                    # Signal in noise range: reduce it
                    reduction = strength * noise_estimate
                    if sample > 0:
                        processed_sample = max(0, sample - reduction)
                    else:
                        processed_sample = min(0, sample + reduction)
                
                processed.append(processed_sample)
            
            # Convert back to int16
            result = array.array('h', [int(s * 32767.0) for s in processed])
            return result.tobytes()
            
        except Exception:
            return pcm_data
    
    def process_audio_for_mumble(self, pcm_data):
        """Apply all enabled audio processing to clean up radio audio before sending to Mumble"""
        if not pcm_data:
            return pcm_data
        
        processed = pcm_data
        
        # Apply high-pass filter first (removes low-frequency rumble from radio)
        if self.config.ENABLE_HIGHPASS_FILTER:
            processed = self.apply_highpass_filter(processed)
        
        # Apply noise suppression (removes constant hiss/static from radio)
        if self.config.ENABLE_NOISE_SUPPRESSION:
            if self.config.NOISE_SUPPRESSION_METHOD == 'spectral':
                processed = self.apply_spectral_noise_suppression(processed)
            # Can add other methods here (wiener, etc.)
        
        # Apply noise gate last (cuts residual RF noise/hiss)
        if self.config.ENABLE_NOISE_GATE:
            processed = self.apply_noise_gate(processed)
        
        return processed
    
    def check_vad(self, pcm_data):
        """Voice Activity Detection - determines if audio should be sent to Mumble"""
        if not self.config.ENABLE_VAD:
            return True  # VAD disabled, always send

        try:
            if not pcm_data:
                return False
            arr = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            if len(arr) == 0:
                return False

            # Calculate RMS level
            rms = float(np.sqrt(np.mean(arr * arr)))

            # Convert to dB
            if rms > 0:
                db_level = 20 * _math_mod.log10(rms / 32767.0)
            else:
                db_level = -100
            
            # Attack and release coefficients (samples per second)
            chunks_per_second = self.config.AUDIO_RATE / self.config.AUDIO_CHUNK_SIZE
            attack_coef = 1.0 / ((self.config.VAD_ATTACK / 1000.0) * chunks_per_second)
            release_coef = 1.0 / ((self.config.VAD_RELEASE / 1000.0) * chunks_per_second)
            
            # Update envelope follower
            if db_level > self.vad_envelope:
                # Attack: fast rise
                self.vad_envelope += (db_level - self.vad_envelope) * min(1.0, attack_coef)
            else:
                # Release: slow decay
                self.vad_envelope += (db_level - self.vad_envelope) * min(1.0, release_coef)
            
            current_time = time.time()
            
            # Check if signal exceeds threshold
            if self.vad_envelope > self.config.VAD_THRESHOLD:
                if not self.vad_active:
                    # VAD opening
                    self.vad_active = True
                    self.vad_open_time = current_time
                    self.vad_transmissions += 1
                return True
            else:
                # Below threshold
                if self.vad_active:
                    # Check minimum duration
                    open_duration = (current_time - self.vad_open_time) * 1000  # ms
                    if open_duration < self.config.VAD_MIN_DURATION:
                        # Haven't met minimum duration yet, stay open
                        return True
                    
                    # Check release time
                    if self.vad_close_time == 0:
                        self.vad_close_time = current_time
                    
                    release_duration = (current_time - self.vad_close_time) * 1000  # ms
                    if release_duration < self.config.VAD_RELEASE:
                        # Still in release tail
                        return True
                    else:
                        # Release complete, close VAD
                        self.vad_active = False
                        self.vad_close_time = 0
                        return False
                else:
                    # VAD is closed and staying closed
                    self.vad_close_time = 0
                    return False
                    
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"\n[VAD] Error: {e}")
            return True  # On error, allow transmission
    
    def check_vox(self, pcm_data):
        """Check if audio level exceeds VOX threshold (indicates radio is receiving)"""
        if not self.config.ENABLE_VOX:
            return True  # VOX disabled, always transmit
        
        try:
            if not pcm_data:
                return False
            arr = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32)
            if len(arr) == 0:
                return False

            # Calculate RMS level
            rms = float(np.sqrt(np.mean(arr * arr)))

            # Convert to dB
            if rms > 0:
                db = 20 * _math_mod.log10(rms / 32767.0)
            else:
                db = -100  # Very quiet

            # Attack and release timing
            attack_time = self.config.VOX_ATTACK_TIME / 1000.0  # ms to seconds
            release_time = self.config.VOX_RELEASE_TIME / 1000.0
            
            # Update VOX level with attack/release envelope
            if db > self.vox_level:
                # Attack: fast rise
                self.vox_level = db
            else:
                # Release: slow decay
                # Calculate decay rate to reach threshold in release_time
                decay_rate = abs(self.config.VOX_THRESHOLD - db) / (release_time * (self.config.AUDIO_RATE / self.config.AUDIO_CHUNK_SIZE))
                self.vox_level = max(db, self.vox_level - decay_rate)
            
            # Check if above threshold
            if self.vox_level > self.config.VOX_THRESHOLD:
                if not self.vox_active:
                    if self.config.VERBOSE_LOGGING:
                        print(f"\n[VOX] Radio receiving (level: {self.vox_level:.1f} dB)")
                self.vox_active = True
                self.last_vox_active_time = time.time()
                return True
            else:
                # Check if we're still in release period
                time_since_active = time.time() - self.last_vox_active_time
                if time_since_active < release_time:
                    return True  # Still in tail
                else:
                    if self.vox_active:
                        if self.config.VERBOSE_LOGGING:
                            print(f"\n[VOX] Radio silent (level: {self.vox_level:.1f} dB)")
                    self.vox_active = False
                    return False
                    
        except Exception:
            return True  # On error, allow transmission
        
    def set_ptt_state(self, state_on):
        """Control AIOC PTT"""
        if not self.aioc_device:
            print(f"\n[PTT] ✗ No AIOC device available!")
            return
        
        try:
            state = 1 if state_on else 0
            iomask = 1 << (self.config.AIOC_PTT_CHANNEL - 1)
            iodata = state << (self.config.AIOC_PTT_CHANNEL - 1)
            data = Struct("<BBBBB").pack(0, 0, iodata, iomask, 0)
            
            if self.config.VERBOSE_LOGGING:
                print(f"\n[PTT] {'KEYING' if state_on else 'UNKEYING'} radio")
                print(f"[PTT] Channel: GPIO{self.config.AIOC_PTT_CHANNEL}")
                print(f"[PTT] Data: {data.hex()}")
            
            self.aioc_device.write(bytes(data))
            
            if self.config.VERBOSE_LOGGING:
                print(f"[PTT] ✓ HID write successful")
            
            # Update PTT state (status line will show it)
            self.ptt_active = state_on
            
        except Exception as e:
            print(f"\n[PTT] ✗ Error: {e}")
            import traceback
            traceback.print_exc()
    
    def sound_received_handler(self, user, soundchunk):
        """Called when audio is received from Mumble server"""
        # Track when we last received audio
        self.last_rx_audio_time = time.time()
        
        # Calculate audio level (with smoothing)
        current_level = self.calculate_audio_level(soundchunk.pcm)
        # Smooth the level display (fast attack, slow decay)
        if current_level > self.rx_audio_level:
            self.rx_audio_level = current_level  # Fast attack
        else:
            self.rx_audio_level = int(self.rx_audio_level * 0.7 + current_level * 0.3)  # Slow decay
        
        # Apply activation delay if configured
        if self.config.PTT_ACTIVATION_DELAY > 0 and not self.ptt_active:
            time.sleep(self.config.PTT_ACTIVATION_DELAY)
        
        # Update last sound time
        self.last_sound_time = time.time()
        
        # Key PTT if not already active AND TX is not muted
        # Don't key the radio if we're muted - that would broadcast silence!
        # Also don't auto-key if manual PTT mode is active
        if not self.ptt_active and not self.tx_muted and not self.manual_ptt_mode:
            self.set_ptt_state(True)
        
        # Play sound to AIOC output (to radio mic input)
        # But only if TX is not muted
        if self.output_stream and not self.tx_muted:
            try:
                # Apply output volume
                pcm = soundchunk.pcm
                if self.config.OUTPUT_VOLUME != 1.0:
                    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                    pcm = np.clip(arr * self.config.OUTPUT_VOLUME, -32768, 32767).astype(np.int16).tobytes()
                
                self.output_stream.write(pcm)
            except Exception as e:
                if self.config.VERBOSE_LOGGING:
                    print(f"\nError playing audio: {e}")
    
    def find_usb_device_path(self):
        """Find the USB device path for the AIOC"""
        try:
            import subprocess
            # Find USB device using VID:PID
            result = subprocess.run(
                ['lsusb', '-d', f'{self.config.AIOC_VID:04x}:{self.config.AIOC_PID:04x}'],
                capture_output=True, text=True
            )
            
            if result.returncode == 0 and result.stdout:
                # Parse output like: "Bus 001 Device 003: ID 1209:7388"
                parts = result.stdout.split()
                if len(parts) >= 4:
                    bus = parts[1]
                    device = parts[3].rstrip(':')
                    return f"/sys/bus/usb/devices/{bus}-*"
            return None
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"  [Diagnostic] Could not find USB device path: {e}")
            return None
    
    def reset_usb_device(self):
        """Attempt to reset the AIOC USB device by power cycling"""
        if self.config.VERBOSE_LOGGING:
            print("  [Diagnostic] Attempting USB device reset...")
        
        try:
            import subprocess
            import glob
            
            # Method 1: Try using usbreset if available
            try:
                result = subprocess.run(
                    ['which', 'usbreset'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    # usbreset is available
                    result = subprocess.run(
                        ['sudo', 'usbreset', f'{self.config.AIOC_VID:04x}:{self.config.AIOC_PID:04x}'],
                        capture_output=True, text=True, timeout=5
                    )
                    if result.returncode == 0:
                        if self.config.VERBOSE_LOGGING:
                            print("  ✓ USB device reset via usbreset")
                        time.sleep(2)  # Wait for device to re-enumerate
                        return True
            except:
                pass
            
            # Method 2: Try sysfs unbind/bind
            try:
                # Find the device in sysfs
                usb_devices = glob.glob(f'/sys/bus/usb/devices/*')
                for dev_path in usb_devices:
                    try:
                        # Read vendor and product IDs
                        with open(f'{dev_path}/idVendor', 'r') as f:
                            vid = f.read().strip()
                        with open(f'{dev_path}/idProduct', 'r') as f:
                            pid = f.read().strip()
                        
                        if vid == f'{self.config.AIOC_VID:04x}' and pid == f'{self.config.AIOC_PID:04x}':
                            # Found our device
                            device_name = os.path.basename(dev_path)
                            
                            # Try to unbind
                            with open('/sys/bus/usb/drivers/usb/unbind', 'w') as f:
                                f.write(device_name)
                            
                            time.sleep(1)
                            
                            # Rebind
                            with open('/sys/bus/usb/drivers/usb/bind', 'w') as f:
                                f.write(device_name)
                            
                            if self.config.VERBOSE_LOGGING:
                                print("  ✓ USB device reset via sysfs unbind/bind")
                            time.sleep(2)
                            return True
                            
                    except (IOError, PermissionError):
                        continue
            except Exception as e:
                if self.config.VERBOSE_LOGGING:
                    print(f"  [Diagnostic] sysfs method failed: {e}")
            
            # Method 3: Try autoreset (no sudo needed)
            try:
                result = subprocess.run(
                    ['lsusb', '-d', f'{self.config.AIOC_VID:04x}:{self.config.AIOC_PID:04x}', '-v'],
                    capture_output=True, text=True, timeout=5
                )
                # Sometimes just querying the device helps
                time.sleep(1)
            except:
                pass
                
            if self.config.VERBOSE_LOGGING:
                print("  ⚠ USB reset methods require sudo permissions")
                print("  Please run: sudo chmod 666 /sys/bus/usb/drivers/usb/unbind")
                print("             sudo chmod 666 /sys/bus/usb/drivers/usb/bind")
                print("  Or manually unplug and replug the AIOC device")
            
            return False
            
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"  ✗ USB reset failed: {type(e).__name__}: {e}")
            return False
    
    def setup_aioc(self):
        """Initialize AIOC device"""
        if self.config.VERBOSE_LOGGING:
            print("Initializing AIOC device...")
        try:
            # Use hid.Device (capital D) - this is what's available
            self.aioc_device = hid.Device(vid=self.config.AIOC_VID, pid=self.config.AIOC_PID)
            print(f"✓ AIOC: {self.aioc_device.product}")
            return True
        except Exception as e:
            print(f"✗ Could not open AIOC: {e}")
            return False
    
    def find_aioc_audio_device(self):
        """Find AIOC audio device index"""
        # Suppress ALSA warnings if not in verbose mode
        import os
        import sys
        
        # Only suppress if not verbose
        if not self.config.VERBOSE_LOGGING:
            # Save stderr
            stderr_fd = sys.stderr.fileno()
            saved_stderr = os.dup(stderr_fd)
            
            try:
                # Redirect stderr to /dev/null
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, stderr_fd)
                os.close(devnull)
                
                p = pyaudio.PyAudio()
                
            finally:
                # Restore stderr
                os.dup2(saved_stderr, stderr_fd)
                os.close(saved_stderr)
        else:
            # Verbose mode - show ALSA messages
            p = pyaudio.PyAudio()
        
        aioc_input_index = None
        aioc_output_index = None
        
        # Check if manually specified
        if self.config.AIOC_INPUT_DEVICE >= 0:
            aioc_input_index = self.config.AIOC_INPUT_DEVICE
        if self.config.AIOC_OUTPUT_DEVICE >= 0:
            aioc_output_index = self.config.AIOC_OUTPUT_DEVICE
        
        # Auto-detect if not specified
        if aioc_input_index is None or aioc_output_index is None:
            if self.config.VERBOSE_LOGGING:
                print("\nSearching for AIOC audio device...")
                print("Available audio devices:")
            
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                name = info['name'].lower()
                
                if self.config.VERBOSE_LOGGING:
                    print(f"  [{i}] {info['name']} (in:{info['maxInputChannels']}, out:{info['maxOutputChannels']})")
                
                # Look for AIOC device by various names
                if any(keyword in name for keyword in ['aioc', 'cm108', 'c-media', 'usb audio', 'usb sound']):
                    if self.config.VERBOSE_LOGGING:
                        print(f"    → Potential AIOC device!")
                    if info['maxInputChannels'] > 0 and aioc_input_index is None:
                        aioc_input_index = i
                        if self.config.VERBOSE_LOGGING:
                            print(f"    → Using as INPUT device")
                    if info['maxOutputChannels'] > 0 and aioc_output_index is None:
                        aioc_output_index = i
                        if self.config.VERBOSE_LOGGING:
                            print(f"    → Using as OUTPUT device")
        
        p.terminate()
        return aioc_input_index, aioc_output_index
    
    def setup_audio(self):
        """Initialize PyAudio streams"""
        if self.config.VERBOSE_LOGGING:
            print("Initializing audio...")
        
        # Find AIOC device
        input_idx, output_idx = self.find_aioc_audio_device()
        
        if input_idx is None or output_idx is None:
            print("✗ Could not find AIOC audio device")
            if self.config.AIOC_INPUT_DEVICE < 0 or self.config.AIOC_OUTPUT_DEVICE < 0:
                print("  Using default audio device instead")
        
        # Suppress ALSA warnings during PyAudio initialization if not verbose
        if not self.config.VERBOSE_LOGGING:
            import os
            import sys
            stderr_fd = sys.stderr.fileno()
            saved_stderr = os.dup(stderr_fd)
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, stderr_fd)
                os.close(devnull)
                self.pyaudio_instance = pyaudio.PyAudio()
            finally:
                os.dup2(saved_stderr, stderr_fd)
                os.close(saved_stderr)
        else:
            self.pyaudio_instance = pyaudio.PyAudio()
        
        # Determine format based on bit depth
        if self.config.AUDIO_BITS == 16:
            audio_format = pyaudio.paInt16
        elif self.config.AUDIO_BITS == 24:
            audio_format = pyaudio.paInt24
        elif self.config.AUDIO_BITS == 32:
            audio_format = pyaudio.paInt32
        else:
            audio_format = pyaudio.paInt16
        
        try:
            # Output stream (Mumble → AIOC → Radio)
            self.output_stream = self.pyaudio_instance.open(
                format=audio_format,
                channels=self.config.AUDIO_CHANNELS,
                rate=self.config.AUDIO_RATE,
                output=True,
                output_device_index=output_idx,
                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE * 4  # 4x buffer for smooth playback
            )
            if self.config.VERBOSE_LOGGING:
                latency_ms = (self.config.AUDIO_CHUNK_SIZE * 4 / self.config.AUDIO_RATE) * 1000
                print(f"✓ Audio output configured ({latency_ms:.1f}ms buffer)")
            else:
                print("✓ Audio configured")
            
            # Input stream (Radio → AIOC → Mumble)
            # frames_per_buffer must match AUDIO_CHUNK_SIZE for input — this sets
            # the ALSA period size.  Using a larger value (e.g. 4x) causes ALSA to
            # accumulate that many samples before making them available, producing
            # 800 ms bursts of audio followed by 800 ms of silence.  Keep at 1x.
            self.input_stream = self.pyaudio_instance.open(
                format=audio_format,
                channels=self.config.AUDIO_CHANNELS,
                rate=self.config.AUDIO_RATE,
                input=True,
                input_device_index=input_idx,
                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE,
                stream_callback=None  # Use blocking mode
            )
            
            # Start the stream explicitly
            if not self.input_stream.is_active():
                self.input_stream.start_stream()
            
            # Initialize stream age
            self.stream_age = time.time()
            
            # Message already shown in quiet mode above
            if self.config.VERBOSE_LOGGING:
                print(f"✓ Audio input configured")
            
            # Initialize radio source with mixer (only if AIOC available)
            if self.aioc_available:
                try:
                    self.radio_source = AIOCRadioSource(self.config, self)
                    self.mixer.add_source(self.radio_source)
                    if self.config.VERBOSE_LOGGING:
                        print("✓ Radio audio source added to mixer")
                except Exception as source_err:
                    print(f"⚠ Warning: Could not initialize radio source: {source_err}")
                    print("  Continuing without radio audio")
                    self.radio_source = None
            else:
                print("  Radio audio: DISABLED (AIOC not available)")
                self.radio_source = None
            
            # Initialize file playback source if enabled
            if self.config.ENABLE_PLAYBACK:
                try:
                    self.playback_source = FilePlaybackSource(self.config, self)
                    self.mixer.add_source(self.playback_source)
                    print("✓ File playback source added to mixer")
                    
                    # Show available audio files
                    import os
                    import glob
                    audio_dir = self.playback_source.announcement_directory
                    # File scanning and mapping happens in FilePlaybackSource.__init__
                    # Mapping will be displayed later (just before status bar)
                    
                except Exception as playback_err:
                    print(f"⚠ Warning: Could not initialize playback source: {playback_err}")
                    self.playback_source = None
            else:
                self.playback_source = None
            
            # Initialize text-to-speech if enabled
            self.tts_engine = None
            if self.config.ENABLE_TTS:
                try:
                    print("Initializing text-to-speech...")
                    from gtts import gTTS
                    self.tts_engine = gTTS  # Store class reference
                    print("✓ Text-to-speech (gTTS) initialized")
                    print("  Use !speak <text> in Mumble to generate TTS")
                except ImportError:
                    print("⚠ gTTS not installed")
                    print("  Install with: pip3 install gtts --break-system-packages")
                    self.tts_engine = None
                except Exception as tts_err:
                    print(f"⚠ Warning: Could not initialize TTS: {tts_err}")
                    self.tts_engine = None
            else:
                print("  Text-to-speech: DISABLED (set ENABLE_TTS = true to enable)")
            
            # Initialize SDR1 source if enabled
            if self.config.ENABLE_SDR:
                try:
                    print("Initializing SDR1 audio source...")
                    self.sdr_source = SDRSource(self.config, self, name="SDR1", sdr_priority=self.config.SDR_PRIORITY)
                    if self.sdr_source.setup_audio():
                        # Set initial state from config
                        self.sdr_source.enabled = True
                        self.sdr_source.duck = self.config.SDR_DUCK
                        self.sdr_source.mix_ratio = self.config.SDR_MIX_RATIO
                        self.sdr_source.sdr_priority = self.config.SDR_PRIORITY
                        self.mixer.add_source(self.sdr_source)
                        print("✓ SDR1 audio source added to mixer")
                        print(f"  Device: {self.config.SDR_DEVICE_NAME}")
                        print(f"  Priority: {self.config.SDR_PRIORITY} (1=higher, 2=lower)")
                        if self.config.SDR_DUCK:
                            print(f"  Ducking: ENABLED (SDR silenced when higher priority audio active)")
                        else:
                            print(f"  Ducking: DISABLED (SDR mixed at {self.config.SDR_MIX_RATIO:.1f}x ratio)")
                        print(f"  Press 's' to mute/unmute SDR1")
                    else:
                        print("⚠ Warning: Could not initialize SDR1 audio")
                        self.sdr_source = None
                except Exception as sdr_err:
                    print(f"⚠ Warning: Could not initialize SDR1 source: {sdr_err}")
                    self.sdr_source = None
            else:
                self.sdr_source = None
                if self.config.VERBOSE_LOGGING:
                    print("  SDR1 audio: DISABLED (set ENABLE_SDR = true to enable)")
            
            # Initialize SDR2 source if enabled
            if self.config.ENABLE_SDR2:
                try:
                    print("Initializing SDR2 audio source...")
                    print(f"  SDR2_DEVICE_NAME from config: {self.config.SDR2_DEVICE_NAME}")
                    print(f"  SDR2_PRIORITY from config: {self.config.SDR2_PRIORITY}")
                    self.sdr2_source = SDRSource(self.config, self, name="SDR2", sdr_priority=self.config.SDR2_PRIORITY)
                    if self.sdr2_source.setup_audio():
                        # Set initial state from config
                        self.sdr2_source.enabled = True
                        self.sdr2_source.duck = self.config.SDR2_DUCK
                        self.sdr2_source.mix_ratio = self.config.SDR2_MIX_RATIO
                        self.sdr2_source.sdr_priority = self.config.SDR2_PRIORITY
                        self.mixer.add_source(self.sdr2_source)
                        print("✓ SDR2 audio source added to mixer")
                        print(f"  Device: {self.config.SDR2_DEVICE_NAME}")
                        print(f"  Priority: {self.config.SDR2_PRIORITY} (1=higher, 2=lower)")
                        if self.config.SDR2_DUCK:
                            print(f"  Ducking: ENABLED (SDR silenced when higher priority audio active)")
                        else:
                            print(f"  Ducking: DISABLED (SDR mixed at {self.config.SDR2_MIX_RATIO:.1f}x ratio)")
                        print(f"  Press 'x' to mute/unmute SDR2")
                    else:
                        print("⚠ Warning: Could not initialize SDR2 audio")
                        print(f"  Device hw:4,1 not found or already in use")
                        print(f"  Try: arecord -l | grep Loopback")
                        print(f"  SDR2 will show as disabled in status bar")
                        # Keep the source object but disable it so status bar shows
                        self.sdr2_source.enabled = False
                except Exception as sdr2_err:
                    print(f"⚠ Warning: Could not initialize SDR2 source: {sdr2_err}")
                    # Create disabled source object so status bar still shows it
                    try:
                        self.sdr2_source = SDRSource(self.config, self, name="SDR2", sdr_priority=self.config.SDR2_PRIORITY)
                        self.sdr2_source.enabled = False
                    except:
                        self.sdr2_source = None
            else:
                self.sdr2_source = None
                if self.config.VERBOSE_LOGGING:
                    print("  SDR2 audio: DISABLED (set ENABLE_SDR2 = true to enable)")
            
            # Initialize EchoLink source if enabled (Phase 3B)
            if self.config.ENABLE_ECHOLINK:
                try:
                    print("Initializing EchoLink integration...")
                    self.echolink_source = EchoLinkSource(self.config, self)
                    if self.echolink_source.connected:
                        self.mixer.add_source(self.echolink_source)
                        print("✓ EchoLink source added to mixer")
                        print("  Audio routing:")
                        if self.config.ECHOLINK_TO_MUMBLE:
                            print("    EchoLink → Mumble: ON")
                        if self.config.ECHOLINK_TO_RADIO:
                            print("    EchoLink → Radio TX: ON")
                        if self.config.RADIO_TO_ECHOLINK:
                            print("    Radio RX → EchoLink: ON")
                        if self.config.MUMBLE_TO_ECHOLINK:
                            print("    Mumble → EchoLink: ON")
                    else:
                        print("  ✗ EchoLink IPC not available")
                        print("    Make sure TheLinkBox is running")
                        self.echolink_source = None
                except Exception as echolink_err:
                    print(f"⚠ Warning: Could not initialize EchoLink: {echolink_err}")
                    self.echolink_source = None
            else:
                self.echolink_source = None
            
            # Initialize Icecast streaming if enabled (Phase 3A)
            if self.config.ENABLE_STREAM_OUTPUT:
                try:
                    print("Connecting to Icecast server...")
                    self.stream_output = StreamOutputSource(self.config, self)
                    if self.stream_output.connected:
                        print("✓ Icecast streaming active")
                        print(f"  Listen at: http://{self.config.STREAM_SERVER}:{self.config.STREAM_PORT}{self.config.STREAM_MOUNT}")
                    else:
                        print("  ✗ Icecast connection failed")
                        self.stream_output = None
                except Exception as stream_err:
                    print(f"⚠ Warning: Could not initialize streaming: {stream_err}")
                    self.stream_output = None
            else:
                self.stream_output = None
            
            return True
            
        except Exception as e:
            error_msg = str(e)
            print(f"✗ Could not initialize audio: {e}")
            
            # Check if this is the "Invalid output device" error that requires USB reset
            if "Invalid output device" in error_msg or "-9996" in error_msg:
                print("\n⚠ Detected USB device initialization error")
                print("  This typically requires unplugging and replugging the AIOC")
                print("  Attempting automatic USB reset...\n")
                
                if self.reset_usb_device():
                    print("\n  ✓ USB reset successful, retrying audio initialization...\n")
                    time.sleep(2)
                    
                    # Retry audio initialization
                    try:
                        input_idx, output_idx = self.find_aioc_audio_device()
                        
                        if input_idx is not None and output_idx is not None:
                            self.output_stream = self.pyaudio_instance.open(
                                format=audio_format,
                                channels=self.config.AUDIO_CHANNELS,
                                rate=self.config.AUDIO_RATE,
                                output=True,
                                output_device_index=output_idx,
                                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE * 4  # Larger buffer for smoother output
                            )
                            
                            self.input_stream = self.pyaudio_instance.open(
                                format=audio_format,
                                channels=self.config.AUDIO_CHANNELS,
                                rate=self.config.AUDIO_RATE,
                                input=True,
                                input_device_index=input_idx,
                                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE * 4  # Larger buffer for smoother output
                            )
                            
                            print("✓ Audio initialized successfully after USB reset")
                            
                            # Initialize radio source with mixer
                            try:
                                self.radio_source = AIOCRadioSource(self.config, self)
                                self.mixer.add_source(self.radio_source)
                                if self.config.VERBOSE_LOGGING:
                                    print("✓ Radio audio source added to mixer")
                            except Exception as source_err:
                                print(f"⚠ Warning: Could not initialize radio source: {source_err}")
                                self.radio_source = None
                            
                            # Initialize file playback source if enabled
                            if self.config.ENABLE_PLAYBACK:
                                try:
                                    self.playback_source = FilePlaybackSource(self.config, self)
                                    self.mixer.add_source(self.playback_source)
                                    print("✓ File playback source added to mixer")
                                    # File mapping will be displayed later
                                    
                                except Exception as playback_err:
                                    print(f"⚠ Warning: Could not initialize playback source: {playback_err}")
                                    self.playback_source = None
                            else:
                                self.playback_source = None
                            
                            return True
                    except Exception as retry_error:
                        print(f"✗ Retry failed: {retry_error}")
                        print("\nPlease manually unplug and replug the AIOC device, then restart")
                else:
                    print("\n✗ Automatic USB reset failed")
                    print("Please manually unplug and replug the AIOC device, then restart")
            
            return False
    
    def setup_mumble(self):
        """Initialize Mumble connection"""
        print(f"\nConnecting to Mumble: {self.config.MUMBLE_SERVER}:{self.config.MUMBLE_PORT}...")
        
        try:
            # Test if server is reachable first
            import socket
            print(f"  Testing connection to {self.config.MUMBLE_SERVER}:{self.config.MUMBLE_PORT}...")
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.settimeout(3)
            try:
                test_sock.connect((self.config.MUMBLE_SERVER, self.config.MUMBLE_PORT))
                test_sock.close()
                print(f"  ✓ Server is reachable")
            except socket.timeout:
                print(f"\n✗ CONNECTION FAILED: Server connection timed out")
                print(f"  Server: {self.config.MUMBLE_SERVER}:{self.config.MUMBLE_PORT}")
                print(f"\n  Possible causes:")
                print(f"  • Server is not running")
                print(f"  • Wrong IP address in gateway_config.txt")
                print(f"  • Firewall blocking connection")
                print(f"  • Network connectivity issue")
                print(f"\n  Check your config:")
                print(f"    MUMBLE_SERVER = {self.config.MUMBLE_SERVER}")
                print(f"    MUMBLE_PORT = {self.config.MUMBLE_PORT}")
                return False
            except socket.error as e:
                print(f"\n✗ CONNECTION FAILED: {e}")
                print(f"  Server: {self.config.MUMBLE_SERVER}:{self.config.MUMBLE_PORT}")
                print(f"\n  Possible causes:")
                print(f"  • Wrong IP address (check MUMBLE_SERVER in config)")
                print(f"  • Wrong port (check MUMBLE_PORT in config)")
                print(f"  • Server not running")
                print(f"\n  Current config:")
                print(f"    MUMBLE_SERVER = {self.config.MUMBLE_SERVER}")
                print(f"    MUMBLE_PORT = {self.config.MUMBLE_PORT}")
                return False
            
            # Create Mumble client
            print(f"  Creating Mumble client...")
            self.mumble = Mumble(
                self.config.MUMBLE_SERVER, 
                self.config.MUMBLE_USERNAME,
                port=self.config.MUMBLE_PORT,
                password=self.config.MUMBLE_PASSWORD if self.config.MUMBLE_PASSWORD else '',
                reconnect=self.config.MUMBLE_RECONNECT,
                stereo=self.config.MUMBLE_STEREO,
                debug=self.config.MUMBLE_DEBUG
            )
            
            # Set loop rate for low latency
            self.mumble.set_loop_rate(self.config.MUMBLE_LOOP_RATE)
            
            # Set up callback for received audio
            self.mumble.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, self.sound_received_handler)
            
            # Set up callback for text messages
            if self.config.ENABLE_TEXT_COMMANDS:
                try:
                    self.mumble.callbacks.set_callback(PYMUMBLE_CLBK_TEXTMESSAGERECEIVED, self.on_text_message)
                    print("✓ Text message callback registered")
                    print("  Send text commands in Mumble chat (e.g., !status, !help)")
                except Exception as callback_err:
                    print(f"⚠ Text callback registration failed: {callback_err}")
            else:
                print("  Text commands: DISABLED (set ENABLE_TEXT_COMMANDS = true to enable)")
            
            # Enable receiving sound
            self.mumble.set_receive_sound(True)
            
            # Connect
            print(f"  Starting Mumble connection...")
            self.mumble.start()
            
            print(f"  Waiting for Mumble to be ready...")
            self.mumble.is_ready()
            
            print(f"✓ Connected as '{self.config.MUMBLE_USERNAME}'")
            
            # Wait for codec to initialize
            print("  Waiting for audio codec to initialize...")
            max_wait = 5  # seconds
            wait_start = time.time()
            while time.time() - wait_start < max_wait:
                if hasattr(self.mumble.sound_output, 'encoder_framesize') and self.mumble.sound_output.encoder_framesize is not None:
                    print(f"  ✓ Audio codec ready (framesize: {self.mumble.sound_output.encoder_framesize})")
                    break
                time.sleep(0.1)
            else:
                print("  ⚠ Audio codec not initialized after 5s")
                print("    Audio may not work until codec is ready")
                print("    This usually resolves itself within 10-30 seconds")

            # Apply audio quality settings now that the codec is ready.
            # set_bandwidth() was never called before — the library default is 50kbps.
            # complexity=10: max Opus quality (marginal CPU cost on Pi)
            # signal=3001: OPUS_SIGNAL_VOICE — tunes psychoacoustic model for speech
            try:
                self.mumble.set_bandwidth(self.config.MUMBLE_BITRATE)
                enc = getattr(self.mumble.sound_output, 'encoder', None)
                if enc is not None:
                    enc.vbr = 1 if self.config.MUMBLE_VBR else 0
                    enc.complexity = 10
                    enc.signal = 3001  # OPUS_SIGNAL_VOICE
                    print(f"  ✓ Opus encoder: {self.config.MUMBLE_BITRATE//1000}kbps, "
                          f"VBR={'on' if self.config.MUMBLE_VBR else 'off'}, "
                          f"complexity=10, signal=voice")
                else:
                    print(f"  ✓ Mumble bandwidth set to {self.config.MUMBLE_BITRATE//1000}kbps "
                          f"(VBR will apply when codec negotiates)")
            except Exception as qe:
                print(f"  ⚠ Could not apply audio quality settings: {qe}")

            # Join channel if specified
            if self.config.MUMBLE_CHANNEL:
                try:
                    print(f"  Joining channel: {self.config.MUMBLE_CHANNEL}")
                    channel = self.mumble.channels.find_by_name(self.config.MUMBLE_CHANNEL)
                    if channel:
                        channel.move_in()
                        print(f"  ✓ Joined channel: {self.config.MUMBLE_CHANNEL}")
                    else:
                        print(f"  ⚠ Channel '{self.config.MUMBLE_CHANNEL}' not found")
                        print(f"    Staying in root channel")
                except Exception as ch_err:
                    print(f"  ✗ Could not join channel: {ch_err}")
            
            if self.config.VERBOSE_LOGGING:
                print(f"  Loop rate: {self.config.MUMBLE_LOOP_RATE}s ({1/self.config.MUMBLE_LOOP_RATE:.0f} Hz)")
            
            return True
            
        except Exception as e:
            print(f"\n✗ MUMBLE CONNECTION FAILED: {e}")
            print(f"\n  Configuration:")
            print(f"    Server: {self.config.MUMBLE_SERVER}")
            print(f"    Port: {self.config.MUMBLE_PORT}")
            print(f"    Username: {self.config.MUMBLE_USERNAME}")
            print(f"\n  Please check:")
            print(f"  1. Is the Mumble server running?")
            print(f"  2. Is the IP address correct in gateway_config.txt?")
            print(f"  3. Is the port correct? (default: 64738)")
            print(f"  4. Can you connect with the official Mumble client?")
            print(f"\n  Test with Mumble client first:")
            print(f"    Server: {self.config.MUMBLE_SERVER}")
            print(f"    Port: {self.config.MUMBLE_PORT}")
            return False
    
    def speak_text(self, text):
        """
        Generate TTS audio from text and play it on radio
        
        Args:
            text: Text to convert to speech
            
        Returns:
            bool: True if successful, False otherwise
        """
        if not self.tts_engine:
            if self.config.VERBOSE_LOGGING:
                print("\n[TTS] Text-to-speech not available")
            return False
        
        if not self.playback_source:
            if self.config.VERBOSE_LOGGING:
                print("\n[TTS] Playback source not available")
            return False
        
        try:
            import tempfile
            import os
            
            if self.config.VERBOSE_LOGGING:
                print(f"\n[TTS] Generating speech: {text[:50]}...")
            
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False)
            temp_path = temp_file.name
            temp_file.close()
            
            # Generate TTS audio using gTTS
            if self.config.VERBOSE_LOGGING:
                print(f"[TTS] Calling gTTS to generate audio...")
            try:
                tts = self.tts_engine(text, lang='en', slow=False)
                if self.config.VERBOSE_LOGGING:
                    print(f"[TTS] Saving to {temp_path}...")
                tts.save(temp_path)
                if self.config.VERBOSE_LOGGING:
                    print(f"[TTS] ✓ Audio file saved")
            except Exception as tts_error:
                print(f"[TTS] ✗ gTTS generation failed: {tts_error}")
                print(f"[TTS] Check internet connection (gTTS requires internet)")
                return False
            
            # Verify file exists and has valid content
            import os
            if not os.path.exists(temp_path):
                print(f"[TTS] ✗ File not created!")
                return False
            
            size = os.path.getsize(temp_path)
            if self.config.VERBOSE_LOGGING:
                print(f"[TTS] File size: {size} bytes")
            
            # Validate it's actually an MP3 file, not an HTML error page
            # MP3 files start with ID3 tag or MPEG frame sync
            try:
                with open(temp_path, 'rb') as f:
                    header = f.read(10)
                    
                    # Check for ID3 tag (ID3v2)
                    is_mp3 = header.startswith(b'ID3')
                    
                    # Check for MPEG frame sync (0xFF 0xFB or 0xFF 0xF3)
                    if not is_mp3 and len(header) >= 2:
                        is_mp3 = (header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)
                    
                    # Check if it's HTML (error page)
                    is_html = header.startswith(b'<!DOCTYPE') or header.startswith(b'<html')
                    
                    if is_html:
                        print(f"[TTS] ✗ gTTS returned HTML error page, not MP3")
                        print(f"[TTS] This usually means:")
                        print(f"  - Rate limiting from Google")
                        print(f"  - Network/firewall blocking")
                        print(f"  - Invalid characters in text")
                        # Read first 200 chars to show error
                        f.seek(0)
                        error_preview = f.read(200).decode('utf-8', errors='ignore')
                        print(f"[TTS] Error preview: {error_preview[:100]}")
                        os.unlink(temp_path)
                        return False
                    
                    if not is_mp3:
                        print(f"[TTS] ✗ File doesn't appear to be valid MP3")
                        print(f"[TTS] Header: {header.hex()}")
                        os.unlink(temp_path)
                        return False
                    
                    if self.config.VERBOSE_LOGGING:
                        print(f"[TTS] ✓ Validated MP3 file format")
                        
            except Exception as val_err:
                print(f"[TTS] ✗ Could not validate file: {val_err}")
                return False
            
            # File is valid MP3
            if size < 1000:
                # Suspiciously small - probably an error
                print(f"[TTS] ✗ File too small ({size} bytes) - likely an error")
                os.unlink(temp_path)
                return False
            
            # Skip padding for now - it was causing corruption
            # The MP3 file is ready to play as-is
            if self.config.VERBOSE_LOGGING:
                print(f"[TTS] MP3 file ready for playback")
            
            if self.config.VERBOSE_LOGGING:
                print(f"[TTS] Queueing for playback...")
            
            # Queue for playback (will go to radio TX)
            if self.playback_source:
                if self.config.VERBOSE_LOGGING:
                    print(f"[TTS] Playback source exists, queueing file...")
                
                # Temporarily boost playback volume for TTS
                # Volume will be reset to 1.0 when file finishes playing
                original_volume = self.playback_source.volume
                self.playback_source.volume = self.config.TTS_VOLUME
                if self.config.VERBOSE_LOGGING:
                    print(f"[TTS] Boosting volume from {original_volume}x to {self.config.TTS_VOLUME}x for TTS playback")
                    print(f"[TTS] Volume will auto-reset to 1.0x when TTS finishes")
                
                result = self.playback_source.queue_file(temp_path)
                
                if self.config.VERBOSE_LOGGING:
                    print(f"[TTS] Queue result: {result}")
                if not result:
                    print(f"[TTS] ✗ Failed to queue file")
                    self.playback_source.volume = original_volume  # Restore on failure
                    return False
            else:
                print(f"[TTS] ✗ No playback source available!")
                return False
            
            return True
            
        except Exception as e:
            print(f"\n[TTS] Error: {e}")
            return False
    
    def send_text_message(self, message):
        """
        Send text message to current Mumble channel
        
        Args:
            message: Text message to send
        """
        try:
            if self.config.VERBOSE_LOGGING:
                print(f"\n[Mumble Text] Attempting to send: {message[:100]}...")
            if self.mumble and hasattr(self.mumble, 'users') and hasattr(self.mumble.users, 'myself'):
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] Mumble object exists, calling send_message...")
                # Try the send_message method (might be the correct one)
                self.mumble.users.myself.send_message(message)
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] ✓ Message sent successfully")
            else:
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] ✗ Mumble not ready")
        except AttributeError as ae:
            # Try alternate method
            try:
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] Trying alternate method...")
                self.mumble.my_channel().send_text_message(message)
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] ✓ Message sent via channel method")
            except Exception as e2:
                print(f"\n[Mumble Text] ✗ Both methods failed: {ae}, {e2}")
        except Exception as e:
            print(f"\n[Mumble Text] ✗ Error sending: {e}")
            import traceback
            traceback.print_exc()
    
    def on_text_message(self, text_message):
        """
        Handle incoming text messages from Mumble users
        
        Supports commands:
            !speak <text>  - Generate TTS and broadcast on radio
            !play <0-9>    - Play announcement file by slot number
            !files         - List loaded announcement files
            !stop          - Stop playback and clear queue
            !mute          - Mute TX (Mumble → Radio)
            !unmute        - Unmute TX
            !id            - Play station ID (shortcut for !play 0)
            !status        - Show gateway status
            !help          - Show available commands
        """
        try:
            # Debug: Print when text is received (if verbose)
            if self.config.VERBOSE_LOGGING:
                print(f"\n[Mumble Text] Message received from user {text_message.actor}")
            
            # Get sender info
            sender = self.mumble.users[text_message.actor]
            sender_name = sender['name']
            message = text_message.message.strip()
            
            if self.config.VERBOSE_LOGGING:
                print(f"[Mumble Text] {sender_name}: {message}")
            
            # Ignore if not a command
            if not message.startswith('!'):
                if self.config.VERBOSE_LOGGING:
                    print(f"[Mumble Text] Not a command (doesn't start with !), ignoring")
                return
            
            # Parse command
            parts = message.split(None, 1)  # Split on first space
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            
            # Handle commands
            if command == '!speak':
                if args:
                    if self.speak_text(args):
                        self.send_text_message(f"Speaking: {args[:50]}...")
                    else:
                        self.send_text_message("TTS not available")
                else:
                    self.send_text_message("Usage: !speak <text>")
            
            elif command == '!play':
                if args and args in '0123456789':
                    key = args
                    if self.playback_source:
                        path = self.playback_source.file_status[key]['path']
                        filename = self.playback_source.file_status[key].get('filename', '')
                        if path:
                            self.playback_source.queue_file(path)
                            self.send_text_message(f"Playing: {filename}")
                        else:
                            self.send_text_message(f"No file on key {key}")
                    else:
                        self.send_text_message("Playback not available")
                else:
                    self.send_text_message("Usage: !play <0-9>")
            
            elif command == '!status':
                import psutil
                import os
                
                status_lines = []
                status_lines.append("╔════════════════════════════════════╗")
                status_lines.append("║     GATEWAY STATUS REPORT          ║")
                status_lines.append("╚════════════════════════════════════╝")
                
                # System Resources
                cpu_percent = psutil.cpu_percent(interval=0.1)
                memory = psutil.virtual_memory()
                status_lines.append("")
                status_lines.append("📊 SYSTEM:")
                status_lines.append(f"  CPU Load: {cpu_percent:.1f}%")
                status_lines.append(f"  Memory: {memory.percent:.1f}% ({memory.used // (1024**2)} MB / {memory.total // (1024**2)} MB)")
                status_lines.append(f"  Uptime: {int((time.time() - self.start_time) // 60)} minutes")
                
                # PTT & Audio Status
                status_lines.append("")
                status_lines.append("📻 RADIO:")
                status_lines.append(f"  PTT: {'🔴 ACTIVE (TX)' if self.ptt_active else '🟢 Idle'}")
                status_lines.append(f"  Manual PTT: {'ON' if self.manual_ptt_mode else 'OFF'}")
                status_lines.append(f"  TX Muted: {'YES' if self.tx_muted else 'NO'}")
                status_lines.append(f"  RX Muted: {'YES' if self.rx_muted else 'NO'}")
                status_lines.append(f"  Audio Level: TX {self.tx_audio_level}% / RX {self.rx_audio_level}%")
                
                # Mumble Status
                status_lines.append("")
                status_lines.append("💬 MUMBLE:")
                status_lines.append(f"  Connected: {'YES' if self.mumble else 'NO'}")
                status_lines.append(f"  Users: {len(self.mumble.users) if self.mumble else 0}")
                status_lines.append(f"  Channel: {self.config.MUMBLE_CHANNEL if self.config.MUMBLE_CHANNEL else 'Root'}")
                
                # Audio Processing
                status_lines.append("")
                status_lines.append("🎛️ PROCESSING:")
                processing = []
                if self.config.ENABLE_VAD: processing.append(f"VAD ({self.config.VAD_THRESHOLD}dB)")
                if self.config.ENABLE_VOX: processing.append(f"VOX ({self.config.VOX_THRESHOLD}dB)")
                if self.config.ENABLE_NOISE_GATE: processing.append("Noise Gate")
                if self.config.ENABLE_HIGHPASS_FILTER: processing.append(f"HPF ({self.config.HIGHPASS_CUTOFF_FREQ}Hz)")
                if self.config.ENABLE_AGC: processing.append("AGC")
                if self.config.ENABLE_NOISE_SUPPRESSION: processing.append(f"Noise Sup ({self.config.NOISE_SUPPRESSION_METHOD})")
                if self.config.ENABLE_ECHO_CANCELLATION: processing.append("Echo Cancel")
                status_lines.append(f"  Active: {', '.join(processing) if processing else 'None'}")
                status_lines.append(f"  Input Vol: {self.config.INPUT_VOLUME}x")
                status_lines.append(f"  Output Vol: {self.config.OUTPUT_VOLUME}x")
                
                # File Playback
                if self.playback_source:
                    status_lines.append("")
                    status_lines.append("🎵 PLAYBACK:")
                    file_count = sum(1 for k in '0123456789' if self.playback_source.file_status[k]['exists'])
                    status_lines.append(f"  Files Loaded: {file_count}/10")
                    playing = [k for k in '0123456789' if self.playback_source.file_status[k]['playing']]
                    if playing:
                        status_lines.append(f"  Now Playing: Key {playing[0]}")
                    if self.playback_source.playlist:
                        status_lines.append(f"  Queue: {len(self.playback_source.playlist)} file(s)")
                
                # TTS Status
                status_lines.append("")
                status_lines.append("🗣️ TEXT-TO-SPEECH:")
                status_lines.append(f"  Available: {'YES' if self.tts_engine else 'NO'}")
                if self.tts_engine:
                    status_lines.append(f"  Volume Boost: {self.config.TTS_VOLUME}x")
                
                # Streaming
                if self.config.ENABLE_STREAM_OUTPUT:
                    status_lines.append("")
                    status_lines.append("📡 STREAMING:")
                    status_lines.append(f"  Enabled: YES")
                    status_lines.append(f"  Server: {self.config.STREAM_SERVER}")
                
                status_lines.append("")
                status_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                self.send_text_message("\n".join(status_lines))
            
            elif command == '!files':
                if self.playback_source:
                    lines = ["=== Announcement Files ==="]
                    found = False
                    for key in '0123456789':
                        info = self.playback_source.file_status[key]
                        if info['exists']:
                            label = "Station ID" if key == '0' else f"Slot {key}"
                            playing = " [PLAYING]" if info['playing'] else ""
                            lines.append(f"  {label}: {info['filename']}{playing}")
                            found = True
                    if not found:
                        lines.append("  No files loaded")
                    self.send_text_message("\n".join(lines))
                else:
                    self.send_text_message("Playback not available")

            elif command == '!stop':
                if self.playback_source:
                    self.playback_source.stop_playback()
                    self.send_text_message("Playback stopped")
                else:
                    self.send_text_message("Playback not available")

            elif command == '!restart':
                self.send_text_message("Gateway restarting...")
                self.restart_requested = True
                self.running = False

            elif command == '!mute':
                self.tx_muted = True
                self.send_text_message("TX muted (Mumble → Radio)")

            elif command == '!unmute':
                self.tx_muted = False
                self.send_text_message("TX unmuted")

            elif command == '!id':
                if self.playback_source:
                    info = self.playback_source.file_status['0']
                    if info['path']:
                        self.playback_source.queue_file(info['path'])
                        self.send_text_message(f"Playing station ID: {info['filename']}")
                    else:
                        self.send_text_message("No station ID file on slot 0")
                else:
                    self.send_text_message("Playback not available")

            elif command == '!help':
                help_text = [
                    "=== Gateway Commands ===",
                    "!speak <text> - TTS broadcast on radio",
                    "!play <0-9>   - Play announcement by slot",
                    "!files        - List loaded announcement files",
                    "!stop         - Stop playback and clear queue",
                    "!mute         - Mute TX (Mumble → Radio)",
                    "!unmute       - Unmute TX",
                    "!id           - Play station ID (slot 0)",
                    "!restart      - Restart the gateway",
                    "!status       - Show gateway status",
                    "!help         - Show this help"
                ]
                self.send_text_message("\n".join(help_text))

            else:
                self.send_text_message(f"Unknown command. Try !help")
        
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"\n[Text Command] Error: {e}")
    
    def audio_transmit_loop(self):
        """Continuously capture audio from sources and send to Mumble via mixer"""
        if self.config.VERBOSE_LOGGING:
            print("✓ Audio transmit thread started (with mixer)")
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.running:
            try:
                # ── AIOC stream health management ────────────────────────────────
                # These checks are AIOC-specific and must NOT block SDR audio.
                # The mixer runs unconditionally below so SDR always reaches Mumble.
                if self.input_stream and not self.restarting_stream:
                    current_time = time.time()
                    time_since_creation = current_time - self.stream_age
                    time_since_vad_active = current_time - self.last_vox_active_time if hasattr(self, 'last_vox_active_time') else 999

                    # Proactive AIOC restart (optional feature, brief gap acceptable)
                    if (self.config.ENABLE_STREAM_HEALTH and
                            self.config.STREAM_RESTART_INTERVAL > 0 and
                            time_since_creation > self.config.STREAM_RESTART_INTERVAL):
                        if not self.vad_active and time_since_vad_active > self.config.STREAM_RESTART_IDLE_TIME:
                            if self.config.VERBOSE_LOGGING:
                                print(f"\n[Maintenance] Proactive stream restart (age: {time_since_creation:.0f}s, idle: {time_since_vad_active:.0f}s)")
                            self.restart_audio_input()
                            self.stream_age = time.time()
                            time.sleep(0.2)
                            continue

                    # AIOC stream inactive: restart it but do NOT raise or skip the
                    # mixer.  AIOCRadioSource.get_audio() returns None while
                    # restarting_stream is True, so only SDR audio flows until AIOC
                    # recovers — which is exactly what the user wants.
                    if not self.input_stream.is_active():
                        if self.config.VERBOSE_LOGGING:
                            print("\n[Diagnostic] AIOC stream inactive, restarting...")
                        self.restart_audio_input()
                        # Fall through — SDR still runs below

                # ── Mixer path: SDR always runs, AIOC contributes when healthy ──
                if self.radio_source and self.mixer:
                    data, ptt_required, active_sources, sdr1_was_ducked, sdr2_was_ducked, rx_audio = self.mixer.get_mixed_audio(self.config.AUDIO_CHUNK_SIZE)

                    # Store SDR ducked states for status bar display
                    self.sdr_ducked = sdr1_was_ducked
                    self.sdr2_ducked = sdr2_was_ducked

                    if data is None:
                        # No audio from any source — substitute silence so the Opus
                        # encoder receives a continuous stream at the correct rate.
                        # Skipping add_sound() starves the codec and causes audible
                        # glitches (distortion, "struggling" sound) when real audio
                        # resumes because Opus resets its state across the gap.
                        data = b'\x00' * (self.config.AUDIO_CHUNK_SIZE * 2)
                        self.audio_capture_active = False
                        # Fall through — add_sound(silence) runs at the bottom of the
                        # loop, and stream_output also receives the silence frame there.

                    # Debug: we got audio from mixer
                    if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Debug] Got audio from mixer: {len(data)} bytes, sources: {active_sources}, PTT={ptt_required}")

                    # Route audio based on PTT requirement
                    if ptt_required:
                        # PTT required (file playback) - send to radio TX output
                        if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] PTT required - routing to radio TX")

                        # Update last sound time so PTT release timer works
                        self.last_sound_time = time.time()

                        # Calculate audio level for TX bar
                        current_level = self.calculate_audio_level(data)
                        # Smooth the level display (fast attack, slow decay)
                        if current_level > self.rx_audio_level:
                            self.rx_audio_level = current_level
                        else:
                            self.rx_audio_level = int(self.rx_audio_level * 0.7 + current_level * 0.3)

                        # Update last RX audio time to prevent decay during file playback
                        self.last_rx_audio_time = time.time()

                        # Activate PTT if not already active and not muted.
                        if not self.ptt_active and not self.tx_muted and not self.manual_ptt_mode:
                            self.set_ptt_state(True)
                            self._announcement_ptt_delay_until = time.time() + self.config.PTT_ANNOUNCEMENT_DELAY
                            self.announcement_delay_active = True

                        # Clear the delay flag once the window has passed.
                        if getattr(self, 'announcement_delay_active', False):
                            if time.time() >= self._announcement_ptt_delay_until:
                                self.announcement_delay_active = False

                        # Send audio to radio output
                        if self.output_stream and not self.tx_muted:
                            try:
                                pcm = data
                                if self.config.OUTPUT_VOLUME != 1.0:
                                    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                                    pcm = np.clip(arr * self.config.OUTPUT_VOLUME, -32768, 32767).astype(np.int16).tobytes()
                                try:
                                    self.output_stream.write(pcm, exception_on_overflow=False)
                                except TypeError:
                                    self.output_stream.write(pcm)
                                if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                                    print(f"\n[Debug] Sent {len(pcm)} bytes to radio TX output")
                            except IOError as io_err:
                                if self.config.VERBOSE_LOGGING:
                                    print(f"\n[Warning] Output stream buffer issue: {io_err}")
                            except Exception as tx_err:
                                if self.config.VERBOSE_LOGGING:
                                    print(f"\n[Error] Failed to send to radio TX: {tx_err}")

                        # EchoLink can optionally receive PTT audio directly
                        if self.echolink_source and self.config.RADIO_TO_ECHOLINK:
                            try:
                                self.echolink_source.send_audio(data)
                            except Exception as el_err:
                                if self.config.VERBOSE_LOGGING:
                                    print(f"\n[EchoLink] Send error: {el_err}")

                        # Forward concurrent radio RX to Mumble/stream even during
                        # announcement playback (full-duplex monitoring).
                        rx_for_mumble = (
                            getattr(self.radio_source, '_rx_cache', None)
                            if self.radio_source else rx_audio
                        )
                        if rx_for_mumble is not None:
                            if (self.mumble and
                                    hasattr(self.mumble, 'sound_output') and
                                    self.mumble.sound_output is not None and
                                    getattr(self.mumble.sound_output, 'encoder_framesize', None) is not None):
                                try:
                                    self.mumble.sound_output.add_sound(rx_for_mumble)
                                except Exception:
                                    pass
                            if self.stream_output and self.stream_output.connected:
                                try:
                                    self.stream_output.send_audio(rx_for_mumble)
                                except Exception:
                                    pass

                        # Skip the normal RX→Mumble path below - this is TX audio
                        continue

                    # No PTT required (radio RX / SDR) — falls through to Mumble send

                elif self.input_stream and not self.restarting_stream:
                    # Fallback: direct AIOC read only (no mixer / no SDR)
                    try:
                        data = self.input_stream.read(
                            self.config.AUDIO_CHUNK_SIZE,
                            exception_on_overflow=False
                        )
                    except IOError as io_err:
                        if io_err.errno == -9981:  # Input overflow
                            if self.config.VERBOSE_LOGGING and consecutive_errors == 0:
                                print("\n[Diagnostic] Input overflow, clearing buffer...")
                            try:
                                self.input_stream.read(self.config.AUDIO_CHUNK_SIZE * 2, exception_on_overflow=False)
                            except:
                                pass
                            time.sleep(0.05)
                            continue
                        else:
                            raise

                    # Calculate audio level for TX
                    current_level = self.calculate_audio_level(data)
                    if current_level > self.tx_audio_level:
                        self.tx_audio_level = current_level
                    else:
                        self.tx_audio_level = int(self.tx_audio_level * 0.7 + current_level * 0.3)

                    self.last_audio_capture_time = time.time()
                    self.last_successful_read = time.time()
                    self.audio_capture_active = True

                    if self.config.INPUT_VOLUME != 1.0 and data:
                        try:
                            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
                            data = np.clip(arr * self.config.INPUT_VOLUME, -32768, 32767).astype(np.int16).tobytes()
                        except Exception:
                            pass

                    data = self.process_audio_for_mumble(data)

                    if not self.check_vad(data):
                        continue

                else:
                    # No mixer and no AIOC stream available — wait
                    self.audio_capture_active = False
                    time.sleep(0.1)
                    continue

                # ── Common: reset error count and send to Mumble ─────────────────
                consecutive_errors = 0

                if not self.mumble:
                    if self.mixer and self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Debug] Mumble not connected, cannot send audio")
                    continue

                if not hasattr(self.mumble, 'sound_output') or self.mumble.sound_output is None:
                    if self.mixer and self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Debug] Mumble sound_output not initialized")
                    continue

                if not hasattr(self.mumble.sound_output, 'encoder_framesize') or self.mumble.sound_output.encoder_framesize is None:
                    if self.mixer and self.mixer.call_count % 500 == 1:
                        print(f"\n⚠ Mumble codec still not ready (encoder_framesize is None)")
                        print(f"   Waiting for server negotiation to complete...")
                        print(f"   Check that MUMBLE_SERVER = {self.config.MUMBLE_SERVER} is correct")
                    continue

                try:
                    self.mumble.sound_output.add_sound(data)
                    if self.mixer and self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Debug] Sent {len(data)} bytes to Mumble")
                except Exception as send_err:
                    print(f"\n[Error] Failed to send to Mumble: {send_err}")
                    import traceback
                    traceback.print_exc()

                if self.echolink_source and self.config.RADIO_TO_ECHOLINK:
                    try:
                        self.echolink_source.send_audio(data)
                    except Exception as el_err:
                        if self.config.VERBOSE_LOGGING:
                            print(f"\n[EchoLink] Send error: {el_err}")

                if self.stream_output and self.stream_output.connected:
                    try:
                        if self.mixer and self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] Sending {len(data)} bytes to Icecast stream")
                        self.stream_output.send_audio(data)
                    except Exception as stream_err:
                        if self.config.VERBOSE_LOGGING:
                            print(f"\n[Stream] Send error: {stream_err}")

            except Exception as e:
                consecutive_errors += 1
                self.audio_capture_active = False

                error_type = type(e).__name__
                error_msg = str(e)

                if "-9999" in error_msg or "Unanticipated host error" in error_msg:
                    if consecutive_errors == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Diagnostic] ALSA Error -9999: {error_type}: {error_msg}")
                        try:
                            if self.input_stream:
                                print(f"  Stream state: active={self.input_stream.is_active()}, stopped={self.input_stream.is_stopped()}")
                        except:
                            pass
                else:
                    if consecutive_errors == 1 and self.config.VERBOSE_LOGGING:
                        print(f"\n[Diagnostic] Audio error #{consecutive_errors}: {error_type}: {error_msg}")

                self.last_stream_error = f"{error_type}: {error_msg}"

                if consecutive_errors >= max_consecutive_errors:
                    if self.config.VERBOSE_LOGGING:
                        print(f"\n✗ Audio capture failed {consecutive_errors} times, restarting AIOC stream...")
                    self.restart_audio_input()
                    self.stream_restart_count += 1
                    consecutive_errors = 0
                    time.sleep(1)
                else:
                    time.sleep(0.1)
    
    def restart_audio_input(self):
        """Attempt to restart the audio input stream"""
        # Suppress ALL stderr during restart (ALSA is very noisy)
        import sys
        import os as restart_os
        
        stderr_fd = sys.stderr.fileno()
        saved_stderr_fd = restart_os.dup(stderr_fd)
        devnull_fd = restart_os.open(restart_os.devnull, restart_os.O_WRONLY)
        
        try:
            # Redirect stderr to suppress ALSA messages
            restart_os.dup2(devnull_fd, stderr_fd)
            
            # Signal audio loop to stop reading
            self.restarting_stream = True
            
            # Give current read operation time to complete
            time.sleep(0.15)
            
            if self.config.VERBOSE_LOGGING:
                # Temporarily restore stderr for our diagnostic messages
                restart_os.dup2(saved_stderr_fd, stderr_fd)
                print("  [Diagnostic] Closing input stream...")
                restart_os.dup2(devnull_fd, stderr_fd)
            
            if self.input_stream:
                try:
                    self.input_stream.stop_stream()
                    self.input_stream.close()
                except:
                    pass  # Ignore all errors during close
            
            # Small delay to let ALSA settle
            time.sleep(0.2)
            
            if self.config.VERBOSE_LOGGING:
                restart_os.dup2(saved_stderr_fd, stderr_fd)
                print("  [Diagnostic] Re-finding AIOC device...")
                # Keep stderr suppressed for device enumeration
            
            # Re-find AIOC device (with stderr still suppressed)
            input_idx, _ = self.find_aioc_audio_device()
            
            if input_idx is None:
                if self.config.VERBOSE_LOGGING:
                    restart_os.dup2(saved_stderr_fd, stderr_fd)
                    print("  ✗ Could not find AIOC input device")
                return
            
            # Determine format
            if self.config.AUDIO_BITS == 16:
                audio_format = pyaudio.paInt16
            elif self.config.AUDIO_BITS == 24:
                audio_format = pyaudio.paInt24
            elif self.config.AUDIO_BITS == 32:
                audio_format = pyaudio.paInt32
            else:
                audio_format = pyaudio.paInt16
            
            if self.config.VERBOSE_LOGGING:
                restart_os.dup2(saved_stderr_fd, stderr_fd)
                print(f"  [Diagnostic] Opening new input stream (device {input_idx})...")
                restart_os.dup2(devnull_fd, stderr_fd)
            
            # Recreate input stream — keep frames_per_buffer at 1x (see initial open comment)
            try:
                self.input_stream = self.pyaudio_instance.open(
                    format=audio_format,
                    channels=self.config.AUDIO_CHANNELS,
                    rate=self.config.AUDIO_RATE,
                    input=True,
                    input_device_index=input_idx,
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE,
                    stream_callback=None
                )
                
                if self.config.VERBOSE_LOGGING:
                    restart_os.dup2(saved_stderr_fd, stderr_fd)
                    print("  ✓ Audio input stream restarted")
                    restart_os.dup2(devnull_fd, stderr_fd)
                
                # Give USB/ALSA time to stabilize after restart
                time.sleep(0.1)
                
                # Update stream age
                self.stream_age = time.time()
                
                # Re-enable audio loop
                self.restarting_stream = False
                
            except Exception as stream_error:
                if self.config.VERBOSE_LOGGING:
                    restart_os.dup2(saved_stderr_fd, stderr_fd)
                    print(f"  ✗ Failed to open stream: {type(stream_error).__name__}: {stream_error}")
                    print("  [Diagnostic] Attempting full PyAudio restart...")
                
                # If stream creation fails, try restarting entire PyAudio instance
                self.restart_pyaudio()
            
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                try:
                    restart_os.dup2(saved_stderr_fd, stderr_fd)
                except:
                    pass
                print(f"  ✗ Failed to restart audio input: {type(e).__name__}: {e}")
        finally:
            # Always restore stderr and cleanup
            try:
                restart_os.dup2(saved_stderr_fd, stderr_fd)
                restart_os.close(saved_stderr_fd)
                restart_os.close(devnull_fd)
            except:
                pass
            # Always re-enable audio loop
            self.restarting_stream = False
    
    def restart_pyaudio(self):
        """Restart the entire PyAudio instance (for serious ALSA errors)"""
        try:
            if self.config.VERBOSE_LOGGING:
                print("  [Diagnostic] Terminating PyAudio instance...")
            
            # Close all streams
            if self.input_stream:
                try:
                    self.input_stream.stop_stream()
                    self.input_stream.close()
                except:
                    pass
            
            if self.output_stream:
                try:
                    self.output_stream.stop_stream()
                    self.output_stream.close()
                except:
                    pass
            
            # Terminate PyAudio
            if self.pyaudio_instance:
                try:
                    self.pyaudio_instance.terminate()
                except:
                    pass
            
            time.sleep(0.5)  # Give ALSA time to clean up
            
            if self.config.VERBOSE_LOGGING:
                print("  [Diagnostic] Reinitializing PyAudio...")
            
            # Reinitialize PyAudio
            self.pyaudio_instance = pyaudio.PyAudio()
            
            # Find devices
            input_idx, output_idx = self.find_aioc_audio_device()
            
            # Determine format
            if self.config.AUDIO_BITS == 16:
                audio_format = pyaudio.paInt16
            else:
                audio_format = pyaudio.paInt16
            
            # Recreate streams
            if output_idx is not None:
                self.output_stream = self.pyaudio_instance.open(
                    format=audio_format,
                    channels=self.config.AUDIO_CHANNELS,
                    rate=self.config.AUDIO_RATE,
                    output=True,
                    output_device_index=output_idx,
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE * 4  # Larger buffer for smoother output
                )
            
            if input_idx is not None:
                self.input_stream = self.pyaudio_instance.open(
                    format=audio_format,
                    channels=self.config.AUDIO_CHANNELS,
                    rate=self.config.AUDIO_RATE,
                    input=True,
                    input_device_index=input_idx,
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE * 4  # Larger buffer for smoother output
                )
            
            if self.config.VERBOSE_LOGGING:
                print("  ✓ PyAudio fully restarted")
            
        except Exception as e:
            if self.config.VERBOSE_LOGGING:
                print(f"  ✗ Failed to restart PyAudio: {type(e).__name__}: {e}")
    
    def keyboard_listener_loop(self):
        """Listen for keyboard input to toggle mute states"""
        import sys
        import tty
        import termios
        
        # Note: Priority scheduling removed - system manages all threads
        
        # Save terminal settings
        try:
            old_settings = termios.tcgetattr(sys.stdin)
        except:
            # Not running in a terminal, can't capture keyboard
            if self.config.VERBOSE_LOGGING:
                print("  [Warning] Keyboard controls not available (not in terminal)")
            return
        
        try:
            # Set terminal to raw mode for character-by-character input
            tty.setcbreak(sys.stdin.fileno())
            
            while self.running:
                # Check if input is available (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1).lower()
                    
                    if char == 't':
                        # Toggle TX mute (Mumble → Radio)
                        self.tx_muted = not self.tx_muted
                    
                    elif char == 'r':
                        # Toggle RX mute (Radio → Mumble)
                        self.rx_muted = not self.rx_muted
                    
                    elif char == 'm':
                        # Global mute toggle
                        if self.tx_muted and self.rx_muted:
                            # Both muted → unmute both
                            self.tx_muted = False
                            self.rx_muted = False
                        else:
                            # One or both unmuted → mute both
                            self.tx_muted = True
                            self.rx_muted = True
                    
                    elif char == 's':
                        # Toggle SDR mute
                        if self.sdr_source:
                            self.sdr_muted = not self.sdr_muted
                            self.sdr_source.muted = self.sdr_muted
                            if self.config.VERBOSE_LOGGING:
                                state = "MUTED" if self.sdr_muted else "UNMUTED"
                                print(f"\n[SDR] {state}")
                    
                    elif char == 'd':
                        # Toggle SDR ducking on/off
                        if self.sdr_source:
                            self.sdr_source.duck = not self.sdr_source.duck
                            if self.config.VERBOSE_LOGGING:
                                if self.sdr_source.duck:
                                    print(f"\n[SDR1] Ducking ENABLED (SDR silenced when higher priority audio active)")
                                else:
                                    print(f"\n[SDR1] Ducking DISABLED (SDR mixed at {self.sdr_source.mix_ratio:.1f}x ratio)")
                    
                    elif char == 'x':
                        # Toggle SDR2 mute
                        if self.sdr2_source:
                            self.sdr2_muted = not self.sdr2_muted
                            self.sdr2_source.muted = self.sdr2_muted
                            if self.config.VERBOSE_LOGGING:
                                state = "MUTED" if self.sdr2_muted else "UNMUTED"
                                print(f"\n[SDR2] {state}")
                    
                    elif char == 'v':
                        # Toggle VAD on/off
                        self.config.ENABLE_VAD = not self.config.ENABLE_VAD
                    
                    elif char == ',':
                        # Decrease RX volume (Radio → Mumble)
                        self.config.INPUT_VOLUME = max(0.1, self.config.INPUT_VOLUME - 0.1)
                    
                    elif char == '.':
                        # Increase RX volume (Radio → Mumble)
                        self.config.INPUT_VOLUME = min(3.0, self.config.INPUT_VOLUME + 0.1)
                    
                    elif char == 'n':
                        # Toggle noise gate
                        self.config.ENABLE_NOISE_GATE = not self.config.ENABLE_NOISE_GATE
                    
                    elif char == 'f':
                        # Toggle high-pass filter
                        self.config.ENABLE_HIGHPASS_FILTER = not self.config.ENABLE_HIGHPASS_FILTER
                    
                    elif char == 'a':
                        # Toggle AGC
                        self.config.ENABLE_AGC = not self.config.ENABLE_AGC
                    
                    elif char == 's':
                        # Toggle spectral noise suppression
                        if self.config.ENABLE_NOISE_SUPPRESSION and self.config.NOISE_SUPPRESSION_METHOD == 'spectral':
                            # Currently on with spectral → turn off
                            self.config.ENABLE_NOISE_SUPPRESSION = False
                        else:
                            # Turn on with spectral
                            self.config.ENABLE_NOISE_SUPPRESSION = True
                            self.config.NOISE_SUPPRESSION_METHOD = 'spectral'
                    
                    elif char == 'w':
                        # Toggle Wiener noise suppression
                        if self.config.ENABLE_NOISE_SUPPRESSION and self.config.NOISE_SUPPRESSION_METHOD == 'wiener':
                            # Currently on with wiener → turn off
                            self.config.ENABLE_NOISE_SUPPRESSION = False
                        else:
                            # Turn on with wiener
                            self.config.ENABLE_NOISE_SUPPRESSION = True
                            self.config.NOISE_SUPPRESSION_METHOD = 'wiener'
                    
                    elif char == 'e':
                        # Toggle echo cancellation
                        self.config.ENABLE_ECHO_CANCELLATION = not self.config.ENABLE_ECHO_CANCELLATION
                    
                    elif char == 'p':
                        # Toggle manual PTT mode
                        self.manual_ptt_mode = not self.manual_ptt_mode
                        # Immediately apply the PTT state
                        self.set_ptt_state(self.manual_ptt_mode)
                    
                    elif char in '0123456789':
                        # Play announcement 0-9
                        if self.playback_source:
                            # Use the stored path from file_status
                            stored_path = self.playback_source.file_status[char]['path']
                            stored_filename = self.playback_source.file_status[char].get('filename', '')
                            
                            if stored_path:
                                # File exists, queue it directly
                                if self.config.VERBOSE_LOGGING:
                                    print(f"\n[Keyboard] Key '{char}' pressed - queueing {stored_filename}")
                                self.playback_source.queue_file(stored_path)
                            else:
                                # File not found
                                if self.config.VERBOSE_LOGGING:
                                    if char == '0':
                                        print(f"\n[Playback] Station ID not found (looked for station_id.mp3 or station_id.wav)")
                                    else:
                                        print(f"\n[Playback] No file assigned to key '{char}'")
                        else:
                            if self.config.VERBOSE_LOGGING:
                                print("\n[Keyboard] File playback not enabled")
                    
                    elif char == '-':
                        # Stop playback
                        if self.playback_source:
                            if self.config.VERBOSE_LOGGING:
                                print("\n[Keyboard] Key '-' pressed - stopping playback")
                            self.playback_source.stop_playback()
                        else:
                            if self.config.VERBOSE_LOGGING:
                                print("\n[Keyboard] File playback not enabled")
                
                time.sleep(0.05)
        
        finally:
            # Restore terminal settings
            try:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
            except:
                pass
    
    def status_monitor_loop(self):
        """Monitor PTT release timeout and audio transmit status"""
        # Note: Priority scheduling removed - system manages all threads
        
        status_check_interval = self.config.STATUS_UPDATE_INTERVAL
        last_status_check = time.time()
        
        while self.running:
            current_time = time.time()
            
            # Check PTT timeout or if TX is muted
            if self.ptt_active and not self.manual_ptt_mode:
                # Release PTT if timeout OR if TX is muted
                # (Don't keep PTT keyed when muted!)
                # But don't release if in manual PTT mode
                if current_time - self.last_sound_time > self.config.PTT_RELEASE_DELAY or self.tx_muted:
                    self.set_ptt_state(False)
            
            # Periodic status check and reporting (only if enabled)
            if status_check_interval > 0 and current_time - last_status_check >= status_check_interval:
                last_status_check = current_time
                
                # Decay RX level if no audio received recently
                time_since_rx_audio = current_time - self.last_rx_audio_time
                if time_since_rx_audio > 1.0:  # 1 second timeout
                    self.rx_audio_level = int(self.rx_audio_level * 0.5)  # Fast decay
                    if self.rx_audio_level < 5:
                        self.rx_audio_level = 0
                
                # Check audio transmit status
                time_since_last_capture = current_time - self.last_audio_capture_time
                
                # ANSI color codes
                YELLOW = '\033[93m'
                GREEN = '\033[92m'
                RED = '\033[91m'
                ORANGE = '\033[33m'
                WHITE = '\033[97m'
                GRAY = '\033[90m'
                CYAN = '\033[96m'
                MAGENTA = '\033[95m'
                RESET = '\033[0m'
                
                # Format status with color-coded symbols (fixed width for alignment)
                if self.audio_capture_active and time_since_last_capture < 2.0:
                    status_label = "ACTIVE"  # 6 chars
                    status_symbol = f"{GREEN}✓{RESET}"
                elif time_since_last_capture < 10.0:
                    status_label = "IDLE  "  # 6 chars (padded)
                    status_symbol = f"{ORANGE}⚠{RESET}"
                else:
                    status_label = "STOP  "  # 6 chars (STOPPED shortened + padded)
                    status_symbol = f"{RED}✗{RESET}"
                    # Attempt recovery
                    if self.config.VERBOSE_LOGGING:
                        print(f"\n{WHITE}{status_label}:{RESET} {status_symbol}")
                        print("  Attempting to restart audio input...")
                    self.restart_audio_input()
                    continue
                
                # Print status
                # Status symbols with colors
                mumble_status = f"{GREEN}✓{RESET}" if self.mumble else f"{RED}✗{RESET}"
                # PTT status: Always 4 chars wide for alignment
                if self.manual_ptt_mode:
                    ptt_status = f"{YELLOW}M-{GREEN}ON{RESET}" if self.ptt_active else f"{YELLOW}M-{GRAY}--{RESET}"
                else:
                    # Pad normal mode to 4 chars to match manual mode width
                    ptt_status = f"  {GREEN}ON{RESET}" if self.ptt_active else f"  {GRAY}--{RESET}"
                
                # VAD status: Always 2 chars wide for alignment
                if not self.config.ENABLE_VAD:
                    vad_status = f"{RED}✗ {RESET}"  # VAD disabled (red X + space) - 2 chars
                elif self.vad_active:
                    vad_status = f"{GREEN}🔊{RESET}"  # VAD active (green speaker) - 2 chars (emoji width)
                else:
                    vad_status = f"{GRAY}--{RESET}"  # VAD silent (gray) - 2 chars
                
                # Format audio levels with bar graphs
                # Note: From radio's perspective:
                #   - rx_audio_level = Mumble → Radio (Radio TX) - RED
                #   - tx_audio_level = Radio → Mumble (Radio RX) - GREEN
                radio_tx_bar = self.format_level_bar(self.rx_audio_level, muted=self.tx_muted, color='red')
                
                # RX bar: Show 0% if VAD is blocking (not actually transmitting to Mumble)
                # Only show level when VAD is active (actually sending to Mumble)
                if self.config.ENABLE_VAD and not self.vad_active:
                    radio_rx_bar = self.format_level_bar(0, muted=self.rx_muted, color='green')  # Not transmitting = 0%
                else:
                    radio_rx_bar = self.format_level_bar(self.tx_audio_level, muted=self.rx_muted, color='green')
                
                # SDR bar: Show SDR audio level (CYAN color)
                sdr_bar = ""
                if self.sdr_source:
                    # Always read current level directly from source
                    # Don't cache in self.sdr_audio_level to prevent freezing
                    if hasattr(self.sdr_source, 'audio_level'):
                        current_sdr_level = self.sdr_source.audio_level
                    else:
                        current_sdr_level = 0
                    
                    # Determine display state
                    # Mirror SDRSource.get_audio(): discard when individually muted OR globally muted
                    global_muted = self.tx_muted and self.rx_muted
                    sdr_muted = self.sdr_muted or global_muted
                    sdr_ducked = self.sdr_ducked if not sdr_muted else False
                    
                    # Format: SDR1: (no mode indicator here - it goes in proc_flags)
                    sdr_bar = f" {WHITE}SDR1:{RESET}" + self.format_level_bar(current_sdr_level, muted=sdr_muted, ducked=sdr_ducked, color='cyan')
                
                # SDR2 bar: Show SDR2 audio level (MAGENTA color for differentiation)
                sdr2_bar = ""
                if self.sdr2_source:
                    # Always read current level directly from source
                    if hasattr(self.sdr2_source, 'audio_level'):
                        current_sdr2_level = self.sdr2_source.audio_level
                    else:
                        current_sdr2_level = 0
                    
                    # Determine display state
                    sdr2_muted = self.sdr2_muted or global_muted
                    sdr2_ducked = self.sdr2_ducked if not sdr2_muted else False
                    
                    # Format: SDR2: with magenta color
                    sdr2_bar = f" {WHITE}SDR2:{RESET}" + self.format_level_bar(current_sdr2_level, muted=sdr2_muted, ducked=sdr2_ducked, color='magenta')
                
                # Add diagnostics if there have been restarts (fixed width: always 6 chars like " R:123" or "      ")
                # This prevents the status line from jumping when restarts occur
                if self.stream_restart_count > 0:
                    diag = f" {WHITE}R:{YELLOW}{self.stream_restart_count}{RESET}"
                else:
                    diag = "      "  # 6 spaces to match " R:XX" width
                
                # Show VAD level in dB if enabled (white label, yellow numbers, fixed width: always 6 chars like " -100dB" or "      ")
                vad_info = f" {YELLOW}{self.vad_envelope:4.0f}{RESET}{WHITE}dB{RESET}" if self.config.ENABLE_VAD else "       "
                
                # Show RX volume (white label, yellow number, always 3 chars for number)
                vol_info = f" {WHITE}Vol:{YELLOW}{self.config.INPUT_VOLUME:3.1f}{RESET}{WHITE}x{RESET}"
                
                # Show audio processing status (compact single-letter flags)
                # This now appears AFTER file status, so width changes don't matter
                proc_flags = []
                if self.config.ENABLE_NOISE_GATE: proc_flags.append("N")
                if self.config.ENABLE_HIGHPASS_FILTER: proc_flags.append("F")
                if self.config.ENABLE_AGC: proc_flags.append("A")
                if self.config.ENABLE_NOISE_SUPPRESSION:
                    if self.config.NOISE_SUPPRESSION_METHOD == 'spectral': proc_flags.append("S")
                    elif self.config.NOISE_SUPPRESSION_METHOD == 'wiener': proc_flags.append("W")
                if self.config.ENABLE_ECHO_CANCELLATION: proc_flags.append("E")
                if not self.config.ENABLE_STREAM_HEALTH: proc_flags.append("X")  # X shows stream health is OFF
                # D flag: SDR ducking enabled (only show if SDR is present)
                if self.sdr_source and hasattr(self.sdr_source, 'duck') and self.sdr_source.duck:
                    proc_flags.append("D")
                
                # Only show brackets if there are flags (saves space)
                proc_info = f" {WHITE}[{YELLOW}{','.join(proc_flags)}{WHITE}]{RESET}" if proc_flags else ""
                
                # File status indicators (if playback enabled)
                file_status_info = ""
                if self.playback_source:
                    file_status_info = " " + self.playback_source.get_file_status_string()
                
                # Extra padding to clear any orphaned text when line shortens
                # Order: ...Vol → FileStatus → ProcessingFlags → Diagnostics
                print(f"\r{WHITE}{status_label}:{RESET} {status_symbol} {WHITE}M:{RESET}{mumble_status} {WHITE}PTT:{RESET}{ptt_status} {WHITE}VAD:{RESET}{vad_status}{vad_info} {WHITE}TX:{RESET}{radio_tx_bar} {WHITE}RX:{RESET}{radio_rx_bar}{sdr_bar}{sdr2_bar}{vol_info}{file_status_info}{proc_info}{diag}     ", end="", flush=True)
            
            # Always check for stuck audio (even if status reporting is disabled)
            elif status_check_interval == 0:
                time_since_last_capture = current_time - self.last_audio_capture_time
                if time_since_last_capture > 30.0:  # 30 seconds with no audio = stuck
                    if self.config.VERBOSE_LOGGING:
                        print(f"\n✗ Audio TX stuck (no audio for {int(time_since_last_capture)}s)")
                        print("  Attempting to restart audio input...")
                    self.restart_audio_input()
                    time.sleep(5)  # Wait before checking again
            
            time.sleep(0.1)
    
    def run(self):
        """Main application"""
        print("=" * 60)
        print("Mumble-to-Radio Gateway via AIOC")
        print("=" * 60)
        print()
        
        # Initialize AIOC (optional - gateway can work without it)
        self.aioc_available = self.setup_aioc()
        if not self.aioc_available:
            print("⚠ AIOC not found - continuing without radio interface")
            print("  Gateway will operate in Mumble + SDR mode")
        
        # Initialize Audio
        if not self.setup_audio():
            self.cleanup()
            return False
        
        # Initialize Mumble
        if not self.setup_mumble():
            self.cleanup()
            return False
        
        print()
        print("=" * 60)
        print("Gateway Active!")
        print("  Mumble → AIOC output → Radio TX (auto PTT)")
        print("  Radio RX → AIOC input → Mumble (VOX)")
        
        # Show audio processing status
        processing_enabled = []
        if self.config.ENABLE_HIGHPASS_FILTER:
            processing_enabled.append(f"HPF@{self.config.HIGHPASS_CUTOFF_FREQ}Hz")
        if self.config.ENABLE_NOISE_SUPPRESSION:
            processing_enabled.append(f"NS({self.config.NOISE_SUPPRESSION_METHOD})")
        if self.config.ENABLE_NOISE_GATE:
            processing_enabled.append(f"Gate@{self.config.NOISE_GATE_THRESHOLD}dB")
        
        if processing_enabled:
            print(f"  Audio Processing: {', '.join(processing_enabled)}")
        
        # Show VAD status
        if self.config.ENABLE_VAD:
            print(f"  Voice Activity Detection: ON (threshold: {self.config.VAD_THRESHOLD}dB)")
            print(f"    → Only sends audio to Mumble when radio signal detected")
        else:
            print(f"  Voice Activity Detection: OFF (continuous transmission)")
        
        # Show stream health management
        if self.config.ENABLE_STREAM_HEALTH and self.config.STREAM_RESTART_INTERVAL > 0:
            print(f"  Stream Health: Auto-restart every {self.config.STREAM_RESTART_INTERVAL}s (when idle {self.config.STREAM_RESTART_IDLE_TIME}s+)")
        else:
            print(f"  Stream Health: DISABLED (may experience -9999 errors if streams get stuck)")
        
        # Print file mapping if playback is enabled
        if self.config.ENABLE_PLAYBACK and hasattr(self, 'playback_source') and self.playback_source:
            print()  # Blank line
            self.playback_source.print_file_mapping()
            print()  # Blank line before keyboard controls
        
        print("Press Ctrl+C to exit")
        print("Keyboard Controls:")
        print("  Mute:  't'=TX | 'r'=RX | 'm'=Global | 's'=SDR1 | 'x'=SDR2  |  Audio: 'v'=VAD | ','=Vol- | '.'=Vol+")
        print("  Proc:  'n'=Gate | 'f'=HPF | 'a'=AGC | 'w'=Wiener | 'e'=Echo")
        print("  SDR:   'd'=SDR1 Duck toggle")
        print("  PTT:   'p'=Manual PTT Toggle (override auto-PTT)")
        if self.config.ENABLE_PLAYBACK:
            print("  Play:  '1-9'=Announcements | '0'=StationID | '-'=Stop")
        print("=" * 60)
        print()
        
        # Print status line legend (only in verbose mode)
        if self.config.VERBOSE_LOGGING:
            print("Status Line Legend:")
            print("  [✓/⚠/✗]  = Audio capture status (ACTIVE/IDLE/STOPPED)")
            print("  M:✓/✗    = Mumble connected/disconnected")
            print("  PTT:ON/M-ON/-- = Push-to-talk (auto/manual-on/off)")
            print("  VAD:✗/🔊/-- = VAD disabled/active/silent (dB = current level)")
            print("  TX:[bar] = Mumble → Radio audio level")
            print("  RX:[bar] = Radio → Mumble audio level")
            print("  SDR1:[bar] = SDR1 receiver audio level (cyan)")
            print("  SDR2:[bar] = SDR2 receiver audio level (magenta)")
            print("  Vol:X.Xx = RX volume multiplier (Radio → Mumble gain)")
            print("  1234567890 = File status (green=loaded, red=playing, white=empty)")
            print("  [N,F,A,W,E,D] = Processing: N=NoiseGate F=HPF A=AGC W=Wiener E=Echo D=SDR1Duck")
            print("  R:n      = Stream restart count (only if >0)")
            print()
        
        # Start audio transmit thread
        tx_thread = threading.Thread(target=self.audio_transmit_loop, daemon=True)
        tx_thread.start()
        
        # Start status monitor thread (handles PTT timeout and status reporting)
        status_thread = threading.Thread(target=self.status_monitor_loop, daemon=True)
        status_thread.start()
        
        # Start keyboard listener thread
        keyboard_thread = threading.Thread(target=self.keyboard_listener_loop, daemon=True)
        keyboard_thread.start()
        
        # Main loop
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up resources"""
        if self.config.VERBOSE_LOGGING:
            print("\nCleaning up...")
        
        # Signal threads to stop
        self.running = False
        
        # Give threads time to finish current operations
        time.sleep(0.2)
        
        # Close stream output pipe first (before stopping other things)
        if hasattr(self, 'stream_output') and self.stream_output:
            try:
                self.stream_output.cleanup()
                if self.config.VERBOSE_LOGGING:
                    print("  Stream output closed")
            except:
                pass
        
        # Release PTT
        if self.ptt_active:
            self.set_ptt_state(False)
        
        # Close Mumble connection first (stops audio callbacks)
        if self.mumble:
            try:
                self.mumble.stop()
            except:
                pass
        
        # Small delay to let Mumble fully stop
        time.sleep(0.1)
        
        # Now close audio streams (with better error handling for ALSA)
        if self.sdr_source:
            try:
                self.sdr_source.cleanup()
                if self.config.VERBOSE_LOGGING:
                    print("  SDR1 audio closed")
            except Exception as e:
                pass  # Suppress ALSA errors during shutdown
        
        if self.sdr2_source:
            try:
                self.sdr2_source.cleanup()
                if self.config.VERBOSE_LOGGING:
                    print("  SDR2 audio closed")
            except Exception as e:
                pass  # Suppress ALSA errors during shutdown
        
        if self.input_stream:
            try:
                # Stop stream first (prevents ALSA mmap errors)
                if self.input_stream.is_active():
                    self.input_stream.stop_stream()
                time.sleep(0.05)  # Give ALSA time to clean up
                self.input_stream.close()
            except Exception as e:
                pass  # Suppress ALSA errors during shutdown
        
        if self.output_stream:
            try:
                # Stop stream first
                if self.output_stream.is_active():
                    self.output_stream.stop_stream()
                time.sleep(0.05)  # Give ALSA time to clean up
                self.output_stream.close()
            except Exception as e:
                pass  # Suppress ALSA errors during shutdown
        
        if self.pyaudio_instance:
            try:
                self.pyaudio_instance.terminate()
            except Exception as e:
                pass  # Suppress errors
        
        # Close AIOC device
        if self.aioc_device:
            try:
                self.aioc_device.close()
            except:
                pass
        
        print("Shutdown complete")

def main():
    # Find config file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_file = os.path.join(script_dir, "gateway_config.txt")
    
    # Load configuration
    config = Config(config_file)
    
    # Create and run gateway
    gateway = MumbleRadioGateway(config)
    
    # Handle signals for clean shutdown
    def signal_handler(sig, frame):
        gateway.running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    gateway.run()

    if gateway.restart_requested:
        print("\nRestarting gateway...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

if __name__ == "__main__":
    main()
