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
from struct import Struct
import select  # For non-blocking keyboard input

# Check for required libraries
try:
    import hid
except ImportError:
    print("ERROR: hidapi library not found!")
    print("Install it with: pip3 install hidapi --break-system-packages")
    sys.exit(1)

try:
    from pymumble_py3 import Mumble
    from pymumble_py3.callbacks import PYMUMBLE_CLBK_SOUNDRECEIVED
    import pymumble_py3.constants as mumble_constants
except ImportError:
    print("ERROR: pymumble library not found!")
    print("Install from: https://github.com/azlux/pymumble")
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
            'MUMBLE_BITRATE': 72000,
            'MUMBLE_VBR': True,
            'MUMBLE_JITTER_BUFFER': 10,
            'AIOC_PTT_CHANNEL': 3,
            'PTT_RELEASE_DELAY': 0.3,
            'PTT_ACTIVATION_DELAY': 0.0,
            'AIOC_VID': 0x1209,
            'AIOC_PID': 0x7388,
            'AIOC_INPUT_DEVICE': -1,
            'AIOC_OUTPUT_DEVICE': -1,
            'ENABLE_AGC': False,
            'ENABLE_NOISE_SUPPRESSION': False,
            'NOISE_SUPPRESSION_METHOD': 'none',
            'NOISE_SUPPRESSION_STRENGTH': 0.6,
            'ENABLE_NOISE_GATE': False,
            'NOISE_GATE_THRESHOLD': -32,
            'NOISE_GATE_ATTACK': 10,
            'NOISE_GATE_RELEASE': 100,
            'ENABLE_HIGHPASS_FILTER': True,
            'HIGHPASS_CUTOFF_FREQ': 120,
            'ENABLE_ECHO_CANCELLATION': False,
            'INPUT_VOLUME': 1.0,
            'OUTPUT_VOLUME': 1.0,
            'MUMBLE_LOOP_RATE': 0.01,
            'MUMBLE_STEREO': False,
            'MUMBLE_RECONNECT': True,
            'MUMBLE_DEBUG': False,
            'NETWORK_TIMEOUT': 10,
            'TCP_NODELAY': True,
            'VERBOSE_LOGGING': True,
            'STATUS_UPDATE_INTERVAL': 2,
            'MAX_MUMBLE_BUFFER_SECONDS': 1.0,
            'BUFFER_MANAGEMENT_VERBOSE': False,
            'ENABLE_VAD': True,
            'VAD_THRESHOLD': -33,
            'VAD_ATTACK': 20,
            'VAD_RELEASE': 300,
            'VAD_MIN_DURATION': 150,
            'ENABLE_STREAM_HEALTH': False,
            'STREAM_RESTART_INTERVAL': 60,
            'STREAM_RESTART_IDLE_TIME': 3,
            'ENABLE_VOX': True,
            'VOX_THRESHOLD': -40,
            'VOX_ATTACK_TIME': 50,
            'VOX_RELEASE_TIME': 500,
            # File Playback
            'ENABLE_PLAYBACK': False,
            'PLAYBACK_DIRECTORY': './audio/',
            'PLAYBACK_ANNOUNCEMENT_FILE': '',
            'PLAYBACK_ANNOUNCEMENT_INTERVAL': 0,  # seconds, 0 = disabled
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
                        
                        # Skip if value is empty
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
        if not self.gateway.input_stream or self.gateway.restarting_stream:
            return None, False
        
        try:
            # Read audio from AIOC with error recovery
            try:
                data = self.gateway.input_stream.read(chunk_size, exception_on_overflow=False)
            except IOError as io_err:
                # Handle buffer overflow gracefully
                if io_err.errno == -9981:  # Input overflow
                    # Try to clear the buffer
                    try:
                        self.gateway.input_stream.read(chunk_size * 2, exception_on_overflow=False)
                    except:
                        pass
                    return None, False
                else:
                    raise  # Re-raise other IOErrors
            
            # Calculate audio level (for status display)
            current_level = self.gateway.calculate_audio_level(data)
            if current_level > self.gateway.tx_audio_level:
                self.gateway.tx_audio_level = current_level
            else:
                self.gateway.tx_audio_level = int(self.gateway.tx_audio_level * 0.7 + current_level * 0.3)
            
            # Update capture time
            self.gateway.last_audio_capture_time = time.time()
            self.gateway.last_successful_read = time.time()
            self.gateway.audio_capture_active = True
            
            # Apply volume if needed
            if self.volume != 1.0 and data:
                import array
                samples = array.array('h', data)
                samples = array.array('h', [int(s * self.volume) for s in samples])
                data = samples.tobytes()
            
            # Apply audio processing
            data = self.gateway.process_audio_for_mumble(data)
            
            # Check VAD - should we transmit this?
            should_transmit = self.gateway.check_vad(data)
            
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
        self.volume = 1.0
        
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
        """Check if announcement files exist"""
        import os
        
        # Check station_id (key 0) - auto-detect
        for ext in ['.mp3', '.ogg', '.flac', '.m4a', '.wav']:
            path = os.path.join(self.announcement_directory, f'station_id{ext}')
            if os.path.exists(path):
                self.file_status['0']['exists'] = True
                self.file_status['0']['path'] = path
                break
        
        # Check announcements 1-9
        for i in range(1, 10):
            for ext in ['.mp3', '.ogg', '.flac', '.m4a', '.wav']:
                path = os.path.join(self.announcement_directory, f'announcement{i}{ext}')
                if os.path.exists(path):
                    self.file_status[str(i)]['exists'] = True
                    self.file_status[str(i)]['path'] = path
                    break
    
    def get_file_status_string(self):
        """Get status indicator string for display"""
        # ANSI color codes
        WHITE = '\033[97m'
        GREEN = '\033[92m'
        RED = '\033[91m'
        RESET = '\033[0m'
        
        status_str = ""
        # Show all 10 slots: 1-9 then 0 (station_id at end)
        for key in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']:
            if self.file_status[key]['playing']:
                # Red when playing
                status_str += f"{RED}[{key}]{RESET}"
            elif self.file_status[key]['exists']:
                # Green when file exists
                status_str += f"{GREEN}[{key}]{RESET}"
            else:
                # White when no file
                status_str += f"{WHITE}[{key}]{RESET}"
        
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
            if 'station_id' in filename:
                file_key = '0'
            else:
                # Check for announcement1-9
                for i in range(1, 10):
                    if f'announcement{i}' in filename:
                        file_key = str(i)
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
                    print(f"\n[Playback] Error: {file_ext.upper()} not supported without soundfile")
                    if self.gateway.config.VERBOSE_LOGGING:
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
        
        # Debug: Log when this is called
        if hasattr(self.gateway, 'mixer') and self.gateway.mixer.call_count % 100 == 1 and self.gateway.config.VERBOSE_LOGGING:
            print(f"  [FilePlayback.get_audio] Called. Current file: {self.current_file}, Queue: {len(self.playlist)}")
        
        # Check for periodic announcements
        self.check_periodic_announcement()
        
        # If no file is playing, try to load next from queue
        if not self.current_file and self.playlist:
            if not self.load_next_file():
                return None, False
        
        # No file playing
        if not self.file_data:
            return None, False
        
        # Calculate chunk size in bytes (16-bit = 2 bytes per sample)
        chunk_bytes = chunk_size * self.config.AUDIO_CHANNELS * 2
        
        # Check if we have enough data left
        if self.file_position >= len(self.file_data):
            # File finished
            if self.gateway.config.VERBOSE_LOGGING:
                print(f"\n[Playback] Finished: {os.path.basename(self.current_file) if self.current_file else 'unknown'}")
            
            # Mark file as not playing
            if self.current_file:
                filename = os.path.basename(self.current_file)
                if 'station_id' in filename:
                    self.file_status['0']['playing'] = False
                else:
                    # Check announcement1-9
                    for i in range(1, 10):
                        if f'announcement{i}' in filename:
                            self.file_status[str(i)]['playing'] = False
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
            import array
            samples = array.array('h', chunk)
            samples = array.array('h', [int(s * self.volume) for s in samples])
            chunk = samples.tobytes()
        
        # File playback triggers PTT
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


class AudioMixer:
    """Mix audio from multiple sources with priority handling"""
    def __init__(self, config):
        self.config = config
        self.sources = []
        self.mixing_mode = 'simultaneous'  # Mix all sources together
        self.call_count = 0  # Debug counter
        
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
        Returns: (mixed_audio, ptt_required, active_sources)
        """
        self.call_count += 1
        
        # Debug output every 100 calls
        if self.call_count % 100 == 0 and self.config.VERBOSE_LOGGING:
            print(f"\n[Mixer Debug] Called {self.call_count} times, {len(self.sources)} sources")
            for src in self.sources:
                print(f"  Source: {src.name}, enabled={src.enabled}, priority={src.priority}")
        
        if not self.sources:
            return None, False, []
        
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
                    return audio, ptt and source.ptt_control, [source.name]
            
            # No sources had audio
            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                print(f"  [Mixer] No sources returned audio")
            return None, False, []
        
        # Simultaneous mode: mix all active sources
        elif self.mixing_mode == 'simultaneous':
            return self._mix_simultaneous(chunk_size)
        
        # Duck mode: reduce volume of lower priority when higher priority active
        elif self.mixing_mode == 'duck':
            return self._mix_with_ducking(chunk_size)
        
        return None, False, []
    
    def _mix_simultaneous(self, chunk_size):
        """Mix all active sources together"""
        mixed_audio = None
        ptt_required = False
        active_sources = []
        ptt_audio = None  # Separate PTT audio
        non_ptt_audio = None  # Non-PTT audio
        
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
            
            # Separate PTT and non-PTT sources
            if ptt and source.ptt_control:
                ptt_required = True
                # PTT source - keep at full volume, don't mix
                if ptt_audio is None:
                    ptt_audio = audio
                else:
                    # Multiple PTT sources - mix them
                    ptt_audio = self._mix_audio_streams(ptt_audio, audio, 0.5)
            else:
                # Non-PTT source (radio RX)
                if non_ptt_audio is None:
                    non_ptt_audio = audio
                else:
                    non_ptt_audio = self._mix_audio_streams(non_ptt_audio, audio, 0.5)
        
        # Priority: PTT audio always wins (full volume, no mixing with radio)
        if ptt_audio is not None:
            mixed_audio = ptt_audio
            if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                print(f"  [Mixer-Simultaneous] Using PTT audio at FULL VOLUME (not mixing with radio)")
        elif non_ptt_audio is not None:
            mixed_audio = non_ptt_audio
        
        if self.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
            print(f"  [Mixer-Simultaneous] Result: {len(active_sources)} active sources, PTT={ptt_required}")
        
        return mixed_audio, ptt_required, active_sources
    
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
        
        return mixed_audio, ptt_required, active_sources
    
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
        import array
        samples = array.array('h', audio)
        samples = array.array('h', [int(s * volume) for s in samples])
        return samples.tobytes()
    
    def get_status(self):
        """Get status of all sources"""
        status = []
        for source in self.sources:
            status.append(source.get_status())
        return status


class MumbleRadioGateway:
    def __init__(self, config):
        self.config = config
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
        
        # Audio processing state
        self.noise_profile = None  # For spectral subtraction
        self.gate_envelope = 0.0  # For noise gate smoothing
        self.highpass_state = None  # For high-pass filter state
        
        # Initialize audio mixer and sources
        self.mixer = AudioMixer(config)
        self.radio_source = None  # Will be initialized after AIOC setup
    
    def calculate_audio_level(self, pcm_data):
        """Calculate RMS audio level from PCM data (0-100 scale)"""
        try:
            import array
            import math
            
            # Convert bytes to 16-bit signed integers
            samples = array.array('h', pcm_data)
            
            if len(samples) == 0:
                return 0
            
            # Calculate RMS (root mean square)
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / len(samples))
            
            # Normalize to 0-100 scale (32767 is max for 16-bit audio)
            # Use log scale for better display
            if rms > 0:
                # Convert to dB scale then to 0-100
                db = 20 * math.log10(rms / 32767.0)
                # Map -60dB to 0dB as 0 to 100
                level = max(0, min(100, (db + 60) * (100/60)))
                return int(level)
            return 0
            
        except Exception:
            return 0
    
    def format_level_bar(self, level, muted=False, color='green'):
        """Format audio level as a visual bar (0-100 scale) with optional color
        
        Args:
            level: Audio level 0-100
            muted: Whether this channel is muted
            color: 'green' for RX, 'red' for TX
        """
        # ANSI color codes
        YELLOW = '\033[93m'
        GREEN = '\033[92m'
        RED = '\033[91m'
        WHITE = '\033[97m'
        RESET = '\033[0m'
        
        # Choose bar color
        bar_color = GREEN if color == 'green' else RED
        
        # Show MUTE if muted (fixed width, colored)
        if muted:
            return f"{WHITE}[{bar_color}---MUTE---{WHITE}]{RESET}  {bar_color}M{RESET} "
        
        # Create a 10-character bar graph
        bar_length = 10
        filled = int((level / 100.0) * bar_length)
        
        # Always use 3-character percentage field (right-aligned)
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
            import array
            import math
            
            # Convert bytes to samples
            samples = array.array('h', pcm_data)
            if len(samples) == 0:
                return False
            
            # Calculate RMS level
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / len(samples))
            
            # Convert to dB
            if rms > 0:
                db_level = 20 * math.log10(rms / 32767.0)
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
            import array
            import math
            
            # Convert bytes to 16-bit signed integers
            samples = array.array('h', pcm_data)
            if len(samples) == 0:
                return False
            
            # Calculate RMS level
            sum_squares = sum(s * s for s in samples)
            rms = math.sqrt(sum_squares / len(samples))
            
            # Convert to dB
            if rms > 0:
                db = 20 * math.log10(rms / 32767.0)
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
            return
            
        state = 1 if state_on else 0
        iomask = 1 << (self.config.AIOC_PTT_CHANNEL - 1)
        iodata = state << (self.config.AIOC_PTT_CHANNEL - 1)
        data = Struct("<BBBBB").pack(0, 0, iodata, iomask, 0)
        self.aioc_device.write(bytes(data))
        
        # Update PTT state (status line will show it)
        self.ptt_active = state_on
    
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
                    import array
                    samples = array.array('h', pcm)
                    samples = array.array('h', [int(s * self.config.OUTPUT_VOLUME) for s in samples])
                    pcm = samples.tobytes()
                
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
            
            # Initialize radio source with mixer
            try:
                self.radio_source = AIOCRadioSource(self.config, self)
                self.mixer.add_source(self.radio_source)
                if self.config.VERBOSE_LOGGING:
                    print("✓ Radio audio source added to mixer")
            except Exception as source_err:
                print(f"⚠ Warning: Could not initialize radio source: {source_err}")
                print("  Continuing without mixer (fallback mode)")
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
                    if os.path.exists(audio_dir):
                        audio_files = []
                        for ext in ['*.mp3', '*.ogg', '*.flac', '*.m4a', '*.wav']:
                            audio_files.extend(glob.glob(os.path.join(audio_dir, ext)))
                        
                        if audio_files:
                            print(f"  Audio directory: {os.path.abspath(audio_dir)}")
                            print(f"  Found {len(audio_files)} audio file(s):")
                            for f in sorted(audio_files):
                                size_kb = os.path.getsize(f) / 1024
                                print(f"    • {os.path.basename(f)} ({size_kb:.1f} KB)")
                        else:
                            print(f"  ⚠ No audio files found in {os.path.abspath(audio_dir)}")
                            print(f"    Create directory: mkdir -p {audio_dir}")
                            print(f"    Add files: cp your_file.mp3 {audio_dir}")
                    else:
                        print(f"  ⚠ Audio directory not found: {os.path.abspath(audio_dir)}")
                        print(f"    Create it with: mkdir -p {audio_dir}")
                    
                    # Show keyboard controls
                    print("  Keyboard controls:")
                    print("    '1' = Play announcement1.mp3 (or .wav, .ogg, etc.)")
                    print("    '2' = Play announcement2.mp3")
                    print("    '3' = Play station ID")
                    
                except Exception as playback_err:
                    print(f"⚠ Warning: Could not initialize playback source: {playback_err}")
                    self.playback_source = None
            else:
                self.playback_source = None
            
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
                                    
                                    # Show available audio files
                                    import os
                                    import glob
                                    audio_dir = self.playback_source.announcement_directory
                                    if os.path.exists(audio_dir):
                                        audio_files = []
                                        for ext in ['*.mp3', '*.ogg', '*.flac', '*.m4a', '*.wav']:
                                            audio_files.extend(glob.glob(os.path.join(audio_dir, ext)))
                                        
                                        if audio_files:
                                            print(f"  Found {len(audio_files)} audio file(s) in {audio_dir}")
                                        else:
                                            print(f"  ⚠ No audio files in {audio_dir}")
                                    
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
    
    def audio_transmit_loop(self):
        """Continuously capture audio from sources and send to Mumble via mixer"""
        if self.config.VERBOSE_LOGGING:
            print("✓ Audio transmit thread started (with mixer)")
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.running:
            # Stream health check (same as before)
            if self.input_stream and not self.restarting_stream:
                try:
                    current_time = time.time()
                    time_since_creation = current_time - self.stream_age
                    time_since_vad_active = current_time - self.last_vox_active_time if hasattr(self, 'last_vox_active_time') else 999
                    
                    # Check if we should do proactive restart
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
                    
                    # Check if stream is active before reading
                    if not self.input_stream.is_active():
                        if self.config.VERBOSE_LOGGING:
                            print("\n[Diagnostic] Stream became inactive, restarting...")
                        consecutive_errors = max_consecutive_errors
                        raise Exception("Stream inactive")
                    
                    # Get audio - use mixer if available, otherwise direct read
                    if self.radio_source and self.mixer:
                        # Use mixer (Phase 1 mode)
                        data, ptt_required, active_sources = self.mixer.get_mixed_audio(self.config.AUDIO_CHUNK_SIZE)
                        
                        if data is None:
                            # No audio from any source
                            self.audio_capture_active = False
                            time.sleep(0.01)
                            continue
                        
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
                                self.rx_audio_level = current_level  # Fast attack (reusing rx_audio_level for TX display)
                            else:
                                self.rx_audio_level = int(self.rx_audio_level * 0.7 + current_level * 0.3)  # Slow decay
                            
                            # Activate PTT if not already active and not muted
                            if not self.ptt_active and not self.tx_muted and not self.manual_ptt_mode:
                                self.set_ptt_state(True)
                            
                            # Send audio to radio output (like Mumble RX does)
                            if self.output_stream and not self.tx_muted:
                                try:
                                    # Apply output volume
                                    pcm = data
                                    if self.config.OUTPUT_VOLUME != 1.0:
                                        import array
                                        samples = array.array('h', pcm)
                                        samples = array.array('h', [int(s * self.config.OUTPUT_VOLUME) for s in samples])
                                        pcm = samples.tobytes()
                                    
                                    # Write to output stream
                                    # Use exception_on_overflow=False to handle buffer issues gracefully
                                    try:
                                        self.output_stream.write(pcm, exception_on_overflow=False)
                                    except TypeError:
                                        # Older PyAudio doesn't support exception_on_overflow parameter
                                        self.output_stream.write(pcm)
                                    
                                    # Small sleep to let audio subsystem process
                                    # Helps prevent stuttering on slower systems
                                    time.sleep(0.001)  # 1ms
                                    
                                    if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                                        print(f"\n[Debug] Sent {len(pcm)} bytes to radio TX output")
                                except IOError as io_err:
                                    # Handle output buffer issues
                                    if self.config.VERBOSE_LOGGING and consecutive_errors == 0:
                                        print(f"\n[Warning] Output stream buffer issue: {io_err}")
                                    # Don't count as critical error - just skip this chunk
                                except Exception as tx_err:
                                    if self.config.VERBOSE_LOGGING:
                                        print(f"\n[Error] Failed to send to radio TX: {tx_err}")
                            
                            # Skip sending to Mumble - this is TX audio, not RX
                            continue
                        
                        # No PTT required (radio RX) - send to Mumble as usual
                        # (falls through to Mumble sending code below)
                        
                    else:
                        # Fallback to direct read (original mode)
                        try:
                            data = self.input_stream.read(
                                self.config.AUDIO_CHUNK_SIZE, 
                                exception_on_overflow=False
                            )
                        except IOError as io_err:
                            # Handle buffer overflow gracefully
                            if io_err.errno == -9981:  # Input overflow
                                if self.config.VERBOSE_LOGGING and consecutive_errors == 0:
                                    print("\n[Diagnostic] Input overflow, clearing buffer...")
                                # Try to clear the buffer
                                try:
                                    self.input_stream.read(self.config.AUDIO_CHUNK_SIZE * 2, exception_on_overflow=False)
                                except:
                                    pass
                                time.sleep(0.05)
                                continue
                            else:
                                raise  # Re-raise other IOErrors
                        
                        # Calculate audio level for TX (with smoothing)
                        current_level = self.calculate_audio_level(data)
                        if current_level > self.tx_audio_level:
                            self.tx_audio_level = current_level  # Fast attack
                        else:
                            self.tx_audio_level = int(self.tx_audio_level * 0.7 + current_level * 0.3)  # Slow decay
                        
                        # Mark that we successfully captured audio
                        self.last_audio_capture_time = time.time()
                        self.last_successful_read = time.time()
                        self.audio_capture_active = True
                        
                        # Apply input volume if needed
                        if self.config.INPUT_VOLUME != 1.0 and data:
                            try:
                                import array
                                samples = array.array('h', data)
                                samples = array.array('h', [int(s * self.config.INPUT_VOLUME) for s in samples])
                                data = samples.tobytes()
                            except Exception:
                                pass  # If volume adjustment fails, use original data
                        
                        # Apply audio processing to clean up noisy radio audio
                        data = self.process_audio_for_mumble(data)
                        
                        # Check VAD - only send to Mumble if radio signal is detected
                        should_transmit = self.check_vad(data)
                        
                        if not should_transmit:
                            # No signal detected, don't send to Mumble
                            continue
                    
                    # Reset consecutive errors on success
                    consecutive_errors = 0
                    
                    # Check RX mute - if muted, don't send to Mumble
                    if self.rx_muted:
                        if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] RX is muted, not sending to Mumble")
                        continue
                    
                    # Check if Mumble is connected and ready
                    if not self.mumble:
                        if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] Mumble not connected, cannot send audio")
                        continue
                    
                    if not hasattr(self.mumble, 'sound_output') or self.mumble.sound_output is None:
                        if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] Mumble sound_output not initialized")
                        continue
                    
                    # Check if codec is initialized (encoder_framesize must not be None)
                    if not hasattr(self.mumble.sound_output, 'encoder_framesize') or self.mumble.sound_output.encoder_framesize is None:
                        # Only print warning occasionally, not every time
                        if self.mixer.call_count % 500 == 1:  # Every ~25 seconds
                            print(f"\n⚠ Mumble codec still not ready (encoder_framesize is None)")
                            print(f"   This means Mumble hasn't finished initializing")
                            print(f"   Waiting for server negotiation to complete...")
                            print(f"   Check that MUMBLE_SERVER = {self.config.MUMBLE_SERVER} is correct")
                        continue
                    
                    # Send audio directly to Mumble
                    try:
                        self.mumble.sound_output.add_sound(data)
                        if self.mixer.call_count % 100 == 1 and self.config.VERBOSE_LOGGING:
                            print(f"\n[Debug] Sent {len(data)} bytes to Mumble")
                    except Exception as send_err:
                        print(f"\n[Error] Failed to send to Mumble: {send_err}")
                        import traceback
                        traceback.print_exc()
                        
                except Exception as e:
                    consecutive_errors += 1
                    self.audio_capture_active = False
                    
                    # Log detailed error information
                    error_type = type(e).__name__
                    error_msg = str(e)
                    
                    # Enhanced diagnostics for -9999 errors
                    if "-9999" in error_msg or "Unanticipated host error" in error_msg:
                        if consecutive_errors == 1:
                            if self.config.VERBOSE_LOGGING:
                                print(f"\n[Diagnostic] ALSA Error -9999 detected")
                                print(f"  Error details: {error_type}: {error_msg}")
                                
                                # Check if stream is still active
                                try:
                                    if self.input_stream:
                                        is_active = self.input_stream.is_active()
                                        is_stopped = self.input_stream.is_stopped()
                                        print(f"  Stream state: active={is_active}, stopped={is_stopped}")
                                except:
                                    print(f"  Stream state: Unable to query")
                                
                                # Check system audio info
                                try:
                                    import subprocess
                                    result = subprocess.run(['arecord', '-l'], capture_output=True, text=True, timeout=1)
                                    if result.returncode == 0:
                                        devices = [line for line in result.stdout.split('\n') if 'card' in line.lower()]
                                        print(f"  Available capture devices: {len(devices)}")
                                    
                                    # Check for USB errors in dmesg
                                    result = subprocess.run(['dmesg', '|', 'tail', '-20'], 
                                                          shell=True, capture_output=True, text=True, timeout=1)
                                    usb_errors = [line for line in result.stdout.split('\n') 
                                                 if 'usb' in line.lower() and ('error' in line.lower() or 'fail' in line.lower())]
                                    if usb_errors:
                                        print(f"  Recent USB errors in dmesg: {len(usb_errors)}")
                                        for err in usb_errors[-3:]:
                                            print(f"    {err.strip()}")
                                    
                                    # Check CPU load
                                    with open('/proc/loadavg', 'r') as f:
                                        load = f.read().split()[0]
                                        print(f"  System load: {load}")
                                    
                                    # Check if we're running out of USB bandwidth
                                    result = subprocess.run(['lsusb', '-t'], capture_output=True, text=True, timeout=1)
                                    if result.returncode == 0 and 'AIOC' in result.stdout or 'All-In-One' in result.stdout:
                                        print(f"  USB tree shows AIOC device present")
                                    
                                    # Suggest USB reset as potential fix
                                    print(f"  Recommendation: This may be a USB/ALSA driver issue")
                                    print(f"  Try: Larger AUDIO_CHUNK_SIZE (2400-4800)")
                                    print(f"       or move AIOC to different USB port")
                                except:
                                    pass
                    else:
                        if consecutive_errors == 1:  # First error
                            if self.config.VERBOSE_LOGGING:
                                print(f"\n[Diagnostic] Audio capture error #{consecutive_errors}: {error_type}: {error_msg}")
                    
                    self.last_stream_error = f"{error_type}: {error_msg}"
                    
                    if consecutive_errors >= max_consecutive_errors:
                        if self.config.VERBOSE_LOGGING:
                            print(f"\n✗ Audio capture failed {consecutive_errors} times")
                            print(f"  Last error: {error_type}: {error_msg}")
                            print(f"  Total restarts this session: {self.stream_restart_count}")
                            print(f"  Attempting to restart audio stream...")
                        
                        # Try to restart the audio stream
                        self.restart_audio_input()
                        self.stream_restart_count += 1
                        consecutive_errors = 0
                        time.sleep(1)
                    else:
                        time.sleep(0.1)
            else:
                self.audio_capture_active = False
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
            
            # Recreate input stream
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
                    
                    elif char == 'x':
                        # Toggle proactive stream health management
                        self.config.ENABLE_STREAM_HEALTH = not self.config.ENABLE_STREAM_HEALTH
                        # If enabling, set interval to 60s if it's 0
                        if self.config.ENABLE_STREAM_HEALTH and self.config.STREAM_RESTART_INTERVAL == 0:
                            self.config.STREAM_RESTART_INTERVAL = 60
                    
                    elif char == 'p':
                        # Toggle manual PTT mode
                        self.manual_ptt_mode = not self.manual_ptt_mode
                        # Immediately apply the PTT state
                        self.set_ptt_state(self.manual_ptt_mode)
                    
                    elif char in '0123456789':
                        # Play announcement 0-9
                        if self.playback_source:
                            if char == '0':
                                # Station ID
                                if self.config.VERBOSE_LOGGING:
                                    print("\n[Keyboard] Key '0' pressed - queueing station_id")
                                # Try multiple extensions for station_id
                                for ext in ['.mp3', '.ogg', '.flac', '.m4a', '.wav']:
                                    if self.playback_source.queue_file(f"station_id{ext}"):
                                        break
                            else:
                                # Announcements 1-9
                                if self.config.VERBOSE_LOGGING:
                                    print(f"\n[Keyboard] Key '{char}' pressed - queueing announcement{char}")
                                # Try multiple extensions
                                for ext in ['.mp3', '.ogg', '.flac', '.m4a', '.wav']:
                                    if self.playback_source.queue_file(f"announcement{char}{ext}"):
                                        break
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
                
                # Add diagnostics if there have been restarts (white label, yellow number)
                diag = f" {WHITE}R:{YELLOW}{self.stream_restart_count}{RESET}" if self.stream_restart_count > 0 else ""
                
                # Show VAD level in dB if enabled (white label, yellow numbers, fixed width: always 5 chars like " -28dB" or "    ")
                vad_info = f" {YELLOW}{self.vad_envelope:3.0f}{RESET}{WHITE}dB{RESET}" if self.config.ENABLE_VAD else "     "
                
                # Show RX volume (white label, yellow number, always 3 chars for number)
                vol_info = f" {WHITE}Vol:{YELLOW}{self.config.INPUT_VOLUME:3.1f}{RESET}{WHITE}x{RESET}"
                
                # Show audio processing status (compact single-letter flags)
                # Only show what's enabled to keep line compact
                proc_flags = []
                if self.config.ENABLE_NOISE_GATE: proc_flags.append("N")
                if self.config.ENABLE_HIGHPASS_FILTER: proc_flags.append("F")
                if self.config.ENABLE_AGC: proc_flags.append("A")
                if self.config.ENABLE_NOISE_SUPPRESSION:
                    if self.config.NOISE_SUPPRESSION_METHOD == 'spectral': proc_flags.append("S")
                    elif self.config.NOISE_SUPPRESSION_METHOD == 'wiener': proc_flags.append("W")
                if self.config.ENABLE_ECHO_CANCELLATION: proc_flags.append("E")
                if not self.config.ENABLE_STREAM_HEALTH: proc_flags.append("X")  # X shows stream health is OFF
                
                proc_info = f" {WHITE}[{YELLOW}{','.join(proc_flags)}{WHITE}]{RESET}" if proc_flags else ""
                
                # File status indicators (if playback enabled)
                file_status_info = ""
                if self.playback_source:
                    file_status_info = " " + self.playback_source.get_file_status_string()
                
                # Extra padding to clear any orphaned text when line shortens
                print(f"\r{WHITE}{status_label}:{RESET} {status_symbol} {WHITE}M:{RESET}{mumble_status} {WHITE}PTT:{RESET}{ptt_status} {WHITE}VAD:{RESET}{vad_status}{vad_info} {WHITE}TX:{RESET}{radio_tx_bar} {WHITE}RX:{RESET}{radio_rx_bar}{vol_info}{proc_info}{file_status_info}{diag}     ", end="", flush=True)
            
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
        
        # Initialize AIOC
        if not self.setup_aioc():
            return False
        
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
        
        print("Press Ctrl+C to exit")
        print("Keyboard Controls:")
        print("  Mute: 't'=TX | 'r'=RX | 'm'=Global  |  Audio: 'v'=VAD | ','=Vol- | '.'=Vol+")
        print("  Proc: 'n'=Gate | 'f'=HPF | 'a'=AGC | 's'=Spectral | 'w'=Wiener | 'e'=Echo | 'x'=Restart")
        print("  PTT:  'p'=Manual PTT Toggle (override auto-PTT)")
        if self.config.ENABLE_PLAYBACK:
            print("  Play: '1-9'=Announcements | '0'=StationID")
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
            print("  Vol:X.Xx = RX volume multiplier (Radio → Mumble gain)")
            print("  [N,F,A,S,W,E,X] = Processing: N=NoiseGate F=HPF A=AGC S=Spectral W=Wiener E=Echo X=StreamHealth-OFF")
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
        
        # Now close audio streams
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
        
        if self.pyaudio_instance:
            try:
                self.pyaudio_instance.terminate()
            except:
                pass
        
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

if __name__ == "__main__":
    main()
