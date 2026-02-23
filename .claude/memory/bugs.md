# Bug History

## 2026-02-21 — AIOC USB timing jitter causing audio dropouts
**Symptom:** Audible dropout glitches in radio RX audio every few seconds.
**Cause:** CM108 chipset on Linux xHCI produces irregular USB frame delivery.
With a 50ms ALSA period (AUDIO_CHUNK_SIZE=2400), jitter accumulates to ~1700ms
of drift per 47s. arecord with `--buffer-size=19200` had zero xruns — confirming
the issue is at the PortAudio/PyAudio layer, not the AIOC hardware itself.
**Fix:** Switch input stream to PortAudio callback mode with
`frames_per_buffer = AUDIO_CHUNK_SIZE * 8` (19200 frames = 400ms period).
`AIOCRadioSource._audio_callback` stores each 400ms blob in `_chunk_queue`.
`get_audio()` slices it into 50ms sub-chunks with `time.sleep()`-based pacing
so the main loop still runs at the right rate. Tested: -0.1ms drift over 48s.

## 2026-02-21 — PTT click/pop on manual toggle
**Symptom:** Audible click in Mumble/speaker when pressing 'p' to toggle PTT.
**Cause:** The AIOC HID write that keys the radio relay causes a brief transient
on the USB isochronous audio path. The one-shot `_ptt_just_changed` flag was
cleared before the blob containing the transient arrived from the sub-buffer.
**Fix:** Replaced `_ptt_just_changed` bool with `_ptt_change_time` monotonic
timestamp. `AIOCRadioSource.get_audio()` applies a continuous gain envelope
(0 for 30ms, linear ramp 0→1 from 30→130ms) to all sub-chunks within 130ms
of any PTT state change. The keyboard handler now queues the HID write via
`_pending_ptt_state` so it runs between audio reads (no USB bus contention).

## 2026-02-21 — Speaker/Mumble desync after first PTT event
**Symptom:** Speaker and Mumble audio start in sync but drift ~100ms apart
permanently after the first PTT key-up or key-down event.
**Cause:** Speaker output bypasses VAD (always receives audio), but Mumble is
VAD-gated. During the 130ms click-suppression window (which applies silence/fade),
VAD reports no signal so Mumble skips ~100ms of chunks that the speaker plays.
**Fix:** Force `should_transmit = True` in `AIOCRadioSource.get_audio()` for the
entire 130ms window so Mumble receives the muted audio and stays in lock-step
with the speaker.

## 2026-02-21 — No click suppression when announcement activates PTT
**Symptom:** AIOC click audible on Mumble when an announcement file auto-keys PTT,
even though manual PTT toggle was click-free.
**Cause:** `_ptt_change_time` was only set in three places (Mumble RX handler,
pending-PTT apply, status_monitor release). The announcement code path at line
3739 called `set_ptt_state(True)` directly without updating `_ptt_change_time`,
so the click suppression envelope in `AIOCRadioSource.get_audio()` never fired.
**Fix:** Added `self._ptt_change_time = time.monotonic()` immediately after the
`set_ptt_state(True)` call in the announcement PTT activation block.

## 2026-02-21 — `announcement_delay_active` stuck True after stop_playback
**Symptom:** After stopping playback mid-announcement, the next queued file would
silently skip audio while returning `ptt_required=True`, keeping PTT active
indefinitely if the delay timer had not yet expired.
**Cause:** `announcement_delay_active` is cleared inside the PTT branch of
`audio_transmit_loop`. When `stop_playback()` terminates the file, `ptt_required`
becomes False and the loop exits the PTT branch — so the expiry check never ran.
**Fix:** Added a safety clear before `get_mixed_audio()` on every loop iteration:
if `announcement_delay_active` and the timer has expired, clear the flag.
Also initialized both `announcement_delay_active` and `_announcement_ptt_delay_until`
in `__init__` (previously used `getattr(..., False)` defensive guards).

## 2026-02-21 — AIOC detection steals C-Media speaker device
**Symptom:** `Warning: Speaker output failed to open: [Errno -9985] Device unavailable`
**Cause:** `find_aioc_audio_device()` keyword list included `'c-media'`, which matched
the C-Media USB Headphone Set before the real AIOC. The headset was then opened as
both input and output AIOC streams, leaving it unavailable for speaker monitoring.
**Fix:** Replaced `'c-media'` with `'all-in-one'` in the keyword list. The real AIOC
("All-In-One-Cable: USB Audio") is still found via `'usb audio'` and `'all-in-one'`.

## 2026-02-22 — Complete silence to Mumble (AUDIO_CHUNK_SIZE=9600)
**Symptom:** No radio audio reaches Mumble at all.
**Cause:** `AUDIO_CHUNK_SIZE` was changed from 2400 to 9600 in config. With 8× multiplier,
ALSA period became 76800 frames (1.6s). The queue timeout was hardcoded to 0.800s,
which always expired before the first callback blob arrived. `get_audio()` always
returned `(None, False)`.
**Fix:** Restored `AUDIO_CHUNK_SIZE = 2400`. Made queue timeout dynamic:
`timeout = (AUDIO_CHUNK_SIZE * 8 / AUDIO_RATE) * 2` so it adapts to any chunk size.

## 2026-02-22 — VAD units mismatch causes choppy audio
**Symptom:** VAD opens/closes too aggressively — no smooth envelope, min-duration and
release checks effectively disabled.
**Cause:** Config values (VAD_ATTACK=0.02, VAD_RELEASE=0.3, VAD_MIN_DURATION=0.1) are in
seconds. Code divided by 1000.0 (treating as ms), making attack_coef=10000 (clamped to 1.0,
instant). Duration comparisons multiplied by 1000 (converting to ms) then compared to
seconds values — e.g., 0.1s compared against 100ms.
**Fix:** Removed `/ 1000.0` from attack/release coefficients. Removed `* 1000` from
duration calculations, keeping everything in seconds.

## 2026-02-22 — SDR buffer_multiplier computed but never used
**Symptom:** SDR_BUFFER_MULTIPLIER config had no effect on audio buffering.
**Cause:** `buffer_size = AUDIO_CHUNK_SIZE * buffer_multiplier` was computed but
`frames_per_buffer=self.config.AUDIO_CHUNK_SIZE` was used instead.
**Fix:** Changed `frames_per_buffer` to use `buffer_size` in both stereo and mono fallback paths.

## 2026-02-22 — Duplicate SDR_BUFFER_MULTIPLIER config key
**Symptom:** SDR1's buffer multiplier setting ignored.
**Cause:** `SDR_BUFFER_MULTIPLIER = 16` appeared at both line 323 (SDR1 section) and
line 354 (after SDR2 section). The second occurrence overwrote the first.
**Fix:** Removed the duplicate entry at line 354.

## 2026-02-21 — SDR duck indicator triggered with no SDR source connected
**Symptom:** Status bar showed `SDR1:[---DUCK---]` even with no SDR audio present.
**Cause:** `sdr1_ducked` / `sdr2_ducked` status flags were not cleared when the
corresponding SDR was muted or had no signal level.
**Fix:** Conditional: `sdr1_ducked = self.sdr1_ducked if not sdr1_muted and current_sdr1_level > 0 else False`.
Same pattern applied to `sdr2_ducked`.
