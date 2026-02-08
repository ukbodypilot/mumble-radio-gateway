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
            'AUDIO_CHUNK_SIZE': 2400,
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
            'ENABLE_NOISE_SUPPRESSION': True,
            'NOISE_SUPPRESSION_METHOD': 'spectral',
            'NOISE_SUPPRESSION_STRENGTH': 0.6,
            'ENABLE_NOISE_GATE': True,
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
            'STATUS_UPDATE_INTERVAL': 5,
            'MAX_MUMBLE_BUFFER_SECONDS': 2.0,
            'BUFFER_MANAGEMENT_VERBOSE': True,
            'ENABLE_VAD': True,
            'VAD_THRESHOLD': -33,
            'VAD_ATTACK': 20,
            'VAD_RELEASE': 300,
            'VAD_MIN_DURATION': 150,
            'STREAM_RESTART_INTERVAL': 60,
            'STREAM_RESTART_IDLE_TIME': 3,
            'ENABLE_VOX': True,
            'VOX_THRESHOLD': -40,
            'VOX_ATTACK_TIME': 50,
            'VOX_RELEASE_TIME': 500
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
        
        # Audio processing state
        self.noise_profile = None  # For spectral subtraction
        self.gate_envelope = 0.0  # For noise gate smoothing
        self.highpass_state = None  # For high-pass filter state
    
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
    
    def format_level_bar(self, level, muted=False):
        """Format audio level as a visual bar (0-100 scale)"""
        # Show MUTE if muted
        if muted:
            return "[---MUTE---]  M"
        
        # Create a 10-character bar graph
        bar_length = 10
        filled = int((level / 100.0) * bar_length)
        
        # Different colors based on level
        if level < 5:
            return "[----------]  0%"
        else:
            bar = '█' * filled + '-' * (bar_length - filled)
            return f"[{bar}] {level:2d}%"
    
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
        
        # Key PTT if not already active
        if not self.ptt_active:
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
        # Suppress ALSA warnings temporarily
        import os
        import sys
        
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
                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE
            )
            if self.config.VERBOSE_LOGGING:
                latency_ms = (self.config.AUDIO_CHUNK_SIZE / self.config.AUDIO_RATE) * 1000
                print(f"✓ Audio output configured ({latency_ms:.1f}ms latency)")
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
                                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE
                            )
                            
                            self.input_stream = self.pyaudio_instance.open(
                                format=audio_format,
                                channels=self.config.AUDIO_CHANNELS,
                                rate=self.config.AUDIO_RATE,
                                input=True,
                                input_device_index=input_idx,
                                frames_per_buffer=self.config.AUDIO_CHUNK_SIZE
                            )
                            
                            print("✓ Audio initialized successfully after USB reset")
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
        if self.config.VERBOSE_LOGGING:
            print(f"\nConnecting to Mumble: {self.config.MUMBLE_SERVER}:{self.config.MUMBLE_PORT}...")
        
        try:
            # Create Mumble client
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
            self.mumble.start()
            self.mumble.is_ready()
            
            print(f"✓ Connected as '{self.config.MUMBLE_USERNAME}'")
            
            # Join channel if specified
            if self.config.MUMBLE_CHANNEL:
                try:
                    channel = self.mumble.channels.find_by_name(self.config.MUMBLE_CHANNEL)
                    if channel:
                        channel.move_in()
                        print(f"✓ Joined channel: {self.config.MUMBLE_CHANNEL}")
                except:
                    print(f"✗ Could not join channel: {self.config.MUMBLE_CHANNEL}")
            
            if self.config.VERBOSE_LOGGING:
                print(f"  Loop rate: {self.config.MUMBLE_LOOP_RATE}s ({1/self.config.MUMBLE_LOOP_RATE:.0f} Hz)")
            
            return True
            
        except Exception as e:
            print(f"✗ Could not connect to Mumble: {e}")
            return False
    
    def audio_transmit_loop(self):
        """Continuously capture audio from radio and send to Mumble"""
        if self.config.VERBOSE_LOGGING:
            print("✓ Audio transmit thread started")
        
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self.running:
            if self.input_stream and not self.restarting_stream:
                try:
                    # Proactive stream health check
                    # If stream has been alive for a while, proactively restart it
                    # This prevents the -9999 error from happening
                    # Only restart when radio is IDLE (no active transmission)
                    current_time = time.time()
                    time_since_creation = current_time - self.stream_age
                    time_since_vad_active = current_time - self.last_vox_active_time if hasattr(self, 'last_vox_active_time') else 999
                    
                    # Check if we should do proactive restart
                    if (self.config.STREAM_RESTART_INTERVAL > 0 and 
                        time_since_creation > self.config.STREAM_RESTART_INTERVAL):
                        
                        # Only restart if radio has been idle for the required time
                        # This prevents interrupting active transmissions
                        if not self.vad_active and time_since_vad_active > self.config.STREAM_RESTART_IDLE_TIME:
                            if self.config.VERBOSE_LOGGING:
                                print(f"\n[Maintenance] Proactive stream restart (age: {time_since_creation:.0f}s, idle: {time_since_vad_active:.0f}s)")
                            self.restart_audio_input()
                            self.stream_age = time.time()
                            time.sleep(0.2)
                            continue
                        # else: Skip restart - radio is active or not idle long enough
                    
                    # Check if stream is active before reading
                    if not self.input_stream.is_active():
                        if self.config.VERBOSE_LOGGING:
                            print("\n[Diagnostic] Stream became inactive, restarting...")
                        consecutive_errors = max_consecutive_errors
                        raise Exception("Stream inactive")
                    
                    # Read audio with error recovery
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
                    consecutive_errors = 0
                    
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
                    # This removes hiss, noise, and rumble before sending to Mumble
                    data = self.process_audio_for_mumble(data)
                    
                    # Check VAD - only send to Mumble if radio signal is detected
                    # This prevents filling Mumble's buffer with silence
                    should_transmit = self.check_vad(data)
                    
                    if not should_transmit:
                        # No signal detected, don't send to Mumble
                        # This is the key to preventing buffer accumulation!
                        continue
                    
                    # Check RX mute - if muted, don't send to Mumble
                    if self.rx_muted:
                        continue
                    
                    # Send to Mumble with buffer management to prevent delay buildup
                    if self.mumble and self.config.MAX_MUMBLE_BUFFER_SECONDS > 0:
                        # Check if Mumble's buffer is too full
                        # pymumble accumulates audio in sound_output.buffer
                        try:
                            current_buffer_len = len(self.mumble.sound_output.buffer)
                            
                            # Calculate max buffer size from config
                            max_buffer_samples = int(self.config.AUDIO_RATE * self.config.MAX_MUMBLE_BUFFER_SECONDS)
                            
                            if current_buffer_len > max_buffer_samples:
                                # Buffer is getting too full - skip this chunk to let it drain
                                if self.config.BUFFER_MANAGEMENT_VERBOSE and self.mumble_buffer_full_count % 10 == 0:
                                    delay_seconds = current_buffer_len / self.config.AUDIO_RATE
                                    print(f"\n[Buffer] Mumble buffer at {delay_seconds:.1f}s ({current_buffer_len} samples), dropping chunk to maintain latency")
                                self.mumble_buffer_full_count += 1
                                time.sleep(0.01)  # Brief pause
                                continue  # Skip adding this chunk
                        except (AttributeError, TypeError):
                            # If buffer attribute doesn't exist or is wrong type, just add normally
                            pass
                        
                        # Add audio to Mumble's output buffer
                        self.mumble.sound_output.add_sound(data)
                    elif self.mumble:
                        # Buffer management disabled, just add audio
                        self.mumble.sound_output.add_sound(data)
                        
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
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE
                )
            
            if input_idx is not None:
                self.input_stream = self.pyaudio_instance.open(
                    format=audio_format,
                    channels=self.config.AUDIO_CHANNELS,
                    rate=self.config.AUDIO_RATE,
                    input=True,
                    input_device_index=input_idx,
                    frames_per_buffer=self.config.AUDIO_CHUNK_SIZE
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
                        status = "MUTED" if self.tx_muted else "UNMUTED"
                        print(f"\n[Keyboard] TX (Mumble → Radio) {status}")
                    
                    elif char == 'r':
                        # Toggle RX mute (Radio → Mumble)
                        self.rx_muted = not self.rx_muted
                        status = "MUTED" if self.rx_muted else "UNMUTED"
                        print(f"\n[Keyboard] RX (Radio → Mumble) {status}")
                    
                    elif char == 'm':
                        # Global mute toggle
                        # If both are unmuted, mute both
                        # If one or both are muted, mute both
                        # If both are muted, unmute both
                        if self.tx_muted and self.rx_muted:
                            # Both muted → unmute both
                            self.tx_muted = False
                            self.rx_muted = False
                            print(f"\n[Keyboard] GLOBAL UNMUTE (TX and RX)")
                        else:
                            # One or both unmuted → mute both
                            self.tx_muted = True
                            self.rx_muted = True
                            print(f"\n[Keyboard] GLOBAL MUTE (TX and RX)")
                
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
            
            # Check PTT timeout
            if self.ptt_active:
                if current_time - self.last_sound_time > self.config.PTT_RELEASE_DELAY:
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
                
                if self.audio_capture_active and time_since_last_capture < 2.0:
                    status = "✓ ACTIVE"
                elif time_since_last_capture < 10.0:
                    status = "⚠ IDLE"
                else:
                    status = "✗ STOPPED"
                    # Attempt recovery
                    if self.config.VERBOSE_LOGGING:
                        print(f"\n{status}")
                        print("  Attempting to restart audio input...")
                    self.restart_audio_input()
                    continue
                
                # Print status
                mumble_status = "✓" if self.mumble else "✗"
                ptt_status = "ON" if self.ptt_active else "--"
                vad_status = "🔊" if self.vad_active else "--"
                
                # Format audio levels with bar graphs
                # Note: From radio's perspective:
                #   - rx_audio_level = Mumble → Radio (Radio TX)
                #   - tx_audio_level = Radio → Mumble (Radio RX)
                radio_tx_bar = self.format_level_bar(self.rx_audio_level, muted=self.tx_muted)
                
                # RX bar: Show 0% if VAD is blocking (not actually transmitting to Mumble)
                # Only show level when VAD is active (actually sending to Mumble)
                if self.config.ENABLE_VAD and not self.vad_active:
                    radio_rx_bar = self.format_level_bar(0, muted=self.rx_muted)  # Not transmitting = 0%
                else:
                    radio_rx_bar = self.format_level_bar(self.tx_audio_level, muted=self.rx_muted)
                
                # Add diagnostics if there have been restarts
                diag = f" R:{self.stream_restart_count}" if self.stream_restart_count > 0 else ""
                
                # Show VAD level in dB if enabled
                vad_info = f" {self.vad_envelope:.0f}dB" if self.config.ENABLE_VAD else ""
                
                print(f"\r[{status}] M:{mumble_status} PTT:{ptt_status} VAD:{vad_status}{vad_info} TX:{radio_tx_bar} RX:{radio_rx_bar}{diag}    ", end="", flush=True)
            
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
        if self.config.STREAM_RESTART_INTERVAL > 0:
            print(f"  Stream Health: Auto-restart every {self.config.STREAM_RESTART_INTERVAL}s (when idle {self.config.STREAM_RESTART_IDLE_TIME}s+)")
        else:
            print(f"  Stream Health: No auto-restart (may experience -9999 errors)")
        
        print("Press Ctrl+C to exit")
        print("Keyboard Controls: 't' = TX mute, 'r' = RX mute, 'm' = Global mute/unmute")
        print("=" * 60)
        print()
        
        # Print status line legend (only in verbose mode)
        if self.config.VERBOSE_LOGGING:
            print("Status Line Legend:")
            print("  [✓/⚠/✗]  = Audio capture status (ACTIVE/IDLE/STOPPED)")
            print("  M:✓/✗    = Mumble connected/disconnected")
            print("  PTT:ON/-- = Push-to-talk active/inactive")
            print("  VAD:🔊/-- = Voice detection active/silent (dB = current level)")
            print("  TX:[bar] = Mumble → Radio audio level")
            print("  RX:[bar] = Radio → Mumble audio level")
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
