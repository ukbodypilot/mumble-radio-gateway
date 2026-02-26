# Bug History — Mumble Radio Gateway

## Audio Pipeline Bugs
- **SDR burst/choppy audio**: `frames_per_buffer=153600` (16× chunk) caused ALSA DMA to fire every 3.2s. Fixed to `AUDIO_CHUNK_SIZE` (9600 = 200ms).
- **Pop at SDR onset**: Old 0.1s attack timer dropped first chunk. Fixed: instant-attack (`check_signal_instant`, -50dB) + 10ms fade-in.
- **Pop at SDR offset (timing-window bug)**: Fade-out triggered by `(hold_until - current_time) < chunk_dur` — missed when AIOC read took >200ms. Fixed: transition-based fade-out when `prev_included` flips False.
- **SDR gated on AIOC failures**: Mixer was inside AIOC gate. Fixed: mixer runs unconditionally; AIOC errors absorbed in `get_audio()`.
- **Sequential double-blocking (400ms loop)**: AIOC 200ms + SDR 200ms in same thread. Fixed: SDR background reader thread, `get_audio()` is non-blocking queue pop.
- **GIL contention causing ALSA overruns**: Reader thread did numpy between reads, competing with main loop. Fixed: reader thread does read+append only; all processing in main thread.
- **Mumble encoder starvation**: `data is None → continue` skipped `add_sound()`. Opus resets across gaps. Fixed: substitute silence, fall through to `add_sound()`.
- **duck-out regression (all audio broken)**: `sdr_active_at_transition` used `check_signal_instant` on raw loopback — always True with SDR app running, silencing first 1s of every AIOC transmission. Fixed: use `sdr_prev_included` instead.
- **SDR2 duck-through on SDR1 buffer gaps**: SDR-to-SDR duck check iterated `sdrs_to_include` (only sources with actual audio data). When SDR1's buffer ran dry between bursty deliveries, SDR1 wasn't in `sdrs_to_include` (get_audio returned None → `continue` at line 1942), so SDR2 didn't see SDR1's active hold timer and played through. Fixed: iterate `sorted_sdrs` (all processed SDRs) instead, checking `_sdr_trace` which is populated even when audio is None.

## Status Bar / UI Bugs
- **SDR2 failure shown as silent (indistinguishable)**: When SDR2 `setup_audio()` returns False, object kept with `enabled=False` but status bar checked only `if self.sdr2_source:` — showed `SDR2:[----------] 0%` identical to a working-but-silent source. Fixed: gate on `self.sdr2_source.enabled` so failed/disabled SDR2 is omitted from status bar.
- **SDR2 error message hardcoded `hw:4,1`**: Warning on init failure always printed the default device name regardless of config. Fixed: use `self.config.SDR2_DEVICE_NAME`.
- **SDR2 init leftover debug prints**: Two unconditional prints dumping `SDR2_DEVICE_NAME from config:` and `SDR2_PRIORITY from config:` were not guarded by VERBOSE_LOGGING. Removed.

## Config / Code Bugs
- **Config parser crash on decimal**: `int('0.3')` raised ValueError, silently abandoning all config after that line. Fixed: `VAD_RELEASE: 1.0` default (float); parser tries `float()` fallback on ValueError.
- **global_muted UnboundLocalError**: Set inside `if self.sdr_source:` block, used in `if self.sdr2_source:` block. Fixed: calculated before both blocks.

## Installer Bugs
- **numlids=3 silently ignored on Debian**: RPi kernel param, not standard. Fixed: `enable=1,1,1 index=4,5,6`.
- **Loopback card count wrong**: `grep -c "Loopback"` counted 2 lines per card. Fixed: count `device 0` lines only.
- **Installer aborted at step 5**: `set -e` + `sudo tee /etc/security/limits.d/...` failed (dir missing on minimal Debian). Fixed: `set +e` around step 5, `sudo mkdir -p` first.
- **darkice.cfg never created**: Path pointed to `examples/darkice.cfg.example` but file is in `scripts/`. Fixed.
- **DarkIce 1.5 parser crash**: Word "password" in comment before first `[section]` header → "no current section" crash. Fixed: removed from comment text.
- **WirePlumber locks loopback to S32_LE**: DarkIce needs S16_LE. Fixed: WirePlumber rule disabling `alsa_card.platform-snd_aloop.*`.
- **WirePlumber hides AIOC from PyAudio**: Fixed: WirePlumber rule disabling `alsa_card.usb-AIOC_*`.
- **AIOC hidraw inaccessible**: udev rule only covered `SUBSYSTEM=="usb"`, not `SUBSYSTEM=="hidraw"`. PTT HID access requires hidraw. Fixed: added hidraw rule.
- **Wrong Python HID package**: Installer installed `hidapi`; gateway uses `hid.Device` from `hid` package. Fixed.
- **pymumble-py3 SSL broken on Python 3.12+**: `ssl.wrap_socket` removed, `ssl.PROTOCOL_TLSv1_2` deprecated. Fixed: monkey-patch before import.
